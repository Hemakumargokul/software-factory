"""M6 spine test: with run_role mocked to canned JSON per stage, the full
graph runs end to end and produces real commits in a temp sandbox."""

import json
import re

import pytest

from factory import claude, git_ops, profiles
from factory.claude import RoleResult
from factory.gates import GateViolation, check_entry
from factory.graph import build_graph, route_after_acceptance, route_after_integrate
from factory.profiles import ProjectProfile
from factory.stages.decompose import validate_and_order

CANNED = {
    "intake": {
        "problem": "Build a tiny greeting service.",
        "assumptions": [],
        "ambiguity_score": 0.1,
        "ambiguities": [],
        "scenario": "greenfield",
    },
    "requirements": {
        "summary": "A greeting service.",
        "functional_requirements": ["FR1: GET /hello greets"],
        "non_functional_requirements": [],
        "acceptance_criteria": ["AC1: GET /hello returns 200"],
        "out_of_scope": [],
    },
    "design": {
        "architecture": "single controller, no persistence",
        "components": [{"name": "HelloController", "responsibility": "greet"}],
        "api_contract": [
            {"method": "GET", "path": "/hello", "request": "",
             "response": "message", "status_codes": [200]}
        ],
        "data_model": [],
        "alternatives_considered": ["CLI tool - rejected, spec wants HTTP"],
        "risks": [{"risk": "r1", "impact": "low", "mitigation": "m1"}],
    },
    "decompose": {
        "tasks": [
            {"id": "T1", "title": "skeleton", "description": "app skeleton",
             "depends_on": [], "verify": "build passes"},
            {"id": "T2", "title": "hello endpoint", "description": "endpoint",
             "depends_on": ["T1"], "verify": "tests pass"},
        ]
    },
    "summary": {"summary_markdown": "# Engineering Summary\n\nAll tasks done."},
}


def fake_run_role_factory():
    """Dispatches on role and prompt markers, mimicking each stage's reply."""

    async def fake_run_role(role, prompt, *, cwd=None, system_prompt=None,
                            can_use_tool=None, hooks=None):
        if role.name == "implementer":
            task_id = re.search(r"TASK (T\d+)", prompt).group(1)
            (cwd / f"{task_id}.txt").write_text(f"work for {task_id}\n")
            return RoleResult(text=f"Implemented {task_id}.", session_id="s",
                              cost_usd=0.01, num_turns=3)

        for marker, key in (
            ("Normalize this engineering request", "intake"),
            ("Write an engineering specification", "requirements"),
            ("Design the system", "design"),
            ("Decompose this design", "decompose"),
            ("engineering summary for this completed factory run", "summary"),
        ):
            if marker in prompt:
                return RoleResult(text=json.dumps(CANNED[key]), session_id="s",
                                  cost_usd=0.005, num_turns=1)
        raise AssertionError(f"unmatched prompt: {prompt[:120]}")

    return fake_run_role


@pytest.fixture
def spine_env(tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.mkdir()
    (template / "README.md").write_text("scaffold\n")

    noop = ProjectProfile(
        language="noop",
        stack_description="no-op stack for tests",
        scaffold_template=template,
        build_cmd=("true",),
        test_cmd=("true",),
        package_cmd=("true",),
        run_cmd=("sleep", "5"),
        health_url="http://127.0.0.1:1/health",
        service_port=1,
        dependency_files=(),
        dependency_allowlist=frozenset(),
        forbidden_patterns=(),
        protected_globs=(),
    )
    monkeypatch.setitem(profiles.PROFILES, "noop", noop)
    monkeypatch.setenv("FACTORY_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("FACTORY_ACCEPTANCE_DIR", str(tmp_path / "no-suite"))
    monkeypatch.setattr(claude, "run_role", fake_run_role_factory())

    import factory.stages.summary as summary_module
    monkeypatch.setattr(summary_module, "RUNS_DIR", tmp_path / "runs")

    return tmp_path


async def test_full_spine_end_to_end(spine_env):
    graph = build_graph()
    final = await graph.ainvoke(
        {"run_id": "spinetest", "goal": "greeting service", "profile": "noop",
         "stage_results": {}, "attempts": 0, "replan_budget": 1},
        {"recursion_limit": 100},
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

    # Lineage: every stage recorded at least one decision
    stages_seen = {d["stage"] for d in final["decisions"]}
    assert {"bootstrap", "intake", "requirements", "design", "decompose",
            "implement", "commit", "integrate", "release",
            "summary"} <= stages_seen

    # Release checklist and generated summary
    assert final["stage_results"]["release"]["ready"] is True
    summary_path = spine_env / "runs" / "spinetest" / "summary.md"
    assert summary_path.read_text().startswith("# Engineering Summary")

    # Design risks made it into the register
    assert any(r.get("risk") == "r1" for r in final["risks"])


async def test_verification_failure_routes_to_safe_stop(spine_env, monkeypatch):
    """A red tests stage must stop the run, not commit broken work."""
    noop = profiles.PROFILES["noop"]
    failing = ProjectProfile(**{**noop.__dict__, "build_cmd": ("false",)})
    monkeypatch.setitem(profiles.PROFILES, "noop", failing)

    graph = build_graph()
    final = await graph.ainvoke(
        {"run_id": "failtest", "goal": "greeting service", "profile": "noop",
         "stage_results": {}, "attempts": 0, "replan_budget": 1},
        {"recursion_limit": 100},
    )

    assert final["stage_results"]["tests"]["status"] == "fail"
    assert final["stage_results"]["safe_stop"]["failed_stages"] == ["tests"]
    assert all(t["status"] == "pending" for t in final["tasks"])  # nothing merged


class TestRouters:
    def test_route_after_acceptance(self):
        ok = {"stage_results": {"tests": {"status": "pass"},
                                "acceptance": {"status": "skipped"}}}
        assert route_after_acceptance(ok) == "commit"

        failed_tests = {"stage_results": {"tests": {"status": "fail"},
                                          "acceptance": {"status": "pass"}}}
        assert route_after_acceptance(failed_tests) == "safe_stop"

        failed_acceptance = {"stage_results": {"tests": {"status": "pass"},
                                               "acceptance": {"status": "fail"}}}
        assert route_after_acceptance(failed_acceptance) == "safe_stop"

    def test_route_after_integrate(self):
        tasks = [{"id": "T1"}, {"id": "T2"}]
        assert route_after_integrate({"task_idx": 1, "tasks": tasks}) == "implement"
        assert route_after_integrate({"task_idx": 2, "tasks": tasks}) == "release"


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
