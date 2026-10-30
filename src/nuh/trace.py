"""Opt-in LLM trace directory — the full record of each hook invocation's LLM review.

Enabled by ``llm.trace_dir`` in the global config (off when unset or empty).
Every hook invocation that reaches the LLM stage gets one new directory::

    <trace_dir>/<YYYYMMDDTHHMMSS.ffffffZ>_<tool_use_id>/   (uuid4 when the payload has none)
        input.json                    the tool call exactly as nuh received it
        classification.json           the deterministic verdicts handed to the LLM
        request-NN-<provider>.json    one per provider attempt: the body as sent
        response-NN-<provider>.json   that attempt's raw response or exception
        result.json                   the LLM decision used, capping, final answer
                                      (its ``_steps``: the decision path, as the
                                      log's ``steps``), and per review the
                                      conversation context sent
                                      (section counts, chars, bytes scanned) and
                                      the action reviewed (size, limit, inline
                                      programs, too_large when not sent)

Traces are a local debugging aid and are NOT redacted, apart from auth header
values and credential query parameters in provider URLs: directories are
created 0700 and files 0600. Traces older than ``llm.trace_keep_days`` are
pruned at most once a day (trace_prune.py).

State is per invocation, like ``hook._transcript_path``: ``hook.main()`` calls
``begin()``/``end()`` around each call, the hook opens a review when it enters
the LLM stage and hands the returned ``Trace`` down the provider cascade, and
``llm._post`` (the only urlopen call site) records into the attempt that
``Trace.attempt()`` has made current.

Tracing never changes a decision or breaks the hook: nothing here raises, and
the first failed write disables the invocation's trace with one stderr line.
"""

import json
import os
import re
import sys
from contextlib import contextmanager
from typing import Callable, Iterator

from nuh import trace_names
from nuh.trace_redact import redact_headers, redact_url

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
_NAME_ATTEMPTS = 8

_invocation: dict | None = None   # hook payload + agent, set by begin()
_trace: "Trace | None" = None     # created on the invocation's first review
_attempt: "_Attempt | None" = None  # the provider attempt in flight


# -- Invocation lifecycle (hook.main) --


def begin(agent: str, payload: dict) -> None:
    """Remember this invocation's payload. No I/O and no config read."""
    global _invocation, _trace, _attempt
    _invocation = {"agent": agent, "payload": payload}
    _trace = None
    _attempt = None


def current_name() -> str:
    """The trace directory name for this invocation, or "" when nothing was traced."""
    if _trace is None or not _trace.path:
        return ""
    return os.path.basename(_trace.path)


def end() -> None:
    """Forget the invocation (hook.main's finally)."""
    global _invocation, _trace, _attempt
    _invocation = None
    _trace = None
    _attempt = None


def start_review(kind: str, classification: Callable[[], dict]) -> "Trace | None":
    """Enter the LLM stage: return the invocation's trace, or None when tracing is off.

    The first review creates the trace directory and writes input.json; each
    review appends *classification()* (built only when tracing is on) to
    classification.json.
    """
    global _trace
    if _invocation is None:
        return None
    if _trace is None:
        trace_dir = _configured_dir()
        if not trace_dir:
            return None
        _trace = Trace(trace_dir, _invocation)
    _trace.add_review(kind, classification)
    return _trace


@contextmanager
def capture() -> Iterator["MemoryTrace"]:
    """Trace the LLM reviews run inside the block in memory, whatever ``llm.trace_dir`` says.

    For ``nuh test --show-request``: the reviews record exactly what the hook's
    would, and nothing reaches the disk.
    """
    global _invocation, _trace, _attempt
    memory = MemoryTrace()
    _invocation, _trace, _attempt = {"agent": "", "payload": {}}, memory, None
    try:
        yield memory
    finally:
        end()


def record_call(llm_call: object, returned: dict | None) -> None:
    """Record the cascade result and the decision dict the hook will cap and use."""
    if _trace is not None:
        _trace.record_call(llm_call, returned)


def record_error(exc: BaseException) -> None:
    """Record an exception that ended the current review before a result."""
    if _trace is not None:
        _trace.record_error(exc)


def finish(decision: dict | None, emitted: dict | None, total_ms: int, error: str = "") -> None:
    """Write result.json for the invocation, if it was traced; then prune old traces, at most daily.

    The hook calls this after printing its decision, so the prune check
    (one stat of trace_prune's marker) never delays the decision.
    """
    if _trace is not None:
        _trace.finish(decision, emitted, total_ms, error)
        if _trace.path:
            _trace.guard(_prune_old, os.path.dirname(_trace.path))


def _prune_old(root: str) -> None:
    from nuh import trace_prune  # lazy import: only a traced invocation pays for it
    from nuh.config import get_config  # lazy import to avoid circular
    trace_prune.maybe_prune(root, get_config().llm_trace_keep_days)


# -- Provider transport (llm._post / llm._parse_response) --


def record_request(req: object, timeout: object) -> None:
    """Record the request object exactly as it is handed to urlopen."""
    if _attempt is not None:
        _attempt.trace.guard(_attempt.record_request, req, timeout)


def record_http_response(status: object, raw: object, latency_ms: int) -> None:
    """Record a completed HTTP exchange: status, latency and the raw body."""
    if _attempt is not None:
        _attempt.trace.guard(_attempt.record_http, status, raw, latency_ms, None)


def record_http_error(exc: BaseException, latency_ms: int) -> None:
    """Record a transport failure (timeout, refused, HTTP error status)."""
    if _attempt is not None:
        _attempt.trace.guard(_attempt.record_http_error, exc, latency_ms)


def record_content(content: object) -> None:
    """Record the content string a provider extracted from its response."""
    if _attempt is not None:
        _attempt.response["content"] = content


def record_parsed(obj: object) -> None:
    """Record the provider's decision JSON in full (the parser truncates reasoning)."""
    if _attempt is not None:
        _attempt.response["parsed"] = obj


def record_prompt_check(check: dict) -> None:
    """Record a provider's check that the model read the whole prompt (Ollama prompt_eval_count)."""
    if _attempt is not None:
        _attempt.response["prompt_check"] = check


def record_exception(exc: BaseException) -> None:
    """Record the exception a provider attempt ended with."""
    if _attempt is not None:
        _attempt.trace.guard(_attempt.record_exception, exc)


# -- The trace directory --


class Trace:
    """One invocation's trace directory. Every method is safe to call after a failure."""

    def __init__(self, root: str, invocation: dict) -> None:
        self.path = ""
        self._failed = False
        self._reviews: list[dict] = []
        self._attempts = 0
        self.guard(self._create, root, invocation)

    def guard(self, fn: Callable, *args: object) -> None:
        """Run a trace write; the first failure disables this trace."""
        if self._failed:
            return
        try:
            fn(*args)
        except Exception as exc:
            # Tracing is a debugging aid: a full disk, a permission error or
            # unserializable data must never alter or break the hook's
            # decision. Report once, then stop tracing this invocation.
            self._failed = True
            try:
                sys.stderr.write(f"nuh: trace: {type(exc).__name__}: {exc}\n")
            except (OSError, ValueError):
                # stderr is closed or broken: there is nowhere left to
                # report, and raising would break the hook's decision.
                pass

    def _create(self, root: str, invocation: dict) -> None:
        if not os.path.isdir(root):
            os.makedirs(root, mode=0o700, exist_ok=True)
            os.chmod(root, 0o700)
        call_id = trace_names.call_id(invocation.get("payload") or {})
        for attempt in range(_NAME_ATTEMPTS):
            path = os.path.join(root, trace_names.dir_name(call_id, attempt))
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                continue
            os.chmod(path, 0o700)
            self.path = path
            break
        else:
            raise FileExistsError(f"no unused trace directory name under {root}")
        self._write("input.json", _input_record(invocation))

    def _write(self, name: str, obj: object) -> None:
        data = json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        target = os.path.join(self.path, name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(target, flags, 0o600), "wb") as f:
            f.write(data)
        os.chmod(target, 0o600)

    def add_review(self, kind: str, classification: Callable[[], dict]) -> None:
        self.guard(self._add_review, kind, classification)

    def _add_review(self, kind: str, classification: Callable[[], dict]) -> None:
        # Snapshot now: the hook keeps mutating its decision dicts afterwards.
        snapshot = json.loads(json.dumps(classification(), default=str))
        self._reviews.append({"kind": kind, "classification": snapshot, "attempts": []})
        self._write("classification.json", {"reviews": [
            {"kind": r["kind"], **r["classification"]} for r in self._reviews
        ]})

    def record_call(self, llm_call: object, returned: dict | None) -> None:
        self.guard(self._record_call, llm_call, returned)

    def _record_call(self, llm_call: object, returned: dict | None) -> None:
        review = self._reviews[-1]
        decision = getattr(llm_call, "decision", None)
        review["llm"] = {
            "decision": dict(decision) if isinstance(decision, dict) else decision,
            "provider": getattr(llm_call, "provider", ""),
            "model": getattr(llm_call, "model", ""),
            "latency_ms": getattr(llm_call, "latency_ms", 0),
            "reasoning": getattr(llm_call, "reasoning", ""),
            "cascade": [vars(a) for a in getattr(llm_call, "cascade", [])],
        }
        # The hook caps this dict in place and may return it as the final
        # decision: keep the object to compare against at finish().
        review["returned_ref"] = returned
        review["returned"] = dict(returned) if returned is not None else None

    def record_context(self, stats: dict) -> None:
        """Record what conversation context the review's prompt carried."""
        if self._reviews:
            self._reviews[-1]["context"] = dict(stats)

    def record_action(self, stats: dict) -> None:
        """Record the action the review's prompt carried, or that it was too large to send."""
        if self._reviews:
            self._reviews[-1]["action"] = dict(stats)

    def record_error(self, exc: BaseException) -> None:
        self.guard(self._record_error, exc)

    def _record_error(self, exc: BaseException) -> None:
        self._reviews[-1]["error"] = _exception(exc)

    @contextmanager
    def attempt(self, provider: str) -> Iterator["_Attempt"]:
        """Make a provider attempt current so the transport layer can record it."""
        global _attempt
        self._attempts += 1
        current = _Attempt(self, self._attempts, provider)
        if self._reviews:
            self._reviews[-1]["attempts"].append(current.stem)
        _attempt = current
        try:
            yield current
        finally:
            _attempt = None
            self.guard(current.close)

    def finish(self, decision: dict | None, emitted: dict | None, total_ms: int, error: str) -> None:
        self.guard(self._finish, decision, emitted, total_ms, error)

    def _finish(self, decision: dict | None, emitted: dict | None, total_ms: int, error: str) -> None:
        from nuh.config import get_config  # lazy import to avoid circular
        used = None
        reviews = []
        for r in self._reviews:
            ref = r.get("returned_ref")
            capped = _decision_fields(ref) if ref is not None else None
            if ref is not None and ref is decision:
                used = capped
            reviews.append({
                "kind": r["kind"],
                "attempts": r["attempts"],
                "context": r.get("context"),
                "action": r.get("action"),
                "llm": r.get("llm"),
                "llm_decision": r.get("returned"),
                "after_cap": capped,
                "capped": bool(capped) and capped.get("decision") != (r.get("returned") or {}).get("decision"),
                **({"error": r["error"]} if "error" in r else {}),
            })
        self._write("result.json", {
            "final": _decision_fields(decision) if decision is not None else None,
            "hook_output": emitted,
            "llm_decision_used": used,
            "can_block": get_config().llm_can_block,
            "reviews": reviews,
            "total_ms": total_ms,
            **({"error": error} if error else {}),
        })


class _Attempt:
    """One provider attempt: writes request-NN-<p>.json and response-NN-<p>.json."""

    def __init__(self, trace: Trace, index: int, provider: str) -> None:
        self.trace = trace
        self.stem = f"{index:02d}-{_UNSAFE_NAME.sub('_', str(provider)) or 'provider'}"
        self.header = {"attempt": index, "provider": provider}
        self.sent = False
        self.response: dict = {}

    def record_request(self, req: object, timeout: object) -> None:
        self.sent = True
        raw = req.data
        body_raw = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        body = _json_or_none(body_raw)
        self.trace._write(f"request-{self.stem}.json", {
            **self.header,
            "sent": True,
            "method": req.get_method(),
            "url": redact_url(req.full_url),
            "model": body.get("model", "") if isinstance(body, dict) else "",
            "timeout": timeout,
            "headers": redact_headers(req.header_items()),
            "body": body,
            "body_raw": body_raw,
        })

    def record_http(self, status: object, raw: object, latency_ms: int, error: dict | None) -> None:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        self.response["http"] = {
            "status": status if isinstance(status, int) else None,
            "latency_ms": latency_ms,
            "body_raw": text,
            "body": _json_or_none(text),
            **({"error": error} if error else {}),
        }

    def record_exception(self, exc: BaseException) -> None:
        self.response["exception"] = _exception(exc)

    def record_http_error(self, exc: BaseException, latency_ms: int) -> None:
        status = getattr(exc, "code", None)
        raw = None
        if isinstance(status, int) and hasattr(exc, "read"):
            # HTTPError carries the server's error body (e.g. "invalid key");
            # the cascade only uses str(exc), so reading it changes nothing.
            raw = exc.read()
        self.record_http(status, raw, latency_ms, _exception(exc))

    def finish(self, result: object, elapsed_ms: int, error: str) -> None:
        """Record the attempt's outcome as the cascade sees it."""
        self.trace.guard(self._finish, result, elapsed_ms, error)

    def _finish(self, result: object, elapsed_ms: int, error: str) -> None:
        self.response["outcome"] = {
            "status": "error" if result is None else "success",
            "latency_ms": elapsed_ms,
            "decision": getattr(result, "decision", None),
            "reasoning": getattr(result, "reasoning", None),
            "error": error,
        }

    def close(self) -> None:
        if not self.sent:
            self.trace._write(f"request-{self.stem}.json", {
                **self.header, "sent": False,
                "note": "no HTTP request was made (see the response file's outcome)",
            })
        self.trace._write(f"response-{self.stem}.json", {
            **self.header, "sent": self.sent, **self.response,
        })


class MemoryTrace(Trace):
    """A trace whose records stay in ``files`` (name -> record) instead of a directory."""

    def __init__(self) -> None:
        self.files: dict[str, object] = {}
        super().__init__("", {})

    def _create(self, root: str, invocation: dict) -> None:
        """Nothing to create: the records live in ``files``."""

    def _write(self, name: str, obj: object) -> None:
        # The same JSON round trip as a file write, so a record cannot change
        # after it is taken (the hook mutates its decision dicts later).
        self.files[name] = json.loads(json.dumps(obj, ensure_ascii=False, default=str))


# -- Helpers --


def _configured_dir() -> str:
    from nuh.config import get_config  # lazy import to avoid circular
    return get_config().llm_trace_dir


def _input_record(invocation: dict) -> dict:
    from nuh import __version__
    from nuh.paths import get_project_root  # lazy import to avoid circular
    payload = invocation["payload"]
    return {
        "agent": invocation["agent"],
        "tool_name": payload.get("tool_name", ""),
        "tool_input": payload.get("tool_input", {}),
        "session_id": payload.get("session_id", ""),
        "transcript_path": payload.get("transcript_path", ""),
        "cwd": os.getcwd(),
        "project_root": get_project_root() or "",
        "nuh_version": __version__,
        "payload": {k: v for k, v in payload.items() if k != "tool_input"},
    }


def _decision_fields(decision: dict) -> dict:
    """The agent-facing part of an internal decision dict."""
    return {k: v for k, v in decision.items() if k in ("decision", "reason", "_hint", "_steps")}


def _exception(exc: BaseException) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}


def _json_or_none(text: object) -> object:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None
