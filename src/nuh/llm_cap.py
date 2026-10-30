"""How far an LLM verdict may go: ``llm.can_block`` (global only, like all of ``llm``).

- ``can_block: false`` (the default): an LLM block is capped to ask. The ask
  stands: it is still stricter than a static allow, so it raises one.
- ``can_block: true``: an LLM block blocks.

The cap never makes a verdict weaker than ask, so it can neither lower a
static decision nor turn an LLM's ask into an allow. Lowering is the
``tools`` word's business (``decide``, gating.py).
"""

from __future__ import annotations

import sys

ALLOW, ASK, BLOCK = "allow", "ask", "block"
_RANK = {ALLOW: 0, ASK: 1, BLOCK: 2}
CAPPED_PREFIX = "LLM suggested block: "  # starts the reason of a block capped to ask


def parse_can_block(llm: dict, *, warn: bool = False) -> bool:
    """``llm.can_block``; False when unset.

    *warn*: say on stderr that a ``can_block`` that is not true/false was
    ignored. The config loader warns; the other callers read the same
    section quietly.
    """
    raw = llm.get("can_block")
    if raw is None or isinstance(raw, bool):
        return bool(raw)
    if warn:
        sys.stderr.write(f"nuh: config: llm.can_block must be true or false, got {raw!r}; using false\n")
    return False


def configured() -> bool:
    """``llm.can_block`` of the loaded config; True (no cap) when the config cannot be read.

    No cap is the strict reading: an LLM block then blocks instead of asking.
    """
    try:
        from nuh.config import get_config  # lazy import to avoid circular
        return get_config().llm_can_block
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm.can_block: {exc}\n")
        return True


def ceiling(can_block: bool) -> str:
    """The strictest decision an LLM verdict may become."""
    return BLOCK if can_block else ASK


def cap(decision: dict, can_block: bool | None = None) -> dict:
    """*decision* (an LLM verdict) after the cap: a block becomes an ask unless *can_block*.

    *can_block* None reads the loaded config. The reason keeps the LLM's own
    after ``LLM suggested block:``. Changes *decision* in place and returns
    it: the LLM trace reads the capped decision through the dict the review
    returned (trace.record_call).
    """
    if decision.get("decision") != BLOCK or (configured() if can_block is None else can_block):
        return decision
    decision["reason"] = f"{CAPPED_PREFIX}{decision.get('reason', '')}"
    decision["decision"] = ASK
    return decision


def stricter(decision: str, than: str) -> bool:
    """True when *decision* is stricter than *than* (allow < ask < block)."""
    return _RANK.get(decision, -1) > _RANK.get(than, _RANK[BLOCK])


def capped_to(verdict: str, can_block: bool) -> str:
    """What the cap turns *verdict* into, "" when it leaves it alone (the log's ``llm.capped``)."""
    top = ceiling(can_block)
    return top if stricter(verdict, top) else ""


def shown(llm: dict) -> tuple[str, str]:
    """(verdict, capped) of a decision-log ``llm`` entry; capped is "" when the cap left it alone."""
    verdict = llm.get("decision") if isinstance(llm.get("decision"), str) else ""
    capped = llm.get("capped") if isinstance(llm.get("capped"), str) else ""
    return verdict, capped


def raises(decision: dict, verdict: str, static: str, can_block: bool | None = None) -> dict | None:
    """The LLM's *decision* after the cap when its *verdict* is stricter than *static*; None otherwise.

    *verdict* is what the LLM said before any cap (``llm.decision`` in the
    log: allow, ask, or block). An ask on a static allow raises it to ask; a
    block raises it to ask under ``can_block: false``, to block otherwise.
    Mutates *decision* like ``cap``.
    """
    if not stricter(verdict, static):
        return None
    capped = cap(decision, can_block)
    return capped if stricter(capped.get("decision", ""), static) else None
