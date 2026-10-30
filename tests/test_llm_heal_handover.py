"""How the heal job reaches the worker (llm_heal._spawn) when the hook exits the moment it has started it.

guard.py, the installed hook, ends with ``os._exit(0)``: no thread of the
hook outlives that call. Each test here runs the real ``_spawn`` in a child
interpreter shaped like it (``-I``, spawn, ``os._exit(0)`` at once), then
waits for the worker's log line. The provider URL is a closed loopback
port, so a worker that got its whole job logs ``down``.
"""

import base64
import fcntl
import json
import os
import subprocess
import sys
import time

import pytest

from nuh import llm_heal, llm_heal_worker
from tests import heal_stub
from tests.llm_stub import closed_port

SPAWN = llm_heal._spawn  # the real one, taken before the conftest replaces it per test
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(llm_heal.__file__)))
MODEL = "qwen3-coder:latest"

# argv: package parent, state dir, provider URL, body bytes, "tempfile" (no memfd, as on macOS) or
# "pipe8k" (any pipe the worker's stdin gets is cut to 8 KiB, as for a user past fs.pipe-user-pages-soft).
GUARD_SHAPED = """
import base64, fcntl, json, os, subprocess, sys
sys.path.insert(0, sys.argv[1])
from nuh import llm_heal
state, url, size, how = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
if how == "tempfile":
    del os.memfd_create
if how == "pipe8k":
    real = subprocess.Popen
    def popen(*args, **kwargs):
        proc = real(*args, **kwargs)
        if proc.stdin is not None:
            fcntl.fcntl(proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, 8192)
        return proc
    subprocess.Popen = popen
job = {"provider": "ollama", "model": %r, "url": url, "method": "POST",
       "headers": {"Content-type": "application/json"}, "body": base64.b64encode(b"a" * size).decode()}
llm_heal._spawn(json.dumps(job).encode(), state, "ollama", %r)
os._exit(0)
""" % (MODEL, MODEL)


def _wait_for_line(state: str) -> dict:
    deadline = time.monotonic() + 20
    while not llm_heal_worker.read_log(state) and time.monotonic() < deadline:
        time.sleep(0.05)
    [record] = llm_heal_worker.read_log(state)
    return record


@pytest.mark.parametrize("size, how", [
    (100_000, "memfd"),
    pytest.param(100_000, "tempfile", marks=pytest.mark.skipif(not hasattr(os, "memfd_create"),
                                                               reason="the fallback is the only path here")),
    pytest.param(16_000, "pipe8k", marks=pytest.mark.skipif(not hasattr(fcntl, "F_SETPIPE_SZ"),
                                                            reason="pipe sizes are Linux only")),
])
def test_the_worker_gets_the_whole_job_when_the_hook_exits_at_once(tmp_path, size, how):
    state = str(tmp_path / "state")
    url = f"http://127.0.0.1:{closed_port()}/api/chat"
    done = subprocess.run([sys.executable, "-I", "-c", GUARD_SHAPED, PACKAGE_PARENT, state, url, str(size), how],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and done.stderr == ""
    record = _wait_for_line(state)
    assert (record["provider"], record["model"], record["outcome"]) == ("ollama", MODEL, "down"), record
    assert "refused" in record["error"].lower()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd") or not hasattr(os, "memfd_create"), reason="Linux only")
def test_the_workers_stdin_is_an_in_memory_file_not_a_pipe(tmp_path):
    """Why pipe capacity no longer matters: there is no pipe, so no buffer to fill and no writer to outlive."""
    with heal_stub.serve() as host:
        host.listed, host.load_s = [MODEL], 1.0  # keeps the worker alive, its stdin open, while this looks
        body = json.dumps({"model": MODEL, "prompt": "a" * 15_000}).encode()  # a job of about 21 KB
        job = json.dumps({"provider": "ollama", "model": MODEL, "url": host.url + "/api/chat", "method": "POST",
                          "headers": {}, "body": base64.b64encode(body).decode()}).encode()
        proc = SPAWN(job, str(tmp_path / "state"), "ollama", MODEL)
        try:
            assert proc.stdin is None
            assert os.readlink(f"/proc/{proc.pid}/fd/0").startswith("/memfd:nuh-heal-job")
        finally:
            proc.wait(timeout=30)
    assert llm_heal_worker.read_log(str(tmp_path / "state"))[-1]["outcome"] == "healed"
