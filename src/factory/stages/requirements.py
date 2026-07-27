"""Requirements: normalized spec with black-box acceptance criteria."""

import time

from factory import gates, tracing
from factory.claude import REQUIREMENTS_PROMPT
from factory.stages.common import compact, run_reasoner
from factory.state import FactoryState, metric_event, record_decision


async def requirements(state: FactoryState) -> dict:
    gates.check_entry("requirements", state)
    started = time.monotonic()

    prompt = REQUIREMENTS_PROMPT.substitute(
        goal=state["goal"],
        intake=compact(state["stage_results"]["intake"]),
    )
    with tracing.stage_span("requirements"):
        spec = await run_reasoner("requirements", prompt)

    criteria = spec.get("acceptance_criteria", [])
    return {
        "spec": spec,
        "stage_results": {"requirements": {"status": "ok"}},
        "decisions": [
            record_decision(
                stage="requirements",
                decision=f"spec written with {len(criteria)} acceptance criteria",
                rationale=spec.get("summary", "")[:500],
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "requirements",
                ok=True,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
