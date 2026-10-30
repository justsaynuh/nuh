"""The LLM cascade shares one wall-clock budget cut from the hook timeout (cascade_budget.py).

Claude Code kills a hook at its timeout and, with onFailure: block, blocks
the call with nothing in nuh's log. These tests run the hook with providers
that hang until their request timeout, on a budget shrunk to about 1.5 s.
"""

import io
import json
import re
import sys
import time

import pytest

from nuh import agents, cascade_budget, cli_install, config, deadline, hook, llm_providers, log

BUDGET_S = 1.5  # what the cascade may use in these tests


@pytest.fixture
def hanging(monkeypatch, tmp_path):
    """Providers ollama and openai that hang until their request timeout; returns the timeouts each got."""
    seen: dict[str, list[float]] = {"ollama": [], "openai": []}

    def provider(name: str, fails_fast: bool = False):
        def call(cfg, _prompt):
            seen[name].append(cfg["timeout"])
            if not fails_fast:
                time.sleep(cfg["timeout"])
            raise TimeoutError("timed out")
        return call

    def install(*, first_fails_fast: bool = False) -> dict[str, list[float]]:
        monkeypatch.setattr(log, "LOG_PATH", str(tmp_path / "nuh.log"))  # this test's entries only
        monkeypatch.setitem(llm_providers._PROVIDERS, "ollama", provider("ollama", first_fails_fast))
        monkeypatch.setitem(llm_providers._PROVIDERS, "openai", provider("openai"))
        config._cached_config = config._merge_configs({"llm": {
            "enabled": True, "providers": ["ollama", "openai"],
            "ollama": {"timeout": 30}, "openai": {"timeout": 30, "key_env": "NUH_TEST_NO_KEY"}}}, {})
        monkeypatch.setenv(cascade_budget.ENV_TIMEOUT, str(cascade_budget.MARGIN_S + BUDGET_S))
        monkeypatch.delenv(cascade_budget.ENV_STARTED, raising=False)
        return seen

    return install


def _run_hook(monkeypatch, capsys, command: str = "frobnicate --all") -> tuple[dict, float]:
    """Run hook.main on a Bash call the LLM reviews; (emitted output, wall seconds)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "Bash", "tool_input": {"command": command}, "hook_event_name": "PreToolUse"})))
    t0 = time.monotonic()
    hook.main()
    elapsed = time.monotonic() - t0
    return json.loads(capsys.readouterr().out), elapsed


def _log_entries() -> list[dict]:
    with open(log.LOG_PATH) as f:
        return [json.loads(line) for line in f]


class TestThroughTheHook:
    def test_budget_out_skips_the_next_provider_and_logs(self, monkeypatch, capsys, hanging, project_root):
        seen = hanging()
        out, elapsed = _run_hook(monkeypatch, capsys)
        assert elapsed < BUDGET_S + 0.4  # answered inside the budget, not after 60 s of providers
        assert len(seen["ollama"]) == 1 and seen["ollama"][0] <= BUDGET_S
        assert seen["openai"] == []  # skipped: less than MIN_PROVIDER_S left
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        [entry] = _log_entries()
        assert entry["decision"] == "ask"
        assert "time budget ran out" in json.dumps(entry)

    def test_a_later_provider_gets_only_what_is_left(self, monkeypatch, capsys, hanging, project_root):
        seen = hanging(first_fails_fast=True)
        _out, elapsed = _run_hook(monkeypatch, capsys)
        assert elapsed < BUDGET_S + 0.4
        assert seen["ollama"][0] <= BUDGET_S
        assert cascade_budget.MIN_PROVIDER_S <= seen["openai"][0] <= BUDGET_S  # capped from 30
        [entry] = _log_entries()
        assert "time budget ran out" not in json.dumps(entry)  # both were asked; both timed out

    def test_budget_cleared_after_the_hook(self, monkeypatch, capsys, hanging, project_root):
        hanging(first_fails_fast=True)
        _run_hook(monkeypatch, capsys)
        assert cascade_budget.remaining() is None
        assert cascade_budget.capped(30.0) == 30.0  # the CLI and tests run without a budget


class TestHookTimeout:
    def test_guard_value_wins(self, monkeypatch):
        monkeypatch.setenv(cascade_budget.ENV_TIMEOUT, "90")
        assert cascade_budget.hook_timeout_s() == 90.0

    @pytest.mark.parametrize("raw", ["", "soon", "-5", "0"])
    def test_without_a_usable_value_the_smallest_install_timeout(self, monkeypatch, capsys, raw):
        monkeypatch.setenv(cascade_budget.ENV_TIMEOUT, raw)
        assert cascade_budget.hook_timeout_s() == deadline.INSTALL_TIMEOUT_FLOOR_S
        assert ("must be a positive number" in capsys.readouterr().err) == bool(raw)

    def test_the_guard_start_mark_counts(self, monkeypatch):
        monkeypatch.setenv(cascade_budget.ENV_TIMEOUT, "60")
        now = time.monotonic()
        monkeypatch.setenv(cascade_budget.ENV_STARTED, repr(now - 3.0))  # 3 s of imports before main()
        cascade_budget.start(now)
        try:
            expected = 60 - cascade_budget.MARGIN_S - 3.0
            assert expected - 0.5 < cascade_budget.remaining() <= expected
        finally:
            cascade_budget.clear()

    def test_a_stale_start_mark_is_ignored(self, monkeypatch):
        monkeypatch.setenv(cascade_budget.ENV_TIMEOUT, "60")
        now = time.monotonic()
        monkeypatch.setenv(cascade_budget.ENV_STARTED, repr(now - 3600.0))
        cascade_budget.start(now)
        try:
            assert cascade_budget.remaining() > 60 - cascade_budget.MARGIN_S - 0.5
        finally:
            cascade_budget.clear()


def test_install_puts_the_settings_timeout_in_the_hook_script(tmp_path, monkeypatch):
    """The script carries exactly the timeout its settings entry was written with."""
    monkeypatch.setattr(cli_install, "_HOOK_SCRIPT", tmp_path / "nuh_guard.py")
    monkeypatch.setitem(agents.AGENT_SETTINGS, agents.CLAUDE, tmp_path / "settings.json")
    config._cached_config = config._merge_configs({"llm": {
        "enabled": True, "providers": ["ollama", "openai"], "ollama": {"timeout": 30}, "openai": {"timeout": 45}}}, {})
    cli_install._write_hook_script()
    cli_install._install_for_agent(agents.CLAUDE)
    settings = json.loads((tmp_path / "settings.json").read_text())
    timeouts = {h["timeout"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]}
    script = re.search(r"^_NUH_HOOK_TIMEOUT_S = (\d+)$", (tmp_path / "nuh_guard.py").read_text(), re.M)
    assert timeouts == {int(script.group(1))} == {deadline.install_timeout_s(config._cached_config.hook_deadline_s,
                                                                            75.0)}
