"""Decompose: tasks with dependencies, validated and topologically ordered.

A bad decomposition (cycle, unknown dependency id) fails HERE, loudly,
rather than mid-run when a task waits on something that can never finish.
"""

import time
from graphlib import CycleError, TopologicalSorter

from factory import gates, tracing
from factory.claude import DECOMPOSE_PROMPT
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


async def decompose(state: FactoryState) -> dict:
    gates.check_entry("decompose", state)
    started = time.monotonic()

    prompt = DECOMPOSE_PROMPT.substitute(
        spec=compact(state["spec"]), design=compact(state["design"])
    )
    with tracing.stage_span("decompose"):
        data = await run_reasoner("decompose", prompt)

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
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
