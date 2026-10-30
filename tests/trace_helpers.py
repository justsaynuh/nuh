"""Shared by the LLM trace tests (test_trace*.py): a fake HTTP layer, config and hook helpers."""

import io
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nuh import config, hook, llm_providers
from nuh.config import NuhConfig


KEY_ENV = "NUH_TRACE_TEST_KEY"
KEY = "sk-trace-test-SECRET-0123456789abcdef"
LONG_REASONING = "the command only lists files; " * 20  # well past the parser's 200-char cut
DIR_NAME = re.compile(r"^\d{8}T\d{6}\.\d{6}Z_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$")
BASH = {"tool_name": "Bash", "tool_input": {"command": "somethingunknown123 --flag"},
        "session_id": "sess-1", "transcript_path": ""}


class FakeHTTP:
    """Stands in for urllib.request.urlopen: records each request, plays back replies."""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[object, bytes, object]] = []

    def __call__(self, req, timeout=None):
        self.sent.append((req, req.data, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = reply
        return resp


def _decision(decision: str, reasoning: str = "ok") -> str:
    return json.dumps({"decision": decision, "reasoning": reasoning})


def ollama_reply(decision: str, reasoning: str = "ok") -> bytes:
    return json.dumps({"model": "qwen-test", "message": {"content": _decision(decision, reasoning)}}).encode()


def openrouter_reply(decision: str, reasoning: str = "ok") -> bytes:
    return json.dumps({"choices": [{"message": {"content": _decision(decision, reasoning)}}]}).encode()


def set_config(trace_dir: object, providers: tuple[str, ...] = ("ollama",)) -> None:
    config._cached_config = NuhConfig(
        llm={
            "enabled": True,
            "providers": list(providers),
            "ollama": {"url": "http://localhost:11434/api/chat", "model": "qwen-test"},
            "openrouter": {"url": "https://openrouter.test/api/v1/chat/completions",
                           "key_env": KEY_ENV, "model": "or-test"},
        },
        llm_trace_dir=str(trace_dir) if trace_dir else "",
    )


def use_http(monkeypatch: pytest.MonkeyPatch, *replies: object) -> FakeHTTP:
    http = FakeHTTP(*replies)
    monkeypatch.setattr(llm_providers.urllib.request, "urlopen", http)
    return http


def invoke(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict | None:
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue()) if out.getvalue() else None


def only_dir(root: Path) -> Path:
    dirs = list(root.iterdir())
    assert len(dirs) == 1, dirs
    return dirs[0]


def load(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text())
