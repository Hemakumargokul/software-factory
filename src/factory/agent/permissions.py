"""Runtime governance for the implementer agent.

Two mechanisms with different jobs:

- `make_can_use_tool` builds the permission callback: the enforcement
  point. Because Write/Edit are deliberately left out of the implementer's
  allowed_tools (see claude.py), every write reaches this callback, which
  denies anything resolving outside the sandbox or matching a protected
  glob. Deny rules run on `Path.resolve()` output, so `../` tricks and
  absolute paths fail the same way.

- `make_pretooluse_hook` builds the observation point: a PreToolUse hook
  fires for EVERY tool call, including ones auto-permitted by
  allowed_tools, so the audit trail is complete even where the permission
  callback is never consulted.

Both are closed over an audit sink (a callable receiving dicts); the graph
wires that sink to state lineage and tracing.
"""

from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
)

AuditSink = Callable[[dict[str, Any]], None]

# Tool-input keys that carry filesystem paths, across the built-in tools.
_PATH_KEYS = ("file_path", "path", "notebook_path", "cwd", "directory")

# Tools that mutate files; protected globs only apply to these.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paths_in(tool_input: dict[str, Any]) -> list[str]:
    return [
        value
        for key in _PATH_KEYS
        if isinstance((value := tool_input.get(key)), str) and value
    ]


def _resolve(raw: str, sandbox: Path) -> Path:
    """Resolve a tool-supplied path the way the agent's process would:
    relative paths anchor at the sandbox (its cwd)."""
    path = Path(raw)
    if not path.is_absolute():
        path = sandbox / path
    return path.resolve()


def make_can_use_tool(sandbox: Path, protected: list[str], audit_sink: AuditSink):
    """Permission callback denying sandbox escapes and protected-path writes.

    `protected` holds sandbox-relative globs (e.g. "tests/acceptance/**").
    Every decision — allow or deny — goes to the audit sink.
    """
    sandbox = sandbox.resolve()

    def _decide(tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Returns a denial reason, or None to allow."""
        for raw in _paths_in(tool_input):
            resolved = _resolve(raw, sandbox)
            if not resolved.is_relative_to(sandbox):
                return f"path escapes sandbox: {raw!r} -> {resolved}"
            if tool_name in _WRITE_TOOLS:
                relative = resolved.relative_to(sandbox).as_posix()
                for pattern in protected:
                    if fnmatch(relative, pattern):
                        return f"write to protected path {relative!r} (rule: {pattern})"
        return None

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context):
        reason = _decide(tool_name, tool_input)
        audit_sink(
            {
                "ts": _now(),
                "kind": "permission_decision",
                "tool": tool_name,
                "input": {k: str(v)[:500] for k, v in tool_input.items()},
                "decision": "deny" if reason else "allow",
                "reason": reason,
            }
        )
        if reason:
            return PermissionResultDeny(message=reason, interrupt=False)
        return PermissionResultAllow()

    return can_use_tool


def make_pretooluse_hook(audit_sink: AuditSink) -> dict[str, list[HookMatcher]]:
    """PreToolUse hook that records every tool attempt, decision-free.

    Deliberately returns no permission decision: an allow from a hook would
    skip the can_use_tool callback and punch through the sandbox guard.
    """

    async def on_pre_tool_use(input_data, tool_use_id, context):
        audit_sink(
            {
                "ts": _now(),
                "kind": "tool_attempt",
                "tool": input_data.get("tool_name", "unknown"),
                "input": {
                    k: str(v)[:500]
                    for k, v in (input_data.get("tool_input") or {}).items()
                },
                "tool_use_id": tool_use_id,
            }
        )
        return {}

    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[on_pre_tool_use])]}
