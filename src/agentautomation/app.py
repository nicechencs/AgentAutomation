"""Same-origin UI/API and authenticated n8n stage calls for Fake-only integration."""

import asyncio
import hmac
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastapi import Depends, FastAPI, Request as APIRequest
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal

from .runtime import RunnerError, Store

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / ".local" / "runner"))
N8N_WEBHOOK_BASE = os.getenv("N8N_WEBHOOK_BASE", "http://127.0.0.1:5678/webhook").rstrip("/")
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "")
WORKFLOW_TOKEN = os.getenv("WORKFLOW_TOKEN", "")
store = Store(DATA_DIR / "runner.sqlite3")
app = FastAPI(title="AgentAutomation Fake Runner", docs_url="/api/docs", redoc_url=None)


class NewRun(BaseModel):
    template: Literal["development", "local-document"]
    goal: str = Field(min_length=1, max_length=2000)
    max_concurrency: int = Field(default=2, ge=1, le=2)
    max_cycles: int = Field(default=20, ge=1, le=30)
    deadline_seconds: int = Field(default=300, ge=30, le=3600)


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel"]


class Decision(BaseModel):
    decision: Literal["approved", "rejected"]


class StageRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(min_length=1, max_length=100)


@app.exception_handler(RunnerError)
async def runner_error(_request, error):
    return JSONResponse({"detail": error.message}, status_code=error.status)


def runner_auth(request: APIRequest):
    supplied = request.headers.get("X-Runner-Token", "")
    if not RUNNER_TOKEN or not hmac.compare_digest(supplied, RUNNER_TOKEN):
        raise RunnerError("需要有效的 n8n Runner 调用凭据", 401)


def reader_auth(request: APIRequest):
    supplied = request.headers.get("X-Runner-Token", "")
    if supplied:
        runner_auth(request)
        return
    # Block cross-site reads and DNS rebinding; no permissive CORS headers.
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
        raise RunnerError("UI 仅接受本机 Host", 403)
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise RunnerError("拒绝跨源请求", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise RunnerError("拒绝跨站请求", 403)


def ui_auth(request: APIRequest):
    reader_auth(request)
    if request.headers.get("X-UI-Request") != "1":
        raise RunnerError("需要同源 UI 请求头", 403)


def n8n_ready():
    try:
        origin = urlparse(N8N_WEBHOOK_BASE)
        with urlopen(f"{origin.scheme}://{origin.netloc}/healthz", timeout=2) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def dispatch(run):
    path = "agentautomation-" + run["template"]
    request = Request(f"{N8N_WEBHOOK_BASE}/{path}", data=json.dumps({"run_id": run["id"]}).encode(),
                      headers={"Content-Type": "application/json", "X-Workflow-Token": WORKFLOW_TOKEN}, method="POST")
    try:
        with urlopen(request, timeout=8) as response:
            if response.status not in {200, 202}:
                raise RunnerError(f"n8n webhook 返回 {response.status}", 502)
        store.dispatch_result(run["id"])
    except (OSError, URLError, HTTPError, RunnerError) as error:
        # The POST may have arrived even if its response was lost. Never resubmit automatically.
        store.dispatch_result(run["id"], f"n8n 启动未确认（不自动重发）：{type(error).__name__}；检查工作流执行记录后再创建新运行")
    return store.get(run["id"])


@app.get("/api/health", dependencies=[Depends(reader_auth)])
async def health():
    ready = await asyncio.to_thread(n8n_ready)
    return {"status": "ok", "mode": "fake", "n8n": {"reachable": ready,
            "configured": bool(RUNNER_TOKEN and WORKFLOW_TOKEN), "url": "http://127.0.0.1:5678"},
            "templates": ["development", "local-document"]}


@app.get("/api/runs", dependencies=[Depends(reader_auth)])
def list_runs():
    return store.list()


@app.post("/api/runs", dependencies=[Depends(ui_auth)])
async def create_run(body: NewRun):
    if not body.goal.strip():
        raise RunnerError("目标不能为空白", 422)
    if not RUNNER_TOKEN or not WORKFLOW_TOKEN:
        raise RunnerError("n8n 调用凭据尚未配置，请按接入说明配置后启动服务", 503)
    if not await asyncio.to_thread(n8n_ready):
        raise RunnerError("n8n 未连接，未创建运行；请检查容器和生产工作流", 503)
    run = store.create(body.template, body.goal.strip(), body.max_concurrency, body.max_cycles, body.deadline_seconds)
    return await asyncio.to_thread(dispatch, run)


@app.get("/api/runs/{run_id}", dependencies=[Depends(reader_auth)])
def get_run(run_id: str):
    return store.get(run_id)


@app.post("/api/runs/{run_id}/control", dependencies=[Depends(ui_auth)])
def control_run(run_id: str, body: Control):
    return store.control(run_id, body.action)


@app.post("/api/runs/{run_id}/approvals/{approval_id}", dependencies=[Depends(ui_auth)])
def approve(run_id: str, approval_id: str, body: Decision):
    return store.approve(run_id, approval_id, body.decision)


@app.post("/api/runs/{run_id}/stages/{stage}", dependencies=[Depends(runner_auth)])
async def run_stage(run_id: str, stage: str, body: StageRequest):
    return await store.stage(run_id, stage, body.request_id, body.execution_id)


app.mount("/", StaticFiles(directory=ROOT / "ui", html=True), name="ui")
