"""M6/M7 spine tests: with run_role mocked to canned JSON per stage, the
full graph runs end to end (through the human gates, auto-approved by the
gate driver) and produces real commits in a temp sandbox."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from factory import git_ops, profiles
from factory.governance.gates import GateViolation, check_entry
from factory.graph import (
    MAX_ATTEMPTS,
    build_graph,
    over_budget,
    route_after_acceptance,
    route_after_decompose,
    route_after_integrate,
    route_after_rollback,
    route_after_sync,
)
from factory.state import metric_event
from factory.profiles import ProjectProfile
from factory.stages.decompose import validate_and_order


def initial_state(run_id: str) -> dict:
    return {"run_id": run_id, "goal": "greeting service", "profile": "noop",
            "stage_results": {}, "attempts": 0, "replan_budget": 1}


def config_for(thread_id: str) -> dict:
    return {"recursion_limit": 100, "configurable": {"thread_id": thread_id}}


async def test_full_spine_end_to_end(spine_env, gate_driver):
    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("spinetest"), config_for("spinetest")
    )

    # Both tasks ran, in dependency order, and were integrated
    assert [t["status"] for t in final["tasks"]] == ["integrated", "integrated"]
    assert final["task_idx"] == 2

    # The sandbox is a real repo: work landed on main via no-ff merges
    sandbox = final["sandbox"]
    assert git_ops.git(sandbox, "branch", "--show-current").strip() == "main"
    log = git_ops.git(sandbox, "log", "--oneline", "--merges")
    assert "integrate T1" in log and "integrate T2" in log
    assert (spine_env / "sandboxes" / "spinetest" / "T1.txt").exists()
    assert (spine_env / "sandboxes" / "spinetest" / "T2.txt").exists()

    # base_sha advanced to the last merge: rollback target is last-known-good
    assert final["base_sha"] == final["head_sha"]

    # Lineage: every stage recorded at least one decision, gates included
    stages_seen = {d["stage"] for d in final["decisions"]}
    assert {"bootstrap", "intake", "requirements", "design", "decompose",
            "implement", "commit", "integrate", "release", "summary",
            "gate_requirements", "gate_design", "gate_merge"} <= stages_seen

    # Release checklist and generated summary
    assert final["stage_results"]["release"]["ready"] is True
    summary_path = spine_env / "runs" / "spinetest" / "summary.md"
    assert summary_path.read_text().startswith("# Engineering Summary")

    # Design AND review risks made it into the register
    assert any(r.get("risk") == "r1" for r in final["risks"])
    assert any(r.get("risk") == "review-r1" for r in final["risks"])

    # Parallel branches all reported through the merge reducer
    for branch in ("tests", "policy", "review"):
        assert final["stage_results"][branch]["status"] == "pass"


async def test_retry_rollback_replan_safestop(spine_env, gate_driver, monkeypatch):
    """The M7 done-when: a deliberately failing tests branch retries twice,
    rolls back, re-plans once, then safe-stops."""
    noop = profiles.PROFILES["noop"]
    failing = ProjectProfile(**{**noop.__dict__, "build_cmd": ("false",)})
    monkeypatch.setitem(profiles.PROFILES, "noop", failing)

    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("failtest"), config_for("failtest")
    )

    # 3 attempts per decomposition, 2 decompositions (initial + one re-plan)
    implement_events = [e for e in final["metric_events"]
                        if e["stage"] == "implement"]
    assert [e["payload"]["attempt"] for e in implement_events] == [1, 2, 3, 1, 2, 3]
    decompose_decisions = [d for d in final["decisions"]
                           if d["stage"] == "decompose"]
    assert len(decompose_decisions) == 2

    # Two rollbacks, budget spent to -1, then safe-stop with the reason
    rollbacks = [e for e in final["metric_events"] if e["kind"] == "rollback"]
    assert len(rollbacks) == 2
    assert final["replan_budget"] == -1
    assert "replan budget exhausted" in final["stage_results"]["safe_stop"]["reason"]

    # Rollback actually restored the tree: no work files, clean status, on main
    sandbox = final["sandbox"]
    assert not (spine_env / "sandboxes" / "failtest" / "T1.txt").exists()
    assert git_ops.git(sandbox, "status", "--porcelain").strip() == ""
    assert git_ops.git(sandbox, "branch", "--show-current").strip() == "main"
    assert all(t["status"] == "pending" for t in final["tasks"])  # nothing merged


class TestRouters:
    def test_route_after_sync_pass_goes_to_acceptance(self):
        state = {"stage_results": {"tests": {"status": "pass"},
                                   "policy": {"status": "pass"},
                                   "review": {"status": "pass"}},
                 "attempts": 1}
        assert route_after_sync(state) == "acceptance"

    def test_route_after_sync_fail_retries_while_attempts_left(self):
        state = {"stage_results": {"tests": {"status": "fail"},
                                   "policy": {"status": "pass"}},
                 "attempts": 1}
        assert route_after_sync(state) == "implement"

    def test_route_after_sync_fail_exhausted_rolls_back(self):
        state = {"stage_results": {"tests": {"status": "fail"},
                                   "policy": {"status": "pass"}},
                 "attempts": MAX_ATTEMPTS}
        assert route_after_sync(state) == "rollback"

    def test_route_after_sync_implement_abort_never_proceeds(self):
        # Tests green but the implementer aborted mid-task: the partial
        # tree building is not the same as the task being done.
        state = {"stage_results": {"implement": {"status": "fail"},
                                   "tests": {"status": "pass"},
                                   "policy": {"status": "pass"}},
                 "attempts": 1}
        assert route_after_sync(state) == "implement"
        assert route_after_sync({**state, "attempts": MAX_ATTEMPTS}) == "rollback"

    def test_route_after_sync_policy_violation_skips_retries(self):
        state = {"stage_results": {"tests": {"status": "pass"},
                                   "policy": {"status": "violation"}},
                 "attempts": 1}  # attempts left, still no retry
        assert route_after_sync(state) == "rollback"

    def test_route_after_acceptance(self):
        ok = {"stage_results": {"acceptance": {"status": "skipped"}}}
        assert route_after_acceptance(ok) == "commit"

        retry = {"stage_results": {"acceptance": {"status": "fail"}},
                 "attempts": 1}
        assert route_after_acceptance(retry) == "implement"

        exhausted = {"stage_results": {"acceptance": {"status": "fail"}},
                     "attempts": MAX_ATTEMPTS}
        assert route_after_acceptance(exhausted) == "rollback"

    def test_route_after_rollback_budget(self):
        assert route_after_rollback({"replan_budget": 0}) == "decompose"
        assert route_after_rollback({"replan_budget": -1}) == "safe_stop"

    def test_route_after_integrate(self):
        tasks = [{"id": "T1"}, {"id": "T2"}]
        assert route_after_integrate({"task_idx": 1, "tasks": tasks}) == "implement"
        assert route_after_integrate({"task_idx": 2, "tasks": tasks}) == "release"


def _spent_state(spent: float, budget: float | None, **extra) -> dict:
    events = [metric_event("stage_end", "implement", cost_usd=spent)]
    state = {"metric_events": events, **extra}
    if budget is not None:
        state["run_budget_usd"] = budget
    return state


class TestRunBudget:
    def test_over_budget_arithmetic(self):
        assert not over_budget(_spent_state(0.5, 1.0))
        assert over_budget(_spent_state(1.0, 1.0))       # cap is inclusive
        assert not over_budget(_spent_state(99.0, None))  # no cap set
        assert not over_budget(_spent_state(99.0, 0))     # 0 disables

    def test_budget_blocks_new_agent_work_everywhere(self):
        over = _spent_state(2.0, 1.0, attempts=1, task_idx=0,
                            tasks=[{"id": "T1"}])
        over["stage_results"] = {"tests": {"status": "fail"},
                                 "acceptance": {"status": "fail"}}
        assert route_after_decompose(over) == "safe_stop"
        assert route_after_sync(over) == "safe_stop"
        assert route_after_acceptance(over) == "safe_stop"
        assert route_after_integrate(over) == "safe_stop"
        assert route_after_rollback({**over, "replan_budget": 1}) == "safe_stop"

    def test_budget_never_blocks_wrapping_up_verified_work(self):
        over = _spent_state(2.0, 1.0, attempts=1, task_idx=1,
                            tasks=[{"id": "T1"}])
        # Passing verification proceeds to acceptance; passed acceptance
        # commits; the finished task list releases — no value is discarded.
        over["stage_results"] = {"tests": {"status": "pass"},
                                 "implement": {"status": "ok"},
                                 "acceptance": {"status": "pass"}}
        assert route_after_sync(over) == "acceptance"
        assert route_after_acceptance(over) == "commit"
        assert route_after_integrate(over) == "release"


async def test_tiny_budget_safe_stops_before_implementation(
    spine_env, gate_driver, prompt_log
):
    graph = build_graph(checkpointer=InMemorySaver())
    state = initial_state("budgettest")
    state["run_budget_usd"] = 0.001  # blown by the first reasoner call
    final = await gate_driver(graph, state, config_for("budgettest"))

    # Stopped at the first budget checkpoint (requirement gate approval),
    # before any design/decompose/implement spend
    stages_run = [stage for stage, _ in prompt_log]
    assert "design" not in stages_run and "implement" not in stages_run
    reason = final["stage_results"]["safe_stop"]["reason"]
    assert "run budget exhausted" in reason
    assert "spent $" in reason  # actual spend and cap are stated


class TestImplementEntryGate:
    def test_unintegrated_dependency_blocks(self):
        state = {"task_idx": 1, "tasks": [
            {"id": "T1", "depends_on": [], "status": "pending"},
            {"id": "T2", "depends_on": ["T1"], "status": "pending"},
        ]}
        with pytest.raises(GateViolation, match="depends on unintegrated"):
            check_entry("implement", state)

    def test_integrated_dependency_passes(self):
        state = {"task_idx": 1, "tasks": [
            {"id": "T1", "depends_on": [], "status": "integrated"},
            {"id": "T2", "depends_on": ["T1"], "status": "pending"},
        ]}
        check_entry("implement", state)  # no raise


class TestDecomposeValidation:
    def test_topological_reordering(self):
        tasks = [
            {"id": "T2", "title": "b", "depends_on": ["T1"]},
            {"id": "T1", "title": "a", "depends_on": []},
        ]
        ordered = validate_and_order(tasks)
        assert [t["id"] for t in ordered] == ["T1", "T2"]
        assert all(t["status"] == "pending" for t in ordered)

    def test_cycle_rejected(self):
        tasks = [
            {"id": "T1", "depends_on": ["T2"]},
            {"id": "T2", "depends_on": ["T1"]},
        ]
        with pytest.raises(ValueError, match="cycle"):
            validate_and_order(tasks)

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValueError, match="unknown"):
            validate_and_order([{"id": "T1", "depends_on": ["T9"]}])

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_and_order([
                {"id": "T1", "depends_on": []},
                {"id": "T1", "depends_on": []},
            ])
