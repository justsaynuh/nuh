"""Time budget for the static part of one hook call.

A Claude Code command hook that times out or crashes does not block (unless
installed with ``onFailure: block``): the call goes through the normal
permission flow. So input crafted to make inspection slow must never run nuh
past its own budget. The hook starts the clock (``hook.deadline_s``, default
20 s) and the static work checks it:

* ``scan_content`` checks before each pattern and, when the budget is gone,
  adds an ask match and stops (a block found already still blocks).
* Bash classification checks before each stage and raises
  ``InspectionTimeout``, which the hook turns into an ask.

LLM calls are not part of this budget: the clock is suspended while the
cascade runs, which has its own wall-clock budget cut from the hook timeout
(cascade_budget.py). The installer sizes Claude Code's hook ``timeout`` from
both (``install_timeout_s``).

Python cannot interrupt a running ``re`` call, so these checks only bound the
work between checkpoints; every built-in pattern is linear time for that
reason (see gap_pattern.py).
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_DEADLINE_S = 20.0
TIMEOUT_REASON = "nuh: inspection timed out"
# Claude Code's hook timeout: static budget + LLM cascade + this margin
# (interpreter start, config load, logging), never below the floor.
INSTALL_TIMEOUT_MARGIN_S = 10
INSTALL_TIMEOUT_FLOOR_S = 60


class InspectionTimeout(BaseException):
    """The static budget ran out at a checkpoint.

    A BaseException on purpose: the classification path has broad
    ``except Exception`` fallbacks (config errors, parser errors) that would
    turn a timeout into some other, possibly weaker, decision. This must reach
    the hook, which answers ask.
    """


_ends_at: float | None = None     # monotonic time the budget runs out
_paused_at: float | None = None   # set while an LLM call is running
_block_reason: str | None = None  # first block found before the budget ran out


def start(budget_s: float, started_at: float | None = None) -> None:
    """Start the budget for this hook call, counted from *started_at* (monotonic; default now)."""
    global _ends_at, _paused_at, _block_reason
    _ends_at = (time.monotonic() if started_at is None else started_at) + budget_s
    _paused_at = None
    _block_reason = None


def clear() -> None:
    """No budget (CLI, tests, after the hook call)."""
    global _ends_at, _paused_at, _block_reason
    _ends_at = None
    _paused_at = None
    _block_reason = None


def note(decision: str, reason: str) -> None:
    """Remember a block found so far, so a later timeout can't weaken it to ask."""
    global _block_reason
    if decision == "block" and _block_reason is None and _ends_at is not None:
        _block_reason = reason or "blocked"


def blocked_reason() -> str | None:
    """The block recorded before the budget ran out, if any."""
    return _block_reason


def remaining() -> float | None:
    """Seconds left in the static budget, or None when no budget is running.

    The one place that knows the budget: a future request to a remote
    classifier sends this, so the server can stop in time.
    """
    if _ends_at is None:
        return None
    now = _paused_at if _paused_at is not None else time.monotonic()
    return max(0.0, _ends_at - now)


def expired() -> bool:
    """True once the static budget is used up."""
    left = remaining()
    return left is not None and left <= 0.0


def check(stage: str) -> None:
    """Raise InspectionTimeout if the budget is used up at *stage*."""
    if expired():
        raise InspectionTimeout(stage)


@contextmanager
def suspended() -> Iterator[None]:
    """Stop the clock while an LLM call runs; it has its own timeout."""
    global _ends_at, _paused_at
    if _ends_at is None or _paused_at is not None:
        yield
        return
    _paused_at = time.monotonic()
    try:
        yield
    finally:
        if _ends_at is not None and _paused_at is not None:
            _ends_at += time.monotonic() - _paused_at
        _paused_at = None


def install_timeout_s(deadline_s: float, llm_timeout_s: float) -> int:
    """Claude Code hook ``timeout`` (seconds) for these budgets."""
    total = deadline_s + llm_timeout_s + INSTALL_TIMEOUT_MARGIN_S
    return max(INSTALL_TIMEOUT_FLOOR_S, math.ceil(total))
