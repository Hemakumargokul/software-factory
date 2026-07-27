"""Shared plumbing for stages: the reasoner call pattern and subprocess
execution with the truncation and timeout rules every stage follows."""

import json
import subprocess
from pathlib import Path
from typing import Any

from factory import claude, tracing
from factory.claude import JSON_ROLE_SYSTEM_PROMPT, extract_json, reasoner_role

REPORT_TAIL_LINES = 100


async def run_reasoner(stage: str, prompt: str) -> dict[str, Any]:
    """One reasoner invocation traced as a generation span, JSON out."""
    role = reasoner_role()
    with tracing.generation_span(stage, role.model, prompt) as span:
        result = await claude.run_role(
            role, prompt, system_prompt=JSON_ROLE_SYSTEM_PROMPT
        )
        span.end_with(result)
    return extract_json(result.text)


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
