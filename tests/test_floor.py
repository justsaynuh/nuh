"""The static floor: floored decisions never reach the LLM, are logged, and only global human_only opens one."""

import json

import pytest

from nuh import bash_review, config, floor, hook, hook_write, log, paths, taxonomy
from nuh.bash_types import ClassifyResult, StageResult
from nuh.config import NuhConfig


def _floored_result(rule: str = "nuh_admin", decision: str = taxonomy.ASK) -> ClassifyResult:
    sr = StageResult(tokens=["nuh", "allow", "lang_exec"], action_type="nuh_admin",
                     default_policy=taxonomy.ASK, decision=decision, reason="nuh_admin → ask", floor=rule)
    return ClassifyResult(command="nuh allow lang_exec", stages=[sr], final_decision=decision,
                          reason="nuh_admin → ask")


def _llm_config(**extra) -> NuhConfig:
    return NuhConfig(llm={"enabled": True}, **extra)


class TestVerdict:
    def test_unpacks_and_compares_like_a_pair(self):
        verdict = floor.Verdict(taxonomy.ASK, "why", "credential_paths")
        decision, reason = verdict
        assert (decision, reason) == (taxonomy.ASK, "why")
        assert verdict == (taxonomy.ASK, "why")
        assert floor.rule_of(verdict) == "credential_paths"

    def test_plain_pair_has_no_rule(self):
        assert floor.rule_of((taxonomy.ASK, "why")) == ""
        assert floor.rule_of(None) == ""


class TestHumanOnlyOpensAFloor:
    def test_false_lets_the_llm_decide_a_rule(self):
        config._cached_config = config._merge_configs({"human_only": {"credential_paths": False}}, {})
        assert floor.is_enforced("credential_paths") is False
        assert floor.is_enforced("git_exec") is True

    def test_project_config_cannot_open_a_floor(self, capsys):
        config._cached_config = config._merge_configs({}, {"human_only": {"credential_paths": False}})
        assert floor.is_enforced("credential_paths") is True
        assert "human_only is global-only; ignored in .nuh.yaml" in capsys.readouterr().err

    def test_old_floor_key_is_unknown_and_opens_nothing(self, capsys):
        config._cached_config = config._merge_configs({"floor": {"credential_paths": "review"}}, {})
        assert floor.is_enforced("credential_paths") is True
        assert capsys.readouterr().err == "nuh: config: unknown key floor; ignored\n"

    def test_unreadable_config_keeps_every_floor(self, monkeypatch):
        monkeypatch.setattr(config, "get_config", lambda: (_ for _ in ()).throw(config.ConfigError("bad")))
        assert floor.is_enforced("credential_paths") is True


class TestBashFloor:
    def test_floored_ask_never_calls_llm(self, monkeypatch):
        config._cached_config = _llm_config()
        monkeypatch.setattr(hook, "classify_command", lambda cmd: _floored_result())
        monkeypatch.setattr(hook, "_try_llm", lambda result: pytest.fail("LLM called on a floored ask"))
        decision = hook.handle_bash({"command": "nuh allow lang_exec"})
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "nuh_admin"

    def test_floored_ask_is_held(self):
        config._cached_config = _llm_config()
        route = bash_review.route(_floored_result(), "decide")
        assert (route.kind, route.held) == ("", ["nuh_admin"])

    def test_human_only_false_lets_the_llm_lower_it(self, monkeypatch):
        config._cached_config = _llm_config(human_only={"nuh_admin": False})
        monkeypatch.setattr(hook, "classify_command", lambda cmd: _floored_result())
        monkeypatch.setattr(hook, "_try_llm", lambda result: ({"decision": taxonomy.ALLOW}, {}))
        decision = hook.handle_bash({"command": "nuh allow lang_exec"})
        assert decision["decision"] == taxonomy.ALLOW
        assert "_floor" not in decision

    def test_human_only_false_sends_it_to_the_llm(self):
        config._cached_config = NuhConfig(human_only={"nuh_admin": False})
        assert bash_review.route(_floored_result(), "decide").kind == bash_review.ASK

    def test_one_floored_stage_holds_the_whole_command(self, monkeypatch):
        config._cached_config = _llm_config()
        plain = StageResult(tokens=["foo"], action_type=taxonomy.UNKNOWN, decision=taxonomy.ASK, reason="unknown")
        result = _floored_result()
        result.stages.insert(0, plain)
        monkeypatch.setattr(hook, "classify_command", lambda cmd: result)
        monkeypatch.setattr(hook, "_try_llm", lambda r: pytest.fail("LLM called on a floored ask"))
        assert hook.handle_bash({"command": "foo; nuh allow lang_exec"})["_floor"] == "nuh_admin"

    def test_inner_stage_floor_counts(self):
        inner = StageResult(tokens=["nuh", "forget", "x"], decision=taxonomy.ASK, floor="nuh_admin")
        outer = StageResult(tokens=["bash", "-c", "nuh forget x"], decision=taxonomy.ASK, inner_stages=[inner])
        result = ClassifyResult(command="bash -c 'nuh forget x'", stages=[outer], final_decision=taxonomy.ASK)
        assert result.floor_rules() == ["nuh_admin"]

    def test_allowed_stage_floor_ignored(self):
        sr = StageResult(tokens=["ls"], decision=taxonomy.ALLOW, floor="credential_paths")
        result = ClassifyResult(command="ls", stages=[sr], final_decision=taxonomy.ALLOW)
        assert result.floor_rules() == []


class TestWriteFloor:
    def test_floored_write_ask_skips_veto_gate(self, monkeypatch):
        config._cached_config = _llm_config()
        monkeypatch.setattr(paths, "check_path", lambda tool, path: {
            "decision": taxonomy.ASK, "reason": "floored", "_floor": "agent_hooks"})
        monkeypatch.setattr(hook_write, "_llm_veto_gate", lambda *a: pytest.fail("LLM called on a floored write"))
        decision = hook.handle_write({"file_path": "x", "content": "y"})
        assert decision["_floor"] == "agent_hooks"

    def test_reviewable_write_floor_reaches_veto_gate(self, monkeypatch):
        config._cached_config = _llm_config(human_only={"agent_hooks": False})
        monkeypatch.setattr(paths, "check_path", lambda tool, path: {
            "decision": taxonomy.ASK, "reason": "floored", "_floor": "agent_hooks"})
        seen = []
        monkeypatch.setattr(hook_write, "_llm_veto_gate", lambda tool, ti, det: seen.append(tool) or det)
        hook.handle_write({"file_path": "x", "content": "y"})
        assert seen == ["Write"]


class TestPatchFloor:
    def test_floored_path_skips_veto_and_keeps_rule(self, project_root, monkeypatch):
        config._cached_config = _llm_config()
        monkeypatch.chdir(project_root)
        monkeypatch.setattr(hook_write, "_llm_veto_gate", lambda *a: pytest.fail("LLM called on a floored patch"))
        patch = "*** Begin Patch\n*** Add File: ok.txt\n+hi\n*** Add File: .nuh.yaml\n+x\n*** End Patch"
        decision = hook.handle_apply_patch({"command": patch})
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "project_config"


class TestFloorLog:
    def _logged(self) -> dict:
        with open(log.LOG_PATH) as f:
            return json.loads(f.readlines()[-1])

    def test_floored_ask_logs_rule_and_replay_flag(self):
        hook._log_hook_decision("Bash", {"command": "nuh allow x"},
                                {"decision": taxonomy.ASK, "reason": "r", "_floor": "nuh_admin"}, "claude", 1)
        entry = self._logged()
        assert entry["floor"] == "nuh_admin"
        assert entry["llm_replayable"] is True

    def test_floored_block_logs_rule_without_replay(self):
        hook._log_hook_decision("Bash", {"command": "rm -rf /"},
                                {"decision": taxonomy.BLOCK, "reason": "r", "_floor": "catastrophic"}, "claude", 1)
        entry = self._logged()
        assert entry["floor"] == "catastrophic"
        assert "llm_replayable" not in entry

    def test_reviewed_rule_is_not_logged_as_floor(self):
        config._cached_config = NuhConfig(human_only={"nuh_admin": False})
        hook._log_hook_decision("Bash", {"command": "nuh allow x"},
                                {"decision": taxonomy.ASK, "reason": "r", "_floor": "nuh_admin"}, "claude", 1)
        assert "floor" not in self._logged()
