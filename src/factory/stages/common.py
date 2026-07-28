"""Shared plumbing for stages: the reasoner call pattern and subprocess
execution with the truncation and timeout rules every stage follows."""

import json
import subprocess
from pathlib import Path
from typing import Any

from factory.agent import claude
from factory.observability import tracing
from factory.agent.claude import extract_json, reasoner_role
from factory.agent.prompts import JSON_ROLE_SYSTEM_PROMPT

REPORT_TAIL_LINES = 100

JSON_RETRY_NUDGE = (
    "\n\nIMPORTANT: your previous reply could not be parsed as JSON. "
    "Reply again with ONLY the JSON object requested above — no prose "
    "before or after it."
)


async def call_json_role(
    stage: str,
    role,
    prompt: str,
    *,
    system_prompt: str,
    cwd: Path | None = None,
) -> tuple[dict[str, Any], float]:
    """Invoke a role that must answer in JSON, traced as a generation span.

    A reply without parseable JSON is retried ONCE with an explicit nudge
    (models occasionally answer with the document instead of the wrapper);
    the second failure propagates. Returns the JSON and the total cost of
    all calls so every stage reports its true spend into the run budget."""
    total_cost = 0.0
    attempt_prompt = prompt
    for attempt in (1, 2):
        with tracing.generation_span(stage, role.model, attempt_prompt) as span:
            result = await claude.run_role(
                role, attempt_prompt, cwd=cwd, system_prompt=system_prompt
            )
            span.end_with(result)
        total_cost += result.cost_usd or 0.0
        try:
            return extract_json(result.text), total_cost
        except claude.JsonExtractionError:
            if attempt == 2:
                raise
            attempt_prompt = prompt + JSON_RETRY_NUDGE
    raise AssertionError("unreachable")


async def run_reasoner(stage: str, prompt: str) -> tuple[dict[str, Any], float]:
    """One reasoner invocation; returns the extracted JSON and its cost."""
    return await call_json_role(
        stage, reasoner_role(), prompt, system_prompt=JSON_ROLE_SYSTEM_PROMPT
    )


def compact(value: Any, limit: int = 4000) -> str:
    """Stage artifact -> prompt fragment, bounded so prompts can't balloon."""
    text = json.dumps(value, indent=1, default=str)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def run_command(
    cmd: tuple[str, ...], cwd: str | Path, timeout_s: int
) -> tuple[bool, str]:
    """Run a build/test command; return (ok, last 100 lines of output).

    Full build logs can be megabytes; the tail is what a fix-it prompt and
    a trace need. A timeout is a failure with the timeout stated, not an
    exception — verification failures are data, not crashes.
    """
    try:
        result = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT: {' '.join(cmd)} exceeded {timeout_s}s"
    except FileNotFoundError as error:
        return False, f"COMMAND NOT FOUND: {error}"

    output = (result.stdout or "") + (result.stderr or "")
    tail = "\n".join(output.splitlines()[-REPORT_TAIL_LINES:])
    return result.returncode == 0, tail
