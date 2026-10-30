"""Skip the LLM step for a static ask no LLM verdict could change.

The hook sends a static ask to the LLM so a verdict can lower it to allow.
When it cannot, the call only adds latency, up to the whole LLM budget,
before the person sees the same prompt. So for a static ask the hook skips
the call, and logs ``llm.skipped: no_effect`` with ``llm.skip_reason``, when:

- a ``human_only`` key is behind it (every floor rule is one by default), or
- the tool's ``tools`` word is not ``decide``: ``check`` reviews allows
  only, ``approve`` and ``defer`` nothing (gating.py).

An ask is sent only to be lowered: under ``llm.can_block: false`` an LLM
block on it is capped to the ask it already is, and with ``can_block: true``
a human-only ask still goes unreviewed. A block is
already final, and an allow can always be raised.
"""

import sys

from nuh import gating, llm_cap, taxonomy

SKIPPED = "no_effect"  # the log's llm.skipped value for a call skipped here
_PREFIX = "cannot change outcome"


def _llm_enabled() -> bool:
    """Whether the LLM layer is on; a broken config reads as on, so a skip is still logged."""
    try:
        from nuh.config import get_config  # lazy import to avoid circular
        cfg = get_config()
        return bool(cfg.llm and cfg.llm.get("enabled", False))
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm: {exc}\n")
        return True


def ask_skip(tool: str, word: str, held: list[str]) -> str:
    """Why the LLM step cannot change *tool*'s static ask, "" when it can.

    *word* is the tool's ``tools`` word, *held* the human-only keys behind the ask.
    """
    if held:
        return f"{_PREFIX} (human_only: {', '.join(held)})"
    if word == gating.CHECK:
        return f"{_PREFIX} (tools.{tool}: check reviews allows only)"
    if word != gating.DECIDE:
        return f"{_PREFIX} (tools.{tool}: {word})"
    return ""


def write_ask(tool: str, decision: dict, word: str) -> str:
    """Why the LLM veto gate cannot change a write-like (or generic) static ask, "" when it can (or it is no ask)."""
    if decision.get("decision") != taxonomy.ASK:
        return ""
    return ask_skip(tool, word, gating.held_by(decision))


def patch_pinned(fixed: list[dict], reviewed: list[dict], word: str) -> str:
    """Why no review in an apply_patch can change the patch's decision, "" when one can.

    The patch decides the strictest of its parts. *fixed* are the parts no
    review sees (action policies, and path checks outside ``decide``);
    *reviewed* the per-operation decisions the veto gate would see. When some
    part is an ask no review can lower, the patch asks whatever each review
    says, unless ``llm.can_block`` lets a review raise it to block.
    """
    if llm_cap.configured():
        return ""  # a review may raise the patch to block (a broken config reads as can_block: true)
    if any(d.get("decision") == taxonomy.ASK for d in fixed):
        return f"{_PREFIX} (a part of the patch no review sees asks, llm.can_block: false)"
    for decision in reviewed:
        reason = write_ask("apply_patch", decision, word)
        if reason:
            return reason
    return ""


def meta(reason: str) -> dict:
    """The decision ``_meta`` that logs a skipped call; empty when nothing was skipped or the LLM is off."""
    if not reason or not _llm_enabled():
        return {}
    return {"llm_skipped": SKIPPED, "llm_skip_reason": reason}


def note(decision: dict, reason: str) -> dict:
    """Record on *decision* that its LLM call was skipped for *reason* (see meta); returns *decision*."""
    skipped = meta(reason)
    if skipped:
        decision.setdefault("_meta", {}).update(skipped)
    return decision


def note_write(tool: str, decision: dict) -> dict:
    """``note`` a write-like decision the hook settles without its LLM veto gate (a block, a floored ask)."""
    return note(decision, write_ask(tool, decision, gating.word(tool)))
