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

from rich.table import Table

from factory.governance import control
from factory.observability import metrics as metrics_module
from factory.observability import tracing
from factory.graph import build_graph
from factory.state import spent_usd

DEFAULT_RUN_BUDGET_USD = 5.0

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
    budget: float = typer.Option(
        None, "--budget",
        help="Aggregate agent-spend cap in USD for the whole run "
        "(default: FACTORY_RUN_BUDGET_USD env or 5.0; 0 disables)",
    ),
):
    """Start a factory run; it pauses at each human gate."""
    profile = profile or os.environ.get("FACTORY_PROFILE", "java-springboot")
    if budget is None:
        budget = float(
            os.environ.get("FACTORY_RUN_BUDGET_USD", DEFAULT_RUN_BUDGET_USD)
        )
    run_id = uuid.uuid4().hex[:8]
    console.print(
        Panel(f"[bold]{goal}[/bold]\nrun_id={run_id}  profile={profile}  "
              f"budget=${budget:.2f}",
              title="factory run")
    )
    initial = {
        "run_id": run_id,
        "goal": goal,
        "profile": profile,
        "stage_results": {},
        "attempts": 0,
        "replan_budget": 1,
        "run_budget_usd": budget,
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
def kill(
    run_id: str,
    clear: bool = typer.Option(
        False, "--clear", help="Lift the kill and make the run resumable again"
    ),
):
    """Kill switch: stop a run from any terminal.

    A running run stops at the next stage boundary (checkpointed — nothing
    verified is lost); a parked run refuses to resume or take gate answers.
    Reversible: `factory kill <run_id> --clear` then `factory resume`.
    """
    if clear:
        control.clear_kill(run_id)
        console.print(f"kill flag cleared for {run_id}; "
                      f"[dim]factory resume {run_id}[/dim] continues it")
        return
    control.request_kill(run_id)
    console.print(
        Panel(
            f"[red]kill requested for {run_id}[/red]\n"
            "a running driver stops at the next stage boundary; "
            "resume/approve are refused until\n"
            f"  factory kill {run_id} --clear",
            title="kill switch",
        )
    )


@app.command()
def status(run_id: str):
    """Show where a run is parked: next nodes and any pending gate."""
    asyncio.run(_status(run_id))


@app.command()
def metrics(run_id: str = typer.Argument(None)):
    """Reliability metrics: one run's report, or the run index."""
    if run_id is None:
        table = Table(title="factory runs")
        for column in ("run_id", "started", "outcome", "scenario", "goal"):
            table.add_column(column)
        for row in metrics_module.list_runs():
            table.add_row(
                row["run_id"], row["started"], row["outcome"],
                row["scenario"], (row["goal"] or "")[:60],
            )
        console.print(table)
        return

    try:
        report = metrics_module.compute(run_id)
    except KeyError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"run {run_id} — {report.outcome}")
    table.add_column("metric")
    table.add_column("value", justify="right")

    def fmt(value, suffix=""):
        return "-" if value is None else f"{value:.2f}{suffix}"

    table.add_row("scenario", report.scenario or "-")
    table.add_row("end-to-end latency", fmt(report.end_to_end_s, "s"))
    table.add_row(
        "verification success rate",
        fmt(report.success_rate and report.success_rate * 100, "%"),
    )
    table.add_row(
        "first-attempt success rate",
        fmt(
            report.first_attempt_success_rate
            and report.first_attempt_success_rate * 100,
            "%",
        ),
    )
    table.add_row("retries", str(report.retries))
    table.add_row("rollbacks", str(report.rollbacks))
    table.add_row("MTTR", fmt(report.mttr_s, "s"))
    table.add_row("unresolved failures", str(report.unresolved_failures))
    table.add_row("agent cost", f"${report.cost_usd:.2f}")
    console.print(table)

    breakdown = Table(title="per-stage time")
    breakdown.add_column("stage")
    breakdown.add_column("total", justify="right")
    for stage, seconds in sorted(
        report.stage_durations.items(), key=lambda kv: -kv[1]
    ):
        breakdown.add_row(stage, f"{seconds:.1f}s")
    console.print(breakdown)


async def _drive(run_id: str, graph_input, *, auto: bool) -> None:
    """Stream the graph until it finishes, parks at a gate, or is killed.
    With auto=True, gates are answered 'approve' in a loop until the run
    finishes."""
    if control.is_killed(run_id):
        console.print(
            f"[red]run {run_id} is killed[/red] — "
            f"[dim]factory kill {run_id} --clear[/dim] to make it resumable"
        )
        raise typer.Exit(1)

    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(checkpointer=saver)
        config = _config(run_id)

        with tracing.run_context(run_id, f"factory:{run_id}"):
            payload = await _stream(graph, graph_input, config, run_id)
            while payload is not None and auto and not control.is_killed(run_id):
                console.print("[yellow]--auto: approving gate "
                              f"'{payload.get('gate')}'[/yellow]")
                payload = await _stream(
                    graph, Command(resume={"action": "approve"}), config, run_id
                )

            killed = control.is_killed(run_id)
            snapshot = await graph.aget_state(config)
            outcome = (
                "killed" if killed
                else _outcome(snapshot.values, paused=payload is not None)
            )
            metrics_module.persist(snapshot.values, outcome)
            if payload is None and not killed:
                # Terminal: attach the reliability scores to the trace.
                report = metrics_module.compute(run_id)
                for name, value in report.scores().items():
                    tracing.score(name, value)
        tracing.flush()

        if killed:
            console.print(
                Panel(
                    f"[red]run stopped by kill switch[/red]\n"
                    f"checkpointed at the last completed stage; "
                    f"sandbox preserved: {snapshot.values.get('sandbox')}\n"
                    f"resume with: factory kill {run_id} --clear && "
                    f"factory resume {run_id}",
                    title="run killed",
                )
            )
            return
        if payload is not None:
            _print_gate(run_id, payload)
            return
        _print_final(snapshot.values)
        console.print(f"[dim]metrics: factory metrics {run_id}[/dim]")


async def _stream(graph, graph_input, config, run_id: str):
    """One streaming leg; returns the interrupt payload if the run parked.

    The kill flag is checked between graph supersteps: breaking out of the
    stream closes it, the just-finished superstep is already checkpointed,
    and no further stage is dispatched.
    """
    payload = None
    async for chunk in graph.astream(graph_input, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                payload = update[0].value
                continue
            console.print(f"[cyan]● {node}[/cyan]")
            for decision in (update or {}).get("decisions") or []:
                console.print(f"  [dim]{decision['decision']}[/dim]")
        if control.is_killed(run_id):
            console.print("[red]kill switch: stopping at stage boundary[/red]")
            break
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


def _outcome(values: dict, *, paused: bool) -> str:
    results = values.get("stage_results") or {}
    if results.get("safe_stop"):
        return "safe_stopped"
    if results.get("summary"):
        return "finished"
    return "paused" if paused else "stopped"


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
            f"spend:   ${spent_usd(values):.2f}\n"
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
        budget = snapshot.values.get("run_budget_usd")
        lines = [
            f"goal:     {snapshot.values.get('goal')}",
            f"next:     {list(snapshot.next) or '(finished)'}",
            f"task:     {snapshot.values.get('task_idx', 0)}"
            f"/{len(snapshot.values.get('tasks') or [])}",
            f"attempts: {snapshot.values.get('attempts', 0)}"
            f"  replan_budget: {snapshot.values.get('replan_budget')}",
            f"spend:    ${spent_usd(snapshot.values):.2f}"
            + (f" of ${budget:.2f} budget" if budget else " (no budget cap)"),
            f"stages:   {sorted(k for k, v in results.items() if v)}",
        ]
        if control.is_killed(run_id):
            lines.append("[red]KILLED — factory kill "
                         f"{run_id} --clear to make it resumable[/red]")
        console.print(Panel("\n".join(lines), title=f"run {run_id}"))
        for payload in interrupts:
            _print_gate(run_id, payload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
