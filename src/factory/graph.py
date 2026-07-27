"""The orchestration graph. M8: human gates over the M7 governance loops.

bootstrap -> intake -> [clarify?] -> requirements -> GATE -> design -> GATE
  -> decompose -> implement -> {tests, policy, review}  (fan-out)
  -> sync (defer=True join)
  -> acceptance -> commit -> MERGE GATE -> integrate
  -> ... next task or release -> summary

Failure edges: sync/acceptance feed back to implement while attempts last;
policy violations, exhausted retries and rejected merges roll back to
base_sha; rollback re-plans through decompose while the budget lasts, then
safe-stops.

Human gates are interrupt-only nodes (stages/human_gates.py): the run
parks in the checkpointer until `factory approve` resumes it. Revise at a
gate re-runs the gated stage with the edits and invalidates everything
derived from it.

Routers are pure sync functions of state — unit-testable without running
a single stage. The three verification branches only read the working
tree, so the fan-out is race-free by construction.
"""

from langgraph.graph import END, START, StateGraph

from factory.stages.acceptance import acceptance
from factory.stages.bootstrap import bootstrap
from factory.stages.commit_stage import commit_stage
from factory.stages.decompose import decompose
from factory.stages.design import design
from factory.stages.human_gates import (
    clarify,
    gate_design,
    gate_merge,
    gate_requirements,
)
from factory.stages.impact import impact
from factory.stages.implement import implement
from factory.stages.intake import intake
from factory.stages.integrate import integrate
from factory.stages.policy_stage import policy_stage
from factory.stages.release import release
from factory.stages.requirements import requirements
from factory.stages.review_stage import review_stage
from factory.stages.rollback import rollback
from factory.stages.summary import summary
from factory.stages.tests_stage import tests_stage
from factory.state import FactoryState, metric_event, record_decision, spent_usd

MAX_ATTEMPTS = 3  # initial implementation plus two retries per task
CLARIFY_THRESHOLD = 0.5  # intake ambiguity_score at/above this asks a human


def over_budget(state: FactoryState) -> bool:
    """Has the run spent its aggregate agent budget? Checked by every router
    that would dispatch more agent work (implement, re-plan). Verification
    and wrap-up of already-produced work are never blocked — a budget stop
    preserves value, it does not discard it."""
    budget = state.get("run_budget_usd")
    return bool(budget) and spent_usd(state) >= budget


async def sync(state: FactoryState) -> dict:
    """Join node for the verification fan-out (defer=True: runs only after
    all three branches finish). Pure bookkeeping; the verdict is routed by
    route_after_sync."""
    results = state.get("stage_results") or {}
    statuses = {
        name: (results.get(name) or {}).get("status")
        for name in ("tests", "policy", "review")
    }
    return {
        "metric_events": [
            metric_event("verification_joined", "sync", statuses=statuses,
                         attempt=state.get("attempts", 0))
        ],
    }


async def safe_stop(state: FactoryState) -> dict:
    """Terminal for a run that cannot proceed autonomously: record why and
    stop with the sandbox intact for a human to inspect."""
    results = state.get("stage_results") or {}
    failed = [
        name
        for name in ("tests", "policy", "review", "acceptance")
        if (results.get(name) or {}).get("status") in ("fail", "violation")
    ]
    rejected = [
        gate
        for gate in ("gate_requirements", "gate_design")
        if (results.get(gate) or {}).get("action") == "reject"
    ]
    replan = results.get("replan")
    if rejected:
        reason = f"human rejected at {rejected[0]}"
    elif over_budget(state):
        reason = (
            f"run budget exhausted: spent ${spent_usd(state):.2f} of the "
            f"${state['run_budget_usd']:.2f} cap"
        )
    elif replan:
        reason = f"replan budget exhausted after rollback of {replan['task']}"
    else:
        reason = f"verification failed: {failed or 'unrecoverable state'}"
    return {
        "stage_results": {"safe_stop": {"failed_stages": failed, "reason": reason}},
        "decisions": [
            record_decision(
                stage="safe_stop",
                decision=f"run stopped: {reason}",
                rationale="bounded autonomy: budgets spent, escalating to a "
                "human with the sandbox preserved for inspection",
            )
        ],
    }


def route_after_intake(state: FactoryState) -> str:
    """Ambiguous requests go to a human once; after answers are folded in,
    the run proceeds on recorded assumptions rather than looping forever."""
    results = state.get("stage_results") or {}
    if results.get("clarify"):
        return "requirements"
    score = (results.get("intake") or {}).get("ambiguity_score", 0.0)
    if state.get("scenario") == "ambiguous" or score >= CLARIFY_THRESHOLD:
        return "clarify"
    return "requirements"


def _route_after_gate(gate: str, on_approve: str, on_revise: str, on_reject: str):
    def router(state: FactoryState) -> str:
        action = ((state.get("stage_results") or {}).get(gate) or {}).get("action")
        if action == "approve":
            return on_approve
        if action == "revise":
            return on_revise
        return on_reject

    router.__name__ = f"route_after_{gate}"
    return router


def route_after_gate_requirements(state: FactoryState) -> str:
    """Approved specs route through impact analysis when the request
    changes an existing system; greenfield goes straight to design."""
    action = ((state.get("stage_results") or {}).get("gate_requirements") or {}).get(
        "action"
    )
    if action == "approve":
        if over_budget(state):
            return "safe_stop"
        return "impact" if state.get("scenario") == "brownfield" else "design"
    if action == "revise":
        return "requirements"
    return "safe_stop"


def route_after_gate_design(state: FactoryState) -> str:
    action = ((state.get("stage_results") or {}).get("gate_design") or {}).get(
        "action"
    )
    if action == "approve":
        return "safe_stop" if over_budget(state) else "decompose"
    if action == "revise":
        return "design"
    return "safe_stop"


# Merge approval is deliberately NOT budget-checked: integrating work that
# already passed verification costs nothing and preserves value.
route_after_gate_merge = _route_after_gate(
    "gate_merge", on_approve="integrate", on_revise="rollback",
    on_reject="rollback",
)


def route_after_sync(state: FactoryState) -> str:
    """Verdict over the joined verification branches.

    Policy violations skip retries entirely — retrying would only teach
    the agent to hide the violation, and the diff is already untrusted.
    An aborted implement attempt (blown cap, stream crash) never proceeds,
    even if its partial tree happens to build — the work is incomplete by
    definition and must be retried or rolled back.
    """
    results = state.get("stage_results") or {}
    if (results.get("policy") or {}).get("status") == "violation":
        return "rollback"
    implement_failed = (results.get("implement") or {}).get("status") == "fail"
    tests_passed = (results.get("tests") or {}).get("status") == "pass"
    if tests_passed and not implement_failed:
        return "acceptance"
    if over_budget(state):
        return "safe_stop"
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "implement"
    return "rollback"


def route_after_acceptance(state: FactoryState) -> str:
    """An acceptance failure feeds back exactly like a unit-test failure,
    on the same attempt budget."""
    status = ((state.get("stage_results") or {}).get("acceptance") or {}).get(
        "status"
    )
    if status in ("pass", "skipped"):
        return "commit"
    if over_budget(state):
        return "safe_stop"
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "implement"
    return "rollback"


def route_after_rollback(state: FactoryState) -> str:
    """Re-plan while the budgets last (rollback already decremented the
    re-plan budget; a re-plan is more agent spend, so the run budget also
    has to allow it)."""
    if over_budget(state):
        return "safe_stop"
    return "decompose" if state.get("replan_budget", 0) >= 0 else "safe_stop"


def route_after_decompose(state: FactoryState) -> str:
    """The last check before agent spend begins on a task."""
    return "safe_stop" if over_budget(state) else "implement"


def route_after_integrate(state: FactoryState) -> str:
    """Next task, or wrap up the run."""
    if state["task_idx"] < len(state.get("tasks") or []):
        return "safe_stop" if over_budget(state) else "implement"
    return "release"


def build_graph(checkpointer=None):
    builder = StateGraph(FactoryState)

    builder.add_node("bootstrap", bootstrap)
    builder.add_node("intake", intake)
    builder.add_node("clarify", clarify)
    builder.add_node("requirements", requirements)
    builder.add_node("gate_requirements", gate_requirements)
    builder.add_node("impact", impact)
    builder.add_node("design", design)
    builder.add_node("gate_design", gate_design)
    builder.add_node("decompose", decompose)
    builder.add_node("implement", implement)
    builder.add_node("tests", tests_stage)
    builder.add_node("policy", policy_stage)
    builder.add_node("review", review_stage)
    builder.add_node("sync", sync, defer=True)  # waits for all branches
    builder.add_node("acceptance", acceptance)
    builder.add_node("commit", commit_stage)
    builder.add_node("gate_merge", gate_merge)
    builder.add_node("integrate", integrate)
    builder.add_node("rollback", rollback)
    builder.add_node("release", release)
    builder.add_node("summary", summary)
    builder.add_node("safe_stop", safe_stop)

    builder.add_edge(START, "bootstrap")
    builder.add_edge("bootstrap", "intake")
    builder.add_conditional_edges(
        "intake", route_after_intake, ["clarify", "requirements"]
    )
    builder.add_edge("clarify", "intake")
    builder.add_edge("requirements", "gate_requirements")
    builder.add_conditional_edges(
        "gate_requirements",
        route_after_gate_requirements,
        ["impact", "design", "requirements", "safe_stop"],
    )
    builder.add_edge("impact", "design")
    builder.add_edge("design", "gate_design")
    builder.add_conditional_edges(
        "gate_design",
        route_after_gate_design,
        ["decompose", "design", "safe_stop"],
    )
    builder.add_conditional_edges(
        "decompose", route_after_decompose, ["implement", "safe_stop"]
    )

    # Parallel verification: one superstep out, deferred join back.
    builder.add_edge("implement", "tests")
    builder.add_edge("implement", "policy")
    builder.add_edge("implement", "review")
    builder.add_edge("tests", "sync")
    builder.add_edge("policy", "sync")
    builder.add_edge("review", "sync")

    builder.add_conditional_edges(
        "sync", route_after_sync,
        ["acceptance", "implement", "rollback", "safe_stop"],
    )
    builder.add_conditional_edges(
        "acceptance", route_after_acceptance,
        ["commit", "implement", "rollback", "safe_stop"],
    )
    builder.add_edge("commit", "gate_merge")
    builder.add_conditional_edges(
        "gate_merge", route_after_gate_merge, ["integrate", "rollback"]
    )
    builder.add_conditional_edges(
        "integrate", route_after_integrate, ["implement", "release", "safe_stop"]
    )
    builder.add_conditional_edges(
        "rollback", route_after_rollback, ["decompose", "safe_stop"]
    )
    builder.add_edge("release", "summary")
    builder.add_edge("summary", END)
    builder.add_edge("safe_stop", END)

    return builder.compile(checkpointer=checkpointer)
