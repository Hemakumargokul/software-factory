"""Decompose: tasks with dependencies, validated and topologically ordered.

A bad decomposition (cycle, unknown dependency id) fails HERE, loudly,
rather than mid-run when a task waits on something that can never finish.
"""

import time
from graphlib import CycleError, TopologicalSorter

from factory.governance import gates
from factory.observability import tracing
from factory.agent.prompts import DECOMPOSE_PROMPT, REPLAN_CONTEXT_PROMPT
from factory.stages.common import compact, run_reasoner
from factory.state import FactoryState, metric_event, record_decision


def validate_and_order(tasks: list[dict]) -> list[dict]:
    """Check dependency ids exist and the DAG is acyclic; return tasks in
    topological order with status initialized."""
    by_id = {task["id"]: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate task ids in decomposition")

    for task in tasks:
        unknown = [dep for dep in task.get("depends_on", []) if dep not in by_id]
        if unknown:
            raise ValueError(
                f"task {task['id']} depends on unknown tasks: {unknown}"
            )

    sorter = TopologicalSorter(
        {task["id"]: set(task.get("depends_on", [])) for task in tasks}
    )
    try:
        order = list(sorter.static_order())
    except CycleError as error:
        raise ValueError(f"dependency cycle in decomposition: {error}") from None

    return [{**by_id[task_id], "status": "pending"} for task_id in order]


def _replan_context(state: FactoryState) -> str:
    """After a rollback, the new decomposition must know what failed."""
    replan = (state.get("stage_results") or {}).get("replan")
    if not replan:
        return ""
    return REPLAN_CONTEXT_PROMPT.substitute(
        failed_task=replan.get("task", "?"),
        failures=compact(replan.get("failures", {}), limit=3000),
    )


async def decompose(state: FactoryState) -> dict:
    gates.check_entry("decompose", state)
    started = time.monotonic()

    prompt = DECOMPOSE_PROMPT.substitute(
        spec=compact(state["spec"]),
        design=compact(state["design"]),
        replan_context=_replan_context(state),
    )
    with tracing.stage_span("decompose"):
        data, cost = await run_reasoner("decompose", prompt)

    tasks = validate_and_order(data.get("tasks", []))
    if not tasks:
        raise ValueError("decomposition produced no tasks")

    dag = {task["id"]: task.get("depends_on", []) for task in tasks}
    return {
        "tasks": tasks,
        "task_idx": 0,
        "stage_results": {"decompose": {"status": "ok", "dag": dag}},
        "decisions": [
            record_decision(
                stage="decompose",
                decision=f"{len(tasks)} tasks: "
                + ", ".join(t["id"] for t in tasks),
                rationale=f"topologically ordered; dependency graph {dag}",
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "decompose",
                ok=True,
                cost_usd=cost,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
