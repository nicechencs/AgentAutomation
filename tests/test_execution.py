"""Real orchestration contracts with actual files/Git and injected CLI responses."""
import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentautomation.runtime import Store, RunnerError
from agentautomation.execution import content_candidate


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


class RealExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="真实执行 ")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "integration")
        git(self.repo, "config", "user.name", "Runner Test")
        git(self.repo, "config", "user.email", "runner-test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.repo / "answer.txt").write_text("before\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.store = Store(self.root / "state" / "runner.sqlite3")
        self.options = {"repository_path": str(self.repo), "agent": "codex", "model": "test-model",
                        "reviewer": "codex", "review_model": "test-review-model", "max_fixes": 1,
                        "check_commands": [[sys.executable, "-I", "-c", "from pathlib import Path; assert Path('answer.txt').read_text().strip() == 'after'"]]}
        self.cli = patch("agentautomation.execution.run_agent", side_effect=self.agent)
        self.which = patch("agentautomation.execution.shutil.which", return_value="test-cli")
        self.cli.start()
        self.which.start()
        self.review_calls = 0
        self.fail_first_review = False

    def tearDown(self):
        self.cli.stop()
        self.which.stop()
        self.temp.cleanup()

    async def agent(self, provider, model, prompt, cwd, *, schema, writable, timeout, cancelled, log_path):
        if "acceptance" in schema["properties"]:
            value = {"title": "Update answer", "instructions": "Write after to answer.txt", "acceptance": ["answer contains after"]}
        elif "verdict" in schema["properties"]:
            self.review_calls += 1
            fail = self.fail_first_review and self.review_calls == 1
            value = {"verdict": "fail" if fail else "pass", "summary": "Needs fix" if fail else "Verified candidate", "findings": ["Fix answer"] if fail else []}
        else:
            self.assertTrue(writable)
            (cwd / "answer.txt").write_text("after\n", encoding="utf-8")
            value = {"summary": "Updated actual answer file"}
        return {"result": value, "provider": provider, "model": model, "session_id": str(log_path),
                "log_path": str(log_path), "process": {"exit_code": 0}}

    def create(self):
        run = self.store.create("development", "Update answer", mode="real", options=self.options, deadline_seconds=60)
        return self.store.dispatch_result(run["id"])

    async def stage(self, run, name):
        return await self.store.stage(run["id"], name, run["id"] + ":" + name, "execution-test", delay=0)

    def approve(self, run, kind):
        approval = next(a for a in run["approvals"] if a["kind"] == kind)
        return self.store.approve(run["id"], approval["id"], "approved")

    async def reviewed(self):
        run = await self.stage(self.create(), "plan")
        self.approve(run, "plan")
        for name in ("code", "check", "review"):
            run = await self.stage(run, name)
            self.assertNotEqual(run["status"], "failed", run.get("error"))
        return run

    async def test_real_files_checks_commit_and_isolated_integration(self):
        run = await self.reviewed()
        self.assertEqual(run["checks"]["source"], "real")
        self.assertEqual(run["checks"]["commands"][0]["exit_code"], 0)
        self.assertEqual((self.repo / "answer.txt").read_text(), "before\n")
        run = await self.stage(run, "commit")
        self.assertEqual(run["phase"], "committed", run.get("error"))
        self.assertEqual(len(run["commit_sha"]), 40)
        self.approve(run, "integration")
        run = await self.stage(run, "integrate")
        self.assertEqual(run["phase"], "integrated", run.get("error"))
        self.assertEqual((self.repo / "answer.txt").read_text(), "after\n")
        run = await self.stage(run, "archive")
        self.assertEqual(run["status"], "completed", run.get("error"))
        self.assertTrue(Path(run["workspace"]["path"]).exists())

    async def test_candidate_change_blocks_commit(self):
        run = await self.reviewed()
        (Path(run["workspace"]["path"]) / "answer.txt").write_text("tampered\n")
        run = await self.stage(run, "commit")
        self.assertEqual(run["status"], "failed")
        self.assertNotIn("commit_sha", run)
        self.assertIn("证据", run["error"])
        self.assertEqual((self.repo / "answer.txt").read_text(), "before\n")

    async def test_failed_actual_check_blocks_review_and_commit(self):
        self.options["check_commands"] = [[sys.executable, "-I", "-c", "raise SystemExit(7)"]]
        run = await self.stage(self.create(), "plan")
        self.approve(run, "plan")
        run = await self.stage(run, "code")
        run = await self.stage(run, "check")
        self.assertEqual(run["status"], "failed")
        self.assertNotIn("review", run)
        self.assertEqual(self.review_calls, 0)

    async def test_independent_review_fix_rechecks_candidate(self):
        self.fail_first_review = True
        run = await self.reviewed()
        self.assertEqual(self.review_calls, 2)
        self.assertEqual(run["review"]["attempts"], 2)
        self.assertEqual(run["checks"]["candidate"], content_candidate(run))

    async def test_changed_plan_rejects_prior_approval(self):
        run = await self.stage(self.create(), "plan")
        self.approve(run, "plan")
        run = self.store.get(run["id"])
        run["plan"]["instructions"] = "another task"
        self.store.save(run)
        with self.assertRaises(RunnerError):
            await self.stage(run, "code")
        self.assertNotIn("workspace", self.store.get(run["id"]))

    async def test_pause_waits_before_starting_a_new_real_agent(self):
        from agentautomation.execution import RealExecutor, CODE_SCHEMA
        run = self.create()
        self.store.control(run["id"], "pause")
        call = asyncio.create_task(RealExecutor(self.store).agent(run, "code", "write", CODE_SCHEMA, True))
        await asyncio.sleep(0.15)
        self.assertFalse(call.done())
        self.store.control(run["id"], "cancel")
        with self.assertRaises(Exception):
            await call

    async def test_cancelled_inflight_run_still_occupies_slot(self):
        run = self.create()
        current = self.store.get(run["id"])
        current["inflight"] = "code"
        self.store.save(current)
        self.store.control(run["id"], "cancel")
        with self.assertRaises(RunnerError):
            self.store.create("local-document", "another")

    async def test_document_real_file_and_approval_without_git(self):
        self.options.pop("repository_path")
        run = self.store.create("local-document", "Write answer document", mode="real", options=self.options, deadline_seconds=60)
        self.store.dispatch_result(run["id"])
        run = await self.stage(run, "plan")
        self.approve(run, "plan")
        for name in ("code", "check", "review"):
            run = await self.stage(run, name)
        self.approve(run, "artifact")
        run = await self.stage(run, "archive")
        self.assertEqual(run["status"], "completed", run.get("error"))
        self.assertIn("answer.txt", run["artifact"]["files"])
        self.assertNotIn("commit_sha", run)


if __name__ == "__main__":
    unittest.main()
