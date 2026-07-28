"""Release readiness: assemble the checklist from what actually happened.

No LLM here — readiness is a mechanical statement over stage results and
task statuses, not prose.
"""

import time

from factory.observability import tracing
from factory.state import FactoryState, metric_event, record_decision


async def release(state: FactoryState) -> dict:
    started = time.monotonic()
    results = state.get("stage_results") or {}
    tasks = state.get("tasks") or []

    checklist = {
        "all_tasks_integrated": all(t.get("status") == "integrated" for t in tasks),
        "build_and_tests_pass": (results.get("tests") or {}).get("status") == "pass",
        "acceptance": (results.get("acceptance") or {}).get("status", "missing"),
        "open_ambiguities": len(state.get("ambiguities") or []),
        "risks_recorded": len(state.get("risks") or []),
        "head_sha": state.get("head_sha"),
    }
    ready = (
        checklist["all_tasks_integrated"]
        and checklist["build_and_tests_pass"]
        and checklist["acceptance"] in ("pass", "skipped")
    )

    with tracing.stage_span("release") as span:
        span.update(output=checklist)

    return {
        "stage_results": {"release": {"ready": ready, "checklist": checklist}},
        "decisions": [
            record_decision(
                stage="release",
                decision="release ready" if ready else "NOT release ready",
                rationale=str(checklist),
                commit_sha=state.get("head_sha"),
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "release",
                ok=ready,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
