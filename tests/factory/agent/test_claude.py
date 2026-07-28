from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage

from factory.agent import claude
from factory.agent.claude import (
    JsonExtractionError,
    RoleConfig,
    RoleError,
    build_options,
    extract_json,
    implementer_role,
    reasoner_role,
    run_role,
)


class TestExtractJson:
    def test_clean_json(self):
        assert extract_json('{"ok": true, "n": 3}') == {"ok": True, "n": 3}

    def test_fenced_json(self):
        text = 'Here is the plan:\n```json\n{"tasks": [1, 2]}\n```\n'
        assert extract_json(text) == {"tasks": [1, 2]}

    def test_fence_without_language_tag(self):
        text = '```\n{"a": 1}\n```'
        assert extract_json(text) == {"a": 1}

    def test_last_fenced_block_wins(self):
        text = (
            'The schema is:\n```json\n{"example": "schema"}\n```\n'
            'And my answer:\n```json\n{"answer": 42}\n```\n'
        )
        assert extract_json(text) == {"answer": 42}

    def test_prose_wrapped_braces(self):
        text = 'Sure! The result is {"status": "done"} — let me know.'
        assert extract_json(text) == {"status": "done"}

    def test_no_json_raises_with_raw_attached(self):
        with pytest.raises(JsonExtractionError) as exc_info:
            extract_json("I could not produce the requested output.")
        assert exc_info.value.raw == "I could not produce the requested output."

    def test_non_object_json_is_not_accepted(self):
        with pytest.raises(JsonExtractionError):
            extract_json("[1, 2, 3]")


class TestRoleConfigs:
    def test_reasoner_has_no_tools(self):
        role = reasoner_role()
        options = build_options(role)
        assert options.tools == []          # every built-in tool stripped
        assert options.allowed_tools == []
        assert options.max_turns == role.max_turns
        assert options.max_budget_usd == role.max_budget_usd

    def test_implementer_never_auto_allows_writes(self):
        """Write/Edit in allowed_tools would be auto-permitted and skip the
        can_use_tool sandbox guard entirely — the one config mistake that
        silently disables governance."""
        options = build_options(implementer_role())
        for tool in ("Write", "Edit"):
            assert tool not in options.allowed_tools
        assert "Bash" in options.disallowed_tools

    def test_cwd_and_system_prompt_pass_through(self):
        options = build_options(
            implementer_role(),
            cwd=Path("/tmp/factory/run-1"),
            system_prompt="You are the implementer.",
        )
        assert options.cwd == "/tmp/factory/run-1"
        assert options.system_prompt == "You are the implementer."

    def test_model_env_overrides(self, monkeypatch):
        monkeypatch.setenv("FACTORY_MODEL_REASONER", "claude-opus-4-5")
        monkeypatch.setenv("FACTORY_MODEL_FALLBACK", "claude-sonnet-4-5")
        role = reasoner_role()
        assert role.model == "claude-opus-4-5"
        assert role.fallback_model == "claude-sonnet-4-5"

    def test_model_defaults_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("FACTORY_MODEL_REASONER", raising=False)
        monkeypatch.delenv("FACTORY_MODEL_IMPLEMENTER", raising=False)
        monkeypatch.delenv("FACTORY_MODEL_FALLBACK", raising=False)
        assert reasoner_role().model == "sonnet"
        assert implementer_role().model == "haiku"
        assert implementer_role().fallback_model == "sonnet"


def _result_message(subtype="success", is_error=False, text="ok"):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=1, session_id="s", result=text, total_cost_usd=0.01, usage={},
    )


def _role(model="primary", fallback="backup"):
    return RoleConfig(
        name="reasoner", allowed_tools=(), disallowed_tools=(),
        model=model, fallback_model=fallback, max_turns=1, max_budget_usd=1.0,
        no_tools=True,
    )


class TestFallbackRetry:
    def test_execution_error_retries_once_on_fallback_model(self, monkeypatch):
        models_called = []

        def fake_query(*, prompt, options, transport=None):
            async def stream():
                models_called.append(options.model)
                if len(models_called) == 1:
                    yield _result_message("error_during_execution", is_error=True)
                else:
                    yield _result_message()
            return stream()

        monkeypatch.setattr(claude, "query", fake_query)

        import asyncio
        result = asyncio.run(run_role(_role(), "hi"))
        assert models_called == ["primary", "backup"]
        assert result.text == "ok"

    def test_budget_and_turn_errors_do_not_fall_back(self, monkeypatch):
        models_called = []

        def fake_query(*, prompt, options, transport=None):
            async def stream():
                models_called.append(options.model)
                yield _result_message("error_max_turns", is_error=True)
            return stream()

        monkeypatch.setattr(claude, "query", fake_query)

        import asyncio
        with pytest.raises(RoleError, match="error_max_turns"):
            asyncio.run(run_role(_role(), "hi"))
        assert models_called == ["primary"]  # no second call

    def test_no_fallback_model_propagates_immediately(self, monkeypatch):
        def fake_query(*, prompt, options, transport=None):
            async def stream():
                yield _result_message("error_during_execution", is_error=True)
            return stream()

        monkeypatch.setattr(claude, "query", fake_query)

        import asyncio
        with pytest.raises(RoleError, match="error_during_execution"):
            asyncio.run(run_role(_role(fallback=None), "hi"))


class TestStreamErrorNormalization:
    """In streaming mode the SDK can raise raw exceptions mid-stream (e.g.
    max turns). They must become RoleErrors with the right subtype — a
    stream crash feeds the retry loop, it must never kill the run."""

    def _query_raising(self, message):
        def fake_query(*, prompt, options, transport=None):
            async def stream():
                raise Exception(message)
                yield  # pragma: no cover
            return stream()
        return fake_query

    def test_max_turns_stream_error_maps_to_subtype(self, monkeypatch):
        monkeypatch.setattr(claude, "query", self._query_raising(
            "Claude Code returned an error result: Reached maximum number "
            "of turns (60)"
        ))
        import asyncio
        with pytest.raises(RoleError) as excinfo:
            asyncio.run(run_role(_role(), "hi"))
        assert excinfo.value.subtype == "error_max_turns"

    def test_unknown_stream_error_falls_back_then_propagates(self, monkeypatch):
        calls = []

        def fake_query(*, prompt, options, transport=None):
            async def stream():
                calls.append(options.model)
                raise Exception("transport exploded")
                yield  # pragma: no cover
            return stream()

        monkeypatch.setattr(claude, "query", fake_query)
        import asyncio
        with pytest.raises(RoleError) as excinfo:
            asyncio.run(run_role(_role(), "hi"))
        # error_during_execution: eligible for the one-shot model fallback
        assert excinfo.value.subtype == "error_during_execution"
        assert calls == ["primary", "backup"]
