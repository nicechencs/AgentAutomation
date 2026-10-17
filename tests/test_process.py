"""Real child processes and stubbed CLI responses; never invoke a model."""

import asyncio
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from agentautomation import agents
from agentautomation.process import ProcessError, run_process


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def alive(pid):
    if os.name == "nt":
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.WaitForSingleObject.restype = w.DWORD
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # Linux zombies have terminated even while awaiting their parent's reap.
        status = Path(f"/proc/{pid}/stat")
        return not (status.exists() and status.read_text().split()[2] == "Z")
    except ProcessLookupError:
        return False


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "logs" / "process.jsonl"

    async def run_python(self, code, *, timeout=5, cancelled=lambda: False,
                         input_text=None, env=None):
        return await run_process([sys.executable, "-I", "-u", "-c", code], self.root,
                                 timeout=timeout, cancelled=cancelled,
                                 log_path=self.log, input_text=input_text, env=env)

    async def wait_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail("Child did not create its readiness marker")
            await asyncio.sleep(0.01)
        return path.read_text()

    async def test_real_output_stdin_cwd_and_timestamps(self):
        result = await self.run_python(
            "import os,sys; print(sys.stdin.read()); print(os.getcwd()); print('diagnostic', file=sys.stderr)",
            input_text="输入 & $(never execute)",
        )
        self.assertEqual(set(result), {"exit_code", "stdout", "stderr", "started_at", "finished_at", "pid"})
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("输入 & $(never execute)", result["stdout"])
        self.assertIn(str(self.root), result["stdout"])
        self.assertIn("diagnostic", result["stderr"])
        self.assertGreater(result["pid"], 0)
        self.assertLessEqual(datetime.fromisoformat(result["started_at"]), datetime.fromisoformat(result["finished_at"]))
        events = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["event"], "started")
        self.assertEqual(events[-1]["kind"], "completed")
        self.assertIn("diagnostic", "".join(event.get("text", "") for event in events))

    async def test_no_console_output_and_no_argv_or_stdin_in_log(self):
        with patch("sys.stdout") as output, patch("sys.stderr") as errors:
            await self.run_python("import sys; sys.stdin.read(); print('private child output')", input_text="PRIVATE_PROMPT")
        output.write.assert_not_called()
        errors.write.assert_not_called()
        text = self.log.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_PROMPT", text)
        self.assertNotIn("sys.stdin.read", text)

    async def test_large_stdin_is_delivered_in_full(self):
        prompt = "输入" * 100000
        result = await self.run_python("import sys; print(len(sys.stdin.buffer.read().decode('utf-8')))", input_text=prompt)
        self.assertEqual(result["stdout"].strip(), str(len(prompt)))

    async def test_deadline_includes_blocked_stdin_writer(self):
        with self.assertRaises(ProcessError) as error:
            await self.run_python("import time; time.sleep(30)", timeout=0.25, input_text="X" * 1000000)
        self.assertEqual(error.exception.kind, "timed_out")
        self.assertFalse(alive(error.exception.process["pid"]))

    async def test_nonzero_exit_fails_with_private_diagnostics(self):
        with self.assertRaises(ProcessError) as error:
            await self.run_python("import sys; print('detail', file=sys.stderr); sys.exit(7)")
        self.assertEqual(error.exception.kind, "failed")
        self.assertEqual(error.exception.process["exit_code"], 7)
        self.assertIn("detail", error.exception.process["stderr"])
        self.assertNotIn("detail", str(error.exception))

    async def test_live_log_before_process_exit(self):
        task = asyncio.create_task(self.run_python("import time; print('live marker'); time.sleep(0.5)"))
        deadline = time.monotonic() + 3
        while not self.log.exists() or "live marker" not in self.log.read_text(encoding="utf-8"):
            if time.monotonic() > deadline:
                self.fail("Output was not logged while the child was running")
            await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        await task

    async def test_deadline_stops_real_process(self):
        start = time.monotonic()
        with self.assertRaises(ProcessError) as error:
            await self.run_python("import time; print('started'); time.sleep(30)", timeout=0.25)
        self.assertEqual(error.exception.kind, "timed_out")
        self.assertLess(time.monotonic() - start, 3)
        self.assertFalse(alive(error.exception.process["pid"]))
        self.assertIn("timed_out", self.log.read_text(encoding="utf-8"))

    async def tree(self, *, task_cancel=False, timeout=10, callback=False, parent_exits=False):
        marker = self.root / "grandchild.pid"
        code = ("import subprocess,sys,time,pathlib; "
                "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)']); "
                f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); "
                + ("" if parent_exits else "time.sleep(30)"))
        task = asyncio.create_task(self.run_python(code, timeout=timeout,
                                                  cancelled=lambda: callback and marker.exists()))
        pid = int(await self.wait_file(marker))
        if task_cancel:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        else:
            with self.assertRaises(ProcessError) as error:
                await task
            self.assertEqual(error.exception.kind, "cancelled" if callback else "timed_out")
        deadline = time.monotonic() + 2
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(alive(pid), f"Grandchild {pid} survived cleanup")

    async def test_callback_cancellation_kills_process_tree(self):
        await self.tree(callback=True)

    async def test_deadline_kills_process_tree(self):
        await self.tree(timeout=0.4)

    async def test_task_cancellation_kills_process_tree(self):
        await self.tree(task_cancel=True)

    async def test_parent_exit_does_not_orphan_pipe_inheriting_children(self):
        await self.tree(timeout=0.4, parent_exits=True)

    async def test_success_kills_detached_output_descendant(self):
        marker = self.root / "child.pid"
        code = ("import subprocess,sys,pathlib; "
                "p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)'], "
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL); "
                f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))")
        await self.run_python(code)
        pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(alive(pid))

    async def test_output_limit_is_explicit_and_bounded(self):
        with patch("agentautomation.process.MAX_OUTPUT_BYTES", 16384):
            with self.assertRaises(ProcessError) as error:
                await self.run_python("import sys; sys.stdout.write('X'*200000); sys.stdout.flush()")
        self.assertEqual(error.exception.kind, "failed")
        self.assertIn("output", str(error.exception))
        self.assertLessEqual(len(error.exception.process["stdout"].encode()), 16384)
        self.assertIn("incomplete", self.log.read_text(encoding="utf-8"))

    async def test_stdout_and_stderr_share_output_limit(self):
        with patch("agentautomation.process.MAX_OUTPUT_BYTES", 10000):
            with self.assertRaises(ProcessError):
                await self.run_python("import sys; sys.stdout.write('X'*6000); sys.stderr.write('Y'*6000)")

    async def test_environment_secrets_redacted_even_across_reads(self):
        environment = dict(os.environ, AUTOMATION_API_KEY="secret-with-boundaries-19382")
        result = await self.run_python(
            "import os,sys,time; s=os.environ['AUTOMATION_API_KEY']; "
            "sys.stdout.write(s[:11]); sys.stdout.flush(); time.sleep(.1); "
            "sys.stdout.write(s[11:]); sys.stderr.write(s)", env=environment,
        )
        secret = environment["AUTOMATION_API_KEY"]
        self.assertNotIn(secret, result["stdout"] + result["stderr"])
        self.assertNotIn(secret, self.log.read_text(encoding="utf-8"))
        self.assertEqual(result["stdout"], "[REDACTED]")
        events = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual("".join(e.get("text", "") for e in events if e["event"] == "stdout"), "[REDACTED]")

    async def test_raw_protocol_output_keeps_json_valid_but_log_is_redacted(self):
        environment = dict(os.environ, AUTOMATION_API_KEY="198273645")
        result = await run_process(
            [sys.executable, "-I", "-c", 'print(\'{"contextWindow":198273645}\')'], self.root,
            timeout=5, cancelled=lambda: False, log_path=self.log, env=environment, raw_output=True,
        )
        self.assertEqual(json.loads(result["stdout"]), {"contextWindow": 198273645})
        self.assertNotIn("198273645", self.log.read_text(encoding="utf-8"))

    async def test_raw_output_failure_still_redacts_exception_diagnostics(self):
        environment = dict(os.environ, AUTOMATION_TOKEN="PRIVATE_TOKEN")
        with self.assertRaises(ProcessError) as error:
            await run_process(
                [sys.executable, "-I", "-c", "import sys; print('PRIVATE_TOKEN'); sys.exit(2)"], self.root,
                timeout=5, cancelled=lambda: False, log_path=self.log, env=environment, raw_output=True,
            )
        self.assertNotIn("PRIVATE_TOKEN", error.exception.process["stdout"])

    async def test_pre_cancelled_never_starts(self):
        with self.assertRaises(ProcessError) as error:
            await self.run_python("raise RuntimeError()", cancelled=lambda: True)
        self.assertEqual(error.exception.kind, "cancelled")
        self.assertIsNone(error.exception.process["pid"])

    async def test_zero_deadline_never_starts(self):
        with self.assertRaises(ProcessError) as error:
            await self.run_python("raise RuntimeError()", timeout=0)
        self.assertEqual(error.exception.kind, "timed_out")
        self.assertIsNone(error.exception.process["pid"])

    async def test_spawn_error_does_not_expose_launch_arguments(self):
        with self.assertRaises(ProcessError) as error:
            await run_process([str(self.root / "missing-executable"), "PRIVATE_ARGUMENT"], self.root,
                              timeout=1, cancelled=lambda: False, log_path=self.log)
        self.assertNotIn("PRIVATE_ARGUMENT", str(error.exception))
        self.assertNotIn("PRIVATE_ARGUMENT", self.log.read_text(encoding="utf-8"))

    async def test_invalid_timeout_and_argv(self):
        for timeout in (float("inf"), float("nan"), -1):
            with self.assertRaises(ProcessError):
                await self.run_python("", timeout=timeout)
        with self.assertRaises(ProcessError):
            await run_process([], self.root, timeout=1, cancelled=lambda: False, log_path=self.log)


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "agent.jsonl"

    async def call_agent(self, provider="claude", *, writable=False, schema=None):
        return await agents.run_agent(provider, "explicit-model", "PRIVATE stdin & prompt", self.root,
                                      schema=SCHEMA if schema is None else schema, writable=writable,
                                      timeout=5, cancelled=lambda: False, log_path=self.log)

    @staticmethod
    def process_result(stdout):
        return {"exit_code": 0, "stdout": stdout, "stderr": "", "started_at": "start",
                "finished_at": "end", "pid": 123}

    @classmethod
    async def fake_process(cls, argv, cwd, **kwargs):
        if "--print" in argv:
            data = {"type": "result", "subtype": "success", "is_error": False,
                    "session_id": argv[argv.index("--session-id") + 1], "structured_output": {"ok": True}}
            return cls.process_result(json.dumps(data))
        return cls.process_result("\n".join(json.dumps(event) for event in [
            {"type": "thread.started", "thread_id": "new-codex-session"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":true}'}},
            {"type": "turn.completed", "usage": {}},
        ]))

    async def test_claude_arguments_fresh_sessions_and_stdin(self):
        with patch.object(agents, "_cli", return_value=["claude.exe"]), patch.object(
            agents, "run_process", side_effect=self.fake_process,
        ) as run:
            first = await self.call_agent(writable=True)
            second = await self.call_agent()
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(first["result"], {"ok": True})
        argv = run.call_args_list[0].args[0]
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "explicit-model")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Edit,Write,Glob,Grep")
        self.assertEqual(run.call_args_list[1].args[0][-1], "Read,Glob,Grep")
        self.assertNotIn("PRIVATE stdin & prompt", argv)
        self.assertEqual(run.call_args_list[0].kwargs["input_text"], "PRIVATE stdin & prompt")
        self.assertFalse(any("dangerously" in arg or "bypass" in arg for arg in argv))
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "manual")
        self.assertEqual(argv[argv.index("--permission-prompts") + 1], "none")
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}})

    async def test_codex_arguments_schema_file_cleanup_and_sandbox(self):
        paths = []

        async def fake(argv, cwd, **kwargs):
            path = Path(argv[argv.index("--output-schema") + 1])
            paths.append(path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), SCHEMA)
            return await self.fake_process(argv, cwd, **kwargs)

        with patch.object(agents, "_cli", return_value=["codex.exe"]), patch.object(
            agents, "run_process", side_effect=fake,
        ) as run:
            result = await self.call_agent("codex", writable=True)
            await self.call_agent("codex", writable=False)
        self.assertEqual(result["session_id"], "new-codex-session")
        for index, sandbox in enumerate(("workspace-write", "read-only")):
            argv = run.call_args_list[index].args[0]
            self.assertEqual(argv[argv.index("--sandbox") + 1], sandbox)
            self.assertIn("--ephemeral", argv)
            self.assertIn("--skip-git-repo-check", argv)
            self.assertIn("--json", argv)
            self.assertEqual(argv[-1], "-")
            self.assertNotIn("PRIVATE stdin & prompt", argv)
            self.assertFalse(any("dangerously" in arg or "bypass" in arg for arg in argv))
        self.assertTrue(all(not path.exists() for path in paths))

    async def test_real_python_cli_stub_roundtrip(self):
        code = ("import sys,json; args=sys.argv; prompt=sys.stdin.read(); "
                "assert prompt=='PRIVATE stdin & prompt'; "
                "print(json.dumps({'subtype':'success','is_error':False,"
                "'session_id':args[args.index('--session-id')+1],'structured_output':{'ok':True}}))")
        with patch.object(agents, "_cli", return_value=[sys.executable, "-I", "-c", code]):
            result = await self.call_agent()
        self.assertEqual(result["result"], {"ok": True})
        self.assertEqual(result["process"]["exit_code"], 0)
        self.assertTrue(self.log.exists())

    async def test_numeric_secret_does_not_break_agent_protocol_and_private_env_is_removed(self):
        async def fake(argv, cwd, **kwargs):
            self.assertTrue(kwargs["raw_output"])
            for key in ("RUNNER_TOKEN", "WORKFLOW_TOKEN", "DATA_DIR", "N8N_WEBHOOK_BASE"):
                self.assertNotIn(key, kwargs["env"])
            self.assertEqual(kwargs["env"]["ANTHROPIC_API_KEY"], "198273645")
            process = await self.fake_process(argv, cwd, **kwargs)
            envelope = json.loads(process["stdout"])
            envelope["contextWindow"] = 198273645
            process["stdout"] = json.dumps(envelope)
            return process

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "198273645", "RUNNER_TOKEN": "runner-secret",
                                     "WORKFLOW_TOKEN": "workflow-secret", "DATA_DIR": "private-dir",
                                     "N8N_WEBHOOK_BASE": "private-webhook"}), patch.object(
            agents, "_cli", return_value=["cli"],
        ), patch.object(agents, "run_process", side_effect=fake):
            result = await self.call_agent()
        self.assertEqual(result["result"], {"ok": True})
        self.assertNotIn("198273645", result["process"]["stdout"])
        self.assertIn("[REDACTED]", result["process"]["stdout"])

    async def test_invalid_responses_fail_closed(self):
        bad = ["not json", "[]", '{"subtype":"error"}',
               '{"subtype":"success","session_id":"wrong","structured_output":{"ok":true}}']
        for stdout in bad:
            with self.subTest(stdout=stdout), patch.object(agents, "_cli", return_value=["cli"]), patch.object(
                agents, "run_process", new=AsyncMock(return_value=self.process_result(stdout)),
            ):
                with self.assertRaises(ProcessError):
                    await self.call_agent()

    async def test_schema_validation_and_permission_denials(self):
        for change in ({"structured_output": {"ok": "yes"}}, {"structured_output": []},
                       {"permission_denials": [{"tool_name": "Write"}]},
                       {"is_error": True}, {"structured_output": {"ok": True, "extra": 1}}):
            async def fake(argv, cwd, **kwargs):
                original = await self.fake_process(argv, cwd, **kwargs)
                data = json.loads(original["stdout"])
                data.update(change)
                original["stdout"] = json.dumps(data)
                return original

            with self.subTest(change=change), patch.object(agents, "_cli", return_value=["cli"]), patch.object(
                agents, "run_process", side_effect=fake,
            ):
                with self.assertRaises(ProcessError) as error:
                    await self.call_agent()
                self.assertEqual(error.exception.kind, "failed")
                self.assertEqual(error.exception.process["pid"], 123)

    async def test_codex_intermediate_prose_does_not_replace_final_json(self):
        events = [
            {"type": "thread.started", "thread_id": "session"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "I will inspect the workspace."}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":true}'}},
            {"type": "turn.completed"},
        ]
        with patch.object(agents, "_cli", return_value=["cli"]), patch.object(
            agents, "run_process", new=AsyncMock(return_value=self.process_result("\n".join(map(json.dumps, events)))),
        ):
            self.assertEqual((await self.call_agent("codex"))["result"], {"ok": True})

    async def test_codex_failures_and_incomplete_turns(self):
        bad = ["{}", '{"type":"error","message":"private failure"}',
               '{"type":"turn.failed"}', '{"type":"thread.started","thread_id":"abc"}',
               '{"type":"turn.completed"}',
               '\n'.join([json.dumps({"type": "thread.started", "thread_id": "abc"}),
                           json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":false,"ok":true}'}}),
                           json.dumps({"type": "turn.completed"})])]
        for stdout in bad:
            with self.subTest(stdout=stdout), patch.object(agents, "_cli", return_value=["cli"]), patch.object(
                agents, "run_process", new=AsyncMock(return_value=self.process_result(stdout)),
            ):
                with self.assertRaises(ProcessError):
                    await self.call_agent("codex")

    async def test_invalid_schema_external_refs_and_unknown_provider_before_cli(self):
        with patch.object(agents, "run_process") as run:
            for schema in ({"type": "unknown"}, {"$ref": "https://example.invalid/schema"},
                           {"$defs": {"x": {"$ref": "file:///secret"}}}):
                with self.assertRaises(ProcessError):
                    await self.call_agent(schema=schema)
            with self.assertRaises(ProcessError):
                await self.call_agent("unknown")
            run.assert_not_called()

    async def test_schema_composition_and_local_reference_are_validated(self):
        schema = {"$defs": {"flag": {"const": True}}, "type": "object",
                  "properties": {"ok": {"$ref": "#/$defs/flag"}}, "required": ["ok"]}
        with patch.object(agents, "_cli", return_value=["cli"]), patch.object(
            agents, "run_process", side_effect=self.fake_process,
        ):
            self.assertEqual((await self.call_agent(schema=schema))["result"], {"ok": True})

    def test_missing_cli_fails(self):
        with patch.object(agents.shutil, "which", return_value=None):
            with self.assertRaises(ProcessError):
                agents._cli("codex")

    def test_npm_wrapper_is_resolved_without_shell(self):
        package = self.root / "node_modules" / "@openai" / "codex" / "bin"
        package.mkdir(parents=True)
        entry = package / "codex.js"
        entry.write_text("// test only", encoding="utf-8")
        shim = self.root / "codex.cmd"
        shim.write_text("DO NOT EXECUTE", encoding="utf-8")
        with patch.object(agents.shutil, "which", side_effect=lambda name: str(shim) if name == "codex" else "node.exe"):
            self.assertEqual(agents._cli("codex"), ["node.exe", str(entry)])
        entry.unlink()
        with patch.object(agents.shutil, "which", side_effect=lambda name: str(shim) if name == "codex" else "node.exe"):
            with self.assertRaises(ProcessError):
                agents._cli("codex")


if __name__ == "__main__":
    unittest.main()
