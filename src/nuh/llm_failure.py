"""A review that failed asks: a failed LLM step never leaves a reviewed call allowed.

Two failures give no verdict (log ``llm.skipped: error``, the error in ``llm.error``):

- transport: every provider in the cascade errored (unreachable, timeout,
  bad reply, an Ollama prompt cut past ``num_ctx`` so the model may not have
  read the action);
- a crash: an exception in nuh's own LLM step (building the prompt, reading
  the transcript, the trace, the result's metadata).

Either way the decision is ask, which the ``llm.can_block`` cap leaves alone.
On a static allow that rests on the review (a clean script file, any write
while the LLM is on) the ask raises the allow; on a static ask it keeps it.
``llm.on_failure: static`` keeps the static decision instead (llm_fallback).

A model that answers but reaches no decision (system 2's "uncertain",
system 1 with nothing past its thresholds) is not a failure: someone
looked. ``llm.on_uncertain`` decides it (llm_fallback).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Protocol

ERROR = "error"  # llm.skipped for a review that got no verdict


class _Attempt(Protocol):
    provider: str
    status: str
    error: str


def provider_errors(cascade: Sequence[_Attempt]) -> str:
    """Each attempt's error when every attempt in *cascade* errored; "" when one answered or none ran."""
    if not cascade or any(a.status != ERROR for a in cascade):
        return ""
    return "; ".join(f"{a.provider}: {a.error or 'error'}" for a in cascade)


def then(on_failure: str) -> str:
    """What a failed review goes on to do under *on_failure* (``llm.on_failure``), as a reason says it.

    The decision dict stays ask either way; with ``static`` the hook keeps the
    static decision instead (llm_fallback), so the reason must not say "asking".
    """
    return "keeping the static decision (on_failure: static)" if on_failure == "static" else "asking"


def unanswered(label: str, cascade: Sequence[_Attempt], on_failure: str = "ask") -> dict | None:
    """The ask for a cascade in which no provider answered; None when one did (or none ran)."""
    errors = provider_errors(cascade)
    if not errors:
        return None
    sys.stderr.write(f"nuh: llm: {label}: no provider answered, {then(on_failure)} ({errors})\n")
    return {"decision": "ask", "reason": f"{label} (LLM): no verdict, {then(on_failure)}: {errors}"}


def crashed(label: str, exc: Exception, on_failure: str = "ask") -> tuple[dict, dict]:
    """(decision, llm_meta) for a review that raised *exc*: ask, with the error on stderr and in the log."""
    err = f"{type(exc).__name__}: {exc}"
    sys.stderr.write(f"nuh: llm: {label} review failed: {err}\n")
    return ({"decision": "ask", "reason": f"{label}: LLM review failed, {then(on_failure)}: {err}"},
            {"llm_skipped": ERROR, "llm_error": err})
