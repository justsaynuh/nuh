"""The ``llm.self_heal`` worker (llm_heal_worker.py), run for real as the detached process the hook starts.

Each test starts the worker with ``llm_heal._spawn`` (the conftest stops
every other test from doing so) against a stub Ollama host on 127.0.0.1
(tests/heal_stub.py), waits for it, and reads its log line.
"""

import base64
import json
import os
import stat
import time

import pytest

from nuh import llm_heal, llm_heal_worker
from tests import heal_stub
from tests.llm_stub import closed_port

SPAWN = llm_heal._spawn  # the real one, taken before the conftest replaces it per test
MODEL = "nimble:9b"
MARKER = "do-not-store-7f3a"  # in the request body only: must never reach the state dir
BODY = json.dumps({"model": MODEL, "state": f"rm -rf ./{MARKER}", "keep_alive": "24h"}).encode()


def _job(url: str, *, model: str = MODEL, headers: dict | None = None, provider: str = "system1") -> bytes:
    return json.dumps({"provider": provider, "model": model, "url": url, "method": "POST",
                       "headers": headers or {"Content-type": "application/json"},
                       "body": base64.b64encode(BODY).decode()}).encode()


def _run(state: str, job: bytes) -> dict:
    """Run one worker to its end; its log line."""
    before = len(llm_heal_worker.read_log(state))
    assert SPAWN(job, state, "system1", MODEL).wait(timeout=30) in (0, 1)
    records = llm_heal_worker.read_log(state)
    assert len(records) == before + 1
    return records[-1]


@pytest.fixture
def state(tmp_path) -> str:
    return str(tmp_path / "state" / "nuh")


@pytest.fixture
def host():
    with heal_stub.serve() as h:
        h.listed = [MODEL, "other:1b"]
        yield h


@pytest.mark.parametrize("path, timing", [
    ("/v1/systemone", {"model_load_ms": None, "timing_note": "no load timing from /v1/systemone"}),
    ("/api/chat", {"model_load_ms": 300, "prompt_ms": 20, "server_ms": 340}),  # the stub's Ollama durations
])
def test_a_cold_model_is_loaded_by_replaying_the_exact_request(host, state, path, timing):
    host.load_s = 0.3
    headers = {"Content-type": "application/json", "Authorization": "Bearer tok-1"}
    record = _run(state, _job(host.url + path, headers=headers))
    assert record["outcome"] == "healed" and record["loaded"] is True
    assert record["ready_ms"] >= 300 and record["elapsed_ms"] >= record["ready_ms"]
    assert {key: record.get(key) for key in timing} == timing and "load_ms" not in record
    assert (record["provider"], record["model"], record["host"], record["error"]) == ("system1", MODEL, host.url, "")
    [post] = host.posts
    assert post["path"] == path and post["body"] == BODY
    assert post["headers"]["Authorization"] == "Bearer tok-1"
    assert host.gets == ["/api/ps", "/api/tags", "/api/ps"]


def test_an_unreachable_host_is_down(state):
    record = _run(state, _job(f"http://127.0.0.1:{closed_port()}/v1/systemone"))
    assert record["outcome"] == "down" and "refused" in record["error"].lower()


def test_a_host_that_is_not_ollama(host, state):
    host.ollama = False
    record = _run(state, _job(host.url + "/v1/systemone"))
    assert record["outcome"] == "not_ollama" and "404" in record["error"]
    assert host.posts == []


def test_a_model_the_server_does_not_have(host, state):
    host.listed = ["other:1b"]
    record = _run(state, _job(host.url + "/api/chat"))
    assert record["outcome"] == "model_missing" and host.posts == []


def test_a_loaded_model_that_still_times_out_is_not_reloaded(host, state):
    host.loaded = [MODEL]
    record = _run(state, _job(host.url + "/api/chat"))
    assert record["outcome"] == "already_loaded" and host.posts == []


def test_a_failed_replay_is_logged(host, state):
    host.post_status = 500
    record = _run(state, _job(host.url + "/api/chat"))
    assert record["outcome"] == "heal_failed" and record["error"] == "HTTP 500"
    assert len(host.posts) == 1


def test_a_job_it_cannot_read_is_an_error_line_that_names_provider_and_model(state):
    assert SPAWN(b'{"provider": "olla', state, "ollama", MODEL).wait(timeout=30) == 1
    [record] = llm_heal_worker.read_log(state)
    assert (record["outcome"], record["provider"], record["model"]) == ("error", "ollama", MODEL)
    assert record["error"].startswith("ValueError: job unreadable (18 bytes received): Unterminated string")


@pytest.mark.parametrize("model, names, found", [
    ("qwen3", ["qwen3:latest"], True), ("qwen3:8b", ["qwen3:latest"], False), ("nimble:9b", ["nimble:9b"], True),
])
def test_an_untagged_model_is_its_latest_tag(model, names, found):
    assert llm_heal_worker._has([{"name": n} for n in names], model) is found


def test_a_second_run_within_the_cooldown_does_nothing(host, state):
    assert _run(state, _job(host.url + "/api/chat"))["outcome"] == "healed"
    host.loaded = []  # unloaded again: still, the cooldown holds
    assert _run(state, _job(host.url + "/api/chat"))["outcome"] == "cooldown"
    assert len(host.posts) == 1


def test_the_cooldown_counts_from_the_last_attempt(host, state):
    assert _run(state, _job(host.url + "/api/chat"))["outcome"] == "healed"
    old = time.time() - llm_heal.HEAL_CAP_S - 1
    os.utime(llm_heal.state_file(state, "system1"), (old, old))
    host.loaded = []
    assert _run(state, _job(host.url + "/api/chat"))["outcome"] == "healed"
    assert len(host.posts) == 2


def test_the_cooldown_is_per_provider(host, state):
    assert _run(state, _job(host.url + "/api/chat", provider="ollama"))["outcome"] == "healed"
    host.loaded = []
    assert _run(state, _job(host.url + "/v1/systemone"))["outcome"] == "healed"


def test_concurrent_workers_heal_once(host, state):
    host.load_s = 1.0
    procs = [SPAWN(_job(host.url + "/api/chat"), state, "system1", MODEL) for _ in range(4)]
    for proc in procs:
        proc.wait(timeout=30)
    outcomes = sorted(r["outcome"] for r in llm_heal_worker.read_log(state))
    assert outcomes.count("healed") == 1
    assert set(outcomes) - {"healed"} <= {"busy", "cooldown"} and len(outcomes) == 4
    assert len(host.posts) == 1


def test_nothing_from_the_request_is_written(host, state):
    _run(state, _job(host.url + "/v1/systemone", headers={"Authorization": f"Bearer {MARKER}"}))
    assert sorted(os.listdir(state)) == ["llm-heal.jsonl", "llm-heal.system1"]
    for name in os.listdir(state):
        with open(os.path.join(state, name), encoding="utf-8") as f:
            assert MARKER not in f.read()
        assert stat.S_IMODE(os.stat(os.path.join(state, name)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700


_NO_LOAD = {"model_load_ms": None, "timing_note": "no load_duration in the /api/chat reply"}


@pytest.mark.parametrize("url, reply, timing", [
    ("http://o/api/generate", b'{"load_duration": 4200000000, "total_duration": 9000000000}',
     {"model_load_ms": 4200, "server_ms": 9000}),
    ("http://o/api/chat", b'{"done": true}', _NO_LOAD),
    ("http://o/api/chat", b'{"message": {"content"', _NO_LOAD),  # cut short: not JSON
])
def test_ollama_timings_or_a_note_saying_why_there_are_none(url, reply, timing):
    assert llm_heal_worker.timings(url, reply) == timing


def test_the_log_keeps_its_newest_lines(state):
    os.makedirs(state)
    with open(os.path.join(state, llm_heal.LOG_NAME), "w") as f:
        f.writelines(json.dumps({"n": i}) + "\n" for i in range(llm_heal_worker.KEEP_LINES))
    llm_heal_worker.append_log(state, {"n": "new"})
    records = llm_heal_worker.read_log(state)
    assert len(records) == llm_heal_worker.KEEP_LINES
    assert records[0] == {"n": 1} and records[-1] == {"n": "new"}
