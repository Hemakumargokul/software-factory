"""Impact: brownfield-only read-only analysis of the existing codebase.

Runs between the requirement gate and design when intake classified the
request as brownfield. The analyst role reads the sandbox (Read/Glob/Grep,
no writes, no Bash) and reports what exists, what must change and what
could regress — design then builds on facts instead of guessing at code
it cannot see.
"""

import time
from pathlib import Path

from factory import claude, tracing
from factory.claude import (
    ANALYST_SYSTEM_PROMPT,
    IMPACT_PROMPT,
    analyst_role,
    extract_json,
)
from factory.stages.common import compact
from factory.state import FactoryState, metric_event, record_decision


async def impact(state: FactoryState) -> dict:
    started = time.monotonic()
    prompt = IMPACT_PROMPT.substitute(spec=compact(state["spec"]))
    role = analyst_role()

    with tracing.stage_span("impact", sandbox=state["sandbox"]):
        with tracing.generation_span("impact", role.model, prompt) as span:
            result = await claude.run_role(
                role,
                prompt,
                cwd=Path(state["sandbox"]),
                system_prompt=ANALYST_SYSTEM_PROMPT,
            )
            span.end_with(result)
    data = extract_json(result.text)

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
                cost_usd=result.cost_usd,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
