"""The ``llm.self_heal`` worker: probe a provider that timed out and, when its model is not loaded, load it.

Started detached by llm_heal.after_failure with the job (provider, model,
the request that timed out) on stdin. One run:

1. Lock ``llm-heal.<provider>`` (non-blocking ``flock``; held: ``busy``).
   A file stamped under ``llm_heal.HEAL_CAP_S`` ago (``in_cooldown``) means
   an attempt is running or ended recently: ``cooldown``. Otherwise the
   file is stamped now, and again when this attempt ends whatever comes of
   it, so the cooldown counts from the end of the last attempt.
2. Probe the host of the request's URL: ``GET /api/ps`` and ``/api/tags``,
   5 s each. Unreachable or an error status: ``down``. 404, or a reply
   that is not Ollama's: ``not_ollama``. The model not in ``/api/tags``:
   ``model_missing``. Already in ``/api/ps``: ``already_loaded`` (it is
   loaded and still timing out; loading it again would not help).
3. Otherwise replay the request (same URL, headers and body) capped at
   ``HEAL_CAP_S`` (10 minutes), discard the answer, and ask ``/api/ps`` again:
   ``healed`` when the model is now loaded, ``not_loaded`` when it is not,
   ``heal_failed`` when the replay itself failed.

Every run appends one JSON line to ``llm-heal.jsonl`` (time, provider,
model, host, outcome, elapsed_ms, error; load_ms and loaded after a
replay), kept to the newest ``KEEP_LINES``. Nothing from the request goes
there. The worker's stdout and stderr go nowhere, so that line is the only
place its failures can be seen: a crash is caught at the top and logged as
``error``.
"""

from __future__ import annotations

import base64
import fcntl
import http.client
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError

from nuh import llm_heal
from nuh.llm_heal import LOG_NAME, in_cooldown, state_file
from nuh.llm_providers import _exchange

PROBE_TIMEOUT_S = 5.0
KEEP_LINES = 500
_ERROR_CHARS = 300
# What a request can fail with: refused, unreachable, timed out, an error status (HTTPError), a broken reply.
_TRANSPORT = (OSError, http.client.HTTPException)

BUSY, COOLDOWN, DOWN, NOT_OLLAMA = "busy", "cooldown", "down", "not_ollama"
MODEL_MISSING, ALREADY_LOADED = "model_missing", "already_loaded"
HEALED, NOT_LOADED, HEAL_FAILED, ERROR = "healed", "not_loaded", "heal_failed", "error"


class _NotOllama(Exception):
    """The host answers, but not as an Ollama server."""


def main(directory: str) -> int:
    """The worker's entry point: read the job from stdin, run it, log one line. 0 when it healed."""
    raw = sys.stdin.buffer.read()
    t0 = time.monotonic()
    job: dict = {}
    try:
        job = _job(raw)
        record = run(job, directory)
    except Exception as exc:
        # Nobody sees the worker's stderr (the hook's caller must not wait on
        # it): the log line is the only place this failure can show.
        record = {"outcome": ERROR, "error": f"{type(exc).__name__}: {exc}"}
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": str(job.get("provider", "")), "model": str(job.get("model", "")),
            "host": _host(job["url"]) if job.get("url") else "", "outcome": record.pop("outcome"),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "error": str(record.pop("error", ""))[:_ERROR_CHARS], **record}
    append_log(directory, line)
    return 0 if line["outcome"] == HEALED else 1


def _job(raw: bytes) -> dict:
    job = json.loads(raw)
    if not isinstance(job, dict) or not str(job.get("provider", "")).isidentifier():
        raise ValueError("job has no valid provider name")
    for key in ("model", "url", "method", "body"):
        if not isinstance(job.get(key), str):
            raise ValueError(f"job has no {key}")
    if not isinstance(job.get("headers"), dict):
        raise ValueError("job has no headers")
    return job


def run(job: dict, directory: str) -> dict:
    """Lock, check the cooldown, probe and maybe heal; the outcome fields of the log line."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(state_file(directory, job["provider"]), flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"outcome": BUSY}
        st = os.fstat(fd)
        # An empty file was just created: no attempt yet. Otherwise its mtime is the last attempt's start or end.
        if st.st_size and in_cooldown(st.st_mtime):
            return {"outcome": COOLDOWN}
        _stamp(fd, "started")
        try:
            return _probe_and_heal(job)
        finally:
            _stamp(fd, "ended")  # an error too: the cooldown runs from this attempt's end
    finally:
        os.close(fd)  # releases the lock


def _stamp(fd: int, what: str) -> None:
    """Rewrite the state file: its mtime, now, is what the cooldown counts from."""
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{what} {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n".encode(), 0)


def _probe_and_heal(job: dict) -> dict:
    base, model = _host(job["url"]), job["model"]
    try:
        loaded = _models(base, "/api/ps")
        listed = _models(base, "/api/tags")
    except _NotOllama as exc:
        return {"outcome": NOT_OLLAMA, "error": str(exc)}
    except _TRANSPORT as exc:
        return {"outcome": DOWN, "error": _describe(exc)}
    if not _has(listed, model):
        return {"outcome": MODEL_MISSING}
    if _has(loaded, model):
        return {"outcome": ALREADY_LOADED}
    t0 = time.monotonic()
    try:
        _replay(job)
    except _TRANSPORT as exc:
        return {"outcome": HEAL_FAILED, "load_ms": int((time.monotonic() - t0) * 1000), "error": _describe(exc)}
    load_ms = int((time.monotonic() - t0) * 1000)
    try:
        now_loaded = _has(_models(base, "/api/ps"), model)
    except (*_TRANSPORT, _NotOllama) as exc:
        return {"outcome": NOT_LOADED, "load_ms": load_ms, "loaded": False,
                "error": f"/api/ps after the replay: {_describe(exc)}"}
    return {"outcome": HEALED if now_loaded else NOT_LOADED, "load_ms": load_ms, "loaded": now_loaded}


def _host(url: str) -> str:
    """``scheme://host[:port]`` of *url*, without any user:password part."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


def _models(base: str, path: str) -> list[dict]:
    """The ``models`` list of Ollama's *path* on *base*; _NotOllama when the host does not speak its API."""
    req = urllib.request.Request(base + path, method="GET")
    try:
        _status, raw = _exchange(req, PROBE_TIMEOUT_S)
    except HTTPError as exc:
        if exc.code == 404:
            raise _NotOllama(f"{path}: HTTP 404") from None
        raise
    try:
        models = json.loads(raw).get("models")
    except (ValueError, AttributeError):
        models = None
    if not isinstance(models, list):
        raise _NotOllama(f"{path}: no models list in the reply")
    return [m for m in models if isinstance(m, dict)]


def _has(models: list[dict], model: str) -> bool:
    """Whether *model* is among *models*; a name without a tag is Ollama's ``:latest``."""
    names = {str(m.get(k)) for m in models for k in ("name", "model") if m.get(k)}
    return model in names or (":" not in model and f"{model}:latest" in names)


def _replay(job: dict) -> None:
    req = urllib.request.Request(job["url"], data=base64.b64decode(job["body"]), headers=job["headers"],
                                 method=job["method"])
    _exchange(req, llm_heal.HEAL_CAP_S)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    return f"{type(exc).__name__}: {exc}"


def append_log(directory: str, record: dict) -> None:
    """Append *record* to the log, keeping its newest ``KEEP_LINES`` lines; failures go nowhere but stderr."""
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(os.path.join(directory, LOG_NAME), flags, 0o600), "r+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # another provider's worker may be writing
            lines = f.read().splitlines(keepends=True)
            line = json.dumps(record).encode() + b"\n"
            if len(lines) < KEEP_LINES:
                f.write(line)
                return
            f.seek(0)
            f.truncate()
            f.writelines([*lines[len(lines) - KEEP_LINES + 1:], line])
    except OSError as exc:
        # The log is the worker's only output; with it unwritable there is
        # nowhere left to report to but stderr, which goes nowhere in a
        # detached worker. Kept for a worker run by hand.
        sys.stderr.write(f"nuh: LLM self-heal: cannot write {LOG_NAME}: {exc}\n")


def read_log(directory: str) -> list[dict]:
    """The log's records, oldest first; unreadable lines are skipped with a note on stderr."""
    try:
        with open(os.path.join(directory, LOG_NAME), "rb") as f:
            raw = f.read().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in raw:
        try:
            record = json.loads(line)
        except ValueError:
            sys.stderr.write("nuh heal-log: skipped a line that is not JSON\n")
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
