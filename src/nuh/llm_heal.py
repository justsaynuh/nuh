"""``llm.self_heal``: after a provider times out, warm its model in a detached worker (llm_heal_worker.py).

A cold Ollama load can take longer than a provider's ``timeout``. Every hook
call then times out, Ollama may drop the load when the client disconnects,
and the model never loads until someone sends a long request by hand. With
``llm.self_heal`` on (the default), a provider attempt that timed out starts
one background worker for that provider; the hook goes on at once, exactly
as without it (next provider, or ``llm.on_failure``).

The worker probes the provider's host (``/api/ps``, ``/api/tags``) and, only
when the server is up and the model is listed but not loaded, replays the
request that timed out with a ``HEAL_CAP_S`` (10-minute) cap so the load
can finish. Its state and log live in ``~/.local/state/nuh``:
``llm-heal.<provider>`` (lock and cooldown) and ``llm-heal.jsonl`` (one line
per worker run, no request content; ``nuh heal-log`` shows it).

The cooldown is the cap itself (``in_cooldown`` reads ``HEAL_CAP_S``, so the
two cannot drift). A worker stamps the provider's file when its attempt
starts and again when it ends, whatever the outcome: no worker starts while
an attempt runs (its start stamp stays under a cap old for as long as the
replay may last), and the next attempt comes at least one cap after the
last one ended. The worker's ``flock`` still guards an attempt that
outlives the cap (its probes come on top of the replay).

``llm_providers._post`` notes the request that timed out (``note_timeout``);
the cascade clears it before each attempt (``forget``) and, after a failed
one, hands it to ``after_failure``. The job (the request that timed out)
reaches the worker as its stdin: an in-memory file (``memfd_create``),
written whole before the worker starts, so the hook may exit at once
(guard.py ends with ``os._exit``). Where there is no memfd (macOS) it is
an unlinked temporary file instead, which the OS may page to disk.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from typing import IO, TYPE_CHECKING
from urllib.error import URLError

if TYPE_CHECKING:
    import subprocess

KEY = "self_heal"
HEAL_CAP_S = 600.0  # the replay's time limit, and the cooldown between a provider's attempts
LOG_NAME = "llm-heal.jsonl"

_timed_out: urllib.request.Request | None = None  # the request of the current attempt that timed out


def state_dir() -> str:
    """Where the worker keeps its lock files and log: ``~/.local/state/nuh``."""
    return os.path.join(os.path.expanduser("~"), ".local", "state", "nuh")


def state_file(directory: str, provider: str) -> str:
    """Provider *provider*'s lock and cooldown file in *directory*."""
    return os.path.join(directory, f"llm-heal.{provider}")


def in_cooldown(mtime: float) -> bool:
    """Whether a state file stamped at *mtime* is under ``HEAL_CAP_S`` old: an attempt running or recently ended.

    The one cooldown check, for the hook (``after_failure``) and the worker
    alike; it reads the cap at call time, so the cooldown is always the cap.
    """
    return 0 <= time.time() - mtime < HEAL_CAP_S


def parse(llm: dict, *, warn: bool = False) -> bool:
    """``llm.self_heal``: true (default) or false; anything else is the default."""
    raw = llm.get(KEY, True) if isinstance(llm, dict) else True
    if isinstance(raw, bool):
        return raw
    if warn:
        sys.stderr.write(f"nuh: config: llm.{KEY} must be true or false, got {raw!r}; using true\n")
    return True


def is_timeout(exc: BaseException) -> bool:
    """Whether *exc* is a request running out of time (the whole-request deadline or a socket timeout)."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    return isinstance(reason, TimeoutError)


def note_timeout(req: urllib.request.Request) -> None:
    """Remember *req* as the request that timed out (llm_providers._post)."""
    global _timed_out
    _timed_out = req


def forget() -> None:
    """Drop any noted request: called before each cascade attempt, so a note belongs to the attempt after it."""
    global _timed_out
    _timed_out = None


def after_failure(provider: str, model: str, llm_config: dict) -> subprocess.Popen[bytes] | None:
    """Start a heal worker for *provider* when its failed attempt timed out; the worker, else None.

    Nothing starts when ``llm.self_heal`` is off, the attempt failed for
    another reason, or the provider's state file was stamped under
    ``HEAL_CAP_S`` ago: an attempt is running or ended within the cooldown
    (one stat). The worker re-checks the cooldown under its lock, so
    concurrent hook calls start at most one heal.
    """
    req = _timed_out
    forget()
    if req is None or not parse(llm_config):
        return None
    directory = state_dir()
    job = {"provider": provider, "model": model, "url": req.full_url, "method": req.get_method(),
           "headers": dict(req.headers), "body": base64.b64encode(req.data or b"").decode()}
    try:
        if _cooling(state_file(directory, provider)):
            return None
        return _spawn(json.dumps(job).encode(), directory, provider, model)
    except OSError as exc:
        # Healing is a side errand: a state dir nuh cannot stat or a worker
        # that cannot start must not change the review's outcome. Reported,
        # and the cascade goes on as without self_heal.
        sys.stderr.write(f"nuh: LLM self-heal: could not start a worker for {provider}: {exc}\n")
        return None


def _cooling(path: str) -> bool:
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return False
    return in_cooldown(mtime)


def _spawn(job: bytes, directory: str, provider: str, model: str) -> subprocess.Popen[bytes]:
    """Run the worker detached: its own session, no inherited stdio, the job on its stdin.

    Claude Code waits for the hook's stdout and stderr to close, so the
    worker gets neither. The job is a file written whole before the worker
    starts (``_job_file``), never a pipe: the hook can exit the moment this
    returns without cutting the job short, whatever its size. *provider*
    and *model* (not secret) also go on the worker's command line, so its
    log line names them even when the job cannot be read.
    """
    import subprocess  # lazy import: needed only after a timeout
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # -I: neither the hook's cwd (a project, maybe untrusted) nor PYTHON* variables shape what the child imports.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from nuh.llm_heal_worker import main; "
            "sys.exit(main(*sys.argv[2:]))")
    with _job_file(job) as stdin:
        return subprocess.Popen(
            [sys.executable, "-I", "-c", code, package_parent, directory, provider, model],
            stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd="/", start_new_session=True, close_fds=True)


def _job_file(job: bytes) -> IO[bytes]:
    """*job* in a file at offset 0: in memory (memfd), or an unlinked temporary file where there is no memfd."""
    if hasattr(os, "memfd_create"):
        f: IO[bytes] = os.fdopen(os.memfd_create("nuh-heal-job", os.MFD_CLOEXEC), "w+b")
    else:
        import tempfile  # lazy import: macOS only
        f = tempfile.TemporaryFile()
    try:
        f.write(job)
        f.flush()
        f.seek(0)
    except OSError:
        f.close()
        raise
    return f
