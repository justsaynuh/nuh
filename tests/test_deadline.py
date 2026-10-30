"""The hook's static time budget, and the hook timeout the installer writes."""

import argparse
import io
import json
from pathlib import Path

import pytest

from nuh import agents, cli_install, config, content, deadline, hook, llm, taxonomy
from nuh.config import NuhConfig, _merge_configs


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(deadline.time, "monotonic", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_budget_left_over():
    deadline.clear()
    yield
    deadline.clear()


# -- The budget itself --


def test_no_budget_outside_a_hook_call() -> None:
    assert deadline.remaining() is None
    assert not deadline.expired()
    deadline.check("anything")  # never raises without a budget


def test_budget_counts_down_and_check_raises(clock: _Clock) -> None:
    deadline.start(5.0, started_at=clock.now - 1.0)
    assert deadline.remaining() == pytest.approx(4.0)
    deadline.check("early")
    clock.now += 4.0
    assert deadline.expired()
    with pytest.raises(deadline.InspectionTimeout, match="late"):
        deadline.check("late")


def test_timeout_escapes_broad_exception_handlers(clock: _Clock) -> None:
    """A fallback written as `except Exception` must not swallow the timeout."""
    deadline.start(0.0)
    with pytest.raises(deadline.InspectionTimeout):
        try:
            deadline.check("stage")
        except Exception:  # the shape of nuh's config/parser fallbacks
            pytest.fail("InspectionTimeout was caught as an Exception")


def test_llm_time_is_not_charged_to_the_static_budget(clock: _Clock) -> None:
    deadline.start(5.0)
    clock.now += 2.0
    with deadline.suspended():
        clock.now += 300.0  # a slow provider within its own timeout
        assert deadline.remaining() == pytest.approx(3.0)
        assert not deadline.expired()
    assert deadline.remaining() == pytest.approx(3.0)
    clock.now += 3.0
    assert deadline.expired()


# -- Content scan --


def test_scan_out_of_time_asks_and_keeps_matches_so_far(clock: _Clock, monkeypatch) -> None:
    # Each check reads the clock once and the clock moves 1 s per read: the
    # two rm -rf patterns run, then the budget is gone.
    def tick() -> float:
        clock.now += 1.0
        return clock.now
    deadline.start(2.5, started_at=clock.now + 0.0)
    monkeypatch.setattr(deadline.time, "monotonic", tick)
    matches = content.scan_content("rm -rf /\nAKIAIOSFODNN7EXAMPLE")
    assert [(m.category, m.pattern_desc) for m in matches] == [
        ("destructive", "rm -rf"),
        (content.TIMEOUT_CATEGORY, deadline.TIMEOUT_REASON),
    ]
    assert matches[-1].policy == taxonomy.ASK


def test_write_with_no_time_left_asks(clock: _Clock, project_root: str) -> None:
    deadline.start(0.0)
    decision = hook.handle_write({"file_path": f"{project_root}/a.txt", "content": "hello"})
    assert decision["decision"] == taxonomy.ASK
    assert deadline.TIMEOUT_REASON in decision["reason"]


# -- Hook --


def _invoke(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict:
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(hook, "_log_hook_decision", lambda *args: None)
    monkeypatch.delenv("NUH_AGENT", raising=False)
    hook.main()
    return json.loads(output.getvalue())["hookSpecificOutput"]


def test_hook_out_of_time_asks_instead_of_allowing(monkeypatch: pytest.MonkeyPatch) -> None:
    config._cached_config = NuhConfig(hook_deadline_s=1e-9)
    out = _invoke(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert out["permissionDecision"] == "ask"
    assert deadline.TIMEOUT_REASON in out["permissionDecisionReason"]
    assert deadline.remaining() is None  # cleared for the next call


def test_timeout_after_a_block_still_blocks(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    deadline.start(5.0, started_at=clock.now)

    def handler(_tool_input: dict) -> dict:
        deadline.note("allow", "fine")
        deadline.note("block", "rm -rf ~ (filesystem_delete)")
        clock.now += 10.0
        deadline.check("Bash classification")
        raise AssertionError("unreachable")

    monkeypatch.setitem(hook.HANDLERS, "Bash", handler)
    out = hook._decide("Bash", {"command": "x"})
    assert out["decision"] == taxonomy.BLOCK
    assert out["reason"].startswith("rm -rf ~")
    assert deadline.TIMEOUT_REASON in out["reason"]


def test_block_noted_without_a_budget_is_ignored() -> None:
    deadline.note("block", "outside a hook call")
    assert deadline.blocked_reason() is None


def test_hook_within_budget_decides_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    config._cached_config = NuhConfig(hook_deadline_s=30)
    out = _invoke(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert out["permissionDecision"] == "allow"


def test_llm_review_runs_outside_the_budget(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider slower than the static budget still decides; its time is its own."""
    config._cached_config = NuhConfig(llm={"enabled": True, "providers": ["ollama"], "ollama": {}})
    seen: list[bool] = []

    def slow_review(*_args, **_kwargs) -> llm.LLMCallResult:
        clock.now += 300.0
        seen.append(any(m.category == content.TIMEOUT_CATEGORY for m in content.scan_content("x")))
        return llm.LLMCallResult(decision={"decision": "allow"},
                                 cascade=[llm.ProviderAttempt("ollama", "success", 300_000, "m")])

    monkeypatch.setattr(llm, "try_llm", slow_review)
    deadline.start(20.0)
    decision = hook.handle_bash({"command": "some-unknown-tool --flag"})
    assert seen == [False]
    assert decision["decision"] == taxonomy.ALLOW


def test_llm_review_is_not_started_when_the_budget_is_gone(clock: _Clock, monkeypatch) -> None:
    config._cached_config = NuhConfig(llm={"enabled": True, "providers": ["ollama"], "ollama": {}})
    monkeypatch.setattr(llm, "try_llm", lambda *_a, **_k: pytest.fail("LLM called after timeout"))
    result = hook.classify_command("some-unknown-tool --flag")
    deadline.start(0.0)
    with pytest.raises(deadline.InspectionTimeout):
        hook._try_llm(result)


# -- Config --


@pytest.mark.parametrize("raw, expected", [
    (None, deadline.DEFAULT_DEADLINE_S),
    (5, 5.0),
    (2.5, 2.5),
    (0, deadline.DEFAULT_DEADLINE_S),
    (-1, deadline.DEFAULT_DEADLINE_S),
    ("10", deadline.DEFAULT_DEADLINE_S),
    (True, deadline.DEFAULT_DEADLINE_S),
])
def test_deadline_config(raw: object, expected: float, capsys) -> None:
    hook_section = {} if raw is None else {"deadline_s": raw}
    assert _merge_configs({"hook": hook_section}, {}).hook_deadline_s == expected
    warned = "hook.deadline_s must be a positive number" in capsys.readouterr().err
    assert warned is (raw is not None and expected == deadline.DEFAULT_DEADLINE_S)


def test_project_config_cannot_set_the_deadline() -> None:
    assert _merge_configs({}, {"hook": {"deadline_s": 999}}).hook_deadline_s == deadline.DEFAULT_DEADLINE_S


# -- Hook timeout written by the installer --


_OLLAMA_30_THEN_OPENROUTER = {"enabled": True, "providers": ["ollama", "openrouter"],
                              "ollama": {"timeout": 30}, "openrouter": {"model": "m"}}


@pytest.mark.parametrize("llm_config, expected", [
    ({}, 0.0),
    ({"enabled": False, "providers": ["ollama"], "ollama": {"timeout": 30}}, 0.0),
    ({"enabled": True, "providers": ["ollama"], "ollama": {"timeout": 30}}, 30.0),
    (_OLLAMA_30_THEN_OPENROUTER, 40.0),  # openrouter's default is 10
    ({"enabled": True, "providers": ["ollama", "anthropic", "openai"],  # openai: no section, skipped
      "ollama": {"model": "m"}, "anthropic": {"timeout": "x"}}, 20.0),  # invalid timeout: default
])
def test_cascade_timeout(llm_config: dict, expected: float) -> None:
    assert llm.cascade_timeout_s(llm_config) == expected


@pytest.mark.parametrize("deadline_s, llm_s, expected", [
    (20, 0, 60),     # floor
    (20, 30, 60),    # 20 + 30 + 10
    (20, 40, 70),
    (20.5, 40, 71),  # rounded up
])
def test_install_timeout(deadline_s: float, llm_s: float, expected: int) -> None:
    assert deadline.install_timeout_s(deadline_s, llm_s) == expected


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {agents.CLAUDE: tmp_path / ".claude/settings.json",
             agents.CODEX: tmp_path / ".codex/hooks.json"}
    monkeypatch.setattr(agents, "AGENT_SETTINGS", paths)
    monkeypatch.setattr(cli_install, "_HOOK_SCRIPT", tmp_path / ".claude/hooks/nuh_guard.py")
    for path in paths.values():
        path.parent.mkdir(parents=True)
    return paths


@pytest.mark.parametrize("agent, on_failure", [(agents.CLAUDE, "block"), (agents.CODEX, None)])
def test_install_writes_timeout_from_config(settings, agent: str, on_failure: str | None, capsys) -> None:
    config._cached_config = NuhConfig(hook_deadline_s=25, llm=_OLLAMA_30_THEN_OPENROUTER)
    cli_install.cmd_install(argparse.Namespace(agent=agent))
    entries = json.loads(settings[agent].read_text())["hooks"]["PreToolUse"]
    for entry in entries:
        (hook_cfg,) = entry["hooks"]
        assert hook_cfg["timeout"] == 75  # 25 + 30 + 10 + 10
        assert hook_cfg.get("onFailure") == on_failure
    assert "rerun `nuh update`" in capsys.readouterr().out


def test_update_recomputes_timeout(settings) -> None:
    cli_install.cmd_install(argparse.Namespace(agent=agents.CLAUDE))
    config._cached_config = NuhConfig(hook_deadline_s=50)
    cli_install.cmd_update(argparse.Namespace(agent=agents.CLAUDE))
    entries = json.loads(settings[agents.CLAUDE].read_text())["hooks"]["PreToolUse"]
    assert {entry["hooks"][0]["timeout"] for entry in entries} == {60}
    config._cached_config = NuhConfig(hook_deadline_s=55)
    cli_install.cmd_update(argparse.Namespace(agent=agents.CLAUDE))
    entries = json.loads(settings[agents.CLAUDE].read_text())["hooks"]["PreToolUse"]
    assert {entry["hooks"][0]["timeout"] for entry in entries} == {65}


def test_session_settings_fail_closed() -> None:
    for entry in cli_install._build_hooks_settings()["hooks"]["PreToolUse"]:
        assert entry["hooks"][0]["onFailure"] == "block"
        assert entry["hooks"][0]["timeout"] >= deadline.INSTALL_TIMEOUT_FLOOR_S
