"""A provider's timeout bounds its whole request, not each socket read (llm_providers._exchange).

The stub model (tests/llm_stub.py) is a real HTTP server on 127.0.0.1 that
sends its reply one byte every 0.2 s. Every gap is far below the 1 s timeout,
so urllib's per-operation timeout alone would read the ~70-byte reply for
about 14 s; the whole-request deadline fails it at 1 s, like a socket timeout.
"""

import io
import json
import os
import time
import urllib.request

import pytest

from nuh import config, hook, llm_providers
from nuh.config import _merge_configs
from tests.llm_stub import last_entry, llm_config

DRIP_S = 0.2
TIMEOUT_S = 1.0
SLACK_S = 0.5  # thread start and scheduling; far below the 13 s the drip would take


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(f"{url}/api/chat", data=b'{"model": "stub"}',
                                  headers={"Content-Type": "application/json"}, method="POST")


@pytest.mark.parametrize("headers_too", [False, True], ids=["body", "status-and-headers"])
def test_a_dripping_reply_times_out_at_the_request_timeout(stub_model, headers_too):
    stub_model.drip, stub_model.drip_headers = DRIP_S, headers_too
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="no complete response within 1 s"):
        llm_providers._post(_request(stub_model.url), TIMEOUT_S)
    elapsed = time.monotonic() - started
    assert TIMEOUT_S <= elapsed < TIMEOUT_S + SLACK_S


def test_a_prompt_reply_is_returned_whole(stub_model):
    stub_model.answer("allow", "fine")
    raw = llm_providers._post(_request(stub_model.url), TIMEOUT_S)
    assert json.loads(json.loads(raw)["message"]["content"]) == {"decision": "allow", "reasoning": "fine"}


@pytest.fixture
def script_call(project_root, hook_log, monkeypatch) -> dict:
    """A clean script run, statically allowed and then reviewed by the LLM; the log in tmp."""
    monkeypatch.chdir(project_root)
    with open(os.path.join(project_root, "probe.py"), "w") as f:
        f.write("print(sum(range(10)))\n")
    return {"tool_name": "Bash", "tool_input": {"command": "python3 probe.py"}, "session_id": "s",
            "tool_use_id": "toolu_drip", "cwd": project_root}


def _run_hook(monkeypatch, payload: dict) -> tuple[dict, float]:
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    started = time.monotonic()
    hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"], time.monotonic() - started


@pytest.mark.parametrize("on_failure, expected", [("ask", "ask"), ("static", "allow")])
def test_on_failure_decides_a_dripping_review_and_it_is_logged(stub_model, script_call, hook_log, monkeypatch,
                                                                on_failure, expected):
    stub_model.drip = DRIP_S
    section = llm_config(stub_model.url, on_failure=on_failure)
    section["ollama"]["timeout"] = TIMEOUT_S
    monkeypatch.setattr(config, "_cached_config", _merge_configs({"llm": section}, {}))
    out, elapsed = _run_hook(monkeypatch, script_call)
    assert out["permissionDecision"] == expected
    assert elapsed < TIMEOUT_S + SLACK_S + 1.0  # the static work and logging, not the 13 s drip
    llm = last_entry(hook_log)["llm"]
    assert llm["skipped"] == "error"
    assert llm["cascade"][0]["status"] == "error"
    assert "TimeoutError: no complete response within 1 s" in llm["error"]
