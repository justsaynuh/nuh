"""Unit tests for _classify_unknown_tool, the Write/Edit boundary and allow delivery.

FD-037, FD-024, FD-045, FD-054, FD-094.
"""

import json
import os

import pytest

from nuh.hook import _classify_unknown_tool, handle_write, handle_edit, handle_read
from nuh import config, paths, sensitive_paths
from nuh.config import NuhConfig


class TestClassifyUnknownTool:
    def setup_method(self):
        config._cached_config = NuhConfig()

    def teardown_method(self):
        config._cached_config = None

    def test_no_config_returns_ask(self):
        d = _classify_unknown_tool("SomeTool")
        assert d["decision"] == "ask"
        assert "unrecognized tool" in d["reason"]

    def test_global_classify_allow(self):
        config._cached_config = NuhConfig(
            classify_global={"mcp_trusted": ["MyTool"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("MyTool")
        assert d["decision"] == "allow"

    def test_global_classify_ask(self):
        config._cached_config = NuhConfig(
            classify_global={"db_write": ["DbTool"]},
        )
        d = _classify_unknown_tool("DbTool")
        assert d["decision"] == "ask"

    def test_mcp_skips_project_classify(self):
        config._cached_config = NuhConfig(
            classify_project={"mcp_trusted": ["mcp__evil__exfil"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("mcp__evil__exfil")
        assert d["decision"] == "ask"  # project ignored

    def test_non_mcp_project_classify_cannot_loosen_unknown(self):
        """A project table may not turn an unknown tool into an allowed type (static floor, §4.1)."""
        config._cached_config = NuhConfig(
            classify_project={"package_run": ["CustomRunner"]},
        )
        d = _classify_unknown_tool("CustomRunner")
        assert d["decision"] == "ask"

    def test_non_mcp_project_classify_can_tighten_unknown(self):
        config._cached_config = NuhConfig(
            classify_project={"git_discard": ["CustomRunner"]},
        )
        d = _classify_unknown_tool("CustomRunner")
        assert d["decision"] == "ask"
        assert "git_discard" in d["reason"]

    # --- FD-024 adversarial tests ---

    def test_mcp_classify_prefix_collision(self):
        """mcp__postgres in config must NOT match mcp__postgres__query."""
        config._cached_config = NuhConfig(
            classify_global={"mcp_trusted": ["mcp__postgres"]},
            actions={"mcp_trusted": "allow"},
        )
        # Exact match
        d = _classify_unknown_tool("mcp__postgres")
        assert d["decision"] == "allow"
        # Different tool — no match (single-token prefix, not substring)
        d = _classify_unknown_tool("mcp__postgres__query")
        assert d["decision"] == "ask"

    def test_mcp_classified_global_allow(self):
        """Global config can classify and allow MCP tools."""
        config._cached_config = NuhConfig(
            classify_global={"mcp_trusted": ["mcp__memory__search"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("mcp__memory__search")
        assert d["decision"] == "allow"

    # --- FD-045 configurable unknown tool policy ---

    def test_unknown_default_ask(self):
        """No actions config → unknown defaults to ask."""
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "ask"
        assert "unrecognized tool" in d["reason"]

    def test_unknown_actions_block(self):
        """actions.unknown: block → block unknown tools."""
        config._cached_config = NuhConfig(actions={"unknown": "block"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "block"
        assert "unrecognized tool" in d["reason"]

    def test_unknown_actions_allow(self):
        """actions.unknown: allow → allow unknown tools."""
        config._cached_config = NuhConfig(actions={"unknown": "allow"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "allow"

    def test_unknown_context_falls_to_ask(self):
        """actions.unknown: context → ask (no context resolver for 'unknown' type)."""
        config._cached_config = NuhConfig(actions={"unknown": "context"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "ask"
        assert "no context resolver" in d["reason"]


class TestClassifyUnknownToolContext:
    """FD-055: MCP tools with context policy resolve context via tool_input."""

    def setup_method(self):
        config._cached_config = NuhConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            actions={"db_write": "context"},
            db_targets=[
                {"database": "SANDBOX"},
                {"database": "SALES", "schema": "DEV"},
            ],
        )

    def teardown_method(self):
        config._cached_config = None

    def test_mcp_db_write_matching_target_allow(self):
        """MCP tool_input with matching database → allow."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "allowed target" in d["reason"]

    def test_mcp_db_write_matching_db_schema_allow(self):
        """MCP tool_input with matching database+schema → allow."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SALES", "schema": "DEV", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "SALES.DEV" in d["reason"]

    def test_mcp_db_write_non_matching_target_ask(self):
        """MCP tool_input with non-matching database → ask."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "PRODUCTION", "query": "DROP TABLE users"},
        )
        assert d["decision"] == "ask"
        assert "unrecognized target" in d["reason"]

    def test_mcp_db_write_no_database_key_ask(self):
        """MCP tool_input without database key → ask."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"query": "SELECT 1"},
        )
        assert d["decision"] == "ask"
        assert "unknown database target" in d["reason"]

    def test_mcp_db_write_no_tool_input_ask(self):
        """MCP with no tool_input → ask."""
        d = _classify_unknown_tool("mcp__snowflake__execute_sql")
        assert d["decision"] == "ask"

    def test_mcp_db_write_no_db_targets_ask(self):
        """No db_targets configured → ask even with matching input."""
        config._cached_config = NuhConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            actions={"db_write": "context"},
            db_targets=[],
        )
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "ask"
        assert "no db_targets configured" in d["reason"]

    def test_mcp_db_write_empty_tool_input_ask(self):
        """Empty dict {} (what main() actually passes) → ask."""
        d = _classify_unknown_tool("mcp__snowflake__execute_sql", {})
        assert d["decision"] == "ask"
        assert "unknown database target" in d["reason"]

    def test_mcp_db_write_case_insensitive(self):
        """Database name matching is case-insensitive."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "sandbox", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "SANDBOX" in d["reason"]

    def test_mcp_non_db_context_falls_to_ask(self):
        """MCP tool classified as network_outbound + context → ask (no tokens)."""
        config._cached_config = NuhConfig(
            classify_global={"network_outbound": ["mcp__api__fetch"]},
            actions={"network_outbound": "context"},
        )
        d = _classify_unknown_tool(
            "mcp__api__fetch",
            {"url": "https://example.com"},
        )
        assert d["decision"] == "ask"
        assert "unknown host" in d["reason"]

    def test_mcp_db_write_default_policy_context_with_targets(self):
        """db_write with default policy (context) + matching db_targets → allow."""
        config._cached_config = NuhConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            db_targets=[{"database": "SANDBOX"}],
        )
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "allowed target" in d.get("reason", "")


# --- FD-054: Write/Edit project boundary tests ---


class TestWriteEditBoundary:
    """FD-054: Write/Edit enforce project boundary check."""

    def setup_method(self):
        config._cached_config = NuhConfig()

    def teardown_method(self):
        config._cached_config = None

    def test_write_inside_project(self, project_root):
        target = os.path.join(project_root, "file.txt")
        d = handle_write({"file_path": target, "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_outside_project(self, project_root):
        d = handle_write({"file_path": "/tmp/outside.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "outside project" in d["reason"]

    def test_edit_outside_project(self, project_root):
        d = handle_edit({"file_path": "/tmp/outside.txt", "old_string": "a", "new_string": "b"})
        assert d["decision"] == "ask"
        assert "outside project" in d["reason"]

    def test_write_to_trusted_path(self, project_root):
        config._cached_config = NuhConfig(trusted_paths=["/tmp"])
        d = handle_write({"file_path": "/tmp/trusted.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_sensitive_unchanged(self, project_root):
        """Sensitive paths still block even with boundary check."""
        d = handle_write({"file_path": "~/.ssh/config", "content": "host"})
        assert d["decision"] == "block"

    def test_write_hook_unchanged(self, project_root):
        """Hook self-protection still blocks."""
        d = handle_write({"file_path": "~/.claude/hooks/evil.py", "content": "x"})
        assert d["decision"] == "block"

    def test_read_outside_no_boundary(self, project_root):
        """Read tool has no boundary check — still allows outside reads."""
        d = handle_read({"file_path": "/tmp/outside.txt"})
        assert d["decision"] == "allow"

    def test_trusted_does_not_override_sensitive(self, project_root):
        """trusted_paths cannot bypass sensitive path block."""
        home = os.path.expanduser("~")
        config._cached_config = NuhConfig(trusted_paths=[home])
        d = handle_write({"file_path": "~/.ssh/id_rsa", "content": "key"})
        assert d["decision"] == "block"

    def test_profile_none_disables_boundary(self, project_root):
        """profile: none disables boundary check for Write."""
        config._cached_config = NuhConfig(profile="none")
        sensitive_paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "/tmp/anywhere.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_profile_none_keeps_credential_floors(self, project_root):
        """profile: none no longer switches the built-in sensitive entries off: they are floors (C2)."""
        config._cached_config = NuhConfig(profile="none")
        sensitive_paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "~/.ssh/config", "content": "host"})
        assert (d["decision"], d["_floor"]) == ("block", "credential_paths")
        assert handle_read({"file_path": "~/.aws/credentials"})["_floor"] == "credential_paths"

    def test_hook_self_protection_immutable_under_none(self, project_root):
        """Hook self-protection is immutable even under profile: none."""
        config._cached_config = NuhConfig(profile="none")
        sensitive_paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "~/.claude/hooks/guard.py", "content": "x"})
        assert d["decision"] == "block"

    def test_write_outside_hint(self, project_root):
        """Outside-project ask includes hint suggesting nuh trust."""
        d = handle_write({"file_path": "/tmp/foo/bar.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "_hint" in d
        assert "nuh trust" in d["_hint"]

    def test_write_nested_trusted(self, project_root):
        """Nested path inside trusted directory is allowed."""
        config._cached_config = NuhConfig(trusted_paths=["/tmp"])
        d = handle_write({"file_path": "/tmp/deep/nested/file.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_no_project_root(self):
        """No project root → ask with hint."""
        paths.set_project_root(None)
        d = handle_write({"file_path": "/tmp/file.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "no git root" in d["reason"]


# --- Delivery: an allow is sent unless the tool's tools word is defer ---


class TestAllowDelivery:
    """hook.main() hands an allow to the agent for approve, check and decide; defer leaves it silent."""

    def setup_method(self):
        config._cached_config = NuhConfig()

    def teardown_method(self):
        config._cached_config = None

    def _run_hook(self, tool_name: str, tool_input: dict) -> str:
        """Run hook.main() with mocked stdin/stdout and return stdout output."""
        import io
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        stdin_mock = io.StringIO(payload)
        stdout_mock = io.StringIO()
        import sys
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = stdin_mock, stdout_mock
        try:
            from nuh.hook import main
            main()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
        return stdout_mock.getvalue()

    def test_default_bash_allow_emits_json(self):
        """Bash is decide by default: its allow is sent."""
        output = self._run_hook("Bash", {"command": "ls"})
        assert output.strip(), "Expected JSON output for an explicit allow"
        result = json.loads(output)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_defer_emits_nothing(self):
        config._cached_config = NuhConfig(tools={"Bash": "defer"})
        output = self._run_hook("Bash", {"command": "ls"})
        assert not output.strip(), "Expected no output for a deferred tool's allow"

    def test_default_glob_allow_is_sent(self):
        """Glob is approve by default."""
        output = self._run_hook("Glob", {"pattern": "*.py"})
        assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_unlisted_tool_allow_is_silent(self):
        """A tool the config does not list is defer: its allow is left to Claude Code."""
        config._cached_config = NuhConfig(classify_global={"filesystem_read": ["mcp__notes__read"]})
        assert not self._run_hook("mcp__notes__read", {}).strip()

    @pytest.mark.parametrize("tool, tool_input", [("WebFetch", {"url": "https://example.com.", "prompt": "x"}),
                                                  ("WebSearch", {"query": "python docs"})])
    def test_unclassified_web_tool_asks(self, tool, tool_input):
        """nuh has no built-in type for WebFetch or WebSearch: unknown asks until a classify entry types it."""
        output = json.loads(self._run_hook(tool, tool_input))["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        assert f"unrecognized tool: {tool}" in output["permissionDecisionReason"]

    def test_glob_entry_sends_an_unlisted_tool_allow(self):
        config._cached_config = NuhConfig(classify_global={"filesystem_read": ["mcp__notes__read"]},
                                          tools={"mcp__notes__*": "approve"})
        output = self._run_hook("mcp__notes__read", {})
        assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_ask_decision_emits_regardless(self):
        """ASK decisions emit JSON whatever the word."""
        config._cached_config = NuhConfig(tools={"Bash": "defer"})
        output = self._run_hook("Bash", {"command": "rm -rf /"})
        assert output.strip(), "ASK/BLOCK should always emit"
        result = json.loads(output)
        assert result["hookSpecificOutput"]["permissionDecision"] in ("deny", "ask")

    def test_block_decision_emits_regardless(self):
        """BLOCK decisions emit JSON whatever the word."""
        config._cached_config = NuhConfig(tools={"Write": "defer"})
        output = self._run_hook("Write", {"file_path": "~/.ssh/id_rsa", "content": "key"})
        assert output.strip(), "BLOCK should always emit"
        result = json.loads(output)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
