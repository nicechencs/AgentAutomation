"""Finite real and Fake stages. n8n owns routing and polling."""

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {"completed", "rejected", "cancelled", "failed", "timed_out", "interrupted"}
STAGES = {"plan", "code", "check", "review", "commit", "integrate", "archive"}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RunnerError(Exception):
    def __init__(self, message, status=409):
        self.message = message
        self.status = status


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        # Never pretend to resume an interrupted in-flight stage after a restart.
        for run in self.list():
            pending_operation = run.get("operation") and run["operation"]["request_id"] not in run["requests"]
            if run["status"] not in TERMINAL and (run.get("inflight") or run["status"] == "dispatching" or pending_operation):
                run["status"] = "interrupted"
                run["error"] = "Runner restarted during an operation; inspect processes, workspace and Git before starting another run."
                run["inflight"] = None
                self.save(run, "启动核对：中断运行已停止，等待人工检查")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, run, message=None):
        with self.lock:
            if message:
                run["events"].append({"at": stamp(), "message": message})
            run["revision"] += 1
            run["updated_at"] = stamp()
            with self.connect() as db:
                db.execute("INSERT INTO runs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (run["id"], json.dumps(run, ensure_ascii=False)))
        return run

    def get(self, run_id):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RunnerError("运行不存在", 404)
            run = json.loads(row[0])
            if run["status"] not in TERMINAL and time.time() >= run["deadline"]:
                run["status"] = "timed_out"
                run["error"] = "运行等待/执行时间已达上限"
                self.save(run, run["error"])
            return run

    def list(self):
        with self.lock, self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 100")]
        return [self.get(run_id) for run_id in ids]

    def create(self, template, goal, max_concurrency=2, max_cycles=20, deadline_seconds=300, *, mode="fake", options=None):
        if mode not in {"fake", "real"}:
            raise RunnerError("未知执行模式", 422)
        if mode == "real":
            from .execution import validate_options
            try:
                options = validate_options(template, options or {})
            except Exception as error:
                raise RunnerError(str(error), 422) from error
        with self.lock:
            if any(run["status"] not in TERMINAL or run.get("inflight") for run in self.list()):
                raise RunnerError("已有活动运行，请先完成或取消；当前仅支持一个活动项目")
            run = {"id": str(uuid.uuid4()), "template": template, "goal": goal,
                   "mode": mode, "options": options or {}, "status": "dispatching", "phase": "created", "revision": 0,
                   "max_concurrency": max_concurrency, "max_cycles": max_cycles, "cycles": 0,
                   "deadline": time.time() + deadline_seconds, "tasks": [], "approvals": [], "events": [],
                   "stages": {}, "requests": {}, "inflight": None, "blocked_by": None,
                   "n8n_execution_id": None, "error": None, "created_at": stamp()}
            return self.save(run, f"已创建 {mode} 运行，等待 n8n webhook 接收")

    def dispatch_result(self, run_id, error=None):
        with self.lock:
            run = self.get(run_id)
            if run["status"] in TERMINAL:
                return run
            if error:
                run["status"] = "failed"
                run["error"] = error
                return self.save(run, error)
            if run["status"] == "dispatching":
                run["status"] = "running"
            return self.save(run, "n8n webhook 已接收运行请求")

    def control(self, run_id, action):
        with self.lock:
            run = self.get(run_id)
            if run["status"] in TERMINAL:
                if action == "cancel" and run["status"] == "cancelled":
                    return run
                raise RunnerError("运行已结束，不能继续控制")
            if action == "pause":
                if run["status"] != "paused":
                    run["resume_status"] = run["status"]
                    run["status"] = "paused"
            elif action == "resume":
                if run["status"] != "paused":
                    raise RunnerError("仅暂停运行可以恢复")
                run["status"] = "awaiting_approval" if any(a["status"] == "pending" for a in run["approvals"]) else "running"
                run["blocked_by"] = None
            else:
                run["status"] = "cancelled"
                run["blocked_by"] = None
            return self.save(run, {"pause": "已暂停新阶段启动", "resume": "已恢复，等待 n8n 下一次查询", "cancel": "已请求取消运行；停止在途进程并保留工作区"}[action])

    def approve(self, run_id, approval_id, decision):
        with self.lock:
            run = self.get(run_id)
            approval = next((a for a in run["approvals"] if a["id"] == approval_id), None)
            if not approval:
                raise RunnerError("审批不存在", 404)
            if approval["status"] == decision:
                return run
            if run["status"] in TERMINAL or approval["status"] != "pending":
                raise RunnerError("审批已结束或运行不可审批")
            if approval["candidate"] != self.candidate(run):
                raise RunnerError("候选已变化，审批证据失效")
            approval["status"] = decision
            approval["decided_at"] = stamp()
            if decision == "rejected":
                run["status"] = "rejected"
            elif run["status"] != "paused":
                run["status"] = "running"
            return self.save(run, f"{approval['kind']} 审批已记录：{decision}；等待 n8n 推进")

    @staticmethod
    def candidate(run):
        if run["mode"] == "real":
            from .execution import content_candidate
            import hashlib
            evidence = {"content": content_candidate(run), "plan": run.get("plan"),
                        "options": run.get("options"), "commit_sha": run.get("commit_sha")}
            return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f"{run['id']}:{run['phase']}"

    def require_approval(self, run, kind):
        approval = next((a for a in run["approvals"] if a["kind"] == kind), None)
        if not approval or approval["status"] != "approved" or approval["candidate"] != self.candidate(run):
            raise RunnerError(f"需要有效的 {kind} 审批")

    def add_approval(self, run, kind):
        run["approvals"].append({"id": str(uuid.uuid4()), "kind": kind, "status": "pending", "candidate": self.candidate(run)})
        run["status"] = "awaiting_approval"

    async def stage(self, run_id, stage, request_id, execution_id=None, delay=0.35):
        if stage not in STAGES:
            raise RunnerError("未知阶段", 422)
        with self.lock:
            run = self.get(run_id)
            if run["n8n_execution_id"] and execution_id and run["n8n_execution_id"] != execution_id:
                raise RunnerError("运行已绑定另一条 n8n execution")
            if request_id in run["requests"]:
                if run["requests"][request_id] != stage:
                    raise RunnerError("request_id 已用于其他阶段")
                return run
            if run["status"] in TERMINAL:
                return run
            if run["status"] == "paused":
                run["blocked_by"] = "paused"
                return run
            if run["inflight"]:
                raise RunnerError("已有阶段正在执行")
            if run["cycles"] >= run["max_cycles"]:
                run["status"] = "failed"
                run["error"] = "有限阶段调用次数已达上限"
                return self.save(run, run["error"])
            expected = {"plan": "created", "code": "planned", "check": "coded", "review": "checked", "commit": "reviewed", "integrate": "committed", "archive": "integrated" if run["template"] == "development" else "reviewed"}
            if run["phase"] != expected[stage]:
                raise RunnerError(f"非法阶段顺序：{run['phase']} → {stage}")
            if stage in {"commit", "integrate"} and run["template"] != "development":
                raise RunnerError("本地产物流程不执行 Git 阶段")
            if stage == "code":
                self.require_approval(run, "plan")
            if stage in {"commit", "integrate", "archive"}:
                if run.get("checks", {}).get("status") != "passed" or run.get("review", {}).get("verdict") != "pass":
                    raise RunnerError("需要通过的检查与 Review 证据")
            if stage == "integrate":
                self.require_approval(run, "integration")
            if stage == "archive" and run["template"] == "local-document":
                self.require_approval(run, "artifact")
            run["n8n_execution_id"] = execution_id or run["n8n_execution_id"]
            run["inflight"] = stage
            run["blocked_by"] = None
            run["cycles"] += 1
            run["status"] = "running"
            self.save(run, f"n8n 启动有限阶段：{stage}（{run['mode']}）")
        if run["mode"] == "real":
            return await self.real_stage(run, stage, request_id)
        try:
            if stage == "code":
                semaphore = asyncio.Semaphore(run["max_concurrency"])

                async def work(task_id):
                    async with semaphore:
                        with self.lock:
                            current = self.get(run_id)
                            if current["status"] in TERMINAL:
                                return
                            task = next(t for t in current["tasks"] if t["id"] == task_id)
                            task.update(status="running", stage="code", started_at=stamp())
                            task["logs"].append("Fake Agent 独立尝试开始；未调用真实 CLI")
                            self.save(current, f"{task_id} Fake 执行开始")
                        await asyncio.sleep(delay)
                        with self.lock:
                            current = self.get(run_id)
                            if current["status"] in TERMINAL:
                                return
                            task = next(t for t in current["tasks"] if t["id"] == task_id)
                            task.update(status="completed", finished_at=stamp())
                            self.save(current, f"{task_id} Fake 执行结束")

                await asyncio.gather(*(work(t["id"]) for t in run["tasks"]))
            else:
                await asyncio.sleep(delay)
            with self.lock:
                run = self.get(run_id)
                if run["status"] in TERMINAL:
                    run["inflight"] = None
                    return self.save(run, "运行已停止，丢弃未完成阶段结果")
                paused = run["status"] == "paused"
                if stage == "plan":
                    titles = ["Fake：实现目标的第一部分", "Fake：实现目标的独立第二部分"] if run["template"] == "development" else ["Fake：生成本地分析文档"]
                    run["tasks"] = [{"id": f"T-{i+1}", "title": title, "status": "queued", "stage": "planned", "attempt": 1, "logs": []} for i, title in enumerate(titles)]
                    run["phase"] = "planned"
                    self.add_approval(run, "plan")
                elif stage == "code":
                    run["phase"] = "coded"
                elif stage == "check":
                    run["phase"] = "checked"
                    run["checks"] = {"status": "passed", "source": "fake", "note": "仅 Fake 协议检查，不代表项目测试已通过"}
                elif stage == "review":
                    run["phase"] = "reviewed"
                    run["review"] = {"verdict": "pass", "source": "fake", "candidate": self.candidate(run), "findings": []}
                    if run["template"] == "local-document":
                        self.add_approval(run, "artifact")
                elif stage == "commit":
                    run["phase"] = "committed"
                    self.add_approval(run, "integration")
                elif stage == "integrate":
                    run["phase"] = "integrated"
                else:
                    run["phase"] = "archived"
                    run["status"] = "completed"
                for task in run["tasks"]:
                    task["stage"] = run["phase"]
                if paused and run["status"] not in TERMINAL:
                    run["status"] = "paused"
                run["stages"][stage] = {"status": "completed", "at": stamp(), "source": "fake"}
                run["requests"][request_id] = stage
                run["inflight"] = None
                return self.save(run, f"{stage} Fake 阶段完成")
        except BaseException:
            with self.lock:
                run = self.get(run_id)
                run["inflight"] = None
                if run["status"] not in TERMINAL:
                    run["status"] = "interrupted"
                    run["error"] = "阶段执行中断，需要人工检查"
                self.save(run, "阶段执行中断")
            raise

    async def real_stage(self, snapshot, stage, request_id):
        from .execution import RealExecutor
        from .process import ProcessError
        try:
            values = await RealExecutor(self).execute(snapshot, stage)
            with self.lock:
                current = self.get(snapshot["id"])
                if current["status"] in TERMINAL:
                    current["inflight"] = None
                    return self.save(current, "运行已停止；外部工作区保留供检查，未推进阶段")
                paused = current["status"] == "paused"
                current.update(values)
                current["phase"] = {"plan": "planned", "code": "coded", "check": "checked", "review": "reviewed",
                                    "commit": "committed", "integrate": "integrated", "archive": "archived"}[stage]
                if stage == "plan":
                    self.add_approval(current, "plan")
                elif stage == "commit":
                    self.add_approval(current, "integration")
                elif stage == "review" and current["template"] == "local-document":
                    self.add_approval(current, "artifact")
                elif stage == "archive":
                    current["status"] = "completed"
                for task in current["tasks"]:
                    task["stage"] = current["phase"]
                    if stage in {"commit", "archive"}:
                        task["status"] = "completed"
                if paused and current["status"] not in TERMINAL:
                    current["status"] = "paused"
                current["stages"][stage] = {"status": "completed", "at": stamp(), "source": "real"}
                current["requests"][request_id] = stage
                current["inflight"] = None
                return self.save(current, f"{stage} 真实阶段完成")
        except BaseException as error:
            with self.lock:
                current = self.get(snapshot["id"])
                current["inflight"] = None
                if current["status"] not in TERMINAL:
                    current["status"] = ("timed_out" if isinstance(error, ProcessError) and error.kind == "timed_out"
                                         else "interrupted" if isinstance(error, asyncio.CancelledError) else "failed")
                    current["error"] = str(error) or type(error).__name__
                for task in current["tasks"]:
                    if task["status"] == "running":
                        task["status"] = current["status"]
                self.save(current, f"真实阶段 {stage} 停止：{current.get('error') or current['status']}；保留现场")
            if isinstance(error, asyncio.CancelledError):
                raise
            return current
