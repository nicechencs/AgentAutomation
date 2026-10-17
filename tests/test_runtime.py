"""Runtime contract tests using temporary SQLite and the built-in Fake stages.

These tests do not import the HTTP app, dispatch n8n, or execute CLI/Git work.
"""

import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentautomation.runtime import RunnerError, Store


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "runner.sqlite3"
        self.store = Store(self.db_path)

    def create(self, template="development", **options):
        run = self.store.create(template, "Exercise runtime contracts", **options)
        return self.store.dispatch_result(run["id"])

    async def stage(self, run_id, stage, request_id=None, **options):
        options.setdefault("delay", 0)
        return await self.store.stage(
            run_id, stage, request_id or f"request-{stage}", **options
        )

    def approve(self, run_id, kind, decision="approved"):
        run = self.store.get(run_id)
        approval = next(a for a in run["approvals"] if a["kind"] == kind)
        return self.store.approve(run_id, approval["id"], decision)

    async def planned(self, template="development", **options):
        run_id = self.create(template, **options)["id"]
        await self.stage(run_id, "plan")
        self.approve(run_id, "plan")
        return run_id

    async def through_review(self, template="development"):
        run_id = await self.planned(template)
        for stage in ("code", "check", "review"):
            await self.stage(run_id, stage)
        return run_id

    async def wait_for(self, predicate):
        end = asyncio.get_running_loop().time() + 2
        while not predicate():
            if asyncio.get_running_loop().time() >= end:
                self.fail("Fake stage did not reach the expected in-flight state")
            await asyncio.sleep(0.001)

    def assert_unchanged(self, before):
        self.assertEqual(self.store.get(before["id"]), before)

    async def test_development_stage_order_and_approval_gates(self):
        run_id = self.create()["id"]
        run = await self.stage(run_id, "plan")
        self.assertEqual(run["phase"], "planned")
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(len(run["tasks"]), 2)
        self.approve(run_id, "plan")
        for stage, phase in (("code", "coded"), ("check", "checked"),
                             ("review", "reviewed"), ("commit", "committed")):
            run = await self.stage(run_id, stage)
            self.assertEqual(run["phase"], phase)
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual([a["kind"] for a in run["approvals"]], ["plan", "integration"])
        self.approve(run_id, "integration")
        self.assertEqual((await self.stage(run_id, "integrate"))["phase"], "integrated")
        run = await self.stage(run_id, "archive")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["phase"], "archived")
        self.assertEqual(list(run["stages"]),
                         ["plan", "code", "check", "review", "commit", "integrate", "archive"])
        self.assertTrue(all(t["status"] == "completed" for t in run["tasks"]))

    async def test_document_stage_order_and_artifact_approval(self):
        run_id = await self.through_review("local-document")
        run = self.store.get(run_id)
        self.assertEqual(len(run["tasks"]), 1)
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual([a["kind"] for a in run["approvals"]], ["plan", "artifact"])
        self.approve(run_id, "artifact")
        run = await self.stage(run_id, "archive")
        self.assertEqual((run["status"], run["phase"]), ("completed", "archived"))
        self.assertEqual(list(run["stages"]), ["plan", "code", "check", "review", "archive"])

    async def test_parallel_two_overlaps_and_one_serializes_real_task_intervals(self):
        intervals = {}
        elapsed = {}
        delay = 0.12
        for concurrency in (2, 1):
            run_id = await self.planned(max_concurrency=concurrency)
            start = time.monotonic()
            run = await self.stage(run_id, "code", delay=delay)
            elapsed[concurrency] = time.monotonic() - start
            intervals[concurrency] = [
                (datetime.fromisoformat(t["started_at"]),
                 datetime.fromisoformat(t["finished_at"]))
                for t in run["tasks"]
            ]
            self.assertTrue(all(end > start for start, end in intervals[concurrency]))
            self.assertTrue(all(t["status"] == "completed" for t in run["tasks"]))
            self.store.control(run_id, "cancel")
        parallel = intervals[2]
        serial = sorted(intervals[1])
        self.assertLess(max(start for start, _ in parallel), min(end for _, end in parallel))
        self.assertLessEqual(serial[0][1], serial[1][0])
        # Lower bounds validate actual waiting without fragile machine-speed ratios.
        self.assertGreaterEqual(elapsed[2], delay * 0.9)
        self.assertGreaterEqual(elapsed[1], delay * 1.8)

    async def test_unknown_and_out_of_order_stages_have_no_side_effects(self):
        run_id = self.create()["id"]
        before = self.store.get(run_id)
        for stage in ("unknown", "code", "check", "review", "commit", "integrate", "archive"):
            with self.subTest(stage=stage):
                with self.assertRaises(RunnerError) as raised:
                    await self.stage(run_id, stage)
                self.assertEqual(raised.exception.status, 422 if stage == "unknown" else 409)
                self.assert_unchanged(before)
        await self.stage(run_id, "plan")
        before = self.store.get(run_id)
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "plan", "different-plan-request")
        self.assert_unchanged(before)

    async def test_cannot_bypass_any_approval_gate(self):
        for template, gate, stage in (("development", "plan", "code"),
                                      ("development", "integration", "integrate"),
                                      ("local-document", "artifact", "archive")):
            with self.subTest(gate=gate):
                if gate == "plan":
                    run_id = self.create(template)["id"]
                    await self.stage(run_id, "plan")
                else:
                    run_id = await self.through_review(template)
                    if gate == "integration":
                        await self.stage(run_id, "commit")
                before = self.store.get(run_id)
                with self.assertRaises(RunnerError):
                    await self.stage(run_id, stage)
                self.assert_unchanged(before)
                self.store.control(run_id, "cancel")

    async def test_document_cannot_enter_git_stage_after_review(self):
        run_id = await self.through_review("local-document")
        self.approve(run_id, "artifact")
        before = self.store.get(run_id)
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "commit")
        self.assert_unchanged(before)

    async def test_completed_request_replay_is_idempotent_even_after_progress(self):
        run_id = await self.planned()
        before = self.store.get(run_id)
        replay = await self.stage(run_id, "plan")
        self.assertEqual(replay, before)
        self.assert_unchanged(before)
        await self.stage(run_id, "code")
        before = self.store.get(run_id)
        self.assertEqual(await self.stage(run_id, "plan"), before)
        self.assert_unchanged(before)

    async def test_request_id_cannot_be_reused_for_a_different_stage(self):
        run_id = await self.planned()
        before = self.store.get(run_id)
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "code", "request-plan")
        self.assert_unchanged(before)

    async def test_inflight_retry_does_not_start_duplicate_work(self):
        run_id = await self.planned()
        pending = asyncio.create_task(self.stage(run_id, "code", delay=0.06))
        try:
            await self.wait_for(lambda: self.store.get(run_id)["inflight"] == "code")
            before = self.store.get(run_id)
            with self.assertRaises(RunnerError):
                await self.stage(run_id, "code")
            self.assert_unchanged(before)
            completed = await pending
            self.assertEqual(completed["cycles"], 2)
            self.assertEqual(await self.stage(run_id, "code"), completed)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_cancel_discards_late_code_results(self):
        run_id = await self.planned()
        pending = asyncio.create_task(self.stage(run_id, "code", delay=0.06))
        try:
            await self.wait_for(lambda: any(t["status"] == "running"
                                           for t in self.store.get(run_id)["tasks"]))
            cancelled = self.store.control(run_id, "cancel")
            run = await pending
            self.assertEqual(run["status"], "cancelled")
            self.assertEqual(run["phase"], cancelled["phase"])
            self.assertEqual(run["phase"], "planned")
            self.assertNotIn("code", run["stages"])
            self.assertNotIn("request-code", run["requests"])
            self.assertIsNone(run["inflight"])
            self.assertTrue(all(t["status"] != "completed" for t in run["tasks"]))
            before = self.store.get(run_id)
            self.assertEqual(await self.stage(run_id, "code", "late-retry"), before)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_pause_prevents_new_stage_until_resume(self):
        run_id = await self.planned()
        paused = self.store.control(run_id, "pause")
        blocked = await self.stage(run_id, "code")
        self.assertEqual(blocked["status"], "paused")
        self.assertEqual(blocked["cycles"], paused["cycles"])
        self.assertEqual(blocked["phase"], "planned")
        self.assertIsNone(blocked["inflight"])
        self.assertNotIn("request-code", blocked["requests"])
        self.store.control(run_id, "resume")
        self.assertEqual((await self.stage(run_id, "code"))["phase"], "coded")

    async def test_pause_survives_inflight_completion_and_approval(self):
        run_id = self.create()["id"]
        pending = asyncio.create_task(self.stage(run_id, "plan", delay=0.06))
        try:
            await self.wait_for(lambda: self.store.get(run_id)["inflight"] == "plan")
            self.store.control(run_id, "pause")
            run = await pending
            self.assertEqual((run["status"], run["phase"]), ("paused", "planned"))
            self.assertEqual(self.approve(run_id, "plan")["status"], "paused")
            self.assertEqual((await self.stage(run_id, "code"))["phase"], "planned")
            self.store.control(run_id, "resume")
            self.assertEqual((await self.stage(run_id, "code"))["phase"], "coded")
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_resume_with_pending_approval_keeps_gate_closed(self):
        run_id = self.create()["id"]
        await self.stage(run_id, "plan")
        self.store.control(run_id, "pause")
        self.assertEqual(self.store.control(run_id, "resume")["status"], "awaiting_approval")
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "code")

    async def test_deadline_expires_while_waiting_for_approval(self):
        run_id = self.create()["id"]
        run = await self.stage(run_id, "plan")
        with patch("agentautomation.runtime.time.time", return_value=run["deadline"] + 1):
            expired = self.store.get(run_id)
            self.assertEqual(expired["status"], "timed_out")
            with self.assertRaises(RunnerError):
                self.approve(run_id, "plan")
            self.assertEqual(await self.stage(run_id, "code"), expired)
        self.assertEqual(self.store.get(run_id)["status"], "timed_out")

    async def test_deadline_discards_inflight_results(self):
        run_id = await self.planned()
        pending = asyncio.create_task(self.stage(run_id, "code", delay=0.06))
        try:
            await self.wait_for(lambda: any(t["status"] == "running"
                                           for t in self.store.get(run_id)["tasks"]))
            deadline = self.store.get(run_id)["deadline"]
            with patch("agentautomation.runtime.time.time", return_value=deadline + 1):
                run = await pending
            self.assertEqual((run["status"], run["phase"]), ("timed_out", "planned"))
            self.assertIsNone(run["inflight"])
            self.assertNotIn("code", run["stages"])
            self.assertTrue(all(t["status"] != "completed" for t in run["tasks"]))
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_stage_call_limit_blocks_new_work_but_allows_replay(self):
        run_id = await self.planned(max_cycles=1)
        before = self.store.get(run_id)
        self.assertEqual(await self.stage(run_id, "plan"), before)
        limited = await self.stage(run_id, "code")
        self.assertEqual((limited["status"], limited["phase"]), ("failed", "planned"))
        self.assertEqual(limited["cycles"], 1)
        self.assertIsNone(limited["inflight"])
        self.assertNotIn("code", limited["stages"])
        self.assertNotIn("request-code", limited["requests"])

    async def test_reopening_sqlite_interrupts_inflight_and_discards_old_completion(self):
        run_id = self.create()["id"]
        pending = asyncio.create_task(self.stage(run_id, "plan", delay=0.06))
        try:
            await self.wait_for(lambda: self.store.get(run_id)["inflight"] == "plan")
            reopened = Store(self.db_path)
            interrupted = reopened.get(run_id)
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["phase"], "created")
            self.assertIsNone(interrupted["inflight"])
            self.assertTrue(interrupted["error"])
            run = await pending
            self.assertEqual((run["status"], run["phase"]), ("interrupted", "created"))
            self.assertEqual(run["tasks"], [])
            self.assertNotIn("plan", run["stages"])
            self.assertEqual((await reopened.stage(run_id, "plan", "retry", delay=0))["status"],
                             "interrupted")
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_reopening_interrupts_unconfirmed_dispatch_but_preserves_idle_run(self):
        run = self.store.create("development", "Unconfirmed dispatch")
        reopened = Store(self.db_path)
        self.assertEqual(reopened.get(run["id"])["status"], "interrupted")
        run_id = self.create()["id"]
        await self.stage(run_id, "plan")
        before = self.store.get(run_id)
        self.assertEqual(Store(self.db_path).get(run_id), before)

    async def test_only_one_active_run_including_paused_and_approval_wait(self):
        run_id = self.store.create("development", "First run")["id"]
        for state in ("dispatching", "running", "awaiting_approval", "paused"):
            if state == "running":
                self.store.dispatch_result(run_id)
            elif state == "awaiting_approval":
                await self.stage(run_id, "plan")
            elif state == "paused":
                self.store.control(run_id, "pause")
            with self.subTest(state=state):
                with self.assertRaises(RunnerError):
                    self.store.create("local-document", "Second run")
                self.assertEqual(len(self.store.list()), 1)
        self.store.control(run_id, "cancel")
        self.assertNotEqual(self.create("local-document")["id"], run_id)

    async def test_approval_same_decision_is_idempotent_and_opposite_is_rejected(self):
        for decision, opposite in (("approved", "rejected"), ("rejected", "approved")):
            with self.subTest(decision=decision):
                run_id = self.create()["id"]
                await self.stage(run_id, "plan")
                decided = self.approve(run_id, "plan", decision)
                self.assertEqual(self.approve(run_id, "plan", decision), decided)
                with self.assertRaises(RunnerError):
                    self.approve(run_id, "plan", opposite)
                self.assert_unchanged(decided)
                if decision == "approved":
                    self.store.control(run_id, "cancel")
                else:
                    self.assertEqual((await self.stage(run_id, "code"))["status"], "rejected")

    async def test_execution_binding_rejects_a_second_workflow(self):
        run_id = self.create()["id"]
        await self.stage(run_id, "plan", execution_id="workflow-one")
        self.approve(run_id, "plan")
        before = self.store.get(run_id)
        # A completed request replay still belongs to the original execution.
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "plan", execution_id="workflow-two")
        self.assert_unchanged(before)
        with self.assertRaises(RunnerError):
            await self.stage(run_id, "code", execution_id="workflow-two")
        self.assert_unchanged(before)
        self.assertEqual((await self.stage(run_id, "code", execution_id="workflow-one"))["phase"],
                         "coded")


if __name__ == "__main__":
    unittest.main()
