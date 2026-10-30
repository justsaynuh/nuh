"""What ``nuh config show`` and ``nuh test`` say about gating."""

import argparse
import json

import pytest

from nuh import cli, config, paths
from nuh.cli_dryrun import cmd_test


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A global config in tmp_path and a project with no .nuh.yaml."""
    project = tmp_path / "project"
    project.mkdir()
    paths.set_project_root(str(project))
    monkeypatch.chdir(project)
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(tmp_path / "config.yaml"))
    config.reset_config()
    yield
    paths.reset_project_root()
    config.reset_config()


def _global(tmp_path, text: str) -> None:
    (tmp_path / "config.yaml").write_text(text)
    config.reset_config()


def _show(capsys) -> list[str]:
    cli.cmd_config(argparse.Namespace(config_command="show"))
    return capsys.readouterr().out.splitlines()


def _dry_run(capsys, tool: str, args: list[str], override: dict | None = None, **extra: object) -> list[str]:
    cmd_test(argparse.Namespace(tool=tool, path=None, content=extra.get("content"), pattern=None,
                                config=json.dumps(override) if override else None, args=args))
    return capsys.readouterr().out.splitlines()


class TestConfigShow:
    def test_tools_table_names_each_word_and_its_source(self, tmp_path, capsys):
        _global(tmp_path, "tools:\n  mcp__forge__*: approve\n  WebFetch: check\n  Edit: approve\n")
        rows = _show(capsys)
        table = rows[rows.index("  tools:") + 1:rows.index("  human_only:")]
        assert [row.split() for row in table] == [
            ["Bash", "decide", "(default)"], ["Write", "decide", "(default)"], ["Edit", "approve", "(config)"],
            ["Read", "approve", "(default)"], ["Glob", "approve", "(default)"], ["Grep", "approve", "(default)"],
            ["MultiEdit", "approve", "(follows", "Edit)"], ["NotebookEdit", "approve", "(follows", "Edit)"],
            ["apply_patch", "approve", "(follows", "Edit)"], ["mcp__forge__*", "approve", "(config)"],
            ["WebFetch", "check", "(config)"], ["any", "other", "tool", "defer", "(default)"]]

    def test_human_only_lists_values_and_sources(self, tmp_path, capsys):
        _global(tmp_path, "human_only:\n  git_remote_write: true\n  sensitive_paths: false\n")
        rows = _show(capsys)
        table = {row.split()[0]: row.split()[1:] for row in rows[rows.index("  human_only:") + 1:]
                 if row.startswith("    ")}
        assert table["git_remote_write"] == ["true", "(config)"]
        assert table["sensitive_paths"] == ["false", "(config)"]
        assert table["credential_paths"] == ["true", "(default)"]
        assert table["content_secret"] == ["true", "(default)"]
        assert "any other action type" in "\n".join(rows)

    def test_old_gating_keys_are_unknown(self, tmp_path, capsys):
        _global(tmp_path, "active_allow: [Bash]\nfloor: {git_exec: review}\nllm:\n  authority: {Bash: raise}\n")
        rows = _show(capsys)
        unknown = next(row for row in rows if row.strip().startswith("unknown keys (ignored):"))
        assert unknown.split(":", 1)[1].strip() == "['active_allow', 'floor', 'llm.authority']"


class TestDryRun:
    def test_an_unlisted_tool(self, capsys):
        rows = _dry_run(capsys, "WebFetch", ["https://example.com."],
                        {"classify": {"network_diagnostic": ["WebFetch"]}})
        assert "Word:       defer (not listed)" in rows
        assert "LLM call:   not called: tools.WebFetch: defer never calls it" in rows
        assert "Delivery:   silent (tools.WebFetch: defer): Claude Code's own permissions decide" in rows

    def test_check_with_the_llm_off(self, capsys):
        rows = _dry_run(capsys, "WebFetch", ["https://example.com."],
                        {"classify": {"network_diagnostic": ["WebFetch"]}, "tools": {"WebFetch": "check"}})
        assert "Word:       check (config)" in rows
        assert ("LLM call:   not called: llm.enabled is off (tools.WebFetch: check would send this allow)"
                in rows)
        assert "Delivery:   explicit allow (tools.WebFetch: check)" in rows

    def test_a_floored_bash_ask_names_its_human_only_key(self, capsys):
        rows = _dry_run(capsys, "Bash", ["nuh allow lang_exec"])
        assert "Human-only:  nuh_admin" in rows
        assert "LLM call:    not called: cannot change outcome (human_only: nuh_admin)" in rows
        assert "Delivery:    sent (asks always reach the agent)" in rows

    def test_a_bash_ask_the_llm_may_decide(self, capsys):
        rows = _dry_run(capsys, "Bash", ["frobnicate123"])
        assert "Human-only:  none (keys behind the ask: unknown)" in rows
        assert "LLM call:    not called: llm.enabled is off (tools.Bash: decide would send this ask)" in rows

    def test_a_write_ask_shows_held_and_free_keys(self, capsys, tmp_path):
        rows = _dry_run(capsys, "Write", [str(tmp_path / "out" / "k.txt")],
                        content="-----BEGIN PRIVATE KEY-----\nabc\n")
        assert "Human-only: content_secret (not human-only: filesystem_write)" in rows
        assert "LLM call:   not called: cannot change outcome (human_only: content_secret)" in rows

    def test_a_glob_entry_reaches_an_mcp_tool(self, capsys):
        rows = _dry_run(capsys, "mcp__forge__create_tag", ['{"tag": "v1"}'],
                        {"classify": {"git_remote_write": ["mcp__forge__create_tag"]},
                         "tools": {"mcp__forge__*": "approve"}})
        assert 'Input:    {"tag": "v1"}' in rows
        assert "Word:       approve (config: mcp__forge__*)" in rows
        assert "Human-only: none (keys behind the ask: git_remote_write)" in rows
        assert "LLM call:   not called: tools.mcp__forge__create_tag: approve never calls it" in rows
