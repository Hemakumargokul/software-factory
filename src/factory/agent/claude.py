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
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

# Read tools (Read/Glob/Grep) are deliberately auto-allowed and therefore
# shadow can_use_tool for those tools — that is the design (see implementer
# role below), so the SDK's warning about it is noise.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


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


# Cost-conscious defaults (aliases resolved by the Claude Code CLI): the
# reasoner needs planning strength (sonnet), the implementer runs many more
# turns so it gets the cheapest tier (haiku) and escalates to sonnet only
# when a call dies with an execution error. Env vars override per role.
DEFAULT_REASONER_MODEL = "sonnet"
DEFAULT_IMPLEMENTER_MODEL = "haiku"
DEFAULT_FALLBACK_MODEL = "sonnet"


def _env_model(var: str, default: str | None = None) -> str | None:
    value = os.environ.get(var, "").strip()
    return value or default


def reasoner_role() -> RoleConfig:
    """Planning/analysis role: no tools, strong model, JSON out."""
    return RoleConfig(
        name="reasoner",
        allowed_tools=(),
        disallowed_tools=(),
        model=_env_model("FACTORY_MODEL_REASONER", DEFAULT_REASONER_MODEL),
        fallback_model=_env_model("FACTORY_MODEL_FALLBACK", DEFAULT_FALLBACK_MODEL),
        max_turns=3,
        max_budget_usd=1.00,
        no_tools=True,
    )


def analyst_role() -> RoleConfig:
    """Brownfield impact analysis: read tools only, JSON out."""
    return RoleConfig(
        name="analyst",
        allowed_tools=("Read", "Glob", "Grep"),
        disallowed_tools=(
            "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
            "WebFetch", "WebSearch", "Task",
        ),
        model=_env_model("FACTORY_MODEL_REASONER", DEFAULT_REASONER_MODEL),
        fallback_model=_env_model("FACTORY_MODEL_FALLBACK", DEFAULT_FALLBACK_MODEL),
        max_turns=15,
        max_budget_usd=1.50,
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
        model=_env_model("FACTORY_MODEL_IMPLEMENTER", DEFAULT_IMPLEMENTER_MODEL),
        fallback_model=_env_model("FACTORY_MODEL_FALLBACK", DEFAULT_FALLBACK_MODEL),
        # Cheap models spend more turns on the same work; the budget cap is
        # the real cost bound, turns just catch pathological loops.
        max_turns=60,
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

    Raises RoleError when the run ends in an error subtype (max turns,
    budget exceeded, execution error). An execution error is retried ONCE
    on the fallback model before propagating — that is the only subtype a
    different model can plausibly fix; blown turn/budget caps would just
    blow again.
    """
    try:
        return await _run_role_once(
            role, prompt, cwd=cwd, system_prompt=system_prompt,
            can_use_tool=can_use_tool, hooks=hooks,
        )
    except RoleError as error:
        can_fall_back = (
            role.fallback_model is not None
            and role.fallback_model != role.model
            and error.subtype == "error_during_execution"
        )
        if not can_fall_back:
            raise
        fallback = RoleConfig(**{**role.__dict__, "model": role.fallback_model})
        return await _run_role_once(
            fallback, prompt, cwd=cwd, system_prompt=system_prompt,
            can_use_tool=can_use_tool, hooks=hooks,
        )


def _subtype_from_stream_error(message: str) -> str:
    """Map a raw SDK stream exception onto the result-subtype vocabulary."""
    lowered = message.lower()
    if "maximum number of turns" in lowered:
        return "error_max_turns"
    if "budget" in lowered:
        return "error_max_budget_usd"
    return "error_during_execution"


async def _run_role_once(
    role: RoleConfig,
    prompt: str,
    *,
    cwd: Path | None = None,
    system_prompt: str | None = None,
    can_use_tool=None,
    hooks=None,
) -> RoleResult:
    options = build_options(
        role,
        cwd=cwd,
        system_prompt=system_prompt,
        can_use_tool=can_use_tool,
        hooks=hooks,
    )

    # The SDK requires streaming-mode input when a can_use_tool callback is
    # attached; a plain string prompt raises. Wrap it as a one-message stream.
    prompt_arg: Any = prompt
    if can_use_tool is not None:

        async def _single_message_stream():
            yield {"type": "user", "message": {"role": "user", "content": prompt}}

        prompt_arg = _single_message_stream()

    text_parts: list[str] = []
    result: ResultMessage | None = None

    try:
        async for message in query(prompt=prompt_arg, options=options):
            if isinstance(message, ResultMessage):
                result = message
            else:
                for block in getattr(message, "content", []) or []:
                    block_text = getattr(block, "text", None)
                    if block_text:
                        text_parts.append(block_text)
    except Exception as error:
        # In streaming mode the SDK surfaces some terminal outcomes (max
        # turns, transport failures) as raw stream exceptions instead of a
        # ResultMessage. Normalize them to RoleError so stage logic sees one
        # failure vocabulary — a stream crash must feed the retry loop, not
        # kill the run.
        raise RoleError(
            f"role {role.name!r} stream error: {error}",
            subtype=_subtype_from_stream_error(str(error)),
            text="\n".join(text_parts),
        ) from error

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
