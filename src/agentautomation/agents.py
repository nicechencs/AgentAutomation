"""Noninteractive, fresh-session CLI adapters; no permission bypasses."""

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable
import uuid

from .process import ProcessError, redact_process_output, run_process


def _cli(provider):
    executable = shutil.which(provider)
    if not executable:
        raise ProcessError(f"{provider} CLI is not installed or is not on PATH")
    path = Path(executable)
    if path.suffix.lower() not in {".cmd", ".bat", ".ps1", ".js"}:
        return [str(path)]
    # npm shims are never passed to cmd.exe or PowerShell. Use known package
    # entry points directly; an unfamiliar wrapper fails closed.
    entries = {"codex": "node_modules/@openai/codex/bin/codex.js",
               "claude": "node_modules/@anthropic-ai/claude-code/cli.js"}
    entry = path if path.suffix.lower() == ".js" else path.parent / entries[provider]
    local_node = path.parent / "node.exe"
    node = str(local_node) if local_node.is_file() else shutil.which("node")
    if not node or not entry.is_file():
        raise ProcessError(f"Cannot safely resolve {provider} CLI wrapper to its Node entry point")
    return [node, str(entry)]


def _json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON value")

    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)


def _validator(schema):
    try:
        from jsonschema.validators import validator_for
    except ImportError:
        raise ProcessError("JSON Schema validation requires the jsonschema package") from None
    if not isinstance(schema, dict):
        raise ProcessError("schema must be a JSON Schema object")

    def local_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ProcessError("Only document-local JSON Schema references are supported")
                local_refs(child)
        elif isinstance(value, list):
            for child in value:
                local_refs(child)

    local_refs(schema)
    try:
        json.dumps(schema, allow_nan=False)
        cls = validator_for(schema)
        cls.check_schema(schema)
        return cls(schema)
    except Exception:
        raise ProcessError("Invalid JSON Schema") from None


def _claude_result(stdout, session_id):
    envelope = _json(stdout)
    if not isinstance(envelope, dict):
        raise ValueError("Invalid Claude envelope")
    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise ValueError("Claude did not report success")
    if envelope.get("permission_denials"):
        raise ValueError("Claude reported denied permissions")
    if envelope.get("session_id") != session_id:
        raise ValueError("Claude returned an unexpected session")
    return envelope.get("structured_output"), session_id


def _codex_result(stdout):
    session_id = None
    final_text = None
    completed = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = _json(line)
        if not isinstance(event, dict):
            raise ValueError("Invalid Codex event")
        kind = event.get("type")
        if kind in {"error", "turn.failed"}:
            raise ValueError("Codex reported failure")
        if kind == "thread.started":
            if session_id is not None:
                raise ValueError("Multiple Codex sessions")
            session_id = event.get("thread_id")
        elif kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                final_text = item["text"]
        elif kind == "turn.completed":
            completed = True
    if not completed or not isinstance(session_id, str) or not session_id:
        raise ValueError("Incomplete Codex session")
    return _json(final_text), session_id


async def run_agent(provider: str, model: str, prompt: str, cwd: Path, *,
                    schema: dict, writable: bool, timeout: float,
                    cancelled: Callable, log_path: Path) -> dict:
    """Execute one new CLI session and validate its final JSON object.

    Prompts travel only over stdin. Account permissions remain effective; denied
    actions fail instead of enabling automatic approval or disabling a sandbox.
    Full process output belongs to the caller and is never printed here.
    """
    if provider not in {"claude", "codex"}:
        raise ProcessError("provider must be claude or codex")
    if not isinstance(model, str) or not model.strip() or model.startswith("-"):
        raise ProcessError("An explicit model is required")
    if not isinstance(prompt, str):
        raise ProcessError("prompt must be text")
    validator = _validator(schema)
    argv = list(_cli(provider))
    session_id = str(uuid.uuid4())
    # Schema files contain no prompt or environment. TemporaryDirectory removes
    # them on success, failure and cancellation.
    with tempfile.TemporaryDirectory(prefix="agentautomation-schema-") as temp:
        if provider == "claude":
            argv += ["--print", "--output-format", "json", "--json-schema",
                     json.dumps(schema, ensure_ascii=False), "--no-session-persistence",
                     "--session-id", session_id, "--model", model,
                     "--permission-mode", "manual", "--permission-prompts", "none",
                     "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--tools", "Read,Edit,Write,Glob,Grep" if writable else "Read,Glob,Grep"]
        else:
            schema_path = Path(temp) / "response-schema.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            argv += ["--ask-for-approval", "never", "exec", "--ephemeral", "--skip-git-repo-check", "--model", model,
                     "--sandbox", "workspace-write" if writable else "read-only",
                     "--output-schema", str(schema_path), "--json", "--color", "never", "-"]
        private_keys = {"RUNNER_TOKEN", "WORKFLOW_TOKEN", "DATA_DIR", "N8N_WEBHOOK_BASE"}
        environment = {key: value for key, value in os.environ.items() if key.upper() not in private_keys}
        raw_process = await run_process(argv, Path(cwd), timeout=timeout, cancelled=cancelled,
                                       log_path=Path(log_path), input_text=prompt, env=environment,
                                       raw_output=True)
    process = redact_process_output(raw_process)
    try:
        result, session_id = (_claude_result(raw_process["stdout"], session_id)
                              if provider == "claude" else _codex_result(raw_process["stdout"]))
        if not isinstance(result, dict):
            raise ValueError("Final response must be an object")
        validator.validate(result)
    except Exception:
        raise ProcessError(
            f"{provider} CLI returned invalid structured output or reported a permission/session failure; inspect the private process log",
            process=process,
        ) from None
    return {"result": result, "provider": provider, "model": model,
            "session_id": session_id, "process": process, "log_path": str(log_path)}
