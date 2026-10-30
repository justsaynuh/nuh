"""What ``nuh config show`` and ``nuh test`` tell you about how a call is handled."""

import argparse
import dataclasses

import pytest

from nuh import cli, cli_config, config, paths


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A global config in tmp_path and a project with no .nuh.yaml."""
    project = tmp_path / "project"
    project.mkdir()
    paths.set_project_root(str(project))
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(tmp_path / "config.yaml"))
    config.reset_config()
    yield
    paths.reset_project_root()
    config.reset_config()


def _global(tmp_path, text: str) -> None:
    (tmp_path / "config.yaml").write_text(text)
    config.reset_config()


def _show(capsys) -> dict[str, str]:
    cli.cmd_config(argparse.Namespace(config_command="show"))
    rows = capsys.readouterr().out.splitlines()[1:]
    shown = {}
    for row in rows:
        label, _, value = row.strip().partition(": ")
        shown[label.rstrip(":")] = value.strip()
    return shown


class TestConfigShow:
    def test_every_setting_has_a_line(self, capsys):
        shown = _show(capsys)
        for f in dataclasses.fields(config.NuhConfig):
            assert cli_config._KEYS.get(f.name, f.name) in shown, f.name

    def test_fallbacks_and_deadline_show_their_effective_values(self, tmp_path, capsys):
        _global(tmp_path, "llm:\n  on_uncertain: static\n  on_failure: static\nhook:\n  deadline_s: 7.5\n"
                          "  on_error: defer\n")
        shown = _show(capsys)
        assert shown["llm.on_uncertain"] == "static"
        assert shown["llm.on_failure"] == "static"
        assert shown["hook.deadline_s"] == "7.5"
        assert shown["hook.on_error"] == "defer"

    def test_unknown_keys_the_config_sets_are_listed(self, tmp_path, capsys):
        _global(tmp_path, "trust_project_config: true\nllm:\n  enabled: true\n"
                          "  decide:\n    url: http://127.0.0.1:8787\n")
        shown = _show(capsys)
        assert shown["unknown keys (ignored)"] == "['trust_project_config', 'llm.decide']"

    def test_defaults_are_shown_too(self, capsys):
        shown = _show(capsys)
        assert shown["llm.on_uncertain"] == "ask"
        assert shown["llm.on_failure"] == "ask"
        assert shown["hook.deadline_s"] == "20"
        assert shown["hook.on_error"] == "ask"
        assert shown["llm.trace_keep_days"] == "30"

    def test_trace_keep_days_zero_reads_as_keep_forever(self, tmp_path, capsys):
        _global(tmp_path, "llm:\n  trace_keep_days: 0\n")
        assert _show(capsys)["llm.trace_keep_days"] == "0 (keep forever)"


def _dry_run(capsys, tool: str, args: list[str], override: dict | None = None) -> str:
    import json
    from nuh.cli_dryrun import cmd_test
    cmd_test(argparse.Namespace(
        tool=tool, path=None, content=None, pattern=None,
        config=json.dumps(override) if override else None, args=args,
    ))
    return capsys.readouterr().out


def _delivery(out: str) -> str:
    rows = [r for r in out.splitlines() if r.startswith("Delivery:")]
    assert len(rows) <= 1, out
    return rows[0] if rows else ""


class TestDryRunDelivery:
    @pytest.mark.parametrize("tool,args", [("Bash", ["ls"]), ("Read", ["./README.md"]), ("Grep", ["."])])
    def test_allow_is_explicit_by_default(self, capsys, tool, args):
        out = _dry_run(capsys, tool, args)
        assert "ALLOW" in out
        assert _delivery(out).startswith("Delivery:") and "explicit allow" in _delivery(out)

    @pytest.mark.parametrize("tool,args", [("Bash", ["ls"]), ("Read", ["./README.md"])])
    def test_allow_is_silent_when_the_word_is_defer(self, capsys, tool, args):
        out = _dry_run(capsys, tool, args, {"tools": {tool: "defer"}})
        assert "ALLOW" in out
        assert _delivery(out).endswith(f"silent (tools.{tool}: defer): Claude Code's own permissions decide")

    def test_default_word_is_named(self, capsys):
        out = _dry_run(capsys, "Read", ["./README.md"])
        assert "explicit allow (tools.Read: approve)" in _delivery(out)

    def test_unlisted_tool_is_silent(self, capsys):
        out = _dry_run(capsys, "WebFetch", ["https://example.com."], {"classify": {"network_diagnostic": ["WebFetch"]}})
        assert "silent (tools.WebFetch: defer)" in _delivery(out)

    def test_glob_entry_names_its_pattern(self, capsys):
        out = _dry_run(capsys, "mcp__forge__get_me", [], {"tools": {"mcp__forge__*": "approve"}})
        assert "Word:       approve (config: mcp__forge__*)" in out

    def test_block_is_always_sent(self, capsys):
        out = _dry_run(capsys, "Bash", ["rm", "-rf", "/"], {"tools": {"Bash": "defer"}})
        assert "ALLOW" not in out
        assert _delivery(out).endswith("sent (blocks always reach the agent)")
