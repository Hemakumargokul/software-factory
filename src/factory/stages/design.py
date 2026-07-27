"""Design: architecture, API contract, data model, and the initial risks."""

import time

from factory import gates, tracing
from factory.claude import DESIGN_PROMPT
from factory.profiles import get_profile
from factory.stages.common import compact, run_reasoner
from factory.state import FactoryState, metric_event, record_decision


async def design(state: FactoryState) -> dict:
    gates.check_entry("design", state)
    started = time.monotonic()
    profile = get_profile(state["profile"])

    prompt = DESIGN_PROMPT.substitute(
        language=profile.stack_description, spec=compact(state["spec"])
    )
    with tracing.stage_span("design"):
        data = await run_reasoner("design", prompt)

    return {
        "design": data,
        "risks": [
            {"stage": "design", **risk} for risk in data.get("risks", [])
        ],
        "stage_results": {"design": {"status": "ok"}},
        "decisions": [
            record_decision(
                stage="design",
                decision=data.get("architecture", "")[:300],
                rationale=f"{len(data.get('components', []))} components, "
                f"{len(data.get('api_contract', []))} endpoints, "
                f"{len(data.get('risks', []))} risks identified",
                alternatives=data.get("alternatives_considered", []),
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "design",
                ok=True,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
