"""Langfuse tracing with graceful degradation.

One module-level guard (`tracing_enabled`) decides everything once per
process: keys present, not explicitly disabled, and the host actually
reachable (1s probe, cached). When any check fails, every helper degrades
to a no-op — call sites never branch on whether tracing is on, and a
missing or down Langfuse can never break a factory run.

All inputs/outputs are truncated before sending: build logs and diffs can
be huge, and the trace needs the shape of events, not full payloads (those
live in git and the run artifacts).
"""

import os
from contextlib import contextmanager
from typing import Any

TRUNCATE_AT = 2000

_enabled_cache: bool | None = None
_client_cache: Any = None


def _truncate(value: Any, limit: int = TRUNCATE_AT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"
    return value


def _reset_for_tests() -> None:
    global _enabled_cache, _client_cache
    _enabled_cache = None
    _client_cache = None


def tracing_enabled() -> bool:
    """True only when keys are set, tracing is not disabled, and the host
    answers a 1s health probe. The result is cached for the process."""
    global _enabled_cache
    if _enabled_cache is not None:
        return _enabled_cache

    if os.environ.get("LANGFUSE_TRACING_ENABLED", "").strip().lower() == "false":
        _enabled_cache = False
        return False
    if not (
        os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
    ):
        _enabled_cache = False
        return False

    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    try:
        import httpx

        response = httpx.get(f"{host}/api/public/health", timeout=1.0)
        _enabled_cache = response.status_code == 200
    except Exception:
        _enabled_cache = False
    return _enabled_cache


def _client() -> Any:
    """The Langfuse client, created lazily so a disabled run never spawns
    the SDK's background export threads. None when tracing is off."""
    global _client_cache
    if not tracing_enabled():
        return None
    if _client_cache is None:
        from langfuse import Langfuse

        _client_cache = Langfuse()
    return _client_cache


class _NoopSpan:
    """Absorbs any method call and does nothing, so call sites can use the
    span unconditionally (update, end_with, score, anything)."""

    def _noop(self, *args: Any, **kwargs: Any) -> "_NoopSpan":
        return self

    def __getattr__(self, name: str) -> Any:
        return self._noop


NOOP_SPAN = _NoopSpan()


class _GenerationHandle:
    """Wraps a live Langfuse generation so callers report results through
    one method instead of knowing the SDK's update() vocabulary."""

    def __init__(self, generation: Any):
        self._generation = generation

    def end_with(self, result: Any) -> None:
        """Record a RoleResult: output text, token usage, and cost."""
        usage = {
            key: value
            for key, value in (result.usage or {}).items()
            if isinstance(value, int)
        }
        self._generation.update(
            output=_truncate(result.text),
            usage_details=usage or None,
            cost_details=(
                {"total": result.cost_usd} if result.cost_usd is not None else None
            ),
        )

    def error(self, message: str) -> None:
        self._generation.update(level="ERROR", status_message=_truncate(message, 500))


@contextmanager
def stage_span(name: str, **attrs: Any):
    """Span for one graph stage. Yields a live span or the no-op."""
    client = _client()
    if client is None:
        yield NOOP_SPAN
        return
    with client.start_as_current_observation(
        name=name,
        as_type="span",
        metadata={key: _truncate(value) for key, value in attrs.items()} or None,
    ) as span:
        yield span


@contextmanager
def generation_span(name: str, model: str | None, prompt: str):
    """Span for one role invocation. The caller finishes it with
    `span.end_with(role_result)` (or `span.error(msg)` on failure)."""
    client = _client()
    if client is None:
        yield NOOP_SPAN
        return
    with client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model or "cli-default",
        input=_truncate(prompt),
    ) as generation:
        yield _GenerationHandle(generation)


def tool_span(
    name: str, input: Any, output: Any = None, *, denied: bool = False
) -> None:
    """Point-in-time record of one tool call the agent made (or was denied)."""
    client = _client()
    if client is None:
        return
    with client.start_as_current_observation(
        name=name,
        as_type="tool",
        input=_truncate(str(input)),
        output=_truncate(str(output)) if output is not None else None,
        level="WARNING" if denied else "DEFAULT",
        metadata={"denied": denied},
    ):
        pass


def score(name: str, value: float) -> None:
    """Attach a reliability metric to the current trace (success rate,
    retries, MTTR ...). Emitted by metrics.py at run end."""
    client = _client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value)
    except Exception:
        # A failed score must never fail the run; the metric still exists
        # in metrics.db.
        pass


@contextmanager
def run_context(session_id: str, run_name: str):
    """Wraps a whole graph invocation: groups every trace of the run under
    one Langfuse session (session_id = LangGraph thread id)."""
    client = _client()
    if client is None:
        yield
        return
    from langfuse import propagate_attributes

    with client.start_as_current_observation(name=run_name, as_type="span"):
        with propagate_attributes(session_id=session_id, trace_name=run_name):
            yield


def flush() -> None:
    """Drain pending events; called once at CLI exit."""
    if _client_cache is not None:
        _client_cache.flush()
