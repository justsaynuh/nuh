"""LLM trace directory (llm.trace_dir): one complete, private record per LLM review."""

import ast
import inspect
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError

import pytest

from nuh import llm, llm_providers, trace, trace_redact
from tests.trace_helpers import (BASH, DIR_NAME, KEY, KEY_ENV, LONG_REASONING, _decision, invoke, load, ollama_reply,
                                 only_dir, openrouter_reply, set_config, use_http)


pytestmark = pytest.mark.usefixtures("trace_isolated")


# -- Complete traces --


def test_bash_ask_path_writes_one_complete_trace(monkeypatch, tmp_path, project_root):
    traces = tmp_path / "traces"
    set_config(traces)
    http = use_http(monkeypatch, ollama_reply("allow", LONG_REASONING))

    out = invoke(monkeypatch, BASH)

    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    d = only_dir(traces)
    assert DIR_NAME.match(d.name)
    assert {p.name for p in d.iterdir()} == {
        "input.json", "classification.json", "request-01-ollama.json",
        "response-01-ollama.json", "result.json",
    }

    inp = load(d, "input.json")
    assert inp["agent"] == "claude"
    assert inp["tool_name"] == "Bash"
    assert inp["tool_input"] == BASH["tool_input"]
    assert inp["session_id"] == "sess-1"
    assert inp["project_root"] == project_root
    assert inp["cwd"] == os.getcwd()

    review = load(d, "classification.json")["reviews"][0]
    assert review["kind"] == "bash"
    assert review["final_decision"] == "ask"
    assert review["stages"][0]["tokens"] == ["somethingunknown123", "--flag"]
    assert review["stages"][0]["action_type"] == "unknown"
    assert review["stages"][0]["decision"] == "ask"

    (req, sent, timeout), = http.sent
    request = load(d, "request-01-ollama.json")
    assert request["body_raw"] == sent.decode("utf-8")
    assert request["body"] == json.loads(sent)
    assert request["url"] == req.full_url
    assert request["model"] == "qwen-test"
    assert request["timeout"] == timeout
    assert request["sent"] is True
    assert "somethingunknown123 --flag" in request["body"]["messages"][1]["content"]

    response = load(d, "response-01-ollama.json")
    assert response["http"]["status"] == 200
    assert response["http"]["body_raw"] == ollama_reply("allow", LONG_REASONING).decode()
    assert response["content"] == _decision("allow", LONG_REASONING)
    assert response["parsed"]["reasoning"] == LONG_REASONING
    assert response["outcome"]["status"] == "success"

    result = load(d, "result.json")
    assert result["final"]["decision"] == "allow"
    assert result["hook_output"] == out
    assert result["llm_decision_used"]["decision"] == "allow"
    assert result["reviews"][0]["attempts"] == ["01-ollama"]


def test_write_path_traces_full_content_and_redacts_auth(monkeypatch, tmp_path, project_root):
    traces = tmp_path / "traces"
    set_config(traces, providers=("openrouter",))
    use_http(monkeypatch, openrouter_reply("allow", "plain source file"))
    content = "def main():\n    print('hello')\n" * 400  # longer than the prompt's 8192-char cut
    payload = {"tool_name": "Write", "session_id": "sess-2",
               "tool_input": {"file_path": os.path.join(project_root, "app.py"), "content": content}}

    out = invoke(monkeypatch, payload)

    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    d = only_dir(traces)
    assert {p.name for p in d.iterdir()} == {
        "input.json", "classification.json", "request-01-openrouter.json",
        "response-01-openrouter.json", "result.json",
    }
    assert load(d, "input.json")["tool_input"]["content"] == content
    review = load(d, "classification.json")["reviews"][0]
    assert review["kind"] == "write"
    assert review["target"] == payload["tool_input"]["file_path"]
    assert review["deterministic_decision"]["decision"] == "allow"
    request = load(d, "request-01-openrouter.json")
    assert request["headers"]["Authorization"] == trace_redact.REDACTED
    assert request["headers"]["Content-type"] == "application/json"
    for f in d.iterdir():
        assert KEY not in f.read_text(), f.name


def test_failed_attempts_record_exceptions(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces, providers=("ollama", "openrouter"))
    unauthorized = HTTPError("https://openrouter.test/api/v1/chat/completions", 401, "Unauthorized",
                             {}, io.BytesIO(b'{"error": {"message": "bad key"}}'))
    use_http(monkeypatch, TimeoutError("timed out"), unauthorized)

    out = invoke(monkeypatch, BASH)

    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    d = only_dir(traces)
    timeout = load(d, "response-01-ollama.json")
    assert timeout["exception"] == {"type": "TimeoutError", "message": "timed out"}
    assert timeout["http"]["error"]["type"] == "TimeoutError"
    assert timeout["outcome"]["status"] == "error"
    assert timeout["outcome"]["error"] == "TimeoutError: timed out"
    http_error = load(d, "response-02-openrouter.json")
    assert http_error["http"]["status"] == 401
    assert http_error["http"]["body"] == {"error": {"message": "bad key"}}
    assert http_error["exception"]["type"] == "HTTPError"
    result = load(d, "result.json")
    assert result["llm_decision_used"]["decision"] == "ask"  # no provider answered: a failed review asks
    assert [a["status"] for a in result["reviews"][0]["llm"]["cascade"]] == ["error", "error"]


def test_attempt_without_request_is_recorded_as_unsent(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces, providers=("openrouter",))
    monkeypatch.delenv(KEY_ENV)
    http = use_http(monkeypatch)

    invoke(monkeypatch, BASH)

    d = only_dir(traces)
    assert http.sent == []
    assert load(d, "request-01-openrouter.json")["sent"] is False
    assert load(d, "response-01-openrouter.json")["outcome"]["error"].startswith("provider returned None")


def test_can_block_capping_is_recorded(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces)
    use_http(monkeypatch, ollama_reply("block", "deletes the home directory"))

    out = invoke(monkeypatch, BASH)

    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    result = load(only_dir(traces), "result.json")
    assert result["can_block"] is False
    review = result["reviews"][0]
    assert review["llm_decision"]["decision"] == "block"
    assert review["after_cap"]["decision"] == "ask"
    assert review["capped"] is True
    # The capped block equals the static ask, so the static ask stands; its steps keep the block and the cap.
    assert result["llm_decision_used"] is None
    assert result["final"]["decision"] == "ask"
    assert [(s["step"], s.get("decision") or s.get("to")) for s in result["final"]["_steps"]] == [
        ("static", "ask"), ("llm", "block"), ("cap", "ask")]
    assert result["final"]["_steps"][1]["reason"] == "deletes the home directory"


# -- Every provider is traced --


def _uses(tree: ast.AST, name: str) -> bool:
    return any((isinstance(node, ast.Attribute) and node.attr == name)
               or (isinstance(node, ast.Name) and node.id == name)
               for node in ast.walk(tree))


def _callers(name: str) -> set[tuple[str, str]]:
    """(module, owner) of every use of *name* in nuh.

    The owner is the top-level function or class that contains the use (a nested
    function counts as its outer one), or ``<module>`` for module-level code.
    """
    package = Path(inspect.getsourcefile(llm)).parent
    found = set()
    for path in sorted(package.glob("*.py")):
        for stmt in ast.parse(path.read_text()).body:
            if _uses(stmt, name):
                owner = stmt.name if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)) else "<module>"
                found.add((path.stem, owner))
    return found


def test_urlopen_is_only_reached_through_post():
    """A provider calling urlopen directly would bypass the trace: only _exchange may, and only _post calls it.

    The one other caller is the llm.self_heal worker, a separate process that
    runs after the hook call: its probes and replay are not provider attempts.
    """
    assert _callers("urlopen") == {("llm_providers", "_exchange")}
    assert _callers("_exchange") == {("llm_providers", "_post"), ("llm_heal_worker", "_models"),
                                     ("llm_heal_worker", "_replay")}


UNIVERSAL_REPLY = json.dumps({
    "message": {"content": _decision("allow")},                      # ollama
    "response": _decision("allow"),                                   # ollama /api/generate
    "choices": [{"message": {"content": _decision("allow")}}],        # openai-compatible, cortex
    "output": [{"type": "message", "content": [{"type": "output_text", "text": _decision("allow")}]}],
    "content": [{"text": _decision("allow")}],                        # anthropic
}).encode()


@pytest.mark.parametrize("provider", sorted(llm_providers._PROVIDERS))
def test_every_provider_attempt_is_traced(monkeypatch, tmp_path, provider):
    http = use_http(monkeypatch, UNIVERSAL_REPLY)
    tr = trace.Trace(str(tmp_path / "traces"), {"agent": "claude", "payload": {}})
    provider_config = {"url": "https://provider.test/v1/endpoint", "key_env": KEY_ENV,
                       "account": "acct", "model": "model-x"}

    prompt = llm_providers.PromptParts("sys", "usr")
    result, _elapsed, error = llm_providers._call_provider(provider, provider_config, prompt, tr)

    assert error == "" and result.decision == "allow"
    (_req, sent, _timeout), = http.sent
    d = Path(tr.path)
    request = load(d, f"request-01-{provider}.json")
    assert request["sent"] is True
    assert request["body_raw"] == sent.decode("utf-8")
    response = load(d, f"response-01-{provider}.json")
    assert response["http"]["body_raw"] == UNIVERSAL_REPLY.decode()
    assert response["outcome"]["status"] == "success"


def test_subagent_call_traces_the_context_it_sent(monkeypatch, tmp_path, project_root):
    """agent_id and tool_use_id flow from the payload to the prompt, and the trace counts sections."""
    session = tmp_path / "sess.jsonl"
    session.write_text(json.dumps({"type": "user", "message": {"content": "TASK: tidy the repo"}}) + "\n")
    sub = tmp_path / "sess" / "subagents" / "agent-a9.jsonl"
    sub.parent.mkdir(parents=True)
    tool = {"type": "tool_use", "id": "toolu_now", "name": "Bash", "input": {"command": "somethingunknown123"}}
    sub.write_text("\n".join(json.dumps(r) for r in (
        {"type": "user", "message": {"content": "Brief: clean dist/"}},
        {"type": "assistant", "message": {"content": [tool]}},
    )) + "\n")
    traces = tmp_path / "traces"
    set_config(traces)
    http = use_http(monkeypatch, ollama_reply("allow"))

    invoke(monkeypatch, {**BASH, "transcript_path": str(session), "agent_id": "a9", "tool_use_id": "toolu_now"})

    sent = json.loads(http.sent[0][1])["messages"][1]["content"]
    assert "TASK: tidy the repo" in sent
    assert "Brief: clean dist/" in sent
    assert "[Bash: somethingunknown123]" not in sent
    context = load(only_dir(traces), "result.json")["reviews"][0]["context"]
    assert context["source"] == "claude-subagent"
    assert (context["user_messages"], context["delegator_messages"], context["tool_calls"]) == (1, 1, 0)
    assert context["chars"] > 0
