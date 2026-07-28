"""FastAPI backend for the factory UI.

Read paths go straight to the same sources the CLI uses (checkpointer,
metrics.db, the sandbox git repo); mutations go through the driver
registry so one server process owns every run it started.

Environment note: model overrides and the acceptance-suite location are
process-global environment variables read by the stages at call time, so
a per-run override applies to anything driven afterwards in this process.
For the single-operator workflow this serves, that is acceptable; the
current values are always visible in /api/health.
"""

import base64
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from factory import git_ops
from factory.cli import CHECKPOINT_DB, DEFAULT_RUN_BUDGET_USD
from factory.governance import control
from factory.graph import build_graph
from factory.observability import metrics as metrics_module
from factory.observability import tracing
from factory.state import spent_usd
from factory.web.driver import Registry, _config

import uuid

STATIC_DIR = Path(__file__).parent / "static"
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = Path("runs")
SKIP_ACCEPTANCE_DIR = "/tmp/factory-no-acceptance"
FILE_SIZE_LIMIT = 512 * 1024
SANDBOX_SKIP_DIRS = {".git", "target", "node_modules", ".idea"}

# Langfuse dev-stack defaults (docker-compose.langfuse.yml): set before any
# run starts so tracing is on by default. Real deployments override via env.
LANGFUSE_DEFAULTS = {
    "LANGFUSE_PUBLIC_KEY": "pk-lf-factory-dev",
    "LANGFUSE_SECRET_KEY": "sk-lf-factory-dev",
    "LANGFUSE_HOST": "http://localhost:3000",
}


def _apply_langfuse_defaults() -> None:
    for key, value in LANGFUSE_DEFAULTS.items():
        os.environ.setdefault(key, value)


app = FastAPI(title="software factory", docs_url="/api/docs", openapi_url="/api/openapi.json")
registry = Registry()
# Runs started this server session that may not have hit metrics.db yet.
_session_runs: dict[str, dict] = {}
_langfuse_project_id: str | None | bool = False  # False = not yet resolved


@app.on_event("startup")
async def _startup() -> None:
    _apply_langfuse_defaults()


class StartRun(BaseModel):
    goal: str
    profile: str | None = None
    budget: float | None = None
    implementer_model: str | None = None
    reasoner_model: str | None = None
    acceptance: str = "default"  # "default" | "skip"
    auto: bool = False


class GateAnswer(BaseModel):
    action: str  # approve | revise | reject
    edits: str = ""


def _apply_run_env(request: StartRun) -> None:
    if request.implementer_model:
        os.environ["FACTORY_MODEL_IMPLEMENTER"] = request.implementer_model
    if request.reasoner_model:
        os.environ["FACTORY_MODEL_REASONER"] = request.reasoner_model
    if request.acceptance == "skip":
        os.environ["FACTORY_ACCEPTANCE_DIR"] = SKIP_ACCEPTANCE_DIR
    else:
        os.environ.pop("FACTORY_ACCEPTANCE_DIR", None)


async def _snapshot(run_id: str):
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(checkpointer=saver)
        return await graph.aget_state(_config(run_id))


async def _langfuse_session_url(run_id: str) -> str | None:
    """Deep link into the Langfuse UI; resolved once via its public API."""
    global _langfuse_project_id
    if not tracing.tracing_enabled():
        return None
    host = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
    if _langfuse_project_id is False:
        _langfuse_project_id = None
        try:
            auth = base64.b64encode(
                f"{os.environ['LANGFUSE_PUBLIC_KEY']}:"
                f"{os.environ['LANGFUSE_SECRET_KEY']}".encode()
            ).decode()
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{host}/api/public/projects",
                    headers={"Authorization": f"Basic {auth}"},
                )
            if response.status_code == 200 and response.json().get("data"):
                _langfuse_project_id = response.json()["data"][0]["id"]
        except Exception:
            _langfuse_project_id = None
    if _langfuse_project_id:
        return f"{host}/project/{_langfuse_project_id}/sessions/{run_id}"
    return host or None


@app.get("/api/health")
async def health() -> dict:
    return {
        "langfuse_enabled": tracing.tracing_enabled(),
        "langfuse_host": os.environ.get("LANGFUSE_HOST"),
        "checkpoint_db": CHECKPOINT_DB.exists(),
        "acceptance_dir": os.environ.get("FACTORY_ACCEPTANCE_DIR") or "tests/acceptance (default)",
        "implementer_model": os.environ.get("FACTORY_MODEL_IMPLEMENTER") or "haiku (default)",
        "reasoner_model": os.environ.get("FACTORY_MODEL_REASONER") or "sonnet (default)",
    }


@app.get("/api/runs")
async def list_runs() -> list[dict]:
    rows = {row["run_id"]: dict(row) for row in metrics_module.list_runs()}
    for run_id, meta in _session_runs.items():
        rows.setdefault(run_id, {
            "run_id": run_id,
            "started": meta["started"],
            "finished": None,
            "scenario": None,
            "outcome": None,
            "goal": meta["goal"],
        })
    result = []
    for run_id, row in rows.items():
        row["driving"] = registry.driving(run_id)
        row["killed"] = control.is_killed(run_id)
        result.append(row)
    result.sort(key=lambda r: r.get("started") or "", reverse=True)
    return result


@app.post("/api/runs")
async def start_run(request: StartRun) -> dict:
    if not request.goal.strip():
        raise HTTPException(400, "goal must not be empty")
    _apply_langfuse_defaults()
    _apply_run_env(request)
    budget = request.budget
    if budget is None:
        budget = float(os.environ.get("FACTORY_RUN_BUDGET_USD", DEFAULT_RUN_BUDGET_USD))
    profile = request.profile or os.environ.get("FACTORY_PROFILE", "java-springboot")
    run_id = uuid.uuid4().hex[:8]
    initial = {
        "run_id": run_id,
        "goal": request.goal,
        "profile": profile,
        "stage_results": {},
        "attempts": 0,
        "replan_budget": 1,
        "run_budget_usd": budget,
    }
    from factory.web.driver import _now
    _session_runs[run_id] = {"goal": request.goal, "started": _now()}
    registry.start(run_id, initial, auto=request.auto)
    return {"run_id": run_id, "profile": profile, "budget": budget}


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> dict:
    snapshot = await _snapshot(run_id)
    if not snapshot.values:
        raise HTTPException(404, f"no run found for id {run_id}")
    values = snapshot.values
    results = values.get("stage_results") or {}
    interrupts = [
        interrupt.value
        for task in snapshot.tasks
        for interrupt in task.interrupts
    ]
    return {
        "run_id": run_id,
        "goal": values.get("goal"),
        "profile": values.get("profile"),
        "scenario": values.get("scenario"),
        "next": list(snapshot.next),
        "driving": registry.driving(run_id),
        "killed": control.is_killed(run_id),
        "pending_gate": interrupts[0] if interrupts else None,
        "task_idx": values.get("task_idx", 0),
        "tasks": values.get("tasks") or [],
        "attempts": values.get("attempts", 0),
        "replan_budget": values.get("replan_budget"),
        "spend_usd": spent_usd(values),
        "budget_usd": values.get("run_budget_usd"),
        "stages_done": sorted(k for k, v in results.items() if v),
        "spec": values.get("spec"),
        "design": values.get("design"),
        "risks": values.get("risks") or [],
        "ambiguities": values.get("ambiguities") or [],
        "decisions": values.get("decisions") or [],
        "sandbox": values.get("sandbox"),
        "base_sha": values.get("base_sha"),
        "head_sha": values.get("head_sha"),
        "safe_stop": results.get("safe_stop"),
        "langfuse_url": await _langfuse_session_url(run_id),
    }


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, after: int = -1) -> StreamingResponse:
    handle = registry.handle(run_id)

    async def stream():
        async for event in handle.subscribe(after_seq=after):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/gate")
async def answer_gate(run_id: str, answer: GateAnswer) -> dict:
    if answer.action not in ("approve", "revise", "reject"):
        raise HTTPException(400, "action must be approve, revise or reject")
    if registry.driving(run_id):
        raise HTTPException(409, "run is currently executing; wait for it to park")
    if control.is_killed(run_id):
        raise HTTPException(409, "run is killed; clear the kill flag first")
    snapshot = await _snapshot(run_id)
    if not snapshot.values:
        raise HTTPException(404, f"no run found for id {run_id}")
    if not any(task.interrupts for task in snapshot.tasks):
        raise HTTPException(409, "run has no pending gate")
    resume: dict[str, Any] = {"action": answer.action}
    if answer.edits:
        resume["edits"] = answer.edits
    registry.start(run_id, Command(resume=resume))
    return {"resumed": True, "action": answer.action}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict:
    if registry.driving(run_id):
        raise HTTPException(409, "run is already being driven")
    if control.is_killed(run_id):
        raise HTTPException(409, "run is killed; clear the kill flag first")
    snapshot = await _snapshot(run_id)
    if not snapshot.values:
        raise HTTPException(404, f"no run found for id {run_id}")
    registry.start(run_id, None)
    return {"resumed": True}


@app.post("/api/runs/{run_id}/kill")
async def kill_run(run_id: str) -> dict:
    control.request_kill(run_id)
    return {"killed": True}


@app.post("/api/runs/{run_id}/kill/clear")
async def clear_kill(run_id: str) -> dict:
    control.clear_kill(run_id)
    return {"killed": False}


@app.get("/api/runs/{run_id}/metrics")
async def run_metrics(run_id: str) -> dict:
    try:
        report = metrics_module.compute(run_id)
    except KeyError as error:
        raise HTTPException(404, str(error))
    return asdict(report)


@app.get("/api/runs/{run_id}/summary")
async def run_summary(run_id: str) -> dict:
    path = RUNS_DIR / run_id / "summary.md"
    if not path.exists():
        raise HTTPException(404, "no summary yet (written when the run finishes)")
    return {"markdown": path.read_text()}


def _archive_branch(run_id: str) -> str | None:
    """Scenario runs archive the product's main into this repo as
    product/<scenario>-<run_id> (scenarios/_lib.sh); that branch is the
    artifact source once the /tmp sandbox has been cleaned up."""
    try:
        out = git_ops.git(
            ORCHESTRATOR_ROOT, "branch", "--list", f"product/*-{run_id}",
            "--format=%(refname:short)",
        )
    except git_ops.GitError:
        return None
    branches = [line.strip() for line in out.splitlines() if line.strip()]
    return branches[0] if branches else None


async def _product_source(run_id: str) -> tuple[str, Any]:
    """Where to read this run's product from: the live sandbox if it still
    exists, else the archived git branch. ("sandbox", Path) | ("git", branch)."""
    snapshot = await _snapshot(run_id)
    values = snapshot.values or {}
    sandbox = values.get("sandbox")
    if sandbox and Path(sandbox).is_dir():
        return "sandbox", (Path(sandbox).resolve(), values.get("base_sha"))
    branch = _archive_branch(run_id)
    if branch:
        return "git", (branch, values.get("base_sha"))
    raise HTTPException(
        404,
        "sandbox removed and no archived product branch for this run "
        "(scenario runs archive to product/<scenario>-<run_id>)",
    )


@app.get("/api/runs/{run_id}/diff")
async def run_diff(run_id: str) -> dict:
    kind, (source, base_sha) = await _product_source(run_id)
    if not base_sha:
        raise HTTPException(404, "run has no base commit recorded")
    repo, head = (source, "HEAD") if kind == "sandbox" else (ORCHESTRATOR_ROOT, str(source))
    try:
        if kind == "sandbox":
            diff = git_ops.diff_readonly(source, base_sha)
        else:
            diff = git_ops.git(repo, "diff", base_sha, head)
        if not diff.strip():
            # base_sha advances with every integration, so a finished run has
            # nothing newer than it; show everything built since the scaffold.
            root = git_ops.git(repo, "rev-list", "--max-parents=0", head).strip()
            diff = git_ops.git(repo, "diff", root, head)
            base_sha = root
    except git_ops.GitError as error:
        raise HTTPException(500, str(error))
    return {"base_sha": base_sha, "diff": diff, "source": kind}


@app.get("/api/runs/{run_id}/log")
async def run_log(run_id: str) -> dict:
    kind, (source, _) = await _product_source(run_id)
    try:
        if kind == "sandbox":
            log = git_ops.git(source, "log", "--graph", "--oneline", "--decorate", "--all")
        else:
            log = git_ops.git(ORCHESTRATOR_ROOT, "log", "--graph", "--oneline", str(source))
    except git_ops.GitError as error:
        raise HTTPException(500, str(error))
    return {"log": log, "source": kind}


@app.get("/api/runs/{run_id}/files")
async def run_files(run_id: str) -> list[dict]:
    kind, (source, _) = await _product_source(run_id)
    if kind == "sandbox":
        files = []
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in SANDBOX_SKIP_DIRS for part in relative.parts):
                continue
            if path.is_file():
                files.append({"path": relative.as_posix(), "size": path.stat().st_size})
        return files
    out = git_ops.git(ORCHESTRATOR_ROOT, "ls-tree", "-r", "-l", str(source))
    files = []
    for line in out.splitlines():
        # format: <mode> blob <sha> <size>\t<path>
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 4 and parts[1] == "blob":
            size = int(parts[3]) if parts[3].isdigit() else 0
            files.append({"path": path, "size": size})
    return files


@app.get("/api/runs/{run_id}/file")
async def run_file(run_id: str, path: str) -> dict:
    kind, (source, _) = await _product_source(run_id)
    if kind == "sandbox":
        target = (source / path).resolve()
        if not target.is_relative_to(source):
            raise HTTPException(400, "path escapes the sandbox")
        if not target.is_file():
            raise HTTPException(404, f"no file at {path}")
        if target.stat().st_size > FILE_SIZE_LIMIT:
            raise HTTPException(413, "file too large to display")
        raw = target.read_bytes()
    else:
        if path.startswith(("/", "..")) or "/../" in path:
            raise HTTPException(400, "invalid path")
        try:
            raw = git_ops.git(
                ORCHESTRATOR_ROOT, "show", f"{source}:{path}"
            ).encode()
        except git_ops.GitError:
            raise HTTPException(404, f"no file at {path}")
        except UnicodeDecodeError:
            raise HTTPException(415, "binary file")
        if len(raw) > FILE_SIZE_LIMIT:
            raise HTTPException(413, "file too large to display")
    if b"\x00" in raw[:8000]:
        raise HTTPException(415, "binary file")
    return {"path": path, "content": raw.decode("utf-8", errors="replace")}


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
