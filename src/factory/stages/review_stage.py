"""Review branch of the verification fan-out: analyst critique of the diff.

Implemented as a no-tools reasoner over the truncated diff — trivially
read-only, cheap, and sufficient for diffs of this size. (An agentic
read-tools analyst is the upgrade path if diffs outgrow a prompt.)

The verdict is advisory by design: concerns become risk-register entries
for the human to read at the merge gate; they do not fail the task. The
blocking checks are the deterministic ones (tests, policy).
"""

import time
from pathlib import Path

from factory import git_ops, tracing
from factory.claude import REVIEW_PROMPT
from factory.stages.common import compact, run_reasoner
from factory.state import FactoryState, metric_event

DIFF_LIMIT = 12000


async def review_stage(state: FactoryState) -> dict:
    started = time.monotonic()
    task = state["tasks"][state["task_idx"]]
    sandbox = Path(state["sandbox"])

    # Read-only diff: runs concurrently with the policy branch (index.lock).
    diff = git_ops.diff_readonly(sandbox, state["base_sha"])
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + "\n...[diff truncated]"

    prompt = REVIEW_PROMPT.substitute(
        task_id=task["id"],
        task_title=task["title"],
        design=compact(state["design"]),
        diff=diff,
    )
    with tracing.stage_span("review", task=task["id"]):
        data = await run_reasoner("review", prompt)

    verdict = data.get("verdict", "approve")
    concerns = data.get("concerns", [])
    return {
        "stage_results": {
            "review": {
                "status": "pass",  # advisory: concerns inform, never block
                "verdict": verdict,
                "concerns": concerns,
            }
        },
        "risks": [
            {"stage": "review", "task": task["id"], **risk}
            for risk in data.get("risks", [])
        ],
        "metric_events": [
            metric_event(
                "verification",
                "review",
                ok=True,
                verdict=verdict,
                concerns=len(concerns),
                attempt=state.get("attempts", 0),
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
