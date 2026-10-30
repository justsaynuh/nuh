"""Codex hook protocol and shared-policy regression tests."""

import io
import json
import os
from pathlib import Path

import pytest

from nuh import config, hook, taxonomy
from nuh.config import NuhConfig


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NUH_AGENT", raising=False)
    config._cached_config = NuhConfig()
    monkeypatch.setattr(hook, "_log_hook_decision", lambda *args: None)


def invoke(monkeypatch: pytest.MonkeyPatch, payload: object) -> dict | None:
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", output)
    hook.main()
    return json.loads(output.getvalue()) if output.getvalue() else None


@pytest.mark.parametrize("codex,expected", [(False, "ask"), (True, "deny")])
def test_shared_unknown_policy_translation(
    monkeypatch: pytest.MonkeyPatch, codex: bool, expected: str,
) -> None:
    payload = {"tool_name": "UnknownTool", "tool_input": {}}
    if codex:
        payload["turn_id"] = "turn-1"
    result = invoke(monkeypatch, payload)
    assert result["hookSpecificOutput"]["permissionDecision"] == expected


# A malformed envelope is an internal error: hook.on_error (default ask) answers, and Codex's ask is a deny.
@pytest.mark.parametrize("payload,expected", [
    (None, "ask"), ([], "ask"), ({}, "ask"), ({"tool_name": 4}, "ask"),
    ({"tool_name": "Bash", "tool_input": []}, "ask"),
    ({"turn_id": None, "tool_name": "Bash", "tool_input": {"command": "ls"}}, "deny"),
    ({"turn_id": "t", "tool_name": "Bash", "tool_input": {}}, "deny"),
    ({"turn_id": "t", "tool_name": "Bash", "tool_input": {"command": 3}}, "deny"),
    ({"turn_id": "t", "tool_name": "Bash", "cwd": "relative"}, "deny"),
    ({"tool_name": "Bash", "hook_event_name": "PermissionRequest"}, "ask"),
])
def test_malformed_envelopes_are_never_allowed(monkeypatch: pytest.MonkeyPatch, payload: object,
                                               expected: str) -> None:
    result = invoke(monkeypatch, payload)
    assert result["hookSpecificOutput"]["permissionDecision"] == expected


def test_invalid_override_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUH_AGENT", "invalid")
    result = invoke(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert result["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_malformed_json_with_codex_override_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUH_AGENT", "codex")
    monkeypatch.setattr("sys.stdin", io.StringIO("not JSON"))
    output = io.StringIO()
    monkeypatch.setattr("sys.stdout", output)
    hook.main()
    result = json.loads(output.getvalue())
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("codex", [False, True])
def test_deferred_allow_remains_empty(monkeypatch: pytest.MonkeyPatch, codex: bool) -> None:
    config._cached_config = NuhConfig(tools={"Bash": "defer"})
    payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    if codex:
        payload["turn_id"] = "t"
    assert invoke(monkeypatch, payload) is None


def test_codex_cwd_used_and_restored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    initial = os.getcwd()
    observed = []

    def handler(tool_input: dict) -> dict:
        observed.append(os.getcwd())
        return {"decision": taxonomy.ALLOW}

    monkeypatch.setitem(hook.HANDLERS, "Bash", handler)
    result = invoke(monkeypatch, {
        "turn_id": "t", "cwd": str(tmp_path), "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    assert observed == [str(tmp_path)]
    assert os.getcwd() == initial
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_codex_handler_failure_denies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    initial = os.getcwd()
    def failing_handler(tool_input: dict) -> dict:
        raise RuntimeError("policy unavailable")

    monkeypatch.setitem(hook.HANDLERS, "Bash", failing_handler)
    result = invoke(monkeypatch, {
        "turn_id": "t", "cwd": str(tmp_path), "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "policy unavailable" in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert os.getcwd() == initial
