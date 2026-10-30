"""``llm.self_heal`` from the hook's side (llm_heal.py): which failed attempts start a worker; none is waited on.

The provider is a stub Ollama host on 127.0.0.1 (tests/heal_stub.py) whose
model takes ``load_s`` to answer, longer than the provider's timeout: a
cold load. The real worker runs; HOME is this test's, so its state lands
under it.
"""

import json
import os
import subprocess
import sys
import time

import pytest

from nuh import cli, config, llm, llm_heal, llm_heal_worker
from nuh.config import _merge_configs
from tests import heal_stub

SPAWN = llm_heal._spawn  # the real one, taken before the conftest replaces it per test
MODEL = "nimble:9b"
TIMEOUT_S = 0.5


@pytest.fixture
def home(tmp_path, monkeypatch) -> str:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return str(home)


@pytest.fixture
def workers(home, monkeypatch):
    """Every worker the hook starts (the real ones), waited for at the end of the test."""
    started: list[subprocess.Popen] = []

    def spawn(job: bytes, directory: str) -> subprocess.Popen:
        started.append(SPAWN(job, directory))
        return started[-1]

    monkeypatch.setattr(llm_heal, "_spawn", spawn)
    yield started
    for proc in started:
        proc.wait(timeout=30)


@pytest.fixture
def host():
    with heal_stub.serve() as h:
        h.listed, h.load_s = [MODEL], 1.0
        yield h


def _llm(url: str, provider: str = "ollama", **extra: object) -> dict:
    path = "" if provider == "system1" else "/api/chat"
    return {"enabled": True, "providers": [provider],
            provider: {"url": url + path, "model": MODEL, "timeout": TIMEOUT_S}, **extra}


def _review(llm_config: dict) -> llm.LLMCallResult:
    return llm.try_llm_generic("mcp__x__y", {"q": 1}, {"decision": "ask", "reason": "unknown tool"}, llm_config)


def _log(home: str) -> list[dict]:
    return llm_heal_worker.read_log(os.path.join(home, ".local", "state", "nuh"))


@pytest.mark.parametrize("provider, path", [("ollama", "/api/chat"), ("system1", "/v1/systemone")])
def test_a_timeout_starts_one_worker_that_loads_the_model(host, workers, home, provider, path):
    result = _review(_llm(host.url, provider))
    assert result.failed and "timeout" in result.cascade[0].error.lower()
    assert len(workers) == 1
    workers[0].wait(timeout=30)
    [record] = _log(home)
    assert (record["outcome"], record["provider"], record["model"]) == ("healed", provider, MODEL)
    sent, replayed = host.posts
    assert replayed["path"] == sent["path"] == path and replayed["body"] == sent["body"]


def test_a_second_timeout_within_the_cooldown_starts_no_worker(host, workers, home):
    _review(_llm(host.url))
    workers[0].wait(timeout=30)
    host.loaded = []
    _review(_llm(host.url))
    assert len(workers) == 1 and len(_log(home)) == 1


def test_self_heal_false_starts_no_worker(host, workers, home):
    result = _review(_llm(host.url, self_heal=False))
    assert result.failed and workers == []
    assert not os.path.exists(os.path.join(home, ".local"))


def test_a_failure_that_is_not_a_timeout_starts_no_worker(host, workers):
    host.load_s, host.post_status = 0.0, 500
    result = _review(_llm(host.url))
    assert result.failed and "500" in result.cascade[0].error and workers == []


def test_the_next_provider_is_asked_without_waiting_for_the_worker(host, workers):
    llm_config = _llm(host.url)
    llm_config["providers"] = ["ollama", "system1"]
    llm_config["system1"] = {"url": host.url, "model": "missing:1b", "timeout": TIMEOUT_S}
    started = time.monotonic()
    result = _review(llm_config)
    elapsed = time.monotonic() - started
    assert [a.provider for a in result.cascade] == ["ollama", "system1"]
    assert len(workers) == 2 and elapsed < 2 * TIMEOUT_S + 0.5  # not the 1 s load of either replay


def test_the_hook_process_exits_before_the_heal_finishes(host, home, tmp_path):
    """Run as Claude Code does: stdout and stderr are pipes read to their end; a worker holding them holds this."""
    host.load_s = 4.0
    config_dir = os.path.join(home, ".config", "nuh")
    os.makedirs(config_dir)
    with open(os.path.join(config_dir, "config.yaml"), "w") as f:
        json.dump({"llm": _llm(host.url)}, f)  # JSON is YAML
    project = tmp_path / "project"
    project.mkdir()
    payload = {"tool_name": "Bash", "tool_input": {"command": "somethingunknown123 --flag"}, "session_id": "s",
               "tool_use_id": "toolu_heal", "cwd": str(project), "hook_event_name": "PreToolUse"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("NUH_")}
    env["HOME"] = home
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "nuh.hook"], input=json.dumps(payload), capture_output=True,
                          text=True, env=env, cwd=str(project), timeout=30)
    elapsed = time.monotonic() - started
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert elapsed < host.load_s - 1.0
    assert _log(home) == []  # the worker is still loading
    deadline = time.monotonic() + 20
    while not _log(home) and time.monotonic() < deadline:
        time.sleep(0.1)
    [record] = _log(home)
    assert record["outcome"] == "healed" and record["load_ms"] >= 4000


def test_config_show_and_parse(capsys):
    cfg = _merge_configs({"llm": {"self_heal": "yes"}}, {})
    assert cfg.llm_self_heal is True
    assert "llm.self_heal must be true or false, got 'yes'; using true" in capsys.readouterr().err
    config._cached_config = _merge_configs({"llm": {"self_heal": False}}, {})
    cli.cmd_config(type("Args", (), {"config_command": "show"})())
    assert [line.split() for line in capsys.readouterr().out.splitlines() if "llm.self_heal" in line] == [
        ["llm.self_heal:", "false"]]


def test_heal_log_shows_each_run(home, capsys, monkeypatch):
    state = os.path.join(home, ".local", "state", "nuh")
    llm_heal_worker.append_log(state, {"ts": "2026-10-23T08:00:00+00:00", "provider": "system1", "model": MODEL,
                                       "host": "https://s1.example.", "outcome": "healed", "load_ms": 41200,
                                       "elapsed_ms": 41500, "error": ""})
    llm_heal_worker.append_log(state, {"ts": "2026-10-23T08:01:00+00:00", "provider": "ollama", "model": MODEL,
                                       "host": "https://o.example.", "outcome": "down", "elapsed_ms": 3,
                                       "error": "URLError: refused"})
    monkeypatch.setattr(sys, "argv", ["nuh", "heal-log"])
    cli.main()
    first, second = capsys.readouterr().out.splitlines()
    assert "system1" in first and "healed: loaded the model" in first and "load 41.2 s" in first
    assert "down: server down" in second and "error: URLError: refused" in second
    monkeypatch.setattr(sys, "argv", ["nuh", "heal-log", "-n", "1", "--json"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["outcome"] == "down"
