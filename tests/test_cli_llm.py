"""nuh test's LLM flags: point the LLM step at any provider or model, in memory only.

Every exchange goes to a stub server on 127.0.0.1 started in-process; the
``loopback_only`` guard fails any test whose LLM step tried to reach another
host. conftest keeps the developer's config and log out of reach.
"""

import io
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nuh import cli, config, hook, log
from nuh.cli_llm import Plan, UsageError
from nuh.cli_llm_args import parse_set
from nuh.config import _merge_configs

_ENV = ("OPENROUTER_API_KEY", "NUH_AGENT")
SECRET = "s3cret-value-never-printed"


class Stub:
    """Every request the stub received; it answers by path (Ollama chat, OpenAI-compatible)."""

    def __init__(self) -> None:
        self.url = ""
        self.decision = "block"
        self.requests: list[dict] = []

    @property
    def last(self) -> dict:
        return self.requests[-1]["body"]

    @property
    def chat(self) -> str:
        """The Ollama chat URL of this stub."""
        return f"{self.url}/api/chat"

    @property
    def prompt(self) -> str:
        """The user text of the newest request."""
        return self.last["messages"][1]["content"]

    def answer(self, path: str) -> dict:
        verdict = json.dumps({"decision": self.decision, "reasoning": "stub says so"})
        if path.endswith("/chat/completions"):
            return {"choices": [{"message": {"content": verdict}}]}
        return {"message": {"content": verdict}}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        stub = self.server.stub
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(raw)})
        body = json.dumps(stub.answer(self.path)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the stub's access log out of the test output."""


@pytest.fixture
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.stub = Stub()
    server.stub.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    yield server.stub
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """Fail the test if the LLM step tried any host but the stub's (the cascade swallows the error)."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    real = urllib.request.urlopen
    strays: list[str] = []

    def guarded(req, *args, **kwargs):
        if urllib.parse.urlsplit(req.full_url).hostname != "127.0.0.1":
            strays.append(req.full_url)
            raise OSError(f"test tried to reach {req.full_url}")
        return real(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    yield
    assert not strays


def use_llm(**llm: object) -> None:
    config._cached_config = _merge_configs({"llm": llm}, {})


def use_stub(stub: Stub, **llm: object) -> None:
    """``llm`` with the stub as the configured ``ollama`` provider; enabled unless *llm* says otherwise."""
    use_llm(**{"enabled": True, "providers": ["ollama"], "ollama": {"url": stub.chat, "model": "stub"}, **llm})


def run(monkeypatch, capsys, *argv: str) -> str:
    """``nuh test ARGV`` through the real parser; its stdout."""
    monkeypatch.setattr(sys, "argv", ["nuh", "test", *argv])
    cli.main()
    return capsys.readouterr().out


def fails(monkeypatch, capsys, *argv: str) -> str:
    """``nuh test ARGV`` must exit 1 with an error; its stderr."""
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, capsys, *argv)
    assert exc.value.code == 1
    return capsys.readouterr().err


def _run_both(monkeypatch, capsys, *argv: str) -> tuple[str, str]:
    monkeypatch.setattr(sys, "argv", ["nuh", "test", *argv])
    cli.main()
    captured = capsys.readouterr()
    return captured.out, captured.err


UNKNOWN = "somethingunknown123 --flag"


def ollama_flags(stub: Stub) -> tuple[str, ...]:
    """The flags that point the run at the stub as ``ollama``."""
    return ("--provider", "ollama", "--provider-url", stub.chat)


# -- Provider flags reach the request --


def test_provider_flags_reach_the_request(stub, project_root, monkeypatch, capsys, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--model", "qwen-test", "--timeout", "8",
              "--can-block", "--cwd", str(work), "--show-request")
    assert [r["path"] for r in stub.requests] == ["/api/chat"]
    assert stub.last["model"] == "qwen-test"
    assert f"Working directory: {work}" in stub.prompt
    assert f"POST {stub.chat} (timeout 8.0s)" in out
    assert "LLM decision: BLOCK" in out
    assert "After cap" not in out  # --can-block lets the block stand
    assert "  can_block: true" in out


def test_default_caps_the_shown_verdict(stub, project_root, monkeypatch, capsys):
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub))
    assert "LLM decision: BLOCK" in out
    assert "After cap:    ASK (llm.can_block: false)" in out


def test_no_can_block_overrides_the_config(stub, project_root, monkeypatch, capsys):
    use_llm(enabled=True, can_block=True)
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--no-can-block")
    assert "After cap:    ASK (llm.can_block: false)" in out


def test_provider_restricts_the_cascade(stub, project_root, monkeypatch, capsys):
    use_llm(enabled=True, providers=["openrouter", "ollama"], openrouter={"key_env": "OPENROUTER_API_KEY"},
            ollama={"url": "http://127.0.0.1:1/api/chat", "model": "configured"})
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub))
    assert [r["path"] for r in stub.requests] == ["/api/chat"]
    assert stub.last["model"] == "configured"
    assert "providers:    ollama" in out
    assert "openrouter" not in out


def test_provider_without_a_config_section_gets_its_default_model(stub, project_root, monkeypatch, capsys):
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub))
    assert stub.last["model"] == "qwen3.5:9b"
    assert "ollama:       model=qwen3.5:9b" in out


# -- --set --


@pytest.mark.parametrize("raw,path,value", [
    ("llm.ollama.timeout=5", ["ollama", "timeout"], 5),
    ("llm.enabled=true", ["enabled"], True),
    ("llm.providers=[\"ollama\"]", ["providers"], ["ollama"]),
    ('llm.ollama={"model": "m"}', ["ollama"], {"model": "m"}),
    ("llm.system1.model=nimble:9b", ["system1", "model"], "nimble:9b"),
    ("llm.ollama.url=https://gpu:11434", ["ollama", "url"], "https://gpu:11434"),
    ("llm.on_failure=", ["on_failure"], ""),
])
def test_set_parses_json_else_keeps_the_string(raw, path, value):
    assert parse_set(raw) == (path, value)


@pytest.mark.parametrize("raw,fragment", [
    ("profile=none", "only llm.* keys"),
    ("llm=5", "only llm.* keys"),
    ("llm..timeout=5", "only llm.* keys"),
    ("actions.git_safe=allow", "only llm.* keys"),
    ("llm.ollama.timeout", "expected llm.KEY=VALUE"),
])
def test_set_rejects_anything_but_llm_keys(raw, fragment):
    with pytest.raises(UsageError, match=fragment.replace("*", r"\*")):
        parse_set(raw)


def test_set_rejected_on_the_command_line(project_root, monkeypatch, capsys):
    assert "only llm.* keys can be set" in fails(monkeypatch, capsys, "ls", "--set", "profile=none")


def test_set_reaches_the_request_and_goes_last(stub, project_root, monkeypatch, capsys):
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--model", "from-flag", "--timeout", "8",
              "--set", "llm.ollama.model=from-set", "--set", "llm.ollama.timeout=11", "--show-request")
    assert stub.last["model"] == "from-set"
    assert f"POST {stub.chat} (timeout 11.0s)" in out


def test_set_through_a_scalar_is_rejected():
    plan = Plan(sets=[parse_set("llm.ollama.url=http://x")])
    with pytest.raises(UsageError, match="llm.ollama is not a mapping"):
        plan.apply({"ollama": "not-a-section"})


# -- Never written to disk --


def test_overrides_never_reach_the_config_file_or_the_log(stub, project_root, monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cfg_file = home / "config.yaml"
    cfg_file.write_text("llm:\n  enabled: false\n  providers: [ollama]\n  ollama:\n    model: base\n")
    before = cfg_file.read_bytes()
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(cfg_file))
    monkeypatch.setattr(log, "LOG_PATH", str(home / "nuh.log"))
    run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--can-block", "--set", "llm.ollama.timeout=4",
        "--user", "hello")
    assert len(stub.requests) == 1
    assert cfg_file.read_bytes() == before
    assert sorted(p.name for p in home.iterdir()) == ["config.yaml"]
    config.reset_config()
    assert config.get_config().llm == {"enabled": False, "providers": ["ollama"], "ollama": {"model": "base"}}


def test_apply_leaves_the_loaded_section_untouched():
    loaded = {"enabled": False, "providers": ["ollama"], "ollama": {"url": "http://127.0.0.1:1", "model": "m"}}
    Plan(provider="ollama", provider_section={"model": "x"}, timeout=3.0).apply(loaded)
    assert loaded == {"enabled": False, "providers": ["ollama"], "ollama": {"url": "http://127.0.0.1:1", "model": "m"}}


def test_user_transcript_is_removed_after_the_run(stub, project_root, monkeypatch, capsys, tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--user", "hello there")
    assert "hello there" in stub.prompt
    assert list(scratch.iterdir()) == []
    assert hook._payload == {} and hook._transcript_path == ""


# -- Printed settings redact secrets --


def test_settings_show_a_provider_key_as_set_only(stub, project_root, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    out = run(monkeypatch, capsys, UNKNOWN, "--provider", "openrouter",
              "--provider-url", f"{stub.url}/v1/chat/completions", "--show-request")
    assert "key=$OPENROUTER_API_KEY (set)" in out
    assert stub.requests[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in out


# -- Context --


def test_user_messages_and_first_prompt_reach_the_prompt(stub, project_root, monkeypatch, capsys):
    run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--first-prompt", "build the parser",
        "--user", "now clean the build dir", "--user", "and run the probe", "--set", "llm.context.user_turns=2")
    prompt = stub.prompt
    assert "Recent conversation" in prompt
    for message in ("build the parser", "now clean the build dir", "and run the probe"):
        assert message in prompt


def test_transcript_file_reaches_the_prompt(stub, project_root, monkeypatch, capsys, tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "from the file"}}) + "\n")
    run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--transcript", str(path))
    assert "from the file" in stub.prompt


def test_write_tool_carries_the_call_and_context(stub, project_root, monkeypatch, capsys):
    target = os.path.join(project_root, "notes.txt")
    out = run(monkeypatch, capsys, "--tool", "Write", "--path", target, "--content", "hello",
              *ollama_flags(stub), "--user", "write the notes", "--can-block", "--show-request")
    assert target in stub.prompt and "write the notes" in stub.prompt
    assert f"POST {stub.chat}" in out and "stub says so" in out
    assert "Decision: BLOCK" in out


# -- On / off --


def test_no_llm_skips_a_configured_review(stub, project_root, monkeypatch, capsys):
    use_stub(stub)
    out = run(monkeypatch, capsys, UNKNOWN, "--no-llm")
    assert "LLM step:     off (--no-llm)" in out
    assert stub.requests == []


def test_no_llm_skips_the_write_review(stub, project_root, monkeypatch, capsys):
    use_stub(stub)
    run(monkeypatch, capsys, "--tool", "Write", "--path", os.path.join(project_root, "a.txt"), "--content", "x",
        "--no-llm")
    assert stub.requests == []


def test_llm_reviews_a_static_allow_anyway(stub, project_root, monkeypatch, capsys):
    use_stub(stub, enabled=False)
    assert "LLM decision" not in run(monkeypatch, capsys, "ls")
    assert stub.requests == []
    out = run(monkeypatch, capsys, "ls", "--llm")
    assert "forced by --llm" in out
    assert len(stub.requests) == 1


def test_choosing_a_provider_turns_a_disabled_llm_on(stub, project_root, monkeypatch, capsys):
    use_llm(enabled=False)
    run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub))
    assert len(stub.requests) == 1


# -- Bad combinations --


@pytest.mark.parametrize("argv,fragment", [
    (["--model", "m"], "need --provider NAME"),
    (["--provider", "bogus"], "not a provider"),
    (["--no-llm", "--provider", "ollama"], "--no-llm turns the LLM step off"),
    (["--no-llm", "--show-request"], "--no-llm turns the LLM step off"),
    (["--transcript", "/nonexistent/t.jsonl"], "no such file"),
    (["--cwd", "/nonexistent/dir"], "not a directory"),
])
def test_bad_flag_combinations(project_root, monkeypatch, capsys, argv, fragment):
    assert fragment in fails(monkeypatch, capsys, UNKNOWN, *argv)


@pytest.mark.parametrize("flag", ["--backend", "--decide-url", "--pipeline", "--key-env", "--session-id"])
def test_the_decide_flags_are_gone(project_root, monkeypatch, capsys, flag):
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch, capsys, UNKNOWN, flag, "x")
    assert exc.value.code == 2  # argparse: unrecognized arguments
    assert flag in capsys.readouterr().err


def test_transcript_and_user_messages_are_exclusive(project_root, monkeypatch, capsys, tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("")
    assert "use one or the other" in fails(monkeypatch, capsys, UNKNOWN, "--transcript", str(path), "--user", "x")


def test_approve_sends_a_read_to_no_llm(stub, project_root, monkeypatch, capsys):
    out = run(monkeypatch, capsys, "--tool", "Read", "./README.md", *ollama_flags(stub))
    assert stub.requests == []
    assert "Decision: ALLOW" in out
    assert "LLM call:   not called: tools.Read: approve never calls it" in out


def test_check_sends_a_read_to_the_generic_review(stub, project_root, monkeypatch, capsys):
    config._cached_config = _merge_configs({"tools": {"Read": "check"}}, {})
    out = run(monkeypatch, capsys, "--tool", "Read", "./README.md", *ollama_flags(stub))  # the stub blocks
    assert len(stub.requests) == 1
    assert "Tool: Read" in stub.prompt and '"file_path": "./README.md"' in stub.prompt
    assert "Decision: ASK" in out  # the LLM's block, capped to ask, raises the static allow
    assert "LLM call:   called (tools.Read: check reviews this allow)" in out


@pytest.mark.parametrize("tool,extra", [("Read", []), ("Glob", []), ("Grep", ["--pattern", "x"])])
def test_no_llm_on_a_tool_without_an_llm_step_is_a_no_op(project_root, monkeypatch, capsys, tool, extra):
    out, err = _run_both(monkeypatch, capsys, "--tool", tool, "./README.md", *extra, "--no-llm")
    assert "Decision: ALLOW" in out
    assert err == ""


# -- An ask the hook would not send (llm_skip.py) --

SKIP_BASH = "LLM step:     skipped, cannot change outcome (tools.Bash: check reviews allows only)"


def _bash_check() -> None:
    config._cached_config = _merge_configs({"llm": {"enabled": True}, "tools": {"Bash": "check"}}, {})


def test_bash_ask_the_hook_would_not_send_says_why(stub, project_root, monkeypatch, capsys):
    _bash_check()
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub))
    assert stub.requests == []
    assert SKIP_BASH in out


def test_llm_flag_still_reviews_an_ask_the_hook_would_not_send(stub, project_root, monkeypatch, capsys):
    _bash_check()
    out = run(monkeypatch, capsys, UNKNOWN, *ollama_flags(stub), "--llm")
    assert len(stub.requests) == 1
    assert SKIP_BASH not in out


def test_write_ask_the_hook_would_not_send_says_why(stub, project_root, monkeypatch, capsys, tmp_path):
    config._cached_config = _merge_configs({"llm": {"enabled": True}, "tools": {"Edit": "check"}}, {})
    out = run(monkeypatch, capsys, "--tool", "Edit", "--content", "hi", *ollama_flags(stub),
              str(tmp_path / "outside" / "a.txt"))
    assert stub.requests == []
    assert "LLM step:     skipped, cannot change outcome (tools.Edit: check reviews allows only)" in out
    assert "no providers responded" not in out


# -- A clean script file the static layer allows: the hook reviews it (bash_review.SCRIPT) --


@pytest.fixture
def probe(project_root, monkeypatch) -> str:
    """A clean script file in the test project, run from there: a static allow the hook reviews."""
    monkeypatch.chdir(project_root)
    with open(os.path.join(project_root, "probe.py"), "w") as f:
        f.write("print(sum(range(10)))\n")
    return "python3 probe.py"


def _hook_decision(monkeypatch, project_root: str, command: str) -> str:
    """What the hook itself decides for *command* (the decision it logs)."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s", "cwd": project_root}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    seen: list[dict] = []
    monkeypatch.setattr(hook, "_log_hook_decision", lambda _tool, _input, decision, *_a: seen.append(decision))
    hook.main()
    return seen[-1]["decision"]


def test_a_clean_script_is_reviewed_as_the_hook_reviews_it(stub, probe, monkeypatch, capsys):
    use_stub(stub)
    out = run(monkeypatch, capsys, probe)
    assert "Decision:    ALLOW" in out
    assert len(stub.requests) == 1
    assert probe in stub.prompt
    assert "LLM review:   script file" in out
    assert "LLM decision: BLOCK" in out
    assert "After review: ASK" in out  # llm.can_block false: the block raises the allow to ask


def test_llm_flag_on_a_clean_script_does_not_say_the_hook_would_skip_it(stub, probe, monkeypatch, capsys):
    use_stub(stub)
    out = run(monkeypatch, capsys, probe, "--llm")
    assert "forced by --llm" not in out
    assert "LLM review:   script file" in out


@pytest.mark.parametrize("verdict,after", [("block", "ASK"), ("allow", "ALLOW"), ("ask", "ASK")])
def test_nuh_test_matches_the_hook_on_a_clean_script(stub, probe, project_root, monkeypatch, capsys,
                                                      verdict, after):
    use_stub(stub)
    stub.decision = verdict
    out = run(monkeypatch, capsys, probe)
    assert f"After review: {after}" in out
    assert _hook_decision(monkeypatch, project_root, probe) == after.lower()


def test_a_static_allow_without_a_script_is_still_not_sent(stub, project_root, monkeypatch, capsys):
    use_stub(stub)
    out = run(monkeypatch, capsys, "ls", "--show-request")
    assert stub.requests == []
    assert "none sent (a static allow with no clean script file to review" in out
    out = run(monkeypatch, capsys, "ls", "--llm")
    assert "forced by --llm (the hook would not send it: a static allow with no clean script file" in out
