"""Graph state, reducers and decision lineage.

Two kinds of state live here and they age differently:

- Working artifacts (``stage_results``) are keyed by stage and overwritable,
  because a re-plan must be able to invalidate downstream work by writing
  ``None`` over a key.
- Lineage (``decisions``, ``audit``, ``metric_events``, ``risks``) is
  append-only on purpose: history is never erased, and an invalidation is
  itself recorded as a decision.
"""

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict


def merge_stage_results(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """Reducer for the keyed stage-results dict.

    Parallel branches write disjoint keys, so a plain merge is concurrency-safe.
    The right side wins on conflict, which is what lets a re-plan invalidate a
    downstream stage by writing ``{"design": None}``.
    """
    return {**(left or {}), **(right or {})}


Scenario = Literal["greenfield", "brownfield", "ambiguous"]


class Decision(TypedDict):
    stage: str
    decision: str
    rationale: str
    alternatives: list[str]
    commit_sha: str | None
    trace_id: str | None
    at: str


class FactoryState(TypedDict, total=False):
    # Run identity and the engineering problem
    run_id: str
    goal: str
    profile: str                   # ProjectProfile name, e.g. "java-springboot"
    scenario: Scenario

    # Stage artifacts (also mirrored into stage_results for invalidation)
    spec: dict | None              # normalized requirement plus acceptance criteria
    ambiguities: list[str]
    impact: dict | None            # brownfield impact analysis
    design: dict | None
    tasks: list[dict]              # each: {id, title, depends_on: [id], status}
    task_idx: int

    # Git anchors: keep the checkpoint and the working tree from diverging
    sandbox: str                   # path to the product repo
    base_sha: str                  # known-good commit; rollback target
    head_sha: str | None

    # Control budgets (in state, not closures, so they survive resume)
    attempts: int
    replan_budget: int

    # Working results: keyed by stage, None means invalidated
    stage_results: Annotated[dict[str, Any], merge_stage_results]

    # Append-only lineage
    risks: Annotated[list[dict], operator.add]
    decisions: Annotated[list[Decision], operator.add]
    audit: Annotated[list[dict], operator.add]
    metric_events: Annotated[list[dict], operator.add]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_decision(
    stage: str,
    decision: str,
    rationale: str,
    alternatives: tuple[str, ...] | list[str] = (),
    commit_sha: str | None = None,
    trace_id: str | None = None,
) -> Decision:
    """Build a lineage record. Every node emits decisions through this helper
    so the final engineering summary can be generated instead of written."""
    return Decision(
        stage=stage,
        decision=decision,
        rationale=rationale,
        alternatives=list(alternatives),
        commit_sha=commit_sha,
        trace_id=trace_id,
        at=_now_iso(),
    )


def audit_event(kind: str, stage: str, **payload: Any) -> dict:
    """An audit trail entry, e.g. a tool call allowed or denied."""
    return {"at": _now_iso(), "kind": kind, "stage": stage, "payload": payload}


def metric_event(kind: str, stage: str, **payload: Any) -> dict:
    """A reliability-metric event; metrics.py aggregates these per run."""
    return {"at": _now_iso(), "kind": kind, "stage": stage, "payload": payload}
