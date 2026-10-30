"""A reviewed call whose LLM review failed asks; it is never left allowed (llm_failure).

Each test runs the whole hook (hook.main). A failure is either a crash in
nuh's own LLM step (simulated by a prompt builder that raises) or no
provider answering (a closed port). A model that answers uncertain is not a
failure: ``llm.on_uncertain`` decides it (ask by default, logged as
``llm.decision: uncertain``). ``static`` for either setting keeps the static
decision (llm_fallback).
"""

import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nuh import config, hook, llm, llm_action
from nuh.config import _merge_configs
from nuh.log_query import outcome
from nuh.steps import Outcome
from tests.llm_stub import closed_port, last_entry


class _Ollama(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.calls += 1
        verdict = {"decision": self.server.verdict, "reasoning": "stub"}
        body = json.dumps({"message": {"content": json.dumps(verdict)}, **self.server.extra}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the stub's access log out of the test output."""


@pytest.fixture
def ollama():
    """A local Ollama that answers ``server.verdict`` (allow unless a test changes it), plus ``server.extra``."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    server.daemon_threads = True
    server.calls, server.verdict, server.extra = 0, "allow", {}
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def home(project_root, tmp_path, monkeypatch) -> str:
    """Run in the test project with a temporary HOME and a clean script file, probe.py."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.chdir(project_root)
    with open(os.path.join(project_root, "probe.py"), "w") as f:
        f.write("print(sum(range(10)))\n")
    return str(fake)


def _use_ollama(port: int, **llm_extra: object) -> None:
    url = f"http://127.0.0.1:{port}/api/chat"
    config._cached_config = _merge_configs(
        {"llm": {"enabled": True, "providers": ["ollama"], "ollama": {"url": url, "model": "stub", "timeout": 5},
                 **llm_extra}},
        {})


def _crash(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("prompt builder bug")


def _run(monkeypatch, payload: dict) -> dict:
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"]


def _bash(project_root: str, command: str = "python3 probe.py") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s",
            "tool_use_id": "toolu_failure", "cwd": project_root}


def _write(project_root: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": f"{project_root}/notes.txt", "content": "hello\n"},
            "session_id": "s", "cwd": project_root}


# -- Bash: a clean script file the static layer allows, reviewed by the LLM --

def test_a_crash_in_the_script_review_asks(ollama, home, project_root, hook_log, monkeypatch, capsys):
    _use_ollama(ollama.server_port)
    monkeypatch.setattr(llm, "_build_prompt", _crash)
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "ask"
    assert "LLM review failed" in out["permissionDecisionReason"]
    assert ollama.calls == 0
    assert "nuh: llm: Bash review failed: RuntimeError: prompt builder bug" in capsys.readouterr().err
    entry = last_entry(hook_log)
    assert entry["decision"] == "ask"
    assert entry["llm"]["skipped"] == "error"
    assert entry["llm"]["error"] == "RuntimeError: prompt builder bug"
    assert entry["steps"][1:] == [
        {"step": "llm", "decision": "", "error": "error",
         "reason": "Bash: LLM review failed, asking: RuntimeError: prompt builder bug"},
        {"step": "fail_closed", "from": "allow", "to": "ask", "rule": "llm.skipped: error"}]


def test_no_provider_answering_the_script_review_asks(home, project_root, hook_log, monkeypatch, capsys):
    _use_ollama(closed_port())
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "ask"
    assert "nuh: llm: Bash: no provider answered" in capsys.readouterr().err
    entry = last_entry(hook_log)["llm"]
    assert entry["skipped"] == "error"
    assert entry["error"].startswith("ollama: ")
    assert entry["cascade"][0]["status"] == "error"


def test_a_cut_ollama_prompt_is_a_failed_script_review(ollama, home, project_root, hook_log, monkeypatch):
    """Ollama dropped part of the prompt: its allow is no verdict, and the lone provider failing asks."""
    _use_ollama(ollama.server_port)
    ollama.extra = {"prompt_eval_count": llm_action.DEFAULT_OLLAMA_NUM_CTX - 10}
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "ask"
    assert "Bash (LLM): no verdict, asking: ollama: the model read" in out["permissionDecisionReason"]
    entry = last_entry(hook_log)["llm"]
    assert entry["skipped"] == "error"
    assert entry["error"].startswith("ollama: the model read 24566 prompt tokens")
    assert entry["error"].endswith("the prompt may have been cut")
    assert entry["cascade"][0]["status"] == "error"


def test_an_uncertain_script_review_asks(ollama, home, project_root, hook_log, monkeypatch):
    """A model's "uncertain" asks by default (llm.on_uncertain: ask): it raises the static allow."""
    _use_ollama(ollama.server_port)
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "ask"
    assert out["permissionDecisionReason"].endswith("Bash (LLM): uncertain: stub; on_uncertain: ask")
    assert ollama.calls == 1
    entry = last_entry(hook_log)
    assert entry["steps"][1:] == [
        {"step": "llm", "decision": "uncertain", "reason": "uncertain: stub; on_uncertain: ask"},
        {"step": "uncertain", "from": "allow", "to": "ask", "rule": "llm.on_uncertain: ask"}]
    assert entry["llm"]["cascade"][0]["status"] == "uncertain"
    assert "skipped" not in entry["llm"]
    assert outcome(entry) == Outcome("llm", "uncertain", "uncertain: stub; on_uncertain: ask", "", "")


def test_on_uncertain_static_keeps_the_script_allow(ollama, home, project_root, hook_log, monkeypatch):
    _use_ollama(ollama.server_port, on_uncertain="static")
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "allow"
    entry = last_entry(hook_log)
    assert entry["steps"][1:] == [
        {"step": "llm", "decision": "uncertain", "reason": "uncertain: stub; on_uncertain: static"},
        {"step": "keep", "from": "allow", "to": "allow", "rule": "llm.on_uncertain: static"}]


def test_on_failure_static_keeps_the_script_allow(home, project_root, hook_log, monkeypatch, capsys):
    _use_ollama(closed_port(), on_failure="static")
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "allow"
    assert "keeping the static decision" in capsys.readouterr().err
    entry = last_entry(hook_log)
    assert entry["llm"]["skipped"] == "error"
    llm_step, kept = entry["steps"][1:]
    assert (llm_step["decision"], llm_step["error"]) == ("", "error")
    assert "(on_failure: static)" in llm_step["reason"]
    assert kept == {"step": "keep", "from": "allow", "to": "allow", "rule": "llm.on_failure: static"}


def test_on_failure_static_keeps_a_crashed_script_review_allowed(ollama, home, project_root, hook_log,
                                                                 monkeypatch):
    """A crash in nuh's own LLM step is a failure too."""
    _use_ollama(ollama.server_port, on_failure="static")
    monkeypatch.setattr(llm, "_build_prompt", _crash)
    assert _run(monkeypatch, _bash(project_root))["permissionDecision"] == "allow"
    llm_step, kept = last_entry(hook_log)["steps"][1:]
    assert llm_step["reason"] == ("Bash: LLM review failed, keeping the static decision (on_failure: static): "
                                  "RuntimeError: prompt builder bug")
    assert kept["step"] == "keep"


def test_on_failure_static_still_asks_when_the_action_is_too_large(ollama, home, project_root, hook_log,
                                                                  monkeypatch):
    """Too large is nuh refusing to send, not a provider failing: it asks whatever on_failure says."""
    _use_ollama(ollama.server_port, on_failure="static", on_uncertain="static", max_action_chars=10)
    assert _run(monkeypatch, _bash(project_root))["permissionDecision"] == "ask"
    assert ollama.calls == 0
    assert last_entry(hook_log)["llm"]["skipped"] == "too_large"


def test_an_allowing_script_review_keeps_the_allow(ollama, home, project_root, hook_log, monkeypatch):
    _use_ollama(ollama.server_port)
    out = _run(monkeypatch, _bash(project_root))
    assert out["permissionDecision"] == "allow"
    assert ollama.calls == 1
    assert [(s["step"], s["decision"]) for s in last_entry(hook_log)["steps"]] == [("static", "allow"),
                                                                                   ("llm", "allow")]


def test_an_uncertain_review_of_a_static_ask_asks(ollama, home, project_root, hook_log, monkeypatch):
    _use_ollama(ollama.server_port)
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _bash(project_root, "somethingunknown123 --flag"))
    assert out["permissionDecision"] == "ask"
    assert ollama.calls == 1
    entry = last_entry(hook_log)
    assert entry["steps"][-1] == {"step": "uncertain", "from": "ask", "to": "ask", "rule": "llm.on_uncertain: ask"}
    assert outcome(entry) == Outcome("llm", "uncertain", "uncertain: stub; on_uncertain: ask", "", "")


@pytest.mark.parametrize("setting", ["on_uncertain", "on_failure"])
def test_static_keeps_a_static_ask_and_its_reason(ollama, home, project_root, hook_log, monkeypatch, setting):
    _use_ollama(ollama.server_port if setting == "on_uncertain" else closed_port(), **{setting: "static"})
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _bash(project_root, "somethingunknown123 --flag"))
    assert out["permissionDecision"] == "ask"
    assert "Bash: unknown → ask" in out["permissionDecisionReason"]
    entry = last_entry(hook_log)
    assert entry["steps"][-1] == {"step": "keep", "from": "ask", "to": "ask", "rule": f"llm.{setting}: static"}
    assert outcome(entry).reason_source == "static"


def test_an_uncertain_write_review_asks(ollama, home, project_root, hook_log, monkeypatch):
    """An uncertain review raises a write's static allow to ask (on_uncertain: ask), on Bash as on writes."""
    _use_ollama(ollama.server_port)
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _write(project_root))
    assert out["permissionDecision"] == "ask"
    assert [s["step"] for s in last_entry(hook_log)["steps"]] == ["static", "llm", "uncertain"]
    assert outcome(last_entry(hook_log)).llm_decision == "uncertain"


@pytest.mark.parametrize("setting", ["on_uncertain", "on_failure"])
def test_static_keeps_a_write_allowed(ollama, home, project_root, hook_log, monkeypatch, setting):
    _use_ollama(ollama.server_port if setting == "on_uncertain" else closed_port(), **{setting: "static"})
    ollama.verdict = "uncertain"
    out = _run(monkeypatch, _write(project_root))
    assert out["permissionDecision"] == "allow"
    assert last_entry(hook_log)["steps"][-1] == {"step": "keep", "from": "allow", "to": "allow",
                                                 "rule": f"llm.{setting}: static"}


def test_an_unknown_on_uncertain_value_warns_and_asks(ollama, home, project_root, hook_log, monkeypatch, capsys):
    _use_ollama(ollama.server_port, on_uncertain="allow")
    assert "llm.on_uncertain must be ask or static, got 'allow'; using ask" in capsys.readouterr().err
    ollama.verdict = "uncertain"
    assert _run(monkeypatch, _bash(project_root))["permissionDecision"] == "ask"


def test_a_crash_reviewing_a_static_ask_keeps_the_ask_and_logs_it(ollama, home, project_root, hook_log,
                                                                  monkeypatch):
    _use_ollama(ollama.server_port)
    monkeypatch.setattr(llm, "_build_prompt", _crash)
    out = _run(monkeypatch, _bash(project_root, "somethingunknown123 --flag"))
    assert out["permissionDecision"] == "ask"
    assert last_entry(hook_log)["llm"]["error"] == "RuntimeError: prompt builder bug"


# -- Write-like tools: the LLM veto gate --

def test_a_crash_in_the_write_review_asks(ollama, home, project_root, hook_log, monkeypatch, capsys):
    _use_ollama(ollama.server_port)
    monkeypatch.setattr(llm, "_build_write_prompt", _crash)
    out = _run(monkeypatch, _write(project_root))
    assert out["permissionDecision"] == "ask"
    assert ollama.calls == 0
    assert "nuh: llm: Write review failed: RuntimeError: prompt builder bug" in capsys.readouterr().err
    assert last_entry(hook_log)["llm"]["error"] == "RuntimeError: prompt builder bug"


def test_no_provider_answering_the_write_review_asks_as_a_failure(home, project_root, hook_log, monkeypatch):
    _use_ollama(closed_port())
    out = _run(monkeypatch, _write(project_root))
    assert out["permissionDecision"] == "ask"
    entry = last_entry(hook_log)["llm"]
    assert entry["skipped"] == "error"
    assert entry["error"].startswith("ollama: ")


def test_a_crash_in_a_codex_patch_review_asks(ollama, home, project_root, hook_log, monkeypatch):
    _use_ollama(ollama.server_port)
    monkeypatch.setattr(llm, "_build_write_prompt", _crash)
    patch = "*** Begin Patch\n*** Add File: a.txt\n+one\n*** Add File: b.txt\n+two\n*** End Patch"
    payload = {"tool_name": "apply_patch", "tool_input": {"command": patch}, "turn_id": "turn-1",
               "cwd": project_root, "session_id": "codex-sess"}
    out = _run(monkeypatch, payload)
    assert out["permissionDecision"] == "deny"  # Codex cannot ask: its ask blocks (agents.format_ask)
    entry = last_entry(hook_log)
    assert entry["decision"] == "ask"
    assert entry["llm"]["error"] == "RuntimeError: prompt builder bug"
