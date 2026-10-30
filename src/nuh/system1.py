"""The ``system1`` provider: a decision model asked fixed questions over /v1/systemone.

One more provider in ``llm.providers``, beside the prompted ones::

    llm:
      providers: [system1, ollama]
      system1: {url: https://classification.example, model: nimble:9b, keep_alive: 24h, timeout: 10}

The definition (system1_def.py) says what is asked; the answers decide by
their thresholds and gates (system1_rules.py, system1_gates.py) and the
reason is built from what triggered (no explainer model). Every question,
gate and the attempt itself are recorded as steps (steps.py).

The request carries the tool call and the user's messages, so it goes over
https, or plain http to loopback only: a tool call never crosses the
network in clear text. Anything that keeps a decision from coming back (bad
definition or URL, transport, a reply without answers, a gate that cannot
run, a body over the endpoint's 64 KiB) is a failure: the cascade moves on.
"""

from __future__ import annotations

import dataclasses
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from nuh import file_context, system1_def, system1_facts, system1_gates, system1_state
from nuh import system1_rules as rules
from nuh import trace as tracing
from nuh.llm_providers import _DEFAULT_MODELS, _TIMEOUT_REMOTE, SYSTEM1, _post, _provider_redacts
from nuh.llm_types import DECISION, FAILURE, UNCERTAIN, Answer, LLMResult
from nuh.redact import redact_secrets
from nuh.tool_call import ToolCall
from nuh.transcript import TranscriptRef, budget_from_config, user_messages

NAME = SYSTEM1
DEFAULT_URL = "http://localhost:11434"
PATH = "/v1/systemone"
MAX_BODY = 64 * 1024  # the largest body /v1/systemone accepts


class Failure(Exception):
    """Why the attempt got no decision; becomes the cascade attempt's error."""


def review(call: ToolCall, section: dict, llm_config: dict, transcript: str | TranscriptRef,
           trace: tracing.Trace | None) -> Answer:
    """Ask system 1 about *call*: a decision, uncertain, or a failure (an Answer for llm._cascade)."""
    model = str(section.get("model") or _DEFAULT_MODELS[NAME])
    t0 = time.monotonic()
    try:
        defn = system1_def.load(section, call.tool_name)
        url, timeout = endpoint(section.get("url", DEFAULT_URL)), _timeout(section)
        state = _state(defn, call, section, llm_config, transcript, trace)
        body = request_body(defn, model, state, section)
    except (rules.DefinitionError, Failure) as exc:
        return _failed(model, str(exc), t0)
    key = json.dumps([NAME, url, body], sort_keys=True)
    if key not in call.replies:
        # Asked once per hook invocation: a Codex apply_patch is reviewed per operation, but the call is one.
        call.replies[key] = _ask(defn, url, body, timeout, section, trace)
    answer = dataclasses.replace(call.replies[key], model=model, prompt=state)
    answer.steps = [*answer.steps, _provider_step(answer)]
    return answer


def _ask(defn: system1_def.Definition, url: str, body: dict, timeout: float, section: dict,
         trace: tracing.Trace | None) -> Answer:
    """Send *body* and decide from the reply, recorded as one trace attempt when tracing."""
    if trace is None:
        return _send_and_decide(defn, url, body, timeout, section)
    with trace.attempt(NAME) as attempt:
        answer = _send_and_decide(defn, url, body, timeout, section)
        said = None if answer.outcome == FAILURE else LLMResult(answer.decision or UNCERTAIN, answer.reasoning)
        attempt.finish(said, answer.latency_ms, answer.error)
    return answer


def _send_and_decide(defn: system1_def.Definition, url: str, body: dict, timeout: float, section: dict) -> Answer:
    reply, err, ms = _send(url, body, timeout, section)
    answer = Answer(FAILURE, error=err) if reply is None else decide(defn, reply)
    answer.latency_ms = ms
    return answer


def _failed(model: str, error: str, t0: float) -> Answer:
    sys.stderr.write(f"nuh: LLM {NAME}: {error}\n")
    answer = Answer(FAILURE, model=model, latency_ms=int((time.monotonic() - t0) * 1000), error=error)
    answer.steps = [_provider_step(answer)]
    return answer


# -- Config --


def endpoint(url: object) -> str:
    """The /v1/systemone URL for base *url*; Failure unless https, or plain http to loopback."""
    if not isinstance(url, str) or not url.strip():
        raise Failure("llm.system1.url is not set")
    base = re.sub(r"/(api|v1)(/.*)?$", "", url.strip().rstrip("/"))
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Failure(f"llm.system1.url must be an http(s) URL: {url}")
    if parts.scheme == "http" and not _loopback(parts.hostname):
        raise Failure(f"llm.system1.url: plain http only reaches loopback; use https for {parts.hostname}")
    return base + PATH


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A name, not an address: where it resolves can change, so it needs https.
        return False


def _timeout(section: dict) -> float:
    raw = section.get("timeout", _TIMEOUT_REMOTE)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    sys.stderr.write(f"nuh: config: llm.system1.timeout must be a positive number; using {_TIMEOUT_REMOTE}\n")
    return float(_TIMEOUT_REMOTE)


# -- Request --


def _state(defn: system1_def.Definition, call: ToolCall, section: dict, llm_config: dict,
           transcript: str | TranscriptRef, trace: tracing.Trace | None) -> str:
    """The state text for *call*: the user's messages, the file a write changes, the context items asked for."""
    messages, first = [], ""
    if defn.context.user:
        ref = transcript if isinstance(transcript, TranscriptRef) else TranscriptRef(transcript or "")
        messages, first, stats = user_messages(ref, budget_from_config(llm_config))
        if trace is not None:
            trace.record_context(stats)
    excerpt = ""
    if defn.context.file_lines > 0:
        settings = {"file_context_lines": defn.context.file_lines,
                    **{k: llm_config[k] for k in ("file_context_chars",) if k in llm_config}}
        found = file_context.build(call.tool_name, call.tool_input, settings)
        excerpt = found.excerpt if found is not None else ""
    facts = system1_facts.gather(defn.context, call) if system1_facts.wanted(defn.context) else None
    state, _clipped = system1_state.build(defn, call, messages, first, excerpt, facts)
    if _provider_redacts(NAME, section):
        # The placeholders say what was masked; the verdict on the masked state stands.
        state = redact_secrets(state)
    return state


def request_body(defn: system1_def.Definition, model: str, state: str, section: dict) -> dict:
    """The /v1/systemone request; Failure when it is over the endpoint's limit."""
    body: dict = {"model": model, "state": state, "questions": {q.name: _asked(q) for q in defn.questions.values()}}
    if section.get("keep_alive") is not None:
        body["keep_alive"] = section["keep_alive"]
    size = len(json.dumps(body).encode())
    if size > MAX_BODY:
        raise Failure(f"request is {size} bytes; {PATH} accepts at most {MAX_BODY}")
    return body


def _asked(q: rules.Question) -> dict:
    item: dict = {"type": q.type, "instructions": q.instructions}
    if q.criteria:
        item["criteria"] = q.criteria
    return item


# -- Transport --


def _send(url: str, body: dict, timeout: float, section: dict) -> tuple[dict | None, str, int]:
    headers = {"Content-Type": "application/json"}
    key_env = section.get("key_env")
    t0 = time.monotonic()
    try:
        if key_env:
            token = os.environ.get(str(key_env), "")
            if not token:
                raise Failure(f"{key_env} is not set")
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        reply = _reply(_post(req, timeout))
    except Failure as exc:
        tracing.record_exception(exc)
        sys.stderr.write(f"nuh: LLM {NAME}: {exc}\n")
        return None, str(exc), int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        err = _describe(exc, url, timeout)
        tracing.record_exception(exc)
        sys.stderr.write(f"nuh: LLM {NAME}: {err}\n")
        return None, err, int((time.monotonic() - t0) * 1000)
    return reply, "", int((time.monotonic() - t0) * 1000)


def _reply(raw: bytes) -> dict:
    try:
        reply = json.loads(raw)
    except ValueError as exc:
        raise Failure(f"reply is not JSON: {exc}") from exc
    tracing.record_parsed(reply)
    if not isinstance(reply, dict) or not isinstance(reply.get("answers"), dict):
        raise Failure("reply has no answers")
    return reply


def _describe(exc: Exception, url: str, timeout: float) -> str:
    """The error a failed request reports: ``timeout after 10 s``, ``HTTP 404: …``, ``… unreachable: …``."""
    if isinstance(exc, HTTPError):
        try:
            detail = json.loads(exc.read() or b"null")
        except (OSError, ValueError, http.client.HTTPException):
            # The status alone still says what failed; the body is detail.
            detail = None
        message = detail.get("error") if isinstance(detail, dict) else None
        return f"HTTP {exc.code}: {message or exc.reason}"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"timeout after {timeout:g} s"
    if isinstance(exc, (OSError, http.client.HTTPException)):
        return f"{url} unreachable: {type(exc).__name__}: {exc}"
    return f"unexpected error: {type(exc).__name__}: {exc}"


# -- Answers to a decision --


def decide(defn: system1_def.Definition, reply: dict) -> Answer:
    """The decision, uncertain, or (a gate could not run) failure for *reply*'s answers, with their steps."""
    raw = reply["answers"]
    values = {}
    for name, q in defn.questions.items():
        value = rules.read(q, raw.get(name))
        if value is not None:
            values[name] = value
    found = [t for q in defn.questions.values() for t in rules.triggers(q, values.get(q.name))]
    steps = [_question_step(q, values.get(q.name), found) for q in defn.questions.values()]
    try:
        fired = system1_gates.evaluate(defn.gates, defn.questions, values)
    except system1_gates.GateError as exc:
        sys.stderr.write(f"nuh: LLM {NAME}: {exc}\n")
        return Answer(FAILURE, error=str(exc), steps=steps)
    steps += [{"step": "gate", "provider": NAME, "name": f.gate.name, "then": f.gate.then, "fired": f.fired}
              for f in fired]
    found += [rules.Trigger(f.gate.then, f.gate.name, gate=True) for f in fired if f.fired]
    decision = rules.strictest(found)
    reasoning = rules.explain(decision, found, defn.questions, values, {f.gate.name: f.refs for f in fired})
    if decision == rules.UNCERTAIN:
        return Answer(UNCERTAIN, reasoning=reasoning, steps=steps)
    return Answer(DECISION, decision, reasoning, steps=steps)


def _question_step(q: rules.Question, value: rules.Value | None, found: list[rules.Trigger]) -> dict:
    step: dict = {"step": "question", "provider": NAME, "name": q.name, "type": q.type}
    if value is None:
        step["answer"] = None
    elif q.type == rules.CHOICE:
        step["answer"] = value.choice
        step["p"] = _round(value.probabilities.get(value.choice))
        step["probabilities"] = {k: _round(v) for k, v in value.probabilities.items()}
    else:
        step["answer"] = _round(value.number)
        if q.type == rules.SCORE:
            step["label"] = value.label
    own = [t for t in found if t.question == q.name and not t.gate]
    if own:
        step["triggered"] = rules.strictest(own)
        # Which threshold fired: the trace and log show why the question decided.
        step["rule"] = "; ".join(dict.fromkeys(t.rule for t in own if t.decision == step["triggered"] and t.rule))
    return step


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def _provider_step(answer: Answer) -> dict:
    """The attempt's own step: its outcome, decision, model and time, and the error of a failure."""
    step = {"step": "provider", "provider": NAME, "model": answer.model, "outcome": answer.outcome,
            "decision": answer.decision, "ms": answer.latency_ms}
    if answer.error:
        step["error"] = answer.error
    return step
