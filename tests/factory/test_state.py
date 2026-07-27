from datetime import datetime

from langgraph.graph import END, START, StateGraph

from factory.state import (
    FactoryState,
    audit_event,
    merge_stage_results,
    metric_event,
    record_decision,
)


class TestMergeStageResults:
    def test_disjoint_keys_merge(self):
        left = {"tests": {"passed": True}}
        right = {"policy": {"violations": []}}
        assert merge_stage_results(left, right) == {
            "tests": {"passed": True},
            "policy": {"violations": []},
        }

    def test_right_wins_and_none_invalidates(self):
        left = {"design": {"api": "v1"}, "tasks": {"count": 3}}
        right = {"design": None, "tasks": None}
        merged = merge_stage_results(left, right)
        assert merged == {"design": None, "tasks": None}

    def test_none_inputs_are_empty(self):
        assert merge_stage_results(None, None) == {}
        assert merge_stage_results({"a": 1}, None) == {"a": 1}
        assert merge_stage_results(None, {"b": 2}) == {"b": 2}

    def test_does_not_mutate_inputs(self):
        left = {"a": 1}
        right = {"b": 2}
        merge_stage_results(left, right)
        assert left == {"a": 1}
        assert right == {"b": 2}


class TestLineageRecords:
    def test_record_decision_shape_and_timestamp(self):
        d = record_decision(
            stage="design",
            decision="H2 embedded database",
            rationale="zero-install persistence for a demo product",
            alternatives=["postgres", "sqlite via jdbc"],
        )
        assert d["stage"] == "design"
        assert d["commit_sha"] is None
        assert d["trace_id"] is None
        assert d["alternatives"] == ["postgres", "sqlite via jdbc"]
        # ISO timestamp, timezone-aware
        parsed = datetime.fromisoformat(d["at"])
        assert parsed.tzinfo is not None

    def test_audit_and_metric_events_share_shape(self):
        a = audit_event("tool_denied", "implement", tool="Write", path="/etc/passwd")
        m = metric_event("stage_failed", "tests", attempt=1)
        for event in (a, m):
            assert set(event) == {"at", "kind", "stage", "payload"}
        assert a["payload"]["tool"] == "Write"
        assert m["payload"]["attempt"] == 1


class TestReducersInsideLangGraph:
    """Prove the schema survives a real parallel superstep: two branches
    writing disjoint stage_results keys and appending lineage concurrently."""

    def test_parallel_branches_merge_without_conflict(self):
        def fan(state: FactoryState):
            return {}

        def tests_branch(state: FactoryState):
            return {
                "stage_results": {"tests": {"passed": True}},
                "decisions": [record_decision("tests", "unit tests green", "n/a")],
                "metric_events": [metric_event("stage_passed", "tests")],
            }

        def policy_branch(state: FactoryState):
            return {
                "stage_results": {"policy": {"violations": []}},
                "decisions": [record_decision("policy", "no violations", "n/a")],
                "metric_events": [metric_event("stage_passed", "policy")],
            }

        def join(state: FactoryState):
            return {}

        builder = StateGraph(FactoryState)
        builder.add_node("fan", fan)
        builder.add_node("tests_branch", tests_branch)
        builder.add_node("policy_branch", policy_branch)
        builder.add_node("join", join, defer=True)
        builder.add_edge(START, "fan")
        builder.add_edge("fan", "tests_branch")
        builder.add_edge("fan", "policy_branch")
        builder.add_edge("tests_branch", "join")
        builder.add_edge("policy_branch", "join")
        builder.add_edge("join", END)
        graph = builder.compile()

        result = graph.invoke({"goal": "demo", "stage_results": {}})

        assert result["stage_results"] == {
            "tests": {"passed": True},
            "policy": {"violations": []},
        }
        assert len(result["decisions"]) == 2
        assert len(result["metric_events"]) == 2

    def test_invalidation_overwrites_downstream_keys(self):
        def do_work(state: FactoryState):
            return {"stage_results": {"design": {"api": "v1"}, "tests": {"passed": True}}}

        def revise(state: FactoryState):
            # A gate revision invalidates downstream work but appends, never
            # erases, lineage.
            return {
                "stage_results": {"design": None, "tests": None},
                "decisions": [
                    record_decision("gate:design", "revised", "spec changed upstream")
                ],
            }

        builder = StateGraph(FactoryState)
        builder.add_node("do_work", do_work)
        builder.add_node("revise", revise)
        builder.add_edge(START, "do_work")
        builder.add_edge("do_work", "revise")
        builder.add_edge("revise", END)
        graph = builder.compile()

        result = graph.invoke({"stage_results": {}})

        assert result["stage_results"] == {"design": None, "tests": None}
        assert [d["stage"] for d in result["decisions"]] == ["gate:design"]
