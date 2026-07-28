"""Shared fixtures: a mocked-roles graph environment and a gate driver.

The graph now interrupts at human gates, so every end-to-end test needs a
checkpointer and a way to answer gates; `gate_driver` centralizes that.
"""

import copy
import json
import re

import pytest
from langgraph.types import Command

from factory import profiles
from factory.agent import claude
from factory.agent.claude import RoleResult
from factory.profiles import ProjectProfile

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
    "review": {"verdict": "approve", "concerns": [],
               "risks": [{"risk": "review-r1", "impact": "low",
                          "mitigation": "none"}]},
    "impact": {
        "current_state": "existing greeting service with one endpoint",
        "affected_files": ["README.md: mentions the endpoint list"],
        "integration_points": ["HelloController"],
        "regression_risks": [{"risk": "impact-r1", "impact": "low",
                              "mitigation": "keep old endpoint"}],
    },
}

PROMPT_MARKERS = (
    ("Normalize this engineering request", "intake"),
    ("Write an engineering specification", "requirements"),
    ("impact analysis for this specification", "impact"),
    ("Design the system", "design"),
    ("Decompose this design", "decompose"),
    ("Review this change as a senior engineer", "review"),
    ("engineering summary for this completed factory run", "summary"),
)


@pytest.fixture
def canned_replies():
    """Deep copy of the canned stage replies. The fake reads it at call
    time, so a test may mutate it BEFORE running the graph (e.g. raise the
    intake ambiguity to force the clarify gate)."""
    return copy.deepcopy(CANNED)


@pytest.fixture
def prompt_log():
    """Every (stage, prompt) the fake roles received, in order — lets tests
    assert that revisions and clarifications reached the prompts."""
    return []


def fake_run_role_factory(canned, prompt_log):
    """Dispatches on role and prompt markers, mimicking each stage's reply."""

    async def fake_run_role(role, prompt, *, cwd=None, system_prompt=None,
                            can_use_tool=None, hooks=None):
        if role.name == "implementer":
            task_id = re.search(r"TASK (T\d+)", prompt).group(1)
            prompt_log.append(("implement", prompt))
            (cwd / f"{task_id}.txt").write_text(f"work for {task_id}\n")
            return RoleResult(text=f"Implemented {task_id}.", session_id="s",
                              cost_usd=0.01, num_turns=3)

        for marker, key in PROMPT_MARKERS:
            if marker in prompt:
                prompt_log.append((key, prompt))
                return RoleResult(text=json.dumps(canned[key]), session_id="s",
                                  cost_usd=0.005, num_turns=1)
        raise AssertionError(f"unmatched prompt: {prompt[:120]}")

    return fake_run_role


@pytest.fixture
def spine_env(tmp_path, monkeypatch, canned_replies, prompt_log):
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
    monkeypatch.delenv("FACTORY_SEED_DIR", raising=False)
    monkeypatch.setattr(
        claude, "run_role", fake_run_role_factory(canned_replies, prompt_log)
    )

    import factory.stages.summary as summary_module
    monkeypatch.setattr(summary_module, "RUNS_DIR", tmp_path / "runs")

    return tmp_path


@pytest.fixture
def gate_driver():
    """Drive a checkpointed graph to completion, answering human gates.

    `answers` maps gate name -> answer dict, or -> list of answer dicts
    consumed in order across repeated visits. Unlisted gates are approved.
    """

    async def drive(graph, graph_input, config, answers=None):
        answers = {
            gate: list(value) if isinstance(value, list) else value
            for gate, value in (answers or {}).items()
        }
        result = await graph.ainvoke(graph_input, config)
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            answer = answers.get(payload.get("gate"), {"action": "approve"})
            if isinstance(answer, list):
                answer = answer.pop(0) if answer else {"action": "approve"}
            result = await graph.ainvoke(Command(resume=answer), config)
        return result

    return drive
