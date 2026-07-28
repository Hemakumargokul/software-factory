"""Commit: snapshot the verified task onto its work branch, with trailers
correlating the commit to the run for the audit trail."""

import time
from pathlib import Path

from factory import git_ops
from factory.observability import tracing
from factory.state import FactoryState, metric_event, record_decision


async def commit_stage(state: FactoryState) -> dict:
    started = time.monotonic()
    task = state["tasks"][state["task_idx"]]
    sandbox = Path(state["sandbox"])

    with tracing.stage_span("commit", task=task["id"]):
        sha = git_ops.commit_all(
            sandbox,
            f"factory({task['id']}): {task['title']}",
            trailers={
                "Factory-Run-Id": state["run_id"],
                "Factory-Task": task["id"],
            },
        )

    tasks = [
        {**t, "status": "committed"} if t["id"] == task["id"] else t
        for t in state["tasks"]
    ]
    return {
        "head_sha": sha,
        "tasks": tasks,
        "stage_results": {"commit": {"status": "ok", "sha": sha}},
        "decisions": [
            record_decision(
                stage="commit",
                decision=f"{task['id']} committed",
                rationale="verification passed for this task",
                commit_sha=sha,
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "commit",
                ok=True,
                task=task["id"],
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
