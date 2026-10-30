"""Unit tests for the LLM layer: try_llm and the providers (Cortex, Ollama /api/chat)."""

import json
import os
from unittest.mock import patch, MagicMock

from nuh.llm import try_llm
from tests.llm_helpers import _make_default_result


# -- try_llm tests --


class TestTryLlm:
    def _ollama_config(self):
        return {
            "providers": ["ollama"],
            "ollama": {"url": "http://localhost:11434/api/generate", "model": "test"},
        }

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_backend_returns_allow(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": '{"decision": "allow", "reasoning": "safe cmd"}'
        }).encode()
        mock_urlopen.return_value = mock_resp

        result = try_llm(_make_default_result(), self._ollama_config())
        assert result.decision["decision"] == "allow"
        assert "LLM" in result.decision.get("reason", "")
        assert result.provider == "ollama"
        assert result.model == "test"
        assert result.latency_ms >= 0
        assert len(result.cascade) == 1
        assert result.cascade[0].status == "success"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_backend_returns_block(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": '{"decision": "block", "reasoning": "dangerous"}'
        }).encode()
        mock_urlopen.return_value = mock_resp

        result = try_llm(_make_default_result(), self._ollama_config())
        assert result.decision["decision"] == "block"
        assert "LLM" in result.decision["reason"]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_backend_returns_uncertain(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": '{"decision": "uncertain", "reasoning": "not sure"}'
        }).encode()
        mock_urlopen.return_value = mock_resp

        result = try_llm(_make_default_result(), self._ollama_config())
        assert result.decision == {"decision": "ask", "reason": "Bash (LLM): uncertain: not sure; on_uncertain: ask"}
        assert result.uncertain and not result.failed
        assert [(a.provider, a.status) for a in result.cascade] == [("ollama", "uncertain")]

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_backend_unavailable_tries_next(self, mock_urlopen):
        from urllib.error import URLError
        call_count = [0]

        def side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise URLError("connection refused")
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"decision": "allow", "reasoning": "ok"}'}}]
            }).encode()
            return mock_resp

        mock_urlopen.side_effect = side_effect

        config = {
            "providers": ["ollama", "openrouter"],
            "ollama": {"url": "http://localhost:11434/api/generate", "model": "test"},
            "openrouter": {"url": "http://fake.api/v1/chat/completions", "model": "test", "key_env": "TEST_KEY"},
        }
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            result = try_llm(_make_default_result(), config)
        assert result.decision["decision"] == "allow"
        assert call_count[0] == 2
        assert len(result.cascade) == 2
        assert result.cascade[0].status == "error"
        assert result.cascade[1].status == "success"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_all_providers_unavailable(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("connection refused")

        result = try_llm(_make_default_result(), self._ollama_config())
        # No provider answered: a failed review, which asks (llm_failure).
        assert result.failed
        assert result.decision["decision"] == "ask"
        assert "connection refused" in result.decision["reason"]
        assert len(result.cascade) == 1
        assert result.cascade[0].status == "error"

    def test_empty_providers_list(self):
        result = try_llm(_make_default_result(), {"providers": []})
        assert result.decision is None

    def test_no_providers_key(self):
        result = try_llm(_make_default_result(), {})
        assert result.decision is None

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_backends_is_not_a_providers_list(self, mock_urlopen):
        config = {"backends": ["ollama"], "ollama": {"url": "http://localhost:11434/api/chat"}}
        result = try_llm(_make_default_result(), config)
        assert result.decision is None and result.cascade == []
        mock_urlopen.assert_not_called()

    def test_backend_not_in_config(self):
        result = try_llm(_make_default_result(), {"providers": ["ollama"]})
        assert result.decision is None  # ollama key missing -> skip

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_openai_backend_allow(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"decision": "allow", "reasoning": "safe"}'}
            ]}]
        }).encode()
        mock_urlopen.return_value = mock_resp

        config = {
            "providers": ["openai"],
            "openai": {"url": "https://api.openai.com/v1/responses", "model": "gpt-4.1-nano", "key_env": "TEST_KEY"},
        }
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            result = try_llm(_make_default_result(), config)
        assert result.decision["decision"] == "allow"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_anthropic_backend_allow(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"decision": "allow", "reasoning": "safe"}'}]
        }).encode()
        mock_urlopen.return_value = mock_resp

        config = {
            "providers": ["anthropic"],
            "anthropic": {"model": "claude-haiku-4-5", "key_env": "TEST_KEY"},
        }
        with patch.dict("os.environ", {"TEST_KEY": "fake-key"}):
            result = try_llm(_make_default_result(), config)
        assert result.decision["decision"] == "allow"

    def test_anthropic_no_key_skips(self):
        config = {
            "providers": ["anthropic"],
            "anthropic": {"model": "claude-haiku-4-5", "key_env": "NONEXISTENT_KEY_12345"},
        }
        result = try_llm(_make_default_result(), config)
        assert result.failed and result.decision["decision"] == "ask"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_allow_without_reasoning(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "response": '{"decision": "allow"}'
        }).encode()
        mock_urlopen.return_value = mock_resp

        result = try_llm(_make_default_result(), self._ollama_config())
        assert result.decision["decision"] == "allow"
        assert "reason" not in result.decision  # no reasoning = no reason key


# -- Cortex provider tests --


class TestCortexProvider:
    def _cortex_config(self, **overrides):
        cfg = {
            "providers": ["cortex"],
            "cortex": {"url": "https://myaccount.snowflakecomputing.com/api/v2/cortex/inference:complete",
                       "model": "claude-haiku-4-5"},
        }
        cfg["cortex"].update(overrides)
        return cfg

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_cortex_backend_allow(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"decision": "allow", "reasoning": "safe"}'}}]
        }).encode()
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {"SNOWFLAKE_PAT": "fake-pat"}):
            result = try_llm(_make_default_result(), self._cortex_config())
        assert result.decision["decision"] == "allow"
        assert result.provider == "cortex"
        assert result.model == "claude-haiku-4-5"
        assert result.cascade[0].status == "success"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_cortex_auth_header(self, mock_urlopen):
        """Verify PAT and token-type headers are sent."""
        captured = []

        def capture(req, **kw):
            captured.append(req)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"decision": "allow", "reasoning": "ok"}'}}]
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        with patch.dict("os.environ", {"SNOWFLAKE_PAT": "test-token-123"}):
            try_llm(_make_default_result(), self._cortex_config())
        assert len(captured) == 1
        req = captured[0]
        assert req.get_header("Authorization") == "Bearer test-token-123"
        assert req.get_header("X-snowflake-authorization-token-type") == "PROGRAMMATIC_ACCESS_TOKEN"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_cortex_account_url_derivation(self, mock_urlopen):
        """URL derived from account config when url not set."""
        captured = []

        def capture(req, **kw):
            captured.append(req)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"decision": "allow", "reasoning": "ok"}'}}]
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        config = {
            "providers": ["cortex"],
            "cortex": {"account": "snowhouse", "model": "claude-haiku-4-5"},
        }
        with patch.dict("os.environ", {"SNOWFLAKE_PAT": "fake-pat"}):
            try_llm(_make_default_result(), config)
        assert len(captured) == 1
        assert captured[0].full_url == "https://snowhouse.snowflakecomputing.com/api/v2/cortex/inference:complete"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_cortex_account_from_env(self, mock_urlopen):
        """SNOWFLAKE_ACCOUNT env var used when no url or account in config."""
        captured = []

        def capture(req, **kw):
            captured.append(req)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"decision": "allow", "reasoning": "ok"}'}}]
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        config = {"providers": ["cortex"], "cortex": {"model": "claude-haiku-4-5"}}
        with patch.dict("os.environ", {"SNOWFLAKE_PAT": "fake-pat", "SNOWFLAKE_ACCOUNT": "testacct"}):
            try_llm(_make_default_result(), config)
        assert len(captured) == 1
        assert "testacct.snowflakecomputing.com" in captured[0].full_url

    def test_cortex_no_pat_skips(self):
        """Missing SNOWFLAKE_PAT → provider skipped."""
        config = self._cortex_config()
        env = {k: v for k, v in os.environ.items() if k != "SNOWFLAKE_PAT"}
        with patch.dict("os.environ", env, clear=True):
            result = try_llm(_make_default_result(), config)
        assert result.failed and result.decision["decision"] == "ask"

    def test_cortex_no_account_no_url_skips(self):
        """No url and no account → provider skipped."""
        config = {"providers": ["cortex"], "cortex": {"model": "claude-haiku-4-5"}}
        env = {k: v for k, v in os.environ.items()
               if k not in ("SNOWFLAKE_PAT", "SNOWFLAKE_ACCOUNT")}
        with patch.dict("os.environ", env, clear=True):
            result = try_llm(_make_default_result(), config)
        assert result.failed and result.decision["decision"] == "ask"


# -- Ollama /api/chat tests --


class TestOllamaChat:
    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_default_url_uses_chat(self, mock_urlopen):
        """Default Ollama config should use /api/chat with messages."""
        captured = []

        def capture(req, **kw):
            captured.append({
                "url": req.full_url,
                "body": json.loads(req.data.decode()),
            })
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "message": {
                    "role": "assistant",
                    "content": '{"decision": "allow", "reasoning": "ok"}',
                },
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        config = {
            "providers": ["ollama"],
            "ollama": {"model": "test"},
        }
        try_llm(_make_default_result(), config)
        assert len(captured) == 1
        assert "/api/chat" in captured[0]["url"]
        body = captured[0]["body"]
        assert "messages" in body
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"

    @patch("nuh.llm_providers.urllib.request.urlopen")
    def test_legacy_generate_url(self, mock_urlopen):
        """Explicit /api/generate URL should use prompt string."""
        captured = []

        def capture(req, **kw):
            captured.append(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "response": '{"decision": "allow", "reasoning": "ok"}',
            }).encode()
            return resp

        mock_urlopen.side_effect = capture

        config = {
            "providers": ["ollama"],
            "ollama": {
                "url": "http://localhost:11434/api/generate",
                "model": "test",
            },
        }
        try_llm(_make_default_result(), config)
        assert len(captured) == 1
        assert "prompt" in captured[0]
        assert "messages" not in captured[0]
