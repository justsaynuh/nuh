"""An internal error answers per hook.on_error and leaves a row in the decision log (hook_error.py)."""

import io
import json
import sys

import pytest

from nuh import cli, config, hook, trace
from nuh.config import _merge_configs

ERROR = "RuntimeError: classifier exploded on ls cmd-err"


@pytest.fixture
def broken_classifier(monkeypatch, project_root, hook_log):
    """Bash classification raises, and the log is in tmp."""
    def boom(command: str):
        raise RuntimeError(f"classifier exploded on {command}")
    monkeypatch.setattr(hook, "classify_command", boom)
    return hook_log


def _on_error(monkeypatch, value: str | None) -> None:
    hook_cfg = {} if value is None else {"hook": {"on_error": value}}
    monkeypatch.setattr(config, "_cached_config", _merge_configs(hook_cfg, {}))


def _run(monkeypatch, stdin: str) -> str:
    """What hook.main() prints for *stdin*; sys.stdout is restored afterwards for `nuh log`."""
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    real_stdout, sys.stdout = sys.stdout, out
    try:
        hook.main()
    finally:
        sys.stdout = real_stdout
    return out.getvalue()


def _answer(monkeypatch, stdin: str) -> dict:
    return json.loads(_run(monkeypatch, stdin))


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _bash(command: str = "ls cmd-err", **extra: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "toolu_err1",
                       **extra})


def test_default_asks_and_logs_the_ask(monkeypatch, broken_classifier):
    _on_error(monkeypatch, None)
    answer = _answer(monkeypatch, _bash())
    assert answer["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert answer["hookSpecificOutput"]["permissionDecisionReason"] == f"nuh? internal error: {ERROR}"
    assert answer["systemMessage"] == f"⚠️ **nuh?** Bash · internal error: {ERROR} · `#u_err1`"
    [row] = _rows(broken_classifier)
    assert (row["decision"], row["reason"]) == ("ask", f"internal error: {ERROR}")
    assert row["internal_error"] == {"error": ERROR, "on_error": "ask"}
    assert (row["tool"], row["tool_input"], row["tool_use_id"]) == ("Bash", {"command": "ls cmd-err"}, "toolu_err1")


def test_block_denies_and_nuh_log_blocks_shows_it(monkeypatch, capsys, broken_classifier):
    _on_error(monkeypatch, "block")
    answer = _answer(monkeypatch, _bash())
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "internal error — blocked for safety" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    [row] = _rows(broken_classifier)
    assert (row["decision"], row["reason"]) == ("block", f"nuh: internal error: {ERROR}")
    assert row["internal_error"] == {"error": ERROR, "on_error": "block"}
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["nuh", "log", "--blocks", "-v"])
    cli.main()
    shown = capsys.readouterr().out
    assert "ls cmd-err" in shown and ERROR in shown


def test_defer_sends_nothing_and_logs_a_labelled_allow(monkeypatch, broken_classifier):
    _on_error(monkeypatch, "defer")
    assert _run(monkeypatch, _bash()) == ""
    [row] = _rows(broken_classifier)
    assert row["decision"] == "allow"
    assert row["reason"] == f"internal error, deferred (hook.on_error: defer, no decision sent): {ERROR}"
    assert row["internal_error"] == {"error": ERROR, "on_error": "defer"}


@pytest.mark.parametrize("on_error, printed", [("ask", "deny"), ("block", "deny"), ("defer", None)])
def test_codex_gets_its_own_shape(monkeypatch, broken_classifier, project_root, on_error, printed):
    """Codex cannot ask: its ask is the review block it gets for any ask; defer prints nothing."""
    _on_error(monkeypatch, on_error)
    out = _run(monkeypatch, _bash(turn_id="turn-1", cwd=project_root))
    if printed is None:
        assert out == ""
    else:
        answer = json.loads(out)
        assert answer["hookSpecificOutput"]["permissionDecision"] == printed
        assert "systemMessage" not in answer
    assert _rows(broken_classifier)[0]["internal_error"]["on_error"] == on_error


def test_a_config_that_cannot_load_gives_the_default(monkeypatch, capsys, broken_classifier):
    """Configured to block, but the config is what broke: the error path asks without it."""
    _on_error(monkeypatch, "block")

    def broken_config():
        raise ValueError("config exploded")
    monkeypatch.setattr(config, "get_config", broken_config)
    answer = _answer(monkeypatch, _bash())
    assert answer["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "nuh: config: hook.on_error: config exploded; using ask" in capsys.readouterr().err
    assert _rows(broken_classifier)[0]["decision"] == "ask"


def test_unparsable_payload_logs_an_unknown_tool(monkeypatch, project_root, hook_log):
    _on_error(monkeypatch, None)
    answer = _answer(monkeypatch, "{not json")
    assert answer["hookSpecificOutput"]["permissionDecision"] == "ask"
    [row] = _rows(hook_log)
    assert (row["tool"], row["decision"]) == ("unknown", "ask")
    assert row["reason"].startswith("internal error: JSONDecodeError: ")


@pytest.mark.parametrize("on_error, printed", [("ask", "ask"), ("block", "deny")])
def test_the_answer_stands_when_logging_raises(monkeypatch, capsys, broken_classifier, on_error, printed):
    _on_error(monkeypatch, on_error)

    def failing_log(*args):
        raise OSError("log disk gone")
    monkeypatch.setattr(hook, "_log_hook_decision", failing_log)
    answer = _answer(monkeypatch, _bash())
    assert answer["hookSpecificOutput"]["permissionDecision"] == printed
    assert "nuh: log error: log disk gone" in capsys.readouterr().err
    assert not broken_classifier.exists()


def _single_answer(out: str) -> dict:
    """The one JSON object on stdout; json.loads rejects a second one ("Extra data")."""
    return json.loads(out)


def test_an_error_after_the_answer_prints_no_second_object(monkeypatch, capsys, project_root, hook_log):
    """The trace step raises after the block went out: stdout keeps that one block, the log records it."""
    _on_error(monkeypatch, None)

    def failing_finish(*args, **kwargs):
        raise RuntimeError("trace disk gone")
    monkeypatch.setattr(trace, "finish", failing_finish)
    answer = _single_answer(_run(monkeypatch, _bash("rm -rf /")))
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    err = capsys.readouterr().err
    assert "nuh: error: trace disk gone" in err
    assert "after the decision was sent; nothing more printed" in err
    [row] = _rows(hook_log)
    assert (row["decision"], row["floor"]) == ("block", "catastrophic")
    assert row["internal_error"] == {"error": "RuntimeError: trace disk gone", "after_answer": True}


class _FlushFails(io.StringIO):
    """A stdout that takes the write, then fails to flush it."""

    def flush(self):
        raise OSError("stdout closed")


def test_a_failed_flush_is_not_followed_by_the_error_answer(monkeypatch, project_root, hook_log):
    _on_error(monkeypatch, "block")
    out = _FlushFails()
    monkeypatch.setattr("sys.stdin", io.StringIO(_bash("rm -rf /")))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    answer = _single_answer(out.getvalue())
    assert "catastrophic" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    [row] = _rows(hook_log)
    assert row["internal_error"] == {"error": "OSError: stdout closed", "after_answer": True}


def test_an_unserializable_answer_still_gets_the_error_answer(monkeypatch, project_root, hook_log):
    """Serialization fails before stdout holds anything, so the on_error answer is the only object."""
    _on_error(monkeypatch, None)
    monkeypatch.setattr(hook, "_to_hook_output", lambda decision, agent: {"bad": object()})
    answer = _single_answer(_run(monkeypatch, _bash("rm -rf /")))
    assert answer["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert answer["hookSpecificOutput"]["permissionDecisionReason"].startswith("nuh? internal error: TypeError: ")
    [row] = _rows(hook_log)
    assert row["internal_error"]["on_error"] == "ask"


class TestSetting:
    def test_invalid_value_warns_and_asks(self, capsys):
        assert _merge_configs({"hook": {"on_error": "allow"}}, {}).hook_on_error == "ask"
        assert "hook.on_error must be one of ask, block, defer: 'allow'; using ask" in capsys.readouterr().err

    def test_a_project_cannot_set_it(self, capsys):
        cfg = _merge_configs({"hook": {"on_error": "block"}}, {"hook": {"on_error": "defer"}})
        assert cfg.hook_on_error == "block"
        assert "hook.on_error is global-only; ignored in .nuh.yaml" in capsys.readouterr().err
