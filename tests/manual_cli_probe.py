"""Opt-in live CLI probe. No credentials or model output are printed."""
import argparse
import asyncio
import json
from pathlib import Path

from agentautomation.agents import run_agent
from agentautomation.process import ProcessError


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["claude", "codex"])
    parser.add_argument("model")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    try:
        result = await run_agent(args.provider, args.model, "Return a JSON object with ok set to true. Do not read files or use tools.",
                                 args.directory, schema=schema, writable=False, timeout=120,
                                 cancelled=lambda: False, log_path=args.directory / "probe.log")
        print(json.dumps({"provider": result["provider"], "model": result["model"], "session_id": result["session_id"],
                          "exit_code": result["process"]["exit_code"], "result": result["result"]}))
    except ProcessError as error:
        print(json.dumps({"provider": args.provider, "status": "failed", "kind": error.kind,
                          "error": error.message, "log_path": str(args.directory / "probe.log")}))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
