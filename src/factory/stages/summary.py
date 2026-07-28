"""Summary: the engineering summary GENERATED from lineage, not written.

The reasoner gets the decision lineage, risk register and metric events —
nothing else — so the summary can only contain what the run actually
recorded. Written to runs/<run_id>/summary.md for the deliverable.
"""

import time
from pathlib import Path

from factory.observability import tracing
from factory.agent import claude
from factory.agent.prompts import SUMMARY_PROMPT
from factory.stages.common import compact, run_reasoner
from factory.state import FactoryState, metric_event, record_decision

RUNS_DIR = Path(__file__).resolve().parents[3] / "runs"


async def summary(state: FactoryState) -> dict:
    started = time.monotonic()

    prompt = SUMMARY_PROMPT.substitute(
        goal=state["goal"],
        decisions=compact(state.get("decisions") or [], limit=8000),
        risks=compact(state.get("risks") or []),
        metrics=compact(state.get("metric_events") or [], limit=4000),
    )
    with tracing.stage_span("summary"):
        try:
            data, cost = await run_reasoner("summary", prompt)
            markdown = data.get("summary_markdown", "")
        except claude.JsonExtractionError as exc:
            # The deliverable IS markdown; a model that answered with the
            # document instead of the JSON wrapper still answered. Use the
            # raw reply rather than failing the whole run at its last step.
            markdown, cost = exc.raw, 0.0
    out_path = RUNS_DIR / state["run_id"] / "summary.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)

    return {
        "stage_results": {"summary": {"status": "ok", "path": str(out_path)}},
        "decisions": [
            record_decision(
                stage="summary",
                decision=f"engineering summary written to {out_path}",
                rationale="generated from decision lineage, risks and metrics",
                commit_sha=state.get("head_sha"),
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "summary",
                ok=True,
                cost_usd=cost,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
