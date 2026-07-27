import pytest

from factory import tracing
from factory.claude import RoleResult


@pytest.fixture(autouse=True)
def clean_tracing_state(monkeypatch):
    """Every test starts with no Langfuse env and a cold cache."""
    for var in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_TRACING_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    tracing._reset_for_tests()
    yield
    tracing._reset_for_tests()


class TestTracingDisabled:
    def test_disabled_without_keys(self):
        assert tracing.tracing_enabled() is False

    def test_disabled_by_explicit_flag_even_with_keys(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
        assert tracing.tracing_enabled() is False

    def test_disabled_when_host_unreachable(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        # Port 1 on localhost: nothing listens, probe fails fast.
        monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:1")
        assert tracing.tracing_enabled() is False

    def test_decision_is_cached(self, monkeypatch):
        assert tracing.tracing_enabled() is False
        # Keys appearing later must not flip the cached decision mid-process.
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        assert tracing.tracing_enabled() is False


class TestNoopHelpers:
    """With tracing off, every helper works and nothing raises —
    call sites never branch."""

    def test_stage_span_yields_span_accepting_anything(self):
        with tracing.stage_span("design", scenario="greenfield") as span:
            span.update(output="artifact")
            span.whatever_method("any", args=True)

    def test_generation_span_end_with_and_error(self):
        result = RoleResult(text="{}", session_id="s", usage={"input_tokens": 5})
        with tracing.generation_span("reasoner", "claude-sonnet-4-5", "prompt") as gen:
            gen.end_with(result)
            gen.error("boom")

    def test_tool_span_and_score_are_silent(self):
        tracing.tool_span("Write", {"path": "/x"}, "ok", denied=False)
        tracing.tool_span("Write", {"path": "/etc/passwd"}, denied=True)
        tracing.score("success_rate", 1.0)

    def test_run_context_and_flush(self):
        with tracing.run_context("thread-1", "factory-run"):
            pass
        tracing.flush()


class TestTruncation:
    def test_short_string_unchanged(self):
        s = "x" * tracing.TRUNCATE_AT
        assert tracing._truncate(s) == s

    def test_long_string_truncated_with_marker(self):
        s = "x" * (tracing.TRUNCATE_AT + 500)
        out = tracing._truncate(s)
        assert out.startswith("x" * tracing.TRUNCATE_AT)
        assert "[truncated 500 chars]" in out

    def test_non_strings_pass_through(self):
        assert tracing._truncate(12345) == 12345
        assert tracing._truncate(None) is None
