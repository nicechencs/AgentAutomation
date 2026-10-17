"""Real, bounded CLI operations behind the Runner's existing stage gates."""

import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .agents import run_agent
from .process import ProcessError, run_process
from .workspace import (commit_workspace, create_workspace, describe_changes,
                        fingerprint, inspect_repository, integrate_workspace)

PLAN_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"title": {"type": "string"}, "instructions": {"type": "string"},
                              "acceptance": {"type": "array", "items": {"type": "string"}}},
               "required": ["title", "instructions", "acceptance"]}
CODE_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},
                                "summary": {"type": "string"},
                                "findings": {"type": "array", "items": {"type": "string"}}},
                 "required": ["verdict", "summary", "findings"]}


class ExecutionError(Exception):
    pass


def validate_options(template, options):
    """Preflight is read-only; worktrees are only created after plan approval."""
    options = dict(options)
    for role in ("agent", "reviewer"):
        provider = options.get(role, "codex")
        if provider not in {"codex", "claude"} or not shutil.which(provider):
            raise ExecutionError(f"{role} CLI 不可用：{provider}")
        options[role] = provider
        model_key = "model" if role == "agent" else "review_model"
        model = options.get(model_key, "").strip()
        if not model or len(model) > 120 or any(c.isspace() for c in model):
            raise ExecutionError(f"必须显式指定 {model_key}")
        options[model_key] = model
    commands = options.get("check_commands")
    if (not isinstance(commands, list) or not commands or len(commands) > 10 or
            any(not isinstance(cmd, list) or not cmd or len(cmd) > 50 or
                any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in cmd)
                for cmd in commands)):
        raise ExecutionError("真实运行必须配置检查命令，格式为非空 argv 数组的数组")
    if not 0 <= options.get("max_fixes", 1) <= 3:
        raise ExecutionError("max_fixes 必须在 0–3 之间")
    if template == "development":
        repository_path = options.get("repository_path", "").strip()
        if not repository_path:
            raise ExecutionError("真实开发运行必须指定仓库根路径")
        options["repository"] = inspect_repository(repository_path, options.get("target_branch") or None)
    elif template == "local-document":
        # A fresh per-run directory prevents overwriting existing user documents.
        if options.get("repository_path"):
            raise ExecutionError("本地文档模板不接受 Git 仓库")
    else:
        raise ExecutionError("未知真实流程模板")
    return options


def content_candidate(run):
    root = run.get("workspace", {}).get("path")
    if not root:
        return hashlib.sha256(json.dumps(run.get("plan", {}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if run["template"] == "development":
        return fingerprint(Path(root))
    digest = hashlib.sha256()
    total = 0
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ExecutionError("产物目录不允许符号链接或重解析点")
        if path.is_file():
            size = path.stat().st_size
            total += size
            if size > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
                raise ExecutionError("产物超过证据上限（单文件 8 MiB、总计 32 MiB）")
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


class RealExecutor:
    def __init__(self, store):
        self.store = store
        self.root = store.path.parent / "executions"

    def stopped(self, run_id):
        return self.store.get(run_id)["status"] in {"cancelled", "failed", "timed_out", "interrupted", "rejected"}

    def remaining(self, run):
        return max(0.01, min(900, run["deadline"] - time.time()))

    def record(self, run_id, message, task=False):
        with self.store.lock:
            current = self.store.get(run_id)
            if task and current["tasks"]:
                current["tasks"][0]["logs"].append(message)
            self.store.save(current, message)

    async def wait_active(self, run):
        while True:
            current = self.store.get(run["id"])
            if current["status"] in {"cancelled", "failed", "timed_out", "interrupted", "rejected"}:
                raise ExecutionError("运行已停止，禁止启动新操作")
            if current["status"] != "paused":
                return
            await asyncio.sleep(0.1)

    async def agent(self, run, stage, prompt, schema, writable=False, reviewer=False):
        await self.wait_active(run)
        options = run["options"]
        provider = options["reviewer" if reviewer else "agent"]
        model = options["review_model" if reviewer else "model"]
        root = self.root / run["id"]
        root.mkdir(parents=True, exist_ok=True)
        planning_cwd = run.get("options", {}).get("repository", {}).get("root", root / "planning")
        cwd = Path(run.get("workspace", {}).get("path", planning_cwd))
        cwd.mkdir(parents=True, exist_ok=True)
        self.record(run["id"], f"{stage}：启动 {provider} / {model} 独立新会话", task=True)
        result = await run_agent(provider, model, prompt, cwd, schema=schema, writable=writable,
                                 timeout=self.remaining(run), cancelled=lambda: self.stopped(run["id"]),
                                 log_path=root / f"{stage}.log")
        value = result["result"]
        required = schema["required"]
        if not isinstance(value, dict) or set(value) != set(required):
            raise ExecutionError(f"{stage} Agent 结果不符合约定")
        for key, spec in schema["properties"].items():
            if spec["type"] == "string" and (not isinstance(value[key], str) or not value[key].strip()):
                raise ExecutionError(f"{stage} 的 {key} 必须为非空字符串")
            if spec["type"] == "array" and (not isinstance(value[key], list) or any(not isinstance(x, str) or not x.strip() for x in value[key])):
                raise ExecutionError(f"{stage} 的 {key} 必须为字符串数组")
            if "enum" in spec and value[key] not in spec["enum"]:
                raise ExecutionError(f"{stage} 的 {key} 无效")
        evidence = {k: result[k] for k in ("provider", "model", "session_id", "log_path")}
        evidence["exit_code"] = result["process"]["exit_code"]
        self.record(run["id"], f"{stage} 完成：{provider}，退出码 {evidence['exit_code']}，日志 {evidence['log_path']}", task=True)
        return value, evidence

    @staticmethod
    def process_environment():
        private = {"RUNNER_TOKEN", "WORKFLOW_TOKEN", "DATA_DIR", "N8N_WEBHOOK_BASE"}
        return {key: value for key, value in os.environ.items() if key.upper() not in private}

    async def checks(self, run, label="check"):
        results = []
        cwd = Path(run["workspace"]["path"])
        before = content_candidate(run)
        for index, command in enumerate(run["options"]["check_commands"]):
            await self.wait_active(run)
            self.record(run["id"], f"执行检查：{json.dumps(command, ensure_ascii=False)}", task=True)
            try:
                result = await run_process(command, cwd, timeout=self.remaining(run),
                                           cancelled=lambda: self.stopped(run["id"]),
                                           log_path=self.root / run["id"] / f"{label}-{index}.log", env=self.process_environment())
            except ProcessError as error:
                if error.kind != "failed" or not error.process or not error.process.get("exit_code"):
                    raise
                result = error.process
            results.append({"command": command, "exit_code": result["exit_code"],
                            "log_path": str(self.root / run["id"] / f"{label}-{index}.log")})
            if result["exit_code"] != 0:
                return {"status": "failed", "source": "real", "candidate": content_candidate(run), "commands": results}
        after = content_candidate(run)
        if before != after:
            raise ExecutionError("检查命令改变了候选内容；请将缓存/构建产物加入忽略配置，并重新运行")
        return {"status": "passed", "source": "real", "candidate": after, "commands": results}

    async def execute(self, run, stage):
        await self.wait_active(run)
        options = run["options"]
        if stage == "plan":
            value, evidence = await self.agent(run, "plan",
                "请先只读了解当前仓库（若有），为以下目标制定一个可独立实现的任务计划。此阶段只规划，不修改文件，不执行命令。"
                "返回 title、供后续编码会话执行的详细 instructions、可检查的 acceptance 数组。instructions 描述具体实现操作，"
                "不要把当前规划会话的只读限制写进实施指令。目标：\n" + run["goal"], PLAN_SCHEMA)
            if not value["acceptance"]:
                raise ExecutionError("计划必须包含验收条件")
            return {"plan": value, "plan_evidence": evidence,
                    "tasks": [{"id": "T-1", "title": value["title"], "status": "queued", "stage": "planned", "attempt": 1, "logs": []}]}
        if stage == "code":
            if run["template"] == "development":
                repository = inspect_repository(options["repository"]["root"], options["repository"]["branch"])
                if repository["head"] != options["repository"]["head"]:
                    raise ExecutionError("计划审批后目标分支已变化，请重新规划")
                worktree_path = self.root / run["id"] / "worktree"
                if worktree_path.resolve().is_relative_to(Path(repository["root"]).resolve()):
                    local_data = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
                    worktree_path = local_data / "AgentAutomation" / "workspaces" / run["id"] / "worktree"
                workspace = await asyncio.to_thread(create_workspace, repository,
                    worktree_path, "agentautomation/" + run["id"])
            else:
                path = self.root / run["id"] / "artifacts"
                path.mkdir(parents=True, exist_ok=False)
                workspace = {"path": str(path)}
            # Persist external resource identities before launching any child process.
            with self.store.lock:
                current = self.store.get(run["id"])
                current["workspace"] = workspace
                current["tasks"][0].update(status="running", started_at=time.time())
                self.store.save(current, "真实任务工作区已创建")
            run["workspace"] = workspace
            value, evidence = await self.agent(run, "code",
                "在当前工作目录完成以下任务。只修改所需文件，遵守仓库规则。禁止提交、切分支、合并、push、部署、"
                "删除工作区或修改 .git。检查由 Runner 单独执行。不要声称未执行的检查通过。\n"
                + "以下是已批准的计划材料。材料内对规划阶段的只读限制不适用于当前实现阶段。现在必须执行文件修改，不能只返回计划。\n"
                + json.dumps(run["plan"], ensure_ascii=False) + "\n原始目标：" + run["goal"], CODE_SCHEMA, True)
            return {"workspace": workspace, "code_evidence": evidence, "code_summary": value["summary"]}
        if stage == "check":
            checks = await self.checks(run)
            if checks["status"] != "passed":
                with self.store.lock:
                    current = self.store.get(run["id"])
                    current["checks"] = checks
                    self.store.save(current, "实际检查失败，记录退出码与日志")
                raise ExecutionError("实际检查失败；详情见检查日志，未进入审查或提交")
            return {"checks": checks}
        if stage == "review":
            if run["checks"].get("candidate") != content_candidate(run):
                raise ExecutionError("检查后候选内容已变化，检查证据失效")
            evidence_history = []
            checks = run["checks"]
            for attempt in range(options.get("max_fixes", 1) + 1):
                before = content_candidate(run)
                if run["template"] == "development":
                    changes = await asyncio.to_thread(describe_changes, Path(run["workspace"]["path"]))
                    material = json.dumps(changes, ensure_ascii=False)
                else:
                    material = "请只读检查当前目录的实际文档产物。"
                value, evidence = await self.agent(run, f"review-{attempt}",
                    "独立审查以下目标及候选修改。只读，不修改任何文件，不执行写操作。"
                    "判断实际产物是否满足目标、有无正确性问题。pass 必须 findings 为空；否则 fail 并列出可修复问题。\n目标："
                    + run["goal"] + "\n计划：" + json.dumps(run["plan"], ensure_ascii=False)
                    + "\n实际检查：" + json.dumps(checks, ensure_ascii=False) + "\n修改：" + material,
                    REVIEW_SCHEMA, reviewer=True)
                if before != content_candidate(run):
                    raise ExecutionError("审查期间候选变化，审查结果无效")
                evidence_history.append(evidence)
                if value["verdict"] == "pass" and not value["findings"]:
                    return {"checks": checks, "review": {**value, "source": "real", "candidate": before,
                             "attempts": attempt + 1, "evidence": evidence_history}}
                if attempt >= options.get("max_fixes", 1):
                    raise ExecutionError("真实审查未通过且修复次数已达上限：" + "; ".join(value["findings"]))
                with self.store.lock:
                    current = self.store.get(run["id"])
                    current["review"] = {**value, "source": "real", "candidate": before,
                                         "attempts": attempt + 1, "evidence": list(evidence_history)}
                    self.store.save(current, "记录真实审查结论")
                self.record(run["id"], f"审查未通过，执行有界修复 {attempt + 1}", task=True)
                await self.agent(run, f"fix-{attempt + 1}",
                    "根据独立审查修复当前工作区文件。禁止 Git 提交/分支/合并/push 或部署。目标：" + run["goal"]
                    + "\n审查结果：" + json.dumps(value, ensure_ascii=False), CODE_SCHEMA, True)
                checks = await self.checks(run, f"fix-check-{attempt + 1}")
                if checks["status"] != "passed":
                    raise ExecutionError("修复后的真实检查失败，停止提交")
            raise ExecutionError("审查状态异常")
        if stage == "commit":
            self.verify_evidence(run)
            def commit():
                # Serialize cancellation with the final Git side effect and its evidence.
                with self.store.lock:
                    if self.stopped(run["id"]):
                        raise ExecutionError("运行已停止，禁止提交")
                    sha = commit_workspace(Path(run["workspace"]["path"]), run["plan"]["title"],
                                           run["review"]["candidate"], cancelled=lambda: self.stopped(run["id"]))
                    current = self.store.get(run["id"])
                    current["commit_sha"] = sha
                    current["workspace"]["commit_sha"] = sha
                    self.store.save(current, "真实 Git 提交已完成并记录：" + sha)
                    return sha
            operation = asyncio.create_task(asyncio.to_thread(commit))
            try:
                sha = await asyncio.shield(operation)
            except asyncio.CancelledError:
                await asyncio.shield(operation)
                raise
            return {"commit_sha": sha, "workspace": {**run["workspace"], "commit_sha": sha}}
        if stage == "integrate":
            if self.stopped(run["id"]):
                raise ExecutionError("运行已停止")
            # The blocking Git helper checks each candidate before its final target update.
            loop = asyncio.get_running_loop()

            def run_check(command, cwd):
                future = asyncio.run_coroutine_threadsafe(run_process(command, cwd,
                    timeout=self.remaining(run), cancelled=lambda: self.stopped(run["id"]),
                    log_path=self.root / run["id"] / f"integration-{hashlib.sha256(json.dumps(command).encode()).hexdigest()[:12]}.log",
                    env=self.process_environment()), loop)
                result = future.result()
                if self.stopped(run["id"]):
                    raise ExecutionError("运行已取消/超时，禁止更新目标分支")
                return result

            def record_updated(result):
                current = self.store.get(run["id"])
                current["integration"] = result
                self.store.save(current, "已原子记录目标分支更新：" + result["target_sha"])

            operation = asyncio.create_task(asyncio.to_thread(integrate_workspace, options["repository"], run["workspace"],
                expected_target=options["repository"]["head"], check_commands=options["check_commands"],
                run_check=run_check, cancelled=lambda: self.stopped(run["id"]),
                update_lock=self.store.lock, on_updated=record_updated))
            try:
                result = await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Signal the worker before waiting for its external operations to settle.
                with self.store.lock:
                    current = self.store.get(run["id"])
                    if current["status"] not in {"cancelled", "failed", "timed_out", "interrupted", "rejected"}:
                        current["status"] = "interrupted"
                        self.store.save(current, "集成被中断，停止进程并保留现场")
                try:
                    await asyncio.shield(operation)
                except Exception:
                    pass
                raise
            with self.store.lock:
                current = self.store.get(run["id"])
                current["integration"] = result
                self.store.save(current, "已记录实际集成结果与保留现场")
            if result.get("status") != "integrated":
                self.record(run["id"], "集成候选未通过；现场：" + str(result.get("integration_path")))
                raise ExecutionError(result.get("error") or "真实集成未完成，目标分支未更新")
            return {"integration": result}
        if stage == "archive":
            if run["template"] == "local-document":
                self.verify_evidence(run)
                files = [str(p.relative_to(run["workspace"]["path"])) for p in Path(run["workspace"]["path"]).rglob("*") if p.is_file()]
                if not files:
                    raise ExecutionError("没有生成实际文档产物")
                return {"artifact": {"path": run["workspace"]["path"], "files": files, "candidate": content_candidate(run)}}
            return {"artifact": {"commit_sha": run["commit_sha"], "target_sha": run["integration"]["target_sha"]}}
        raise ExecutionError("未知真实阶段")

    @staticmethod
    def verify_evidence(run):
        candidate = content_candidate(run)
        if (run.get("checks", {}).get("status") != "passed" or run["checks"].get("source") != "real" or
                run["checks"].get("candidate") != candidate or run.get("review", {}).get("verdict") != "pass" or
                run["review"].get("source") != "real" or run["review"].get("candidate") != candidate):
            raise ExecutionError("真实检查/审查证据缺失或候选已变化")
