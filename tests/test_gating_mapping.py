"""Each removed gating key and what replaced it, one test per row of the mapping.

Every test runs the whole hook (hook.main) against the stub model
(tests/llm_stub.py), so "the LLM was called" means a request reached it,
and "sent" / "silent" is what Claude Code receives for an allow.
"""

import io
import json
import os

import pytest

from nuh import config, content, hook
from nuh.config import _merge_configs
from tests.llm_stub import last_entry, llm_config

SECRET = "-----BEGIN PRIVATE KEY-----\nabc\n"
RUNNABLE = "#!/bin/sh\nrm -rf build\n"


def use(url: str, **global_cfg: object) -> None:
    llm = {**llm_config(url), **global_cfg.pop("llm", {})}
    config._cached_config = _merge_configs({"llm": llm, **global_cfg}, {})
    content.reset_content_patterns()


def run(monkeypatch, tool: str, tool_input: dict) -> str:
    """The permissionDecision the hook sends for the call; "" when it stays silent."""
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": tool, "tool_input": tool_input})))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"]["permissionDecision"] if text else ""


def bash(command: str = "frobnicate123 --x") -> tuple[str, dict]:
    return "Bash", {"command": command}


def write(path: str, text: str = "hi\n") -> tuple[str, dict]:
    return "Write", {"file_path": path, "content": text}


@pytest.fixture
def outside(tmp_path) -> str:
    os.makedirs(tmp_path / "outside", exist_ok=True)
    return str(tmp_path / "outside")


class TestEligibleBecameHumanOnly:
    """``llm.eligible`` -> ``human_only``: action types are false by default, as ``eligible: all``."""

    @pytest.mark.parametrize("command", ["frobnicate123 --x", "python3 -c 'print(1)'", "curl -s https://x.example./"])
    def test_eligible_all_is_the_default(self, stub_model, project_root, hook_log, monkeypatch, command):
        use(stub_model.url)
        assert run(monkeypatch, *bash(command)) == "allow"  # the stub allows: the ask was sent and lowered
        assert len(stub_model.requests) == 1

    def test_an_action_type_left_out_of_eligible_is_now_human_only_true(self, stub_model, project_root,
                                                                         hook_log, monkeypatch):
        use(stub_model.url, human_only={"lang_exec": True})
        assert run(monkeypatch, *bash("python3 -c 'print(1)'")) == "ask"
        assert run(monkeypatch, *bash()) == "allow"
        assert len(stub_model.requests) == 1


class TestAuthorityBecameTheToolsWord:
    """``llm.authority`` -> ``tools``: review -> decide, raise -> check, none -> approve or defer."""

    def test_review_is_decide(self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url, tools={"Write": "decide"})
        assert run(monkeypatch, *write(f"{outside}/a.txt")) == "allow"  # the boundary ask, lowered

    def test_raise_is_check(self, stub_model, project_root, outside, hook_log, monkeypatch):
        use(stub_model.url, tools={"Write": "check"})
        assert run(monkeypatch, *write(f"{outside}/a.txt")) == "ask"  # never lowered: not even sent
        assert stub_model.requests == []
        stub_model.answer("block", "overwrites the build")
        assert run(monkeypatch, *write(f"{project_root}/a.txt")) == "ask"  # an allow raised, capped to ask
        assert len(stub_model.requests) == 1

    @pytest.mark.parametrize("word,delivered", [("approve", "allow"), ("defer", "")])
    def test_none_is_approve_or_defer(self, stub_model, project_root, outside, hook_log, monkeypatch, word,
                                      delivered):
        use(stub_model.url, tools={"Write": word})
        assert run(monkeypatch, *write(f"{project_root}/a.txt")) == delivered
        assert run(monkeypatch, *write(f"{outside}/a.txt")) == "ask"
        assert stub_model.requests == []


class TestActiveAllowBecameApproveAndAbove:
    """``active_allow`` -> the word: approve, check and decide send an allow; defer leaves it silent."""

    @pytest.mark.parametrize("word,delivered", [("defer", ""), ("approve", "allow"), ("check", "allow"),
                                                ("decide", "allow")])
    def test_delivery_by_word(self, stub_model, project_root, hook_log, monkeypatch, word, delivered):
        use(stub_model.url, tools={"Glob": word})
        assert run(monkeypatch, "Glob", {"pattern": "*.py"}) == delivered

    def test_an_unlisted_tool_is_silent(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, classify={"filesystem_read": ["mcp__notes__read"]})
        assert run(monkeypatch, "mcp__notes__read", {}) == ""


class TestFloorBecameHumanOnly:
    """``floor:`` -> ``human_only``: never -> true (the default), review -> false."""

    def test_never_is_true_by_default(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        assert run(monkeypatch, *bash("nuh allow lang_exec")) == "ask"
        assert stub_model.requests == []

    def test_review_is_false(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, human_only={"nuh_admin": False})
        assert run(monkeypatch, *bash("nuh allow lang_exec")) == "allow"
        assert len(stub_model.requests) == 1


class TestMaxDecisionBecameCanBlock:
    """``llm.max_decision`` -> ``llm.can_block``."""

    def test_can_block_lets_an_llm_block_stand(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url, llm={"can_block": True})
        stub_model.answer("block", "exfiltrates")
        assert run(monkeypatch, *bash()) == "deny"

    def test_max_decision_block_no_longer_blocks(self, stub_model, project_root, hook_log, monkeypatch, capsys):
        use(stub_model.url, llm={"max_decision": "block"})
        stub_model.answer("block", "exfiltrates")
        assert run(monkeypatch, *bash()) == "ask"
        assert "unknown key llm.max_decision; ignored" in capsys.readouterr().err


class TestLowerableMarksBecameHumanOnlyKeys:
    """The hard-coded lowerable marks of a write ask -> human_only keys, each with today's default."""

    def test_outside_project_is_filesystem_write_false(self, stub_model, project_root, outside, hook_log,
                                                       monkeypatch):
        use(stub_model.url)
        assert run(monkeypatch, *write(f"{outside}/a.txt")) == "allow"
        use(stub_model.url, human_only={"filesystem_write": True})
        assert run(monkeypatch, *write(f"{outside}/a.txt")) == "ask"

    def test_runnable_text_is_lang_exec_false(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        assert run(monkeypatch, *write(f"{project_root}/run.sh", RUNNABLE)) == "allow"
        use(stub_model.url, human_only={"lang_exec": True})
        assert run(monkeypatch, *write(f"{project_root}/run.sh", RUNNABLE)) == "ask"

    def test_a_secret_is_content_secret_true(self, stub_model, project_root, hook_log, monkeypatch):
        use(stub_model.url)
        assert run(monkeypatch, *write(f"{project_root}/k.txt", SECRET)) == "ask"
        assert stub_model.requests == []
        use(stub_model.url, human_only={"content_secret": False})
        assert run(monkeypatch, *write(f"{project_root}/k.txt", SECRET)) == "allow"

    def test_a_config_added_category_is_content_custom_true(self, stub_model, project_root, hook_log,
                                                             monkeypatch):
        patterns = {"add": [{"category": "internal", "pattern": "ACME-INTERNAL", "description": "internal"}]}
        use(stub_model.url, content_patterns=patterns)
        assert run(monkeypatch, *write(f"{project_root}/a.txt", "ACME-INTERNAL\n")) == "ask"
        assert stub_model.requests == []
        use(stub_model.url, content_patterns=patterns, human_only={"content_custom": False})
        assert run(monkeypatch, *write(f"{project_root}/a.txt", "ACME-INTERNAL\n")) == "allow"

    def test_too_large_to_scan_is_content_too_large_true(self, stub_model, project_root, hook_log, monkeypatch):
        monkeypatch.setattr(content, "MAX_SCAN_CHARS", 10)
        use(stub_model.url)
        assert run(monkeypatch, *write(f"{project_root}/a.txt", "x" * 50)) == "ask"
        assert stub_model.requests == []
        use(stub_model.url, human_only={"content_too_large": False})
        assert run(monkeypatch, *write(f"{project_root}/a.txt", "x" * 50)) == "allow"

    def test_a_secret_holds_a_lowerable_ask_beside_it(self, stub_model, project_root, outside, hook_log,
                                                       monkeypatch):
        use(stub_model.url)
        assert run(monkeypatch, *write(f"{outside}/k.txt", SECRET)) == "ask"
        assert stub_model.requests == []
        assert last_entry(hook_log)["llm"]["skip_reason"] == "cannot change outcome (human_only: content_secret)"


class TestOldKeysAreUnknown:
    """A config that still sets a gating key tools and human_only replaced loads, warns as unknown, and ignores it."""

    @pytest.mark.parametrize("global_cfg,warning", [
        ({"active_allow": ["Bash"]}, "unknown key active_allow; ignored"),
        ({"floor": {"nuh_admin": "review"}}, "unknown key floor; ignored"),
        ({"llm": {"eligible": ["unknown"]}}, "unknown key llm.eligible; ignored"),
        ({"llm": {"authority": {"Bash": "none"}}}, "unknown key llm.authority; ignored"),
    ])
    def test_warns_and_changes_nothing(self, stub_model, project_root, hook_log, monkeypatch, capsys, global_cfg,
                                       warning):
        use(stub_model.url, **global_cfg)
        assert warning in capsys.readouterr().err
        assert run(monkeypatch, *bash()) == "allow"  # the default: decide, unknown false
        assert run(monkeypatch, *bash("nuh allow lang_exec")) == "ask"  # the default: nuh_admin true
        assert len(stub_model.requests) == 1

    def test_a_real_config_with_its_old_keys_behaves_as_migrated(self, stub_model, project_root,
                                                                 outside, hook_log, monkeypatch):
        old = {"active_allow": ["Bash", "Read", "Glob", "Grep"],
               "llm": {"eligible": "all", "authority": {"Write": "review", "Edit": "review"}}}
        calls = [bash(), bash("nuh allow lang_exec"), bash("ls"), write(f"{outside}/a.txt"),
                 write(f"{project_root}/k.txt", SECRET), ("Read", {"file_path": "/etc/hosts"}),
                 ("Glob", {"pattern": "*"})]
        use(stub_model.url, **old)
        before = [run(monkeypatch, *call) for call in calls]
        use(stub_model.url)
        assert [run(monkeypatch, *call) for call in calls] == before

    def test_the_one_delivery_change_is_a_reviewed_write_s_allow(self, stub_model, project_root, hook_log,
                                                                monkeypatch):
        # F4: active_allow left Write out, so its LLM-reviewed allow stayed silent. decide sends it.
        use(stub_model.url, active_allow=["Bash"], llm={"authority": {"Write": "review"}})
        assert run(monkeypatch, *write(f"{project_root}/a.txt")) == "allow"
