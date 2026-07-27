"""SDK roles: configured entry points into the Claude Agent SDK.

One function (`run_role`) wraps `claude_agent_sdk.query()`; the roles are
configurations of it. The separation that matters:

- reasoner / analyst: no tools at all. They think and return JSON. They
  cannot touch the filesystem, so their outputs are pure data for the graph.
- implementer: file tools only, no Bash, cwd pinned to the sandbox. Write
  and Edit are deliberately NOT in `allowed_tools` — tools listed there are
  auto-permitted and never reach the `can_use_tool` callback, which would
  silently bypass the sandbox path guard (permissions.py). Leaving them in
  "ask" state routes every write through the callback.

Auth note: subscription login (`claude /login`) is the expected auth path.
A stale ANTHROPIC_API_KEY in the environment silently overrides it.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

# System prompt shared by the JSON-emitting roles (reasoner, analyst).
# Stage prompts (intake, requirements, design, decompose) build on this and
# live as module constants here so every prompt is reviewable in one place.
JSON_ROLE_SYSTEM_PROMPT = (
    "You are a software engineering reasoning agent inside an automated "
    "pipeline. You have no tools; do not attempt to read or write files. "
    "Answer with a single fenced ```json code block matching the schema in "
    "the user message. No text after the closing fence."
)


class RoleError(RuntimeError):
    """A role invocation failed. Carries the result subtype and any partial
    text so gate logic can decide between fallback, retry, and escalation."""

    def __init__(self, message: str, *, subtype: str | None = None, text: str = ""):
        super().__init__(message)
        self.subtype = subtype
        self.text = text


class JsonExtractionError(ValueError):
    """The role replied but no parseable JSON was found; `.raw` has the reply."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class RoleConfig:
    name: str                          # "reasoner" | "analyst" | "implementer"
    allowed_tools: tuple[str, ...]     # auto-permitted; never hit can_use_tool
    disallowed_tools: tuple[str, ...]  # hard-blocked by the CLI
    model: str | None                  # None = Claude Code default model
    fallback_model: str | None
    max_turns: int
    max_budget_usd: float
    permission_mode: str | None = None
    no_tools: bool = False             # strip every built-in tool


@dataclass
class RoleResult:
    text: str
    session_id: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    num_turns: int = 0
    duration_ms: int = 0


def _env_model(var: str) -> str | None:
    value = os.environ.get(var, "").strip()
    return value or None


def reasoner_role() -> RoleConfig:
    """Planning/analysis role: no tools, strong model, JSON out."""
    return RoleConfig(
        name="reasoner",
        allowed_tools=(),
        disallowed_tools=(),
        model=_env_model("FACTORY_MODEL_REASONER"),
        fallback_model=_env_model("FACTORY_MODEL_FALLBACK"),
        max_turns=3,
        max_budget_usd=1.00,
        no_tools=True,
    )


def implementer_role() -> RoleConfig:
    """Coding role: read tools auto-allowed; Write/Edit left in "ask" state so
    the can_use_tool sandbox guard sees every write; Bash and network denied.

    Callers MUST pass can_use_tool to run_role for this role — without it,
    the first write attempt fails hard rather than escaping governance.
    """
    return RoleConfig(
        name="implementer",
        allowed_tools=("Read", "Glob", "Grep"),
        disallowed_tools=("Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit"),
        model=_env_model("FACTORY_MODEL_IMPLEMENTER"),
        fallback_model=_env_model("FACTORY_MODEL_FALLBACK"),
        max_turns=40,
        max_budget_usd=3.00,
    )


def build_options(
    role: RoleConfig,
    *,
    cwd: Path | None = None,
    system_prompt: str | None = None,
    can_use_tool=None,
    hooks=None,
) -> ClaudeAgentOptions:
    """Translate a RoleConfig into SDK options. Pure; unit-tested directly."""
    return ClaudeAgentOptions(
        # tools=[] removes every built-in tool; None keeps the default set
        # filtered by allowed/disallowed below.
        tools=[] if role.no_tools else None,
        allowed_tools=list(role.allowed_tools),
        disallowed_tools=list(role.disallowed_tools),
        model=role.model,
        fallback_model=role.fallback_model,
        max_turns=role.max_turns,
        max_budget_usd=role.max_budget_usd,
        permission_mode=role.permission_mode,
        cwd=str(cwd) if cwd else None,
        system_prompt=system_prompt,
        can_use_tool=can_use_tool,
        hooks=hooks,
        # setting_sources stays None: user/project Claude settings are never
        # loaded, so factory runs behave the same on every machine.
    )


async def run_role(
    role: RoleConfig,
    prompt: str,
    *,
    cwd: Path | None = None,
    system_prompt: str | None = None,
    can_use_tool=None,
    hooks=None,
) -> RoleResult:
    """Run one role invocation to completion and return its result.

    Iterates the message stream, concatenates assistant text, and keeps the
    terminal ResultMessage for usage/cost. Raises RoleError when the run
    ends in an error subtype (max turns, budget exceeded, execution error)
    so callers can trigger fallback or gate logic.
    """
    options = build_options(
        role,
        cwd=cwd,
        system_prompt=system_prompt,
        can_use_tool=can_use_tool,
        hooks=hooks,
    )

    text_parts: list[str] = []
    result: ResultMessage | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
        else:
            for block in getattr(message, "content", []) or []:
                block_text = getattr(block, "text", None)
                if block_text:
                    text_parts.append(block_text)

    text = "\n".join(text_parts)

    if result is None:
        raise RoleError(
            f"role {role.name!r} produced no result message", text=text
        )
    if result.is_error or result.subtype != "success":
        raise RoleError(
            f"role {role.name!r} failed: {result.subtype}"
            + (f" — {result.result}" if result.result else ""),
            subtype=result.subtype,
            text=text,
        )

    return RoleResult(
        text=result.result or text,
        session_id=result.session_id,
        usage=result.usage or {},
        cost_usd=result.total_cost_usd,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
    )


_FENCED_JSON = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Pull the reasoner's JSON out of its reply.

    Tries the LAST fenced code block first (models often restate schemas or
    show examples before the final answer), then the raw text, then the
    outermost brace span. Raises JsonExtractionError with the raw reply
    attached; the caller decides whether to retry.
    """
    candidates: list[str] = []

    fenced = _FENCED_JSON.findall(text)
    candidates.extend(reversed(fenced))
    candidates.append(text.strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise JsonExtractionError("no parseable JSON object in role reply", raw=text)
