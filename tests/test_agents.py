"""Tests for multi-agent support — tool mapping, detection, output formatting."""

from nuh import agents
import pytest


# --- Tool mapping ---


class TestNormalizeTool:
    def test_claude_tools_identity(self):
        for tool in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
            assert agents.normalize_tool(tool) == tool

    def test_unknown_passthrough(self):
        assert agents.normalize_tool("UnknownTool") == "UnknownTool"
        assert agents.normalize_tool("SomeOtherThing") == "SomeOtherThing"


# --- Agent detection ---


class TestDetectAgent:
    """Agent detection defaults to claude for all inputs."""

    def test_claude_tools_bare(self):
        """Bare Claude tool names → claude."""
        for tool in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
            assert agents.detect_agent(tool) == "claude"

    def test_claude_payload(self):
        """Claude payload (no special fields) → claude."""
        assert agents.detect_agent({"tool_name": "Bash"}) == "claude"
        assert agents.detect_agent({"tool_name": "Read"}) == "claude"

    def test_unknown_defaults_claude(self):
        assert agents.detect_agent("UnknownTool") == "claude"
        assert agents.detect_agent("") == "claude"
        assert agents.detect_agent({"tool_name": "UnknownTool"}) == "claude"

    def test_shared_tool_without_payload_is_claude(self):
        """Read/Write/Grep without payload markers → claude (safe default)."""
        assert agents.detect_agent("Read") == "claude"
        assert agents.detect_agent("Write") == "claude"
        assert agents.detect_agent("Grep") == "claude"

    def test_claude_payload_with_hook_event_name(self):
        """FD-029: Claude Code sends hook_event_name — still detected as claude."""
        data = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
                "session_id": "abc123", "cwd": "/tmp"}
        assert agents.detect_agent(data) == "claude"
        for tool in ("Bash", "Read", "Write", "Edit", "Glob", "Grep"):
            assert agents.detect_agent({"tool_name": tool, "hook_event_name": "PreToolUse"}) == "claude"


# --- Output formatting ---


class TestFormatBlock:
    def test_claude_format(self):
        result = agents.format_block("dangerous command", "claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert hso["permissionDecisionReason"] == "nuh. dangerous command"
        assert hso["hookEventName"] == "PreToolUse"

    def test_claude_empty_reason(self):
        result = agents.format_block("", "claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert hso["permissionDecisionReason"] == "nuh."
        assert "systemMessage" not in result

    def test_claude_reason_also_shown_to_user(self):
        result = agents.format_block("dangerous command", "claude")
        assert result["systemMessage"] == "⛔ **nuh!** dangerous command"

    def test_codex_has_no_system_message(self):
        assert "systemMessage" not in agents.format_block("dangerous command", agents.CODEX)


class TestFormatAsk:
    def test_claude_format(self):
        result = agents.format_ask("needs confirmation", "claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask"
        assert hso["permissionDecisionReason"] == "nuh? needs confirmation"
        assert result["systemMessage"] == "⚠️ **nuh?** needs confirmation"

    def test_empty_reason(self):
        result = agents.format_ask("", "claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecisionReason"] == "nuh?"
        assert "systemMessage" not in result

    def test_llm_warning_reaches_codex_not_the_notice(self):
        result = agents.format_ask("needs confirmation", "claude", "looks off")
        assert result["systemMessage"] == "⚠️ **nuh?** needs confirmation"
        codex = agents.format_ask("needs confirmation", agents.CODEX, "looks off")
        assert codex["hookSpecificOutput"]["permissionDecisionReason"].endswith("needs confirmation\nlooks off")

    def test_system_message_is_one_line(self):
        reason = "unknown → ask\n     To classify: nuh classify x <type>\n     See available types: nuh types"
        result = agents.format_ask(reason, "claude")
        assert result["systemMessage"] == "⚠️ **nuh?** unknown → ask"  # hints stay in the permission prompt
        assert "\n" in result["hookSpecificOutput"]["permissionDecisionReason"]


class TestFormatAllow:
    def test_claude_format(self):
        result = agents.format_allow("claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecision"] == "allow"


class TestFormatError:
    def test_claude_format(self):
        result = agents.format_error("oops", "claude")
        hso = result["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert "oops" in hso["permissionDecisionReason"]
        assert "nuh: internal error" in hso["permissionDecisionReason"]


# --- MCP matcher registration (FD-024) ---


class TestMcpMatchers:
    def test_mcp_matcher_registered(self):
        assert "mcp__.*" in agents.AGENT_TOOL_MATCHERS[agents.CLAUDE]


class TestCodexAdapter:
    @pytest.mark.parametrize("turn_id", ["turn-1", "", None, 42])
    def test_marker_detects_interface_before_validation(self, turn_id: object) -> None:
        assert agents.detect_agent({"tool_name": "Bash", "turn_id": turn_id}) == agents.CODEX

    @pytest.mark.parametrize("agent", [agents.CLAUDE, agents.CODEX])
    def test_override_wins(self, monkeypatch: pytest.MonkeyPatch, agent: str) -> None:
        monkeypatch.setenv("NUH_AGENT", agent)
        assert agents.detect_agent({"turn_id": "turn-1"}) == agent
        assert agents.detect_agent("Bash") == agent

    @pytest.mark.parametrize("override", ["", "CODEX", "other"])
    def test_invalid_override_rejected(self, monkeypatch: pytest.MonkeyPatch, override: str) -> None:
        monkeypatch.setenv("NUH_AGENT", override)
        with pytest.raises(ValueError, match="NUH_AGENT"):
            agents.detect_agent({})

    def test_generic_environment_does_not_select_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_HOME", "/tmp/codex")
        monkeypatch.setenv("CODEX_THREAD_ID", "thread")
        assert agents.detect_agent({"tool_name": "Bash"}) == agents.CLAUDE

    def test_ask_denies_with_review_reason_and_warning(self) -> None:
        result = agents.format_ask("sensitive path", agents.CODEX, "LLM warning")
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "Review required" in output["permissionDecisionReason"]
        assert "sensitive path" in output["permissionDecisionReason"]
        assert "LLM warning" in output["permissionDecisionReason"]
        assert "systemMessage" not in result
