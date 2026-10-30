"""The generic review: the LLM step for tools that are neither Bash nor a write tool (hook_generic.py).

Each test runs the whole hook (hook.main) against the stub model
(tests/llm_stub.py): "the LLM was called" means a request reached it.
"""

import io
import json

import pytest

from nuh import config, hook
from nuh.config import _merge_configs
from tests.llm_stub import last_entry, llm_config

READS = {"filesystem_read": ["mcp__notes__read"]}
WRITES = {"git_remote_write": ["mcp__forge__create_tag"]}


def use(url: str, tools: dict, **extra: object) -> None:
    config._cached_config = _merge_configs({"llm": llm_config(url, **extra.pop("llm", {})), "tools": tools,
                                            "classify": {**READS, **WRITES}, **extra}, {})


def run(monkeypatch, tool: str, tool_input: dict) -> str:
    """The hook's permissionDecision for the call; "" when it stayed silent."""
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": tool, "tool_input": tool_input})))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"]["permissionDecision"] if text else ""


class TestCheck:
    def test_an_allow_is_reviewed_and_a_block_raises_it_to_ask(self, stub_model, project_root, hook_log,
                                                                 monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"})
        stub_model.answer("block", "reads the password vault")
        assert run(monkeypatch, "mcp__notes__read", {"note": "vault"}) == "ask"
        assert len(stub_model.requests) == 1
        steps = [(s["step"], s.get("decision") or s.get("to")) for s in last_entry(hook_log)["steps"]]
        assert steps == [("static", "allow"), ("llm", "block"), ("cap", "ask"), ("escalate", "ask")]

    def test_can_block_lets_the_block_stand(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"}, llm={"can_block": True})
        stub_model.answer("block", "reads the password vault")
        assert run(monkeypatch, "mcp__notes__read", {"note": "vault"}) == "deny"

    def test_an_llm_allow_keeps_the_allow_sent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"})
        assert run(monkeypatch, "mcp__notes__read", {"note": "todo"}) == "allow"
        assert len(stub_model.requests) == 1

    def test_an_ask_is_not_sent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__forge__*": "check"})
        assert run(monkeypatch, "mcp__forge__create_tag", {"tag": "v1"}) == "ask"
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == (
            "cannot change outcome (tools.mcp__forge__create_tag: check reviews allows only)")

    def test_no_answer_fails_closed(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"})
        stub_model.answer(status=500, body={"error": "down"})
        assert run(monkeypatch, "mcp__notes__read", {"note": "todo"}) == "ask"


class TestDecide:
    def test_an_ask_is_lowered_by_an_llm_allow(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__forge__*": "decide"})
        assert run(monkeypatch, "mcp__forge__create_tag", {"tag": "v1"}) == "allow"
        entry = last_entry(hook_log)
        assert entry["steps"][-1] == {"step": "lower", "from": "ask", "to": "allow",
                                      "rule": "tools.mcp__forge__create_tag: decide"}

    def test_an_llm_ask_keeps_the_ask(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__forge__*": "decide"})
        stub_model.answer("ask", "a release is public")
        assert run(monkeypatch, "mcp__forge__create_tag", {"tag": "v1"}) == "ask"

    def test_a_human_only_action_type_is_not_sent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__forge__*": "decide"}, human_only={"git_remote_write": True})
        assert run(monkeypatch, "mcp__forge__create_tag", {"tag": "v1"}) == "ask"
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: git_remote_write)"

    def test_an_allow_is_reviewed_too(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "decide"})
        stub_model.answer("ask", "an odd note to read")
        assert run(monkeypatch, "mcp__notes__read", {"note": "x"}) == "ask"

    def test_a_floored_read_is_not_sent(self, stub_model, project_root, hook_log, monkeypatch, secrets_home):
        use(stub_model.url, {"Read": "decide"}, sensitive_paths={f"{secrets_home}/.aws": "ask"})
        assert run(monkeypatch, "Read", {"file_path": f"{secrets_home}/.aws/credentials"}) == "ask"
        assert run(monkeypatch, "Read", {"file_path": "/etc/hosts"}) == "allow"
        assert len(stub_model.requests) == 1  # the allow, not the floored credential read

    def test_an_unattributed_ask_is_human_only(self, stub_model, project_root, hook_log, monkeypatch, tmp_path):
        use(stub_model.url, {"Grep": "decide"})
        assert run(monkeypatch, "Grep", {"pattern": "password", "path": str(tmp_path)}) == "ask"
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: unattributed)"


class TestWordsThatSendNothing:
    @pytest.mark.parametrize("word,delivered", [("defer", ""), ("approve", "allow")])
    def test_no_request(self, stub_model, project_root, hook_log, monkeypatch, word, delivered):
        use(stub_model.url, {"mcp__notes__*": word})
        assert run(monkeypatch, "mcp__notes__read", {"note": "x"}) == delivered
        assert run(monkeypatch, "mcp__forge__create_tag", {"tag": "v1"}) == "ask"
        assert stub_model.requests == []

    def test_unlisted_tools_defer(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {})
        assert run(monkeypatch, "mcp__notes__read", {"note": "x"}) == ""
        assert stub_model.requests == []


class TestWhatTheModelIsShown:
    def test_tool_and_input_json(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"})
        run(monkeypatch, "mcp__notes__read", {"note": "todo", "limit": 3})
        prompt = stub_model.last_prompt
        assert "Tool: mcp__notes__read" in prompt
        assert '"note": "todo"' in prompt and '"limit": 3' in prompt
        assert "input cut here" not in prompt

    def test_a_long_input_is_cut_with_a_visible_marker(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"mcp__notes__*": "check"}, llm={"max_action_chars": 200})
        run(monkeypatch, "mcp__notes__read", {"note": "n" * 5000})
        prompt = stub_model.last_prompt
        assert "…[nuh: input cut here, 200 of " in prompt and "the rest was not reviewed]" in prompt
        assert "n" * 300 not in prompt
        assert last_entry(hook_log)["llm"]["action"]["input_cut"] is True
