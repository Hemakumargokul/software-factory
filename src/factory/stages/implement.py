"""Implement: the implementer role edits the sandbox, under governance.

The only stage that writes product code. Everything M3/M4 set up converges
here: cwd pinned to the sandbox, Write/Edit routed through the sandbox
guard, every tool attempt audited, the whole invocation traced.
"""

import time
from pathlib import Path

from factory import claude, gates, git_ops, tracing
from factory.claude import (
    FAILURE_CONTEXT_PROMPT,
    IMPLEMENT_TASK_PROMPT,
    IMPLEMENTER_SYSTEM_PROMPT,
    implementer_role,
)
from factory.permissions import make_can_use_tool, make_pretooluse_hook
from factory.profiles import get_profile
from factory.stages.common import compact
from factory.state import FactoryState, audit_event, metric_event, record_decision


def _work_branch(task_id: str) -> str:
    return f"factory/{task_id.lower()}"


def _failure_context(state: FactoryState) -> str:
    """On retry, the truncated verification report rides into the prompt."""
    results = state.get("stage_results") or {}
    reports = [
        f"[{name}]\n{result.get('report', '')}"
        for name in ("tests", "acceptance", "policy")
        if (result := results.get(name)) and result.get("status") == "fail"
    ]
    if not reports:
        return ""
    return FAILURE_CONTEXT_PROMPT.substitute(report="\n\n".join(reports)[:6000])


async def implement(state: FactoryState) -> dict:
    gates.check_entry("implement", state)
    started = time.monotonic()

    task = state["tasks"][state["task_idx"]]
    sandbox = Path(state["sandbox"])
    profile = get_profile(state["profile"])

    # First attempt creates the task branch; retries continue on it.
    branch = _work_branch(task["id"])
    try:
        git_ops.create_branch(sandbox, branch)
    except git_ops.GitError:
        git_ops.git(sandbox, "switch", branch)

    prompt = IMPLEMENT_TASK_PROMPT.substitute(
        task_id=task["id"],
        task_title=task["title"],
        task_description=task.get("description", ""),
        spec=compact(state["spec"]),
        design=compact(state["design"]),
        failure_context=_failure_context(state),
    )

    # The audit sink collects during the run; entries land in state below.
    audit_entries: list[dict] = []

    def sink(event: dict) -> None:
        payload = dict(event)
        kind = payload.pop("kind", "tool")
        audit_entries.append(audit_event(kind, "implement", **payload))
        if event.get("decision") == "deny":
            tracing.tool_span(
                event.get("tool", "?"), event.get("input"), denied=True
            )

    role = implementer_role()
    with tracing.stage_span("implement", task=task["id"]):
        with tracing.generation_span(
            f"implement:{task['id']}", role.model, prompt
        ) as span:
            result = await claude.run_role(
                role,
                prompt,
                cwd=sandbox,
                system_prompt=IMPLEMENTER_SYSTEM_PROMPT,
                can_use_tool=make_can_use_tool(
                    sandbox, list(profile.protected_globs), sink
                ),
                hooks=make_pretooluse_hook(sink),
            )
            span.end_with(result)

    files = git_ops.changed_files(sandbox, state["base_sha"])
    update = {
        "stage_results": {
            "implement": {"status": "ok", "task": task["id"], "files": files}
        },
        "audit": audit_entries,
        "decisions": [
            record_decision(
                stage="implement",
                decision=f"{task['id']} implemented: {len(files)} files changed",
                rationale=result.text[:500],
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "implement",
                ok=True,
                task=task["id"],
                attempt=state.get("attempts", 0),
                cost_usd=result.cost_usd,
                num_turns=result.num_turns,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
    gates.check_exit("implement", {**state, **update})
    return update
