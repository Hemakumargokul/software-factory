from pathlib import Path

import pytest

from factory.claude import (
    JsonExtractionError,
    build_options,
    extract_json,
    implementer_role,
    reasoner_role,
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

    def test_model_defaults_to_cli_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("FACTORY_MODEL_REASONER", raising=False)
        assert reasoner_role().model is None
