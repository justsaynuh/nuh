"""Execute the exact rendered installation shim in a separate interpreter."""

import json
import io
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from nuh.cli_install import _SHIM_TEMPLATE


def response(decision: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision,
    }})


@pytest.fixture
def guard(tmp_path: Path):
    script = tmp_path / "nuh_guard.py"
    script.write_text(_SHIM_TEMPLATE.format(interpreter=sys.executable, hook_timeout_s=60))
    package = tmp_path / "nuh"
    package.mkdir()
    (package / "__init__.py").write_text("")

    def run(body: str, agent: str = "claude", raw: str | None = None,
            override: str | None = None, import_error: bool = False):
        if import_error:
            (package / "__init__.py").write_text("raise ImportError('package unavailable')")
        (package / "hook.py").write_text("import sys, json\ndef main():\n" +
                                         "\n".join("    " + line for line in body.splitlines()))
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}
        if agent == "codex":
            payload["turn_id"] = "turn-1"
        env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(tmp_path)}
        env.pop("NUH_AGENT", None)
        if override is not None:
            env["NUH_AGENT"] = override
        result = subprocess.run([sys.executable, str(script)],
                                input=json.dumps(payload) if raw is None else raw,
                                text=True, capture_output=True, env=env, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout

    return run


@pytest.mark.parametrize("agent,decision", [("claude", "ask"), ("codex", "deny")])
@pytest.mark.parametrize("failure", ["raise RuntimeError('boom')", "raise SystemExit(2)"])
def test_runtime_failure(guard, tmp_path: Path, agent: str, decision: str, failure: str) -> None:
    output = guard(failure, agent)
    assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == decision
    assert "Bash " in (tmp_path / ".config/nuh/hook-errors.log").read_text()


@pytest.mark.parametrize("agent,decision", [("claude", "ask"), ("codex", "deny")])
def test_import_failure(guard, agent: str, decision: str) -> None:
    output = guard("pass", agent, import_error=True)
    assert json.loads(output)["hookSpecificOutput"]["permissionDecision"] == decision


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("exit_statement", ["", "raise SystemExit(0)", "raise SystemExit()"])
def test_exit_zero_preserves_deny(guard, agent: str, exit_statement: str) -> None:
    expected = response("deny")
    assert guard(f"print({expected!r})\n{exit_statement}", agent) == expected + "\n"


@pytest.mark.parametrize("output", ["not json", "[]", "null", "{}",
    '{"hookSpecificOutput": []}',
    '{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "permissionDecision": "allow"}}',
    '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", '
    '"permissionDecisionReason": []}}',
    response("allow") + response("deny")])
@pytest.mark.parametrize("agent,decision", [("claude", "ask"), ("codex", "deny")])
def test_invalid_output_fails_safely(guard, output: str, agent: str, decision: str) -> None:
    result = guard(f"print({output!r})", agent)
    assert json.loads(result)["hookSpecificOutput"]["permissionDecision"] == decision


def test_codex_rejects_ask_but_claude_preserves_it(guard) -> None:
    body = f"print({response('ask')!r})"
    assert json.loads(guard(body, "codex"))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(guard(body))["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_input_replayed_and_allow_preserved(guard, agent: str) -> None:
    body = "data = json.load(sys.stdin)\nassert data['tool_input']['command'] == 'echo hello'\n"
    body += f"print({response('allow')!r})"
    assert json.loads(guard(body, agent))["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("body", ["pass", "raise SystemExit(0)"])
def test_empty_output_preserves_active_allow_disabled(guard, body: str) -> None:
    assert guard(body) == ""


@pytest.mark.parametrize("raw", ["{", "null", "[]", "{}",
    '{"tool_name":"Bash","tool_input":[]}',
    '{"tool_name":"Bash","tool_input":{},"turn_id":null}'])
def test_malformed_input_denied(guard, raw: str) -> None:
    result = guard("raise AssertionError('must not call package')", raw=raw)
    assert json.loads(result)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("override,decision", [("codex", "deny"), ("claude", "ask"), ("bad", "deny")])
def test_override_controls_failure_interface(guard, override: str, decision: str) -> None:
    result = guard("raise RuntimeError('boom')", override=override)
    assert json.loads(result)["hookSpecificOutput"]["permissionDecision"] == decision


def test_original_stdin_is_read_once_and_restored(monkeypatch) -> None:
    from nuh.guard import run

    class Once(io.StringIO):
        reads: int = 0

        def read(self, size: int = -1) -> str:
            self.reads += 1
            assert self.reads == 1
            return super().read(size)

    incoming = Once('{"tool_name":"Bash","tool_input":{"command":"echo hello"}}')
    outgoing = io.StringIO()
    fake_hook = ModuleType("nuh.hook")

    def main() -> None:
        assert sys.stdin is not incoming
        assert json.load(sys.stdin)["tool_input"]["command"] == "echo hello"
        print(response("allow"))

    fake_hook.main = main
    monkeypatch.setitem(sys.modules, "nuh.hook", fake_hook)
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    monkeypatch.delenv("NUH_AGENT", raising=False)
    run()
    assert incoming.reads == 1
    assert sys.stdin is incoming
    assert sys.stdout is outgoing
    assert json.loads(outgoing.getvalue())["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_broken_output_pipe_does_not_raise(monkeypatch) -> None:
    from nuh.guard import run

    class ClosedPipe(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError()

    fake_hook = ModuleType("nuh.hook")

    def main() -> None:
        print(response("deny"))

    fake_hook.main = main
    monkeypatch.setitem(sys.modules, "nuh.hook", fake_hook)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"tool_name":"Bash","tool_input":{}}'))
    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    monkeypatch.delenv("NUH_AGENT", raising=False)
    run()


def test_shim_passes_its_install_timeout_and_start(guard) -> None:
    """The shim tells nuh the hook timeout it was installed with and when it started (cascade_budget)."""
    body = ("import os, time\n"
            "started = float(os.environ['NUH_HOOK_STARTED'])\n"
            "fresh = 0 <= time.monotonic() - started < 5\n"
            "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',"
            " 'permissionDecisionReason': f\"{os.environ['NUH_HOOK_TIMEOUT_S']} {fresh}\"}}))")
    assert json.loads(guard(body))["hookSpecificOutput"]["permissionDecisionReason"] == "60 True"
