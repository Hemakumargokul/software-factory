"""Rollback: restore the sandbox to the last known-good commit.

Reached when retries are exhausted or policy found a violation. The tree
is reset hard to base_sha (untracked leftovers removed), the failure is
packaged as re-plan context for decompose, and the re-plan budget is
spent. Whether the run re-plans or safe-stops is the router's call.
"""

import time
from pathlib import Path

from factory import git_ops, tracing
from factory.state import FactoryState, metric_event, record_decision


def _failure_summary(state: FactoryState) -> dict:
    results = state.get("stage_results") or {}
    return {
        name: {
            "status": result.get("status"),
            "report": (result.get("report") or "")[-2000:],
        }
        for name in ("tests", "policy", "review", "acceptance")
        if (result := results.get(name))
        and result.get("status") in ("fail", "violation")
    }


async def rollback(state: FactoryState) -> dict:
    started = time.monotonic()
    sandbox = Path(state["sandbox"])
    task = state["tasks"][state["task_idx"]]
    failures = _failure_summary(state)

    with tracing.stage_span("rollback", task=task["id"]) as span:
        restored = git_ops.reset_hard(sandbox, state["base_sha"])
        git_ops.git(sandbox, "switch", "main")
        span.update(output={"restored_sha": restored, "failures": list(failures)})

    budget = state.get("replan_budget", 0) - 1
    return {
        "head_sha": restored,
        "attempts": 0,
        "replan_budget": budget,
        # Re-plan context for decompose; downstream task list is rebuilt there.
        "stage_results": {
            "replan": {"task": task["id"], "failures": failures},
            "tests": None,       # invalidated: they describe rolled-back work
            "policy": None,
            "review": None,
            "acceptance": None,
            "implement": None,
        },
        "decisions": [
            record_decision(
                stage="rollback",
                decision=f"rolled back {task['id']} to {restored[:12]}",
                rationale=f"failed stages: {sorted(failures)}; "
                f"replan budget now {budget}",
                commit_sha=restored,
            )
        ],
        "metric_events": [
            metric_event(
                "rollback",
                "rollback",
                task=task["id"],
                failed_stages=sorted(failures),
                replan_budget=budget,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
