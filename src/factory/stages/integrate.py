"""Integrate: merge the task branch to main — the only path to main.

The merge SHA becomes the new base_sha: the next task's rollback target is
the last integrated state, not the original scaffold.
"""

import time
from pathlib import Path

from factory import git_ops
from factory.observability import tracing
from factory.state import FactoryState, metric_event, record_decision


async def integrate(state: FactoryState) -> dict:
    started = time.monotonic()
    task = state["tasks"][state["task_idx"]]
    sandbox = Path(state["sandbox"])
    branch = f"factory/{task['id'].lower()}"

    with tracing.stage_span("integrate", task=task["id"]):
        merge_sha = git_ops.merge_to_main(
            sandbox, branch, message=f"factory: integrate {task['id']}"
        )

    tasks = [
        {**t, "status": "integrated"} if t["id"] == task["id"] else t
        for t in state["tasks"]
    ]
    return {
        "base_sha": merge_sha,   # new known-good rollback target
        "head_sha": merge_sha,
        "tasks": tasks,
        "task_idx": state["task_idx"] + 1,
        "attempts": 0,           # fresh budget for the next task
        "stage_results": {"integrate": {"status": "ok", "sha": merge_sha}},
        "decisions": [
            record_decision(
                stage="integrate",
                decision=f"{task['id']} merged to main",
                rationale="no-ff merge; every integration is a distinct "
                "auditable merge commit",
                commit_sha=merge_sha,
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "integrate",
                ok=True,
                task=task["id"],
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
