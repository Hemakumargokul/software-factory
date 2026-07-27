"""Intake: normalize the request, classify the scenario, score ambiguity.

On the second pass (after the clarify gate) the human's answers are folded
into the prompt as authoritative parts of the request.
"""

import time

from factory import tracing
from factory.claude import CLARIFICATIONS_PROMPT, INTAKE_PROMPT
from factory.stages.common import run_reasoner
from factory.state import FactoryState, metric_event, record_decision


def _clarifications(state: FactoryState) -> str:
    answers = ((state.get("stage_results") or {}).get("clarify") or {}).get(
        "answers"
    )
    if not answers:
        return ""
    return CLARIFICATIONS_PROMPT.substitute(answers=answers)


async def intake(state: FactoryState) -> dict:
    started = time.monotonic()
    prompt = INTAKE_PROMPT.substitute(
        goal=state["goal"], clarifications=_clarifications(state)
    )
    with tracing.stage_span("intake", goal=state["goal"]):
        data = await run_reasoner("intake", prompt)

    scenario = data.get("scenario", "greenfield")
    score = data.get("ambiguity_score", 0.0)
    return {
        "scenario": scenario,
        "ambiguities": data.get("ambiguities", []),
        "stage_results": {"intake": data},
        "decisions": [
            record_decision(
                stage="intake",
                decision=f"classified as {scenario}, ambiguity {score}",
                rationale=data.get("problem", "")[:500],
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "intake",
                ok=True,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
