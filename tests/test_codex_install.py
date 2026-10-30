"""Per-agent registration preserves unrelated settings and synchronous enforcement."""

import argparse
import json
from pathlib import Path
import stat

import pytest

from nuh import agents, cli_install


@pytest.fixture
def settings(tmp_path: Path, monkeypatch):
    paths = {agents.CLAUDE: tmp_path / ".claude/settings.json",
             agents.CODEX: tmp_path / ".codex/hooks.json"}
    monkeypatch.setattr(agents, "AGENT_SETTINGS", paths)
    monkeypatch.setattr(cli_install, "_HOOK_SCRIPT", tmp_path / ".claude/hooks/nuh_guard.py")
    for path in paths.values():
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"unrelated": {"keep": True}, "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "other guard"},
                {"type": "command", "command": "old nuh_guard.py", "async": True},
            ]}],
        }}))
    return paths


@pytest.mark.parametrize("agent", [agents.CLAUDE, agents.CODEX])
def test_install_and_update_preserve_settings(settings, agent: str) -> None:
    other = agents.CODEX if agent == agents.CLAUDE else agents.CLAUDE
    untouched = settings[other].read_bytes()
    cli_install.cmd_install(argparse.Namespace(agent=agent))
    installed = settings[agent].read_bytes()
    cli_install.cmd_update(argparse.Namespace(agent=agent))
    assert settings[agent].read_bytes() == installed
    assert settings[other].read_bytes() == untouched
    data = json.loads(installed)
    assert data["unrelated"] == {"keep": True}
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "notify"
    entries = data["hooks"]["PreToolUse"]
    assert entries[0]["hooks"] == [{"type": "command", "command": "other guard"}]
    owned = [entry for entry in entries if cli_install._is_nuh_hook(entry)]
    assert [entry["matcher"] for entry in owned] == agents.AGENT_TOOL_MATCHERS[agent]
    expected = {"type": "command", "command": cli_install._hook_command(agent), "timeout": 60}
    if agent == agents.CLAUDE:
        expected["onFailure"] = "block"  # Codex hooks.json has no failure setting
    for entry in owned:
        assert entry["hooks"] == [expected]
    script = cli_install._hook_script(agent)
    assert stat.S_IMODE(script.stat().st_mode) == 0o444
    assert "def run()" in script.read_text()


def test_codex_uninstall_keeps_claude_and_other_commands(settings) -> None:
    cli_install.cmd_install(argparse.Namespace(agent="all"))
    claude_settings = settings[agents.CLAUDE].read_bytes()
    claude_guard = cli_install._hook_script().read_bytes()
    cli_install.cmd_uninstall(argparse.Namespace(agent=agents.CODEX))
    assert not cli_install._hook_script(agents.CODEX).exists()
    assert cli_install._hook_script().read_bytes() == claude_guard
    assert settings[agents.CLAUDE].read_bytes() == claude_settings
    data = json.loads(settings[agents.CODEX].read_text())
    assert data["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "other guard"}]}]
    assert "Stop" in data["hooks"]


def test_default_install_still_targets_claude(settings) -> None:
    codex = settings[agents.CODEX].read_bytes()
    cli_install.cmd_install(argparse.Namespace())
    assert cli_install._hook_script().exists()
    assert not cli_install._hook_script(agents.CODEX).exists()
    assert settings[agents.CODEX].read_bytes() == codex


def test_uninstall_all_removes_both_guards(settings) -> None:
    cli_install.cmd_install(argparse.Namespace(agent="all"))
    cli_install.cmd_uninstall(argparse.Namespace(agent="all"))
    assert not cli_install._hook_script().exists()
    assert not cli_install._hook_script(agents.CODEX).exists()


def test_codex_update_preserves_read_network_and_custom_matchers(settings) -> None:
    original = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
                "Glob", "Grep", "mcp__.*", "WebFetch", "WebSearch", "CustomNetworkTool"]
    data = json.loads(settings[agents.CODEX].read_text())
    data["hooks"]["PreToolUse"] = [
        {"matcher": name, "hooks": [{"type": "command", "command": "old nuh_guard.py"}]}
        for name in original
    ]
    settings[agents.CODEX].write_text(json.dumps(data))
    cli_install._write_hook_script(agents.CODEX)
    for _ in range(2):
        cli_install.cmd_update(argparse.Namespace(agent=agents.CODEX))
        entries = json.loads(settings[agents.CODEX].read_text())["hooks"]["PreToolUse"]
        assert {entry["matcher"] for entry in entries} == set(original) | {"apply_patch"}
        assert len(entries) == len(original) + 1
        assert all(entry["hooks"] == [{"type": "command", "command": cli_install._hook_command(agents.CODEX),
                                       "timeout": 60}]
                   for entry in entries)


def test_claude_update_adds_web_tools_to_an_older_install(settings) -> None:
    """An install from before WebFetch and WebSearch were guarded gains both on `nuh update`."""
    older = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "mcp__.*"]
    data = json.loads(settings[agents.CLAUDE].read_text())
    data["hooks"]["PreToolUse"] = [
        {"matcher": name, "hooks": [{"type": "command", "command": "old nuh_guard.py"}]} for name in older
    ]
    settings[agents.CLAUDE].write_text(json.dumps(data))
    cli_install._write_hook_script(agents.CLAUDE)
    cli_install.cmd_update(argparse.Namespace(agent=agents.CLAUDE))
    entries = json.loads(settings[agents.CLAUDE].read_text())["hooks"]["PreToolUse"]
    assert [entry["matcher"] for entry in entries] == older + ["WebFetch", "WebSearch"]


def test_claude_session_settings_guard_web_tools() -> None:
    """`nuh claude` (no install) sends WebFetch and WebSearch to the hook too."""
    matchers = [entry["matcher"] for entry in cli_install._build_hooks_settings()["hooks"]["PreToolUse"]]
    assert "WebFetch" in matchers and "WebSearch" in matchers
