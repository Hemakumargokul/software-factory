"""CLI: run the factory, answer its human gates, inspect and resume runs.

Every run is a checkpointed thread in runs/checkpoints.db. When the graph
hits a human gate it parks there — the process can exit, the machine can
reboot — and `factory approve <run_id>` resumes it exactly where it
stopped. Ctrl-C is therefore always safe: nothing is lost but the stage
that was mid-flight, and `factory resume <run_id>` re-runs it.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

import typer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from rich.console import Console
from rich.panel import Panel

from factory import tracing
from factory.graph import build_graph

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

RECURSION_LIMIT = 200  # stage-per-task loops exceed LangGraph's default 25
CHECKPOINT_DB = Path("runs/checkpoints.db")


@app.callback()
def _root() -> None:
    """Software factory: agentic SDLC orchestrator."""


def _config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


@app.command()
def run(
    goal: str,
    profile: str = typer.Option(
        None, help="Project profile (default: FACTORY_PROFILE env or java-springboot)"
    ),
    auto: bool = typer.Option(
        False, "--auto", help="Unattended demo mode: auto-approve every gate"
    ),
):
    """Start a factory run; it pauses at each human gate."""
    profile = profile or os.environ.get("FACTORY_PROFILE", "java-springboot")
    run_id = uuid.uuid4().hex[:8]
    console.print(
        Panel(f"[bold]{goal}[/bold]\nrun_id={run_id}  profile={profile}",
              title="factory run")
    )
    initial = {
        "run_id": run_id,
        "goal": goal,
        "profile": profile,
        "stage_results": {},
        "attempts": 0,
        "replan_budget": 1,
    }
    asyncio.run(_drive(run_id, initial, auto=auto))


@app.command()
def approve(
    run_id: str,
    revise: str = typer.Option(
        None, "--revise", help="Request changes / answer clarifications with this text"
    ),
    reject: bool = typer.Option(False, "--reject", help="Reject at this gate"),
    auto: bool = typer.Option(
        False, "--auto", help="Auto-approve every subsequent gate too"
    ),
):
    """Answer the pending gate of a paused run and continue it."""
    if revise is not None and reject:
        raise typer.BadParameter("--revise and --reject are mutually exclusive")
    if reject:
        answer = {"action": "reject"}
    elif revise is not None:
        answer = {"action": "revise", "edits": revise}
    else:
        answer = {"action": "approve"}
    asyncio.run(_drive(run_id, Command(resume=answer), auto=auto))


@app.command()
def resume(run_id: str):
    """Continue an interrupted run without answering anything: re-displays
    a pending gate, or re-runs the stage a crash/Ctrl-C cut short."""
    asyncio.run(_drive(run_id, None, auto=False))


@app.command()
def status(run_id: str):
    """Show where a run is parked: next nodes and any pending gate."""
    asyncio.run(_status(run_id))


async def _drive(run_id: str, graph_input, *, auto: bool) -> None:
    """Stream the graph until it finishes or parks at a gate. With auto=True,
    gates are answered 'approve' in a loop until the run finishes."""
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(checkpointer=saver)
        config = _config(run_id)

        with tracing.run_context(run_id, f"factory:{run_id}"):
            payload = await _stream(graph, graph_input, config)
            while payload is not None and auto:
                console.print("[yellow]--auto: approving gate "
                              f"'{payload.get('gate')}'[/yellow]")
                payload = await _stream(
                    graph, Command(resume={"action": "approve"}), config
                )
        tracing.flush()

        if payload is not None:
            _print_gate(run_id, payload)
            return
        snapshot = await graph.aget_state(config)
        _print_final(snapshot.values)


async def _stream(graph, graph_input, config):
    """One streaming leg; returns the interrupt payload if the run parked."""
    payload = None
    async for chunk in graph.astream(graph_input, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                payload = update[0].value
                continue
            console.print(f"[cyan]● {node}[/cyan]")
            for decision in (update or {}).get("decisions") or []:
                console.print(f"  [dim]{decision['decision']}[/dim]")
    return payload


def _print_gate(run_id: str, payload: dict) -> None:
    gate = payload.get("gate", "?")
    body = [f"[bold]{payload.get('question', '')}[/bold]", ""]
    for key, value in payload.items():
        if key in ("gate", "question", "diff"):
            continue
        if value in (None, [], ""):
            continue
        rendered = value if isinstance(value, str) else json.dumps(
            value, indent=2, default=str
        )
        body.append(f"[bold]{key}[/bold]: {rendered}")
    console.print(Panel("\n".join(body), title=f"HUMAN GATE: {gate}",
                        border_style="yellow"))
    if payload.get("diff"):
        console.print(Panel(payload["diff"], title="diff", border_style="dim"))

    hint = (
        f"  factory approve {run_id}                    # approve\n"
        f"  factory approve {run_id} --revise \"...\"     # "
        + ("answer the questions" if gate == "clarify" else "request changes")
        + f"\n  factory approve {run_id} --reject           # reject"
    )
    console.print(Panel(hint, title=f"run {run_id} is paused", border_style="yellow"))


def _print_final(values: dict) -> None:
    results = values.get("stage_results") or {}
    release_info = results.get("release") or {}
    stopped = results.get("safe_stop")
    if stopped:
        console.print(
            Panel(f"[red]{stopped.get('reason')}[/red]\n"
                  f"sandbox preserved: {values.get('sandbox')}",
                  title="run safe-stopped")
        )
        return
    console.print(
        Panel(
            f"sandbox: {values.get('sandbox')}\n"
            f"head:    {values.get('head_sha')}\n"
            f"ready:   {release_info.get('ready')}\n"
            f"summary: {(results.get('summary') or {}).get('path')}",
            title="run finished",
        )
    )


async def _status(run_id: str) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(checkpointer=saver)
        snapshot = await graph.aget_state(_config(run_id))

        if not snapshot.values:
            console.print(f"[red]no run found for id {run_id}[/red]")
            raise typer.Exit(1)

        interrupts = [
            interrupt.value
            for task in snapshot.tasks
            for interrupt in task.interrupts
        ]
        results = snapshot.values.get("stage_results") or {}
        lines = [
            f"goal:     {snapshot.values.get('goal')}",
            f"next:     {list(snapshot.next) or '(finished)'}",
            f"task:     {snapshot.values.get('task_idx', 0)}"
            f"/{len(snapshot.values.get('tasks') or [])}",
            f"attempts: {snapshot.values.get('attempts', 0)}"
            f"  replan_budget: {snapshot.values.get('replan_budget')}",
            f"stages:   {sorted(k for k, v in results.items() if v)}",
        ]
        console.print(Panel("\n".join(lines), title=f"run {run_id}"))
        for payload in interrupts:
            _print_gate(run_id, payload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
