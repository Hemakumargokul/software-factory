"""The orchestration graph. M6: sequential spine.

bootstrap -> intake -> requirements -> design -> decompose
  -> [per task] implement -> tests -> acceptance -> commit -> integrate
  -> release -> summary

Routers are pure sync functions of state — that is what makes every
conditional edge unit-testable without running a single stage. M7 replaces
the linear verification with the parallel fan-out plus retry/rollback.
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
from factory.stages.release import release
from factory.stages.requirements import requirements
from factory.stages.summary import summary
from factory.stages.tests_stage import tests_stage
from factory.state import FactoryState, record_decision


async def safe_stop(state: FactoryState) -> dict:
    """Terminal for a run that cannot proceed autonomously: record why and
    stop with the sandbox intact for a human to inspect."""
    results = state.get("stage_results") or {}
    failed = [
        name
        for name in ("tests", "acceptance")
        if (results.get(name) or {}).get("status") == "fail"
    ]
    return {
        "stage_results": {"safe_stop": {"failed_stages": failed}},
        "decisions": [
            record_decision(
                stage="safe_stop",
                decision=f"run stopped: {failed or 'unrecoverable state'}",
                rationale="verification failed and M6 has no retry path; "
                "sandbox preserved for inspection",
            )
        ],
    }


def route_after_acceptance(state: FactoryState) -> str:
    """Verification verdict for the current task (M6: pass/stop; M7 adds
    retries and rollback)."""
    results = state.get("stage_results") or {}
    tests_ok = (results.get("tests") or {}).get("status") == "pass"
    acceptance_ok = (results.get("acceptance") or {}).get("status") in (
        "pass",
        "skipped",
    )
    return "commit" if tests_ok and acceptance_ok else "safe_stop"


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
    builder.add_node("acceptance", acceptance)
    builder.add_node("commit", commit_stage)
    builder.add_node("integrate", integrate)
    builder.add_node("release", release)
    builder.add_node("summary", summary)
    builder.add_node("safe_stop", safe_stop)

    builder.add_edge(START, "bootstrap")
    builder.add_edge("bootstrap", "intake")
    builder.add_edge("intake", "requirements")
    builder.add_edge("requirements", "design")
    builder.add_edge("design", "decompose")
    builder.add_edge("decompose", "implement")
    builder.add_edge("implement", "tests")
    builder.add_edge("tests", "acceptance")
    builder.add_conditional_edges(
        "acceptance", route_after_acceptance, ["commit", "safe_stop"]
    )
    builder.add_edge("commit", "integrate")
    builder.add_conditional_edges(
        "integrate", route_after_integrate, ["implement", "release"]
    )
    builder.add_edge("release", "summary")
    builder.add_edge("summary", END)
    builder.add_edge("safe_stop", END)

    return builder.compile(checkpointer=checkpointer)
