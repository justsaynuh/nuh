"""The LLM step is skipped for a static ask no LLM verdict could change (llm_skip.py).

Each test runs the whole hook (hook.main) against the stub model
(tests/llm_stub.py), so "the LLM was called" means a request reached it.
"""

import io
import json
import os

import pytest

from nuh import config, hook, log_query
from nuh.config import _merge_configs
from nuh.log_view import LogView
from tests.llm_stub import last_entry, llm_config

SECRET = "-----BEGIN PRIVATE KEY-----\nabc\n"
# Codex takes its project from the git root of the payload's cwd, which holds the
# pytest tmp dir here; this target is outside any repo (and never created).
CODEX_OUTSIDE = "/nuh-test-outside/a.txt"


def use(url: str, tools: dict | None = None, human_only: dict | None = None, **llm_extra: object) -> None:
    config._cached_config = _merge_configs({"llm": {**llm_config(url), **llm_extra}, "tools": tools or {},
                                            "human_only": human_only or {}}, {})


def run(monkeypatch, payload: dict) -> dict:
    """The hook's whole stdout for *payload*."""
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue())


def edit(path: str, new: str = "hi\n") -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": new}}


def bash(command: str = "frobnicate123 --x") -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.fixture
def outside(tmp_path) -> str:
    os.makedirs(tmp_path / "outside", exist_ok=True)
    return str(tmp_path / "outside")


class TestSkipped:
    def test_outside_project_edit_under_check_does_not_wait_for_the_model(
            self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url, {"Edit": "check"})
        stub_model.answer("block", "would have blocked")
        out = run(monkeypatch, edit(f"{outside}/notes.md"))
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert "outside project" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "🤖" not in out.get("systemMessage", "")  # no LLM reason: the model was never asked
        llm = last_entry(hook_log)["llm"]
        assert (llm["provider"], llm["skipped"]) == ("(none)", "no_effect")
        assert llm["skip_reason"] == "cannot change outcome (tools.Edit: check reviews allows only)"

    def test_floored_ask_logs_why_it_was_not_sent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, can_block=True)
        out = run(monkeypatch, {"tool_name": "Write",
                                "tool_input": {"file_path": f"{project_root}/.nuh.yaml", "content": "x: 1\n"}})
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        entry = last_entry(hook_log)
        assert (entry["floor"], entry["llm"]["skip_reason"]) == (
            "project_config", "cannot change outcome (human_only: project_config)")

    def test_floored_bash_ask_logs_why_it_was_not_sent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        run(monkeypatch, bash("nuh allow lang_exec"))
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: nuh_admin)"

    def test_secret_in_a_decided_write_is_human_only(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        run(monkeypatch, {"tool_name": "Write",
                          "tool_input": {"file_path": f"{project_root}/k.txt", "content": SECRET}})
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: content_secret)"

    def test_human_only_action_type_holds_a_bash_ask(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, human_only={"unknown": True})
        out = run(monkeypatch, bash())
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: unknown)"

    def test_bash_check_sends_no_ask(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"Bash": "check"}, can_block=True)
        out = run(monkeypatch, bash())
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert "nuh classify frobnicate123" in out["hookSpecificOutput"]["permissionDecisionReason"]

    def test_codex_patch_outside_project_under_check(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, {"Edit": "check"})  # apply_patch follows Edit
        patch = f"*** Begin Patch\n*** Add File: {CODEX_OUTSIDE}\n+hi\n*** End Patch"
        out = run(monkeypatch, {"tool_name": "apply_patch", "tool_input": {"command": patch},
                                "turn_id": "turn-1", "cwd": project_root})
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"  # Codex cannot ask
        assert last_entry(hook_log)["llm"]["skipped"] == "no_effect"

    def test_patch_held_at_ask_by_a_secret_skips_the_lowerable_part(
            self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        patch = (f"*** Begin Patch\n*** Add File: {CODEX_OUTSIDE}\n+hi\n"
                 f"*** Add File: {project_root}/k.txt\n+-----BEGIN PRIVATE KEY-----\n*** End Patch")
        out = run(monkeypatch, {"tool_name": "apply_patch", "tool_input": {"command": patch},
                                "turn_id": "turn-1", "cwd": project_root})
        assert stub_model.requests == []
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestStillCalled:
    def test_outside_project_edit_under_decide_can_be_lowered(
            self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url)
        out = run(monkeypatch, edit(f"{outside}/notes.md"))
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_decide_with_can_block_can_raise(self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url, can_block=True)
        stub_model.answer("block", "voter risk=high p=0.97")
        out = run(monkeypatch, edit(f"{outside}/notes.md"))
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_unknown_bash_under_decide_can_be_lowered(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        out = run(monkeypatch, bash())
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_an_allow_is_always_reviewed_under_check(self, stub_model, project_root, hook_log, monkeypatch):
        # Under check and can_block false an LLM block still raises a static allow to ask.
        use(stub_model.url, {"Edit": "check"})
        stub_model.answer("block", "capped")
        out = run(monkeypatch, edit(f"{project_root}/notes.md"))
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
        said = log_query.outcome(last_entry(hook_log))
        assert (said.llm_decision, said.capped, said.reason_source) == ("block", "ask", "llm")

    def test_an_llm_ask_does_not_lower(self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url)
        stub_model.answer("ask", "unsure")
        out = run(monkeypatch, edit(f"{outside}/notes.md"))
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"

    def test_patch_with_a_lowerable_ask_only(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        patch = f"*** Begin Patch\n*** Add File: {CODEX_OUTSIDE}\n+hi\n*** End Patch"
        out = run(monkeypatch, {"tool_name": "apply_patch", "tool_input": {"command": patch},
                                "turn_id": "turn-1", "cwd": project_root})
        assert len(stub_model.requests) == 1
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestLogViews:
    def test_verbose_view_and_llm_filter(self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url, {"Edit": "check"})
        run(monkeypatch, edit(f"{outside}/notes.md"))
        entry = last_entry(hook_log)
        shown = LogView(color=False, width=200, details=True).render(entry)
        assert "skipped      no_effect — cannot change outcome (tools.Edit: check reviews allows only)" in shown
        assert "llm:skipped(no_effect)" in LogView(color=False, width=200).render(entry)
        assert not log_query.LogFilter(llm=True).matches(entry)

    def test_no_record_when_the_llm_is_off(self, project_root, outside, hook_log, monkeypatch):
        config._cached_config = _merge_configs({"llm": {"enabled": False}}, {})
        run(monkeypatch, edit(f"{outside}/notes.md"))
        assert "llm" not in last_entry(hook_log)
