"""The ``llm.self_heal`` cooldown is the replay cap, counted from the end of the last attempt (llm_heal.py).

``llm_heal.HEAL_CAP_S`` is cut to seconds here. A detached worker would not
see that, so the worker code runs in this process instead: on a thread
where the hook would spawn one, or called directly. The provider is the
stub Ollama host (tests/heal_stub.py); a POST takes ``load_s``, a cold load.
"""

import base64
import json
import os
import threading
import time

import pytest

from nuh import llm, llm_heal, llm_heal_worker
from tests import heal_stub

MODEL = "nimble:9b"
CAP_S = 2.0
LOAD_S = 1.8  # longer than half the cap: the old cooldown (5 of the 10 minutes) at this scale
TIMEOUT_S = 0.2


@pytest.fixture
def state(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(llm_heal, "HEAL_CAP_S", CAP_S)
    return llm_heal.state_dir()


@pytest.fixture
def workers(state, monkeypatch):
    """Each worker the hook starts: the real worker code (``run``) on a thread here, joined at the end."""
    started: list[threading.Thread] = []

    def spawn(job: bytes, directory: str) -> threading.Thread:
        thread = threading.Thread(target=llm_heal_worker.run, args=(json.loads(job), directory), daemon=True)
        thread.start()
        started.append(thread)
        return thread

    monkeypatch.setattr(llm_heal, "_spawn", spawn)
    yield started
    for thread in started:
        thread.join(timeout=30)


@pytest.fixture
def host():
    with heal_stub.serve() as h:
        h.listed, h.load_s = [MODEL], LOAD_S
        yield h


def _review(url: str) -> None:
    """One hook review whose ollama attempt times out (a POST takes LOAD_S, the provider waits TIMEOUT_S)."""
    llm_config = {"enabled": True, "providers": ["ollama"],
                  "ollama": {"url": url + "/api/chat", "model": MODEL, "timeout": TIMEOUT_S}}
    result = llm.try_llm_generic("mcp__x__y", {"q": 1}, {"decision": "ask", "reason": "unknown tool"}, llm_config)
    assert result.failed and "timeout" in result.cascade[0].error.lower()


def _until(when: float) -> None:
    time.sleep(max(0.0, when - time.time()))


def _stamped(path: str) -> float:
    """The state file's mtime once a worker has stamped it (the worker creates it empty, then writes)."""
    deadline = time.monotonic() + 10
    while not (os.path.exists(path) and os.path.getsize(path)) and time.monotonic() < deadline:
        time.sleep(0.01)
    return os.stat(path).st_mtime


def test_no_worker_starts_while_a_heal_runs_or_within_a_cap_of_its_end(host, workers, state):
    path = llm_heal.state_file(state, "ollama")
    _review(host.url)
    [worker] = workers
    started = _stamped(path)
    _until(started + CAP_S / 2)  # past the old cooldown; the replay still loading
    _review(host.url)
    assert worker.is_alive() and len(workers) == 1
    worker.join(timeout=30)
    ended = os.stat(path).st_mtime
    assert ended - started >= LOAD_S - 0.1  # stamped again at the end (file times: a coarser clock)
    _until(started + CAP_S)  # a cap after the start, under a cap after the end
    _review(host.url)
    assert len(workers) == 1
    _until(ended + CAP_S)
    _review(host.url)
    assert len(workers) == 2



def _job(url: str) -> dict:
    """A worker's job: a system1 request for MODEL to *url*."""
    body = base64.b64encode(json.dumps({"model": MODEL}).encode()).decode()
    return {"provider": "system1", "model": MODEL, "url": url, "method": "POST",
            "headers": {"Content-type": "application/json"}, "body": body}


def test_the_worker_cooldown_is_the_cap_from_the_last_end(host, state):
    host.load_s = 0.3
    job = _job(host.url + "/api/chat")
    assert llm_heal_worker.run(job, state)["outcome"] == "healed"
    ended = os.stat(llm_heal.state_file(state, "system1")).st_mtime
    host.loaded = []
    _until(ended + CAP_S * 0.75)
    assert llm_heal_worker.run(job, state)["outcome"] == "cooldown"
    _until(ended + CAP_S)
    assert llm_heal_worker.run(job, state)["outcome"] == "healed"
    assert len(host.posts) == 2


def test_the_replay_is_cut_at_the_cap(host, state, monkeypatch):
    monkeypatch.setattr(llm_heal, "HEAL_CAP_S", 0.5)
    host.load_s = 1.5
    record = llm_heal_worker.run(_job(host.url + "/api/chat"), state)
    assert record["outcome"] == "heal_failed" and "within 0.5 s" in record["error"]
    assert 500 <= record["load_ms"] < 1500


def test_a_worker_that_errors_still_stamps_its_end(state, monkeypatch):
    path = llm_heal.state_file(state, "system1")
    seen: list[float] = []

    def broken(job: dict) -> dict:
        seen.append(os.stat(path).st_mtime)
        time.sleep(0.3)
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(llm_heal_worker, "_probe_and_heal", broken)
    job = _job("http://127.0.0.1:9/api/chat")  # never reached: the probe fails first
    with pytest.raises(RuntimeError, match="probe blew up"):
        llm_heal_worker.run(job, state)
    [started] = seen
    assert os.stat(path).st_mtime - started >= 0.25  # file times run on a coarser clock than time.sleep
    with open(path, encoding="utf-8") as f:
        assert f.read().startswith("ended ")
    assert llm_heal_worker.run(job, state)["outcome"] == "cooldown"
