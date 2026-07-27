"""The orchestration graph. M7: parallel verification with governance loops.

bootstrap -> intake -> requirements -> design -> decompose
  -> implement -> {tests, policy, review}   (one superstep, fan-out)
  -> sync (defer=True join)
  -> acceptance -> commit -> integrate -> ... next task or release -> summary

Failure edges: sync/acceptance feed back to implement while attempts last;
policy violations and exhausted retries roll back to base_sha; rollback
re-plans through decompose while the budget lasts, then safe-stops.

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
from factory.state import FactoryState, metric_event, record_decision

MAX_ATTEMPTS = 3  # initial implementation plus two retries per task


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
    replan = results.get("replan")
    reason = (
        f"replan budget exhausted after rollback of {replan['task']}"
        if replan
        else f"verification failed: {failed or 'unrecoverable state'}"
    )
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


def route_after_sync(state: FactoryState) -> str:
    """Verdict over the joined verification branches.

    Policy violations skip retries entirely — retrying would only teach
    the agent to hide the violation, and the diff is already untrusted.
    """
    results = state.get("stage_results") or {}
    if (results.get("policy") or {}).get("status") == "violation":
        return "rollback"
    if (results.get("tests") or {}).get("status") == "pass":
        return "acceptance"
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
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "implement"
    return "rollback"


def route_after_rollback(state: FactoryState) -> str:
    """Re-plan while the budget lasts (rollback already decremented it)."""
    return "decompose" if state.get("replan_budget", 0) >= 0 else "safe_stop"


def route_after_integrate(state: FactoryState) -> str:
    """Next task, or wrap up the run."""
    if state["task_idx"] < len(state.get("tasks") or []):
        return "implement"
    return "release"


def build_graph(checkpointer=None):
    builder = StateGraph(FactoryState)

    builder.add_node("bootstrap", bootstrap)
    builder.add_node("intake", intake)
    builder.add_node("requirements", requirements)
    builder.add_node("design", design)
    builder.add_node("decompose", decompose)
    builder.add_node("implement", implement)
    builder.add_node("tests", tests_stage)
    builder.add_node("policy", policy_stage)
    builder.add_node("review", review_stage)
    builder.add_node("sync", sync, defer=True)  # waits for all branches
    builder.add_node("acceptance", acceptance)
    builder.add_node("commit", commit_stage)
    builder.add_node("integrate", integrate)
    builder.add_node("rollback", rollback)
    builder.add_node("release", release)
    builder.add_node("summary", summary)
    builder.add_node("safe_stop", safe_stop)

    builder.add_edge(START, "bootstrap")
    builder.add_edge("bootstrap", "intake")
    builder.add_edge("intake", "requirements")
    builder.add_edge("requirements", "design")
    builder.add_edge("design", "decompose")
    builder.add_edge("decompose", "implement")

    # Parallel verification: one superstep out, deferred join back.
    builder.add_edge("implement", "tests")
    builder.add_edge("implement", "policy")
    builder.add_edge("implement", "review")
    builder.add_edge("tests", "sync")
    builder.add_edge("policy", "sync")
    builder.add_edge("review", "sync")

    builder.add_conditional_edges(
        "sync", route_after_sync, ["acceptance", "implement", "rollback"]
    )
    builder.add_conditional_edges(
        "acceptance", route_after_acceptance, ["commit", "implement", "rollback"]
    )
    builder.add_edge("commit", "integrate")
    builder.add_conditional_edges(
        "integrate", route_after_integrate, ["implement", "release"]
    )
    builder.add_conditional_edges(
        "rollback", route_after_rollback, ["decompose", "safe_stop"]
    )
    builder.add_edge("release", "summary")
    builder.add_edge("summary", END)
    builder.add_edge("safe_stop", END)

    return builder.compile(checkpointer=checkpointer)
