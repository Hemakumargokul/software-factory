"""M8 tests: human gates pause the graph, revise invalidates downstream
work, gates have no side effects, and runs survive process restarts."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from factory import git_ops
from factory.graph import (
    build_graph,
    route_after_gate_design,
    route_after_gate_merge,
    route_after_gate_requirements,
    route_after_intake,
)


def initial_state(run_id: str) -> dict:
    return {"run_id": run_id, "goal": "greeting service", "profile": "noop",
            "stage_results": {}, "attempts": 0, "replan_budget": 1}


def config_for(thread_id: str) -> dict:
    return {"recursion_limit": 100, "configurable": {"thread_id": thread_id}}


def pending_gate(result: dict) -> str:
    return result["__interrupt__"][0].value["gate"]


async def test_run_pauses_at_requirements_gate_with_spec(spine_env):
    graph = build_graph(checkpointer=InMemorySaver())
    result = await graph.ainvoke(
        initial_state("pause"), config_for("pause")
    )

    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "requirements"
    assert payload["spec"]["summary"] == "A greeting service."
    # Nothing past the gate has run
    assert "design" not in {k for k, v in result["stage_results"].items() if v}


async def test_revise_at_requirements_gate_invalidates_and_reruns(
    spine_env, gate_driver, prompt_log
):
    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("revise"), config_for("revise"),
        answers={"requirements": [
            {"action": "revise", "edits": "must also support UNICORN greetings"},
            {"action": "approve"},
        ]},
    )

    # Requirements ran twice; the second prompt carried the human's edits
    req_prompts = [p for stage, p in prompt_log if stage == "requirements"]
    assert len(req_prompts) == 2
    assert "UNICORN greetings" not in req_prompts[0]
    assert "UNICORN greetings" in req_prompts[1]

    # The invalidation was recorded as a decision, and the run completed
    assert any(
        "invalidated downstream stages: design, tasks" in d["decision"]
        for d in final["decisions"]
    )
    assert final["stage_results"]["release"]["ready"] is True


async def test_revise_at_design_gate_reruns_design(
    spine_env, gate_driver, prompt_log
):
    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("redesign"), config_for("redesign"),
        answers={"design": [
            {"action": "revise", "edits": "use hexagonal architecture"},
            {"action": "approve"},
        ]},
    )

    design_prompts = [p for stage, p in prompt_log if stage == "design"]
    assert len(design_prompts) == 2
    assert "hexagonal architecture" in design_prompts[1]
    assert any(
        "invalidated downstream stage: tasks" in d["decision"]
        for d in final["decisions"]
    )
    assert final["stage_results"]["release"]["ready"] is True


async def test_reject_at_requirements_gate_safe_stops(spine_env, gate_driver):
    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("rejected"), config_for("rejected"),
        answers={"requirements": {"action": "reject"}},
    )
    assert "human rejected at gate_requirements" in (
        final["stage_results"]["safe_stop"]["reason"]
    )
    assert not final.get("tasks")  # nothing downstream ran


async def test_clarify_gate_folds_answers_into_intake(
    spine_env, gate_driver, canned_replies, prompt_log
):
    canned_replies["intake"]["ambiguity_score"] = 0.9
    canned_replies["intake"]["ambiguities"] = ["Which greeting language?"]

    graph = build_graph(checkpointer=InMemorySaver())
    result = await graph.ainvoke(
        initial_state("vague"), config_for("vague")
    )
    payload = result["__interrupt__"][0].value
    assert payload["gate"] == "clarify"
    assert payload["questions"] == ["Which greeting language?"]

    final = await gate_driver(
        graph, Command(resume={"action": "revise", "edits": "English only"}),
        config_for("vague"),
    )

    # Intake ran twice; the second pass saw the human's answers. One round
    # only: the still-high ambiguity score must not re-trigger the gate.
    intake_prompts = [p for stage, p in prompt_log if stage == "intake"]
    assert len(intake_prompts) == 2
    assert "English only" in intake_prompts[1]
    assert final["stage_results"]["release"]["ready"] is True


async def test_merge_gate_reject_rolls_back_and_replans(
    spine_env, gate_driver
):
    graph = build_graph(checkpointer=InMemorySaver())
    final = await gate_driver(
        graph, initial_state("mergerej"), config_for("mergerej"),
        answers={"merge": [
            {"action": "reject", "edits": "wrong endpoint shape"},
        ]},  # subsequent merge gates fall back to approve
    )

    rollbacks = [e for e in final["metric_events"] if e["kind"] == "rollback"]
    assert len(rollbacks) == 1
    # The human's reason became the re-plan context...
    replan_decision = [d for d in final["decisions"] if d["stage"] == "rollback"]
    assert "human_merge_rejection" in replan_decision[0]["rationale"]
    # ...and the run recovered and finished
    assert [t["status"] for t in final["tasks"]] == ["integrated", "integrated"]
    assert final["stage_results"]["release"]["ready"] is True


async def test_gate_has_no_side_effects_across_resume(spine_env, gate_driver):
    """Interrupted nodes re-execute from the top on resume, so a gate must
    not touch the sandbox: its git state is identical before and after the
    pause, however long the run sits parked."""
    graph = build_graph(checkpointer=InMemorySaver())
    config = config_for("noside")
    result = await graph.ainvoke(initial_state("noside"), config)
    assert pending_gate(result) == "requirements"

    sandbox = result["sandbox"]
    log_before = git_ops.git(sandbox, "log", "--oneline")
    status_before = git_ops.git(sandbox, "status", "--porcelain")

    # Park, inspect twice (status is read-only), then reject -> safe_stop:
    # the gate and the terminal node must leave the sandbox untouched.
    state = await graph.aget_state(config)
    assert [t.interrupts[0].value["gate"] for t in state.tasks
            if t.interrupts] == ["requirements"]
    final = await gate_driver(
        graph, Command(resume={"action": "reject"}), config
    )

    assert git_ops.git(sandbox, "log", "--oneline") == log_before
    assert git_ops.git(sandbox, "status", "--porcelain") == status_before
    assert final["stage_results"]["safe_stop"]


async def test_run_survives_process_restart(spine_env, tmp_path, gate_driver):
    """SQLite persistence: pause with one graph+saver instance, throw both
    away, build fresh ones on the same file, resume to completion."""
    db = str(tmp_path / "checkpoints.db")
    config = config_for("restart")

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        graph = build_graph(checkpointer=saver)
        result = await graph.ainvoke(initial_state("restart"), config)
        assert pending_gate(result) == "requirements"

    # "New process": fresh saver and graph over the same database file.
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        graph = build_graph(checkpointer=saver)
        state = await graph.aget_state(config)
        assert state.values["spec"]["summary"] == "A greeting service."

        final = await gate_driver(
            graph, Command(resume={"action": "approve"}), config
        )
        assert final["stage_results"]["release"]["ready"] is True
        assert [t["status"] for t in final["tasks"]] == [
            "integrated", "integrated"
        ]


class TestGateRouters:
    def test_route_after_intake(self):
        clear = {"scenario": "greenfield",
                 "stage_results": {"intake": {"ambiguity_score": 0.1}}}
        assert route_after_intake(clear) == "requirements"

        vague = {"scenario": "greenfield",
                 "stage_results": {"intake": {"ambiguity_score": 0.7}}}
        assert route_after_intake(vague) == "clarify"

        ambiguous = {"scenario": "ambiguous",
                     "stage_results": {"intake": {"ambiguity_score": 0.2}}}
        assert route_after_intake(ambiguous) == "clarify"

        # One round only: once answers exist, proceed even if still vague
        answered = {"scenario": "ambiguous",
                    "stage_results": {"intake": {"ambiguity_score": 0.9},
                                      "clarify": {"answers": "English"}}}
        assert route_after_intake(answered) == "requirements"

    def test_gate_requirements_routes(self):
        route = route_after_gate_requirements
        approve = {"stage_results": {"gate_requirements": {"action": "approve"}}}
        revise = {"stage_results": {"gate_requirements": {"action": "revise"}}}
        reject = {"stage_results": {"gate_requirements": {"action": "reject"}}}
        assert route(approve) == "design"
        assert route(revise) == "requirements"
        assert route(reject) == "safe_stop"

    def test_gate_design_routes(self):
        route = route_after_gate_design
        assert route({"stage_results": {"gate_design": {"action": "approve"}}}) \
            == "decompose"
        assert route({"stage_results": {"gate_design": {"action": "revise"}}}) \
            == "design"
        assert route({"stage_results": {"gate_design": {"action": "reject"}}}) \
            == "safe_stop"

    def test_gate_merge_routes_and_fails_safe(self):
        route = route_after_gate_merge
        assert route({"stage_results": {"gate_merge": {"action": "approve"}}}) \
            == "integrate"
        assert route({"stage_results": {"gate_merge": {"action": "reject"}}}) \
            == "rollback"
        # Unknown/missing answers must never merge
        assert route({"stage_results": {}}) == "rollback"
