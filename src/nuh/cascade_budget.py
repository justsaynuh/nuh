"""One wall-clock budget for the LLM cascade of a hook call, so nuh answers (and logs) before Claude Code kills it.

Claude Code cancels a command hook at its ``timeout`` (``nuh install`` writes
one, at least ``deadline.INSTALL_TIMEOUT_FLOOR_S``) and, installed with
``onFailure: block``, blocks the call with nothing in nuh's log. Each
provider keeps its own ``timeout``, but they add up: with two providers at
30 s each, a cold classifier and a slow model take the hook past a 60 s
timeout. So the cascade shares one budget: the hook timeout, minus a margin
for answering and logging, minus the time already spent since the hook
started. Each provider's request timeout is capped to what is left; a
provider reached with less than ``MIN_PROVIDER_S`` left is skipped with an
error saying so, and ``llm.on_failure`` decides the call as for any review
no provider answered.

The installed hook script (guard.py) passes the hook timeout it was
installed with (``ENV_TIMEOUT``) and the moment it started (``ENV_STARTED``).
Without them (an older hook script, tests, the CLI) the timeout is the
smallest one ``nuh install`` writes.

A capped request timeout bounds the whole request, connect to last byte
(``llm_providers._exchange``), so a server that drips bytes cannot outlast it.
"""

from __future__ import annotations

import os
import sys
import time

from nuh import deadline

ENV_TIMEOUT = "NUH_HOOK_TIMEOUT_S"
ENV_STARTED = "NUH_HOOK_STARTED"
MARGIN_S = 5.0        # after the cascade: merge, answer, log, exit
MIN_PROVIDER_S = 1.0  # a provider gets at least this much, or is skipped
# The guard's start mark must be this recent to count: an inherited, stale value would end the budget at once.
_MAX_STARTUP_S = 30.0

_ends_at: float | None = None  # monotonic time the cascade must be done by
_timeout_s: float = 0.0        # the hook timeout the budget was cut from (for the skip reason)


def hook_timeout_s() -> float:
    """The hook timeout Claude Code applies: the guard's ``ENV_TIMEOUT``, else the smallest ``nuh install`` writes."""
    raw = os.environ.get(ENV_TIMEOUT, "")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return value
        sys.stderr.write(f"nuh: {ENV_TIMEOUT} must be a positive number, got {raw!r}; "
                         f"using {deadline.INSTALL_TIMEOUT_FLOOR_S} s\n")
    return float(deadline.INSTALL_TIMEOUT_FLOOR_S)


def _started(fallback: float) -> float:
    """When the hook process started: the guard's mark when it is recent and not later than *fallback*."""
    try:
        mark = float(os.environ.get(ENV_STARTED, ""))
    except ValueError:
        return fallback  # no mark (or garbage): the hook's own start is the best known
    return mark if 0.0 <= fallback - mark <= _MAX_STARTUP_S else fallback


def start(started_at: float) -> None:
    """Start the cascade budget for this hook call; *started_at* is ``time.monotonic()`` at the hook's start."""
    global _ends_at, _timeout_s
    _timeout_s = hook_timeout_s()
    _ends_at = _started(started_at) + _timeout_s - MARGIN_S


def clear() -> None:
    """No budget (CLI, tests, after the hook call)."""
    global _ends_at
    _ends_at = None


def remaining() -> float | None:
    """Seconds the cascade may still use, or None when no budget is running."""
    if _ends_at is None:
        return None
    return max(0.0, _ends_at - time.monotonic())


def capped(timeout_s: float) -> float | None:
    """*timeout_s* cut to what the budget has left; None when too little is left to call a provider at all."""
    left = remaining()
    if left is None:
        return timeout_s
    if left < MIN_PROVIDER_S:
        return None
    return min(timeout_s, left)


def skip_reason() -> str:
    """The error a skipped provider records."""
    return (f"skipped: the hook's time budget ran out ({_timeout_s:g} s hook timeout, "
            f"{MARGIN_S:g} s kept to answer)")
