"""Opt-in paid live CLI example; writes only a newly created local example repo.

This drives the finite Store stages directly, so it is not an n8n E2E test.
Approvals below apply exclusively to this script's fresh disposable fixture.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from agentautomation.runtime import Store


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["claude", "codex"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--template", choices=["development", "local-document"], default="development")
    parser.add_argument("--directory", type=Path, default=Path(".local/live-examples"))
    args = parser.parse_args()
    root = args.directory.resolve() / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=False)
    options = {"agent": args.agent, "model": args.model, "reviewer": args.agent,
               "review_model": args.model, "max_fixes": 1}
    if args.template == "development":
        repo = root / "repository"
        repo.mkdir()
        git(repo, "init", "-b", "integration")
        git(repo, "config", "user.name", "AgentAutomation Example")
        git(repo, "config", "user.email", "example@localhost.invalid")
        (repo / "answer.txt").write_text("before\n", encoding="utf-8")
        (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "Create isolated example fixture\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>")
        options["repository_path"] = str(repo)
        goal = "只将当前目录 answer.txt 的全部内容修改为 after 加换行。不要修改其他文件。"
        check = "from pathlib import Path; assert Path('answer.txt').read_text(encoding='utf-8') == 'after\\n'"
    else:
        goal = "在当前目录生成 result.md，内容严格为 '# Result\\n\\nReal CLI artifact.\\n' 对应的三行Markdown（转义换行表示实际换行）。只创建这个文件。"
        check = "from pathlib import Path; assert Path('result.md').read_text(encoding='utf-8') == '# Result\\n\\nReal CLI artifact.\\n'"
    options["check_commands"] = [[sys.executable, "-I", "-c", check]]
    store = Store(root / "state" / "runner.sqlite3")
    run = store.create(args.template, goal, max_concurrency=1, deadline_seconds=600,
                       mode="real", options=options)
    store.dispatch_result(run["id"])
    stages = ["plan", "code", "check", "review"] + (["commit", "integrate"] if args.template == "development" else []) + ["archive"]
    for stage in stages:
        run = store.get(run["id"])
        for approval in run["approvals"]:
            if approval["status"] == "pending":
                store.approve(run["id"], approval["id"], "approved")
        run = await store.stage(run["id"], stage, run["id"] + ":" + stage, "local-example")
        print(json.dumps({"stage": stage, "phase": run["phase"], "status": run["status"],
                          "error": run.get("error")}, ensure_ascii=True), flush=True)
        if run["status"] in {"failed", "interrupted", "cancelled", "timed_out", "rejected"}:
            print(json.dumps({"example_path": str(root), "run_id": run["id"]}))
            raise SystemExit(1)
    report = {"run_id": run["id"], "status": run["status"], "template": args.template,
              "agent": args.agent, "model": args.model, "artifact": run.get("artifact"),
              "commit_sha": run.get("commit_sha"), "integration": run.get("integration"),
              "checks": run.get("checks"), "review": run.get("review"), "n8n_e2e": False}
    (root / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evidence_path": str(root / "evidence.json"), "status": run["status"]}))


if __name__ == "__main__":
    asyncio.run(main())
