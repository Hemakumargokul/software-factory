"""Langfuse smoke check: a mocked-role factory run that produces a full
trace — graph structure, generation spans, tool spans (one allowed, one
denied, through the REAL permission callback) and reliability scores —
without spending any Claude quota.

Usage (stack must be up: docker compose -f docker-compose.langfuse.yml up -d):

    LANGFUSE_PUBLIC_KEY=pk-lf-factory-dev \
    LANGFUSE_SECRET_KEY=sk-lf-factory-dev \
    LANGFUSE_HOST=http://localhost:3000 \
    .venv/bin/python scripts/langfuse_smoke.py

Then open http://localhost:3000 (factory@example.com / factory-dev-password)
and find the trace named factory:<run_id>.
"""

import asyncio
import json
import re
import sys
import tempfile
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from factory import profiles, tracing  # noqa: E402
from factory.agent import claude
from factory.observability import metrics
from factory.agent.claude import RoleResult  # noqa: E402
from factory.graph import build_graph  # noqa: E402
from factory.profiles import ProjectProfile  # noqa: E402

CANNED = {
    "Normalize this engineering request": {
        "problem": "Trace smoke: tiny greeting service.",
        "assumptions": [], "ambiguity_score": 0.1, "ambiguities": [],
        "scenario": "greenfield",
    },
    "Write an engineering specification": {
        "summary": "A greeting service.",
        "functional_requirements": ["FR1: GET /hello greets"],
        "non_functional_requirements": [],
        "acceptance_criteria": ["AC1: GET /hello returns 200"],
        "out_of_scope": [],
    },
    "Design the system": {
        "architecture": "single controller", "components": [],
        "api_contract": [], "data_model": [],
        "alternatives_considered": [],
        "risks": [{"risk": "smoke", "impact": "low", "mitigation": "none"}],
    },
    "Decompose this design": {
        "tasks": [{"id": "T1", "title": "hello endpoint",
                   "description": "endpoint", "depends_on": [],
                   "verify": "build passes"}]
    },
    "Review this change as a senior engineer": {
        "verdict": "approve", "concerns": [], "risks": []
    },
    "engineering summary for this completed factory run": {
        "summary_markdown": "# Engineering Summary\n\nTrace smoke run."
    },
}


async def fake_run_role(role, prompt, *, cwd=None, system_prompt=None,
                        can_use_tool=None, hooks=None):
    if role.name == "implementer":
        # Simulate the agent's tool traffic through the REAL governance
        # path: the PreToolUse hook and permission callback are the ones
        # the graph wired up, so the trace shows genuine tool spans.
        pre = hooks["PreToolUse"][0].hooks[0]
        task_id = re.search(r"TASK (T\d+)", prompt).group(1)
        calls = [
            ("Write", {"file_path": f"{task_id}.txt", "content": "work"}),
            ("Write", {"file_path": "/etc/passwd", "content": "escape!"}),
        ]
        for tool, tool_input in calls:
            await pre({"tool_name": tool, "tool_input": tool_input}, "tid", None)
            decision = await can_use_tool(tool, tool_input, None)
            if type(decision).__name__ == "PermissionResultAllow":
                (cwd / tool_input["file_path"]).write_text(tool_input["content"])
        return RoleResult(text=f"Implemented {task_id}.", session_id="s",
                          cost_usd=0.01, num_turns=3)

    for marker, reply in CANNED.items():
        if marker in prompt:
            return RoleResult(text=json.dumps(reply), session_id="s",
                              cost_usd=0.005, num_turns=1)
    raise AssertionError(f"unmatched prompt: {prompt[:120]}")


async def main() -> None:
    if not tracing.tracing_enabled():
        sys.exit("tracing disabled — set LANGFUSE_* env vars and start the stack")

    workdir = Path(tempfile.mkdtemp(prefix="langfuse-smoke-"))
    template = workdir / "template"
    template.mkdir()
    (template / "README.md").write_text("scaffold\n")

    profiles.PROFILES["smoke"] = ProjectProfile(
        language="smoke", stack_description="no-op stack for the trace smoke",
        scaffold_template=template, build_cmd=("true",), test_cmd=("true",),
        package_cmd=("true",), run_cmd=("sleep", "5"),
        health_url="http://127.0.0.1:1/health", service_port=1,
        dependency_files=(), dependency_allowlist=frozenset(),
        forbidden_patterns=(), protected_globs=(),
    )
    claude.run_role = fake_run_role

    import os
    os.environ["FACTORY_SANDBOX_ROOT"] = str(workdir / "sandboxes")
    os.environ["FACTORY_ACCEPTANCE_DIR"] = str(workdir / "no-suite")
    import factory.stages.summary as summary_module
    summary_module.RUNS_DIR = workdir / "runs"

    run_id = f"smoke-{uuid.uuid4().hex[:6]}"
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"recursion_limit": 100, "configurable": {"thread_id": run_id}}

    with tracing.run_context(run_id, f"factory:{run_id}"):
        result = await graph.ainvoke(
            {"run_id": run_id, "goal": "trace smoke: greeting service",
             "profile": "smoke", "stage_results": {}, "attempts": 0,
             "replan_budget": 1},
            config,
        )
        while "__interrupt__" in result:
            result = await graph.ainvoke(
                Command(resume={"action": "approve"}), config
            )
        metrics.persist(result, "finished", db_path=workdir / "metrics.db")
        report = metrics.compute(run_id, db_path=workdir / "metrics.db")
        for name, value in report.scores().items():
            tracing.score(name, value)
    tracing.flush()

    denied = [a for a in result["audit"]
              if a["payload"].get("decision") == "deny"]
    print(f"run {run_id}: outcome finished, "
          f"{len(result['audit'])} audit events ({len(denied)} denied), "
          f"scores {report.scores()}")
    print(f"trace: http://localhost:3000 -> project software-factory "
          f"-> traces -> factory:{run_id}")


if __name__ == "__main__":
    asyncio.run(main())
