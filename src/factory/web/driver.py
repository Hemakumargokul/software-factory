"""Drive checkpointed runs from the web server.

Mirrors cli._drive: stream the graph until it finishes, parks at a human
gate, or is killed. The difference is the audience — instead of printing
to a console, every node update is published to in-memory subscriber
queues that the SSE endpoint forwards to browsers.

One driver task per run at a time, enforced by the registry. The
checkpointer remains the source of truth: a server restart loses only the
stage that was mid-flight, exactly like Ctrl-C on the CLI.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from factory.cli import CHECKPOINT_DB, RECURSION_LIMIT
from factory.governance import control
from factory.graph import build_graph
from factory.observability import metrics as metrics_module
from factory.observability import tracing

MAX_EVENTS_KEPT = 500  # replay buffer per run; SSE reconnects get history


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def _outcome(values: dict, *, paused: bool) -> str:
    results = values.get("stage_results") or {}
    if results.get("safe_stop"):
        return "safe_stopped"
    if results.get("summary"):
        return "finished"
    return "paused" if paused else "stopped"


class RunHandle:
    """Event history and live subscribers for one run being driven."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None

    @property
    def driving(self) -> bool:
        return self.task is not None and not self.task.done()

    def publish(self, event: dict) -> None:
        event = {**event, "at": _now(), "seq": len(self.events)}
        self.events.append(event)
        if len(self.events) > MAX_EVENTS_KEPT:
            del self.events[: -MAX_EVENTS_KEPT]
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    async def subscribe(self, after_seq: int = -1) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        try:
            for event in list(self.events):
                if event["seq"] > after_seq:
                    yield event
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat", "at": _now(), "seq": -1}
                    if not self.driving:
                        return
        finally:
            self.subscribers.discard(queue)


class Registry:
    """All runs this server process knows about."""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}

    def handle(self, run_id: str) -> RunHandle:
        if run_id not in self._handles:
            self._handles[run_id] = RunHandle(run_id)
        return self._handles[run_id]

    def driving(self, run_id: str) -> bool:
        handle = self._handles.get(run_id)
        return handle.driving if handle else False

    def start(self, run_id: str, graph_input: Any, *, auto: bool = False) -> None:
        """Launch the driver task; raises if the run is already being driven."""
        handle = self.handle(run_id)
        if handle.driving:
            raise RuntimeError(f"run {run_id} is already being driven")
        handle.task = asyncio.create_task(
            _drive(handle, graph_input, auto=auto), name=f"drive-{run_id}"
        )


async def _drive(handle: RunHandle, graph_input: Any, *, auto: bool) -> None:
    run_id = handle.run_id
    if control.is_killed(run_id):
        handle.publish({"type": "error", "message": f"run {run_id} is killed"})
        return

    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
            graph = build_graph(checkpointer=saver)
            config = _config(run_id)

            with tracing.run_context(run_id, f"factory:{run_id}"):
                payload = await _stream(graph, graph_input, config, handle)
                while payload is not None and auto and not control.is_killed(run_id):
                    handle.publish({"type": "auto_approve", "gate": payload.get("gate")})
                    payload = await _stream(
                        graph, Command(resume={"action": "approve"}), config, handle
                    )

                killed = control.is_killed(run_id)
                snapshot = await graph.aget_state(config)
                outcome = (
                    "killed" if killed
                    else _outcome(snapshot.values, paused=payload is not None)
                )
                metrics_module.persist(snapshot.values, outcome)
                if payload is None and not killed:
                    report = metrics_module.compute(run_id)
                    for name, value in report.scores().items():
                        tracing.score(name, value)
            tracing.flush()

            if killed:
                handle.publish({"type": "killed"})
            elif payload is not None:
                handle.publish({"type": "gate", "payload": payload})
            else:
                handle.publish({"type": "done", "outcome": outcome})
    except Exception as error:  # surfaced to the UI instead of a dead task
        handle.publish({"type": "error", "message": f"{type(error).__name__}: {error}"})
        raise


async def _stream(graph, graph_input, config, handle: RunHandle):
    """One streaming leg; returns the interrupt payload if the run parked."""
    payload = None
    async for chunk in graph.astream(graph_input, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                payload = update[0].value
                continue
            handle.publish({
                "type": "node",
                "node": node,
                "decisions": [
                    {"decision": d["decision"], "rationale": d["rationale"]}
                    for d in (update or {}).get("decisions") or []
                ],
            })
        if control.is_killed(handle.run_id):
            handle.publish({"type": "kill_pending"})
            break
    return payload
