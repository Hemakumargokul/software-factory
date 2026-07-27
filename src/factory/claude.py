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
from string import Template
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

# System prompt shared by the JSON-emitting roles (reasoner, analyst).
# Stage prompts (intake, requirements, design, decompose) build on this and
# live as module constants here so every prompt is reviewable in one place.
JSON_ROLE_SYSTEM_PROMPT = (
    "You are a software engineering reasoning agent inside an automated "
    "pipeline. You have no tools; do not attempt to read or write files. "
    "Answer with a single fenced ```json code block matching the schema in "
    "the user message. No text after the closing fence."
)

# ---------------------------------------------------------------------------
# Stage prompt templates. string.Template ($placeholders) so the literal JSON
# braces in the schemas need no escaping. All prompts live here, in one file,
# so the entire surface between the factory and the models is reviewable at
# a glance.
# ---------------------------------------------------------------------------

INTAKE_PROMPT = Template("""\
Normalize this engineering request and classify it.

REQUEST:
$goal

Score ambiguity from 0.0 (fully specified) to 1.0 (impossible to act on
without answers). List concrete ambiguities a human must resolve — questions
where a wrong guess would change the architecture or the API contract.
Scenario: "greenfield" builds a new system, "brownfield" changes an existing
one, "ambiguous" means the request cannot be classified without answers.

Reply with JSON:
{
  "problem": "one-paragraph normalized problem statement",
  "assumptions": ["assumption made to interpret the request", ...],
  "ambiguity_score": 0.0,
  "ambiguities": ["open question needing a human answer", ...],
  "scenario": "greenfield" | "brownfield" | "ambiguous"
}
""")

REQUIREMENTS_PROMPT = Template("""\
Write an engineering specification for this problem.

PROBLEM (from intake):
$intake

ORIGINAL REQUEST:
$goal

Acceptance criteria must be black-box observable — statements a tester can
verify over HTTP without reading the code.

Reply with JSON:
{
  "summary": "one paragraph",
  "functional_requirements": ["FR1: ...", ...],
  "non_functional_requirements": ["NFR1: ...", ...],
  "acceptance_criteria": ["AC1: given/when/then ...", ...],
  "out_of_scope": ["...", ...]
}
""")

DESIGN_PROMPT = Template("""\
Design the system for this specification. Target stack: $language.

SPECIFICATION:
$spec

Constraints: single deployable service; embedded database only; every
dependency beyond the pre-approved starter set is a governance event, so
prefer the standard library of the framework. Include risks — things that
could plausibly fail in implementation or operation — with mitigations.

Reply with JSON:
{
  "architecture": "prose overview of layers and data flow",
  "components": [{"name": "...", "responsibility": "..."}],
  "api_contract": [{"method": "GET", "path": "/...", "request": "...", "response": "...", "status_codes": [200]}],
  "data_model": [{"entity": "...", "fields": ["name: type", ...]}],
  "alternatives_considered": ["option and why it was rejected", ...],
  "risks": [{"risk": "...", "impact": "...", "mitigation": "..."}]
}
""")

DECOMPOSE_PROMPT = Template("""\
Decompose this design into implementation tasks.

SPECIFICATION:
$spec

DESIGN:
$design
$replan_context

Rules: as few tasks as the scope honestly allows (1-4). Each task must be
independently verifiable by building and running the test suite. Wire
dependencies with depends_on (task ids). Every task includes writing its
unit tests. The first task must produce a compilable application skeleton.

Reply with JSON:
{
  "tasks": [
    {
      "id": "T1",
      "title": "...",
      "description": "what to build, which files, which tests",
      "depends_on": [],
      "verify": "how the verification stage should judge this task"
    }
  ]
}
""")

IMPLEMENTER_SYSTEM_PROMPT = (
    "You are the implementer inside an automated software factory. You work "
    "ONLY in the current working directory, which contains the product "
    "repository. Write production code AND its unit tests for the task you "
    "are given. Follow the provided design and API contract exactly. Never "
    "hardcode credentials. Never add dependencies beyond those already in "
    "the build file unless the task explicitly authorizes it. Do not run "
    "shell commands; only read and edit files. Do not create git commits. "
    "When the task is complete, stop and summarize what you changed."
)

IMPLEMENT_TASK_PROMPT = Template("""\
TASK $task_id: $task_title

$task_description

SPECIFICATION:
$spec

DESIGN (follow the API contract exactly):
$design
$failure_context
""")

FAILURE_CONTEXT_PROMPT = Template("""
PREVIOUS ATTEMPT FAILED. Fix the code so verification passes.
Verification report (truncated):
$report
""")

REPLAN_CONTEXT_PROMPT = Template("""
THIS IS A RE-PLAN. The previous decomposition failed at task $failed_task
and its work was rolled back. Produce a DIFFERENT decomposition that avoids
the failure below — smaller steps, a different order, or a simpler approach.
Failure summary:
$failures
""")

REVIEW_PROMPT = Template("""\
Review this change as a senior engineer. You see the diff and the design;
judge whether the change is correct, safe and consistent with the contract.

TASK: $task_id - $task_title

DESIGN (the contract the change must honor):
$design

DIFF (working tree vs last integrated state):
$diff

Concerns are advisory: they become entries in the risk register, they do
not block the pipeline. Reserve "concerns" for things a human should read
during the merge review.

Reply with JSON:
{
  "verdict": "approve" | "concerns",
  "concerns": ["specific, actionable observation", ...],
  "risks": [{"risk": "...", "impact": "...", "mitigation": "..."}]
}
""")

SUMMARY_PROMPT = Template("""\
Write the engineering summary for this completed factory run, in Markdown.

GOAL:
$goal

DECISION LINEAGE (chronological):
$decisions

RISK REGISTER:
$risks

METRIC EVENTS:
$metrics

Sections: What was built; Key decisions and why (with alternatives that were
rejected); Risks and how they were addressed; Verification outcome; What a
reviewer should look at first. Be specific and cite task ids and commit SHAs
from the lineage. Do not invent anything not present in the inputs.

Reply with JSON:
{
  "summary_markdown": "..."
}
""")


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

    async for message in query(prompt=prompt_arg, options=options):
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
