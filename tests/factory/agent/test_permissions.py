from pathlib import Path

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from factory.agent.permissions import make_can_use_tool, make_pretooluse_hook

PROTECTED = ["tests/acceptance/**"]


@pytest.fixture
def sandbox(tmp_path):
    box = tmp_path / "sandbox"
    box.mkdir()
    return box


@pytest.fixture
def audit():
    return []


@pytest.fixture
def can_use_tool(sandbox, audit):
    return make_can_use_tool(sandbox, PROTECTED, audit.append)


class TestSandboxEscapes:
    async def test_write_inside_sandbox_allowed(self, can_use_tool, audit):
        result = await can_use_tool(
            "Write", {"file_path": "src/Main.java", "content": "x"}, None
        )
        assert isinstance(result, PermissionResultAllow)
        assert audit[-1]["decision"] == "allow"

    async def test_relative_dotdot_escape_denied(self, can_use_tool, audit):
        result = await can_use_tool(
            "Write", {"file_path": "../../etc/passwd", "content": "x"}, None
        )
        assert isinstance(result, PermissionResultDeny)
        assert "escapes sandbox" in result.message
        assert audit[-1]["decision"] == "deny"

    async def test_absolute_path_outside_denied(self, can_use_tool):
        result = await can_use_tool(
            "Write", {"file_path": "/etc/hosts", "content": "x"}, None
        )
        assert isinstance(result, PermissionResultDeny)

    async def test_sneaky_inside_then_out_denied(self, can_use_tool, sandbox):
        raw = str(sandbox / "src" / ".." / ".." / "outside.txt")
        result = await can_use_tool("Write", {"file_path": raw, "content": "x"}, None)
        assert isinstance(result, PermissionResultDeny)

    async def test_read_outside_sandbox_also_denied(self, can_use_tool):
        result = await can_use_tool("Read", {"file_path": "/etc/passwd"}, None)
        assert isinstance(result, PermissionResultDeny)

    async def test_tool_without_path_args_allowed(self, can_use_tool):
        result = await can_use_tool("Grep", {"pattern": "TODO"}, None)
        assert isinstance(result, PermissionResultAllow)


class TestProtectedGlobs:
    async def test_write_to_protected_path_denied(self, can_use_tool):
        result = await can_use_tool(
            "Edit",
            {"file_path": "tests/acceptance/test_shortener.py", "old_string": "a",
             "new_string": "b"},
            None,
        )
        assert isinstance(result, PermissionResultDeny)
        assert "protected path" in result.message

    async def test_nested_protected_path_denied(self, can_use_tool):
        result = await can_use_tool(
            "Write",
            {"file_path": "tests/acceptance/sub/helper.py", "content": "x"},
            None,
        )
        assert isinstance(result, PermissionResultDeny)

    async def test_read_of_protected_path_allowed(self, can_use_tool):
        """Protection is against tampering, not reading."""
        result = await can_use_tool(
            "Read", {"file_path": "tests/acceptance/test_shortener.py"}, None
        )
        assert isinstance(result, PermissionResultAllow)

    async def test_write_next_to_protected_dir_allowed(self, can_use_tool):
        result = await can_use_tool(
            "Write", {"file_path": "tests/unit/AppTest.java", "content": "x"}, None
        )
        assert isinstance(result, PermissionResultAllow)


class TestAuditCompleteness:
    async def test_every_decision_is_audited(self, can_use_tool, audit):
        await can_use_tool("Write", {"file_path": "ok.txt", "content": "x"}, None)
        await can_use_tool("Write", {"file_path": "/etc/hosts", "content": "x"}, None)
        assert [e["decision"] for e in audit] == ["allow", "deny"]
        assert all(e["kind"] == "permission_decision" for e in audit)
        assert all("ts" in e and "tool" in e for e in audit)


class TestPreToolUseHook:
    async def test_hook_records_attempt_and_makes_no_decision(self, audit):
        hooks = make_pretooluse_hook(audit.append)
        matcher = hooks["PreToolUse"][0]
        assert matcher.matcher is None  # observes every tool, not a subset

        callback = matcher.hooks[0]
        output = await callback(
            {"tool_name": "Read", "tool_input": {"file_path": "pom.xml"}},
            "tool-use-1",
            {"signal": None},
        )

        assert output == {}  # an allow here would bypass can_use_tool
        assert audit[-1]["kind"] == "tool_attempt"
        assert audit[-1]["tool"] == "Read"
        assert audit[-1]["tool_use_id"] == "tool-use-1"
