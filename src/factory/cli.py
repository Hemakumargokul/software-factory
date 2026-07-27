"""Minimal CLI: `factory run "<goal>"` streams stage progress and decisions.

M8 adds approve/status/resume once human gates exist.
"""

import asyncio
import os
import uuid

import typer
from rich.console import Console
from rich.panel import Panel

from factory import tracing
from factory.graph import build_graph

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def _root() -> None:
    """Software factory: agentic SDLC orchestrator."""
    # Explicit callback so typer keeps `run` as a subcommand even while it
    # is the only one; approve/status/resume join it in M8.

RECURSION_LIMIT = 200  # stage-per-task loops exceed LangGraph's default 25


@app.command()
def run(
    goal: str,
    profile: str = typer.Option(
        None, help="Project profile (default: FACTORY_PROFILE env or java-springboot)"
    ),
):
    """Run the factory on an engineering goal."""
    profile = profile or os.environ.get("FACTORY_PROFILE", "java-springboot")
    run_id = uuid.uuid4().hex[:8]
    console.print(
        Panel(f"[bold]{goal}[/bold]\nrun_id={run_id}  profile={profile}",
              title="factory run")
    )
    final = asyncio.run(_run(goal, profile, run_id))

    results = final.get("stage_results") or {}
    release_info = results.get("release") or {}
    console.print(
        Panel(
            f"sandbox: {final.get('sandbox')}\n"
            f"head:    {final.get('head_sha')}\n"
            f"ready:   {release_info.get('ready')}\n"
            f"summary: {(results.get('summary') or {}).get('path')}",
            title="run finished",
        )
    )


async def _run(goal: str, profile: str, run_id: str) -> dict:
    graph = build_graph()
    initial = {
        "run_id": run_id,
        "goal": goal,
        "profile": profile,
        "stage_results": {},
        "attempts": 0,
        "replan_budget": 1,
    }
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": RECURSION_LIMIT,
    }

    final: dict = {}
    with tracing.run_context(run_id, f"factory:{run_id}"):
        async for chunk in graph.astream(initial, config, stream_mode="updates"):
            for node, update in chunk.items():
                console.print(f"[cyan]● {node}[/cyan]")
                for decision in (update or {}).get("decisions") or []:
                    console.print(
                        f"  [dim]{decision['decision']}[/dim]"
                    )
                final = _merge_view(final, update or {})
    tracing.flush()
    return final


def _merge_view(view: dict, update: dict) -> dict:
    """Cheap client-side mirror of the state for the final banner only."""
    merged = {**view, **{k: v for k, v in update.items() if k != "stage_results"}}
    merged["stage_results"] = {
        **(view.get("stage_results") or {}),
        **(update.get("stage_results") or {}),
    }
    return merged


def main() -> None:
    app()


if __name__ == "__main__":
    main()
