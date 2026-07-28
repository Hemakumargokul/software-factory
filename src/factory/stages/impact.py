"""Impact: brownfield-only read-only analysis of the existing codebase.

Runs between the requirement gate and design when intake classified the
request as brownfield. The analyst role reads the sandbox (Read/Glob/Grep,
no writes, no Bash) and reports what exists, what must change and what
could regress — design then builds on facts instead of guessing at code
it cannot see.
"""

import time
from pathlib import Path

from factory.observability import tracing
from factory.agent.claude import analyst_role
from factory.agent.prompts import ANALYST_SYSTEM_PROMPT, IMPACT_PROMPT
from factory.stages.common import call_json_role, compact
from factory.state import FactoryState, metric_event, record_decision


async def impact(state: FactoryState) -> dict:
    started = time.monotonic()
    prompt = IMPACT_PROMPT.substitute(spec=compact(state["spec"]))

    with tracing.stage_span("impact", sandbox=state["sandbox"]):
        data, cost = await call_json_role(
            "impact",
            analyst_role(),
            prompt,
            system_prompt=ANALYST_SYSTEM_PROMPT,
            cwd=Path(state["sandbox"]),
        )

    return {
        "impact": data,
        "stage_results": {"impact": data},
        "risks": list(data.get("regression_risks", [])),
        "decisions": [
            record_decision(
                stage="impact",
                decision=f"impact analysis: {len(data.get('affected_files', []))} "
                f"files affected, {len(data.get('regression_risks', []))} "
                "regression risks",
                rationale=data.get("current_state", "")[:500],
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "impact",
                ok=True,
                cost_usd=cost,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
