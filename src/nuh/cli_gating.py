"""What ``nuh test`` says about gating: the tool's word, the human_only keys behind an ask, the LLM call, delivery.

The same answers the hook acts on (gating.py, bash_review.route,
llm_skip), so the dry run explains exactly what the hook would do.
"""

from __future__ import annotations

import sys

from nuh import gating, llm_skip, taxonomy


def _llm_on() -> bool:
    try:
        from nuh.config import get_config
        llm = get_config().llm
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm: {exc}\n")
        return False
    return bool(llm and llm.get("enabled", False))


def word_line(tool: str) -> str:
    """``tools.<tool>: <word> (<source>)``."""
    try:
        from nuh.config import get_config
        configured = get_config().tools
    except Exception as exc:
        sys.stderr.write(f"nuh: config: tools: {exc}\n")
        return f"{gating.word(tool)} (config unreadable)"
    word, source = gating.resolve(tool, configured)
    return f"{word} ({source})"


def keys_line(keys: list[str]) -> str:
    """The keys behind an ask, the human-only ones named first."""
    held = gating.held_keys(keys)
    others = [key for key in keys if key not in held]
    if held:
        return ", ".join(held) + (f" (not human-only: {', '.join(others)})" if others else "")
    return f"none (keys behind the ask: {', '.join(others) or 'none'})"


def llm_line(tool: str, decision: str, why_not: str) -> str:
    """Whether the hook would call the LLM for this call, and why not; *why_not* is "" when the routing sends it."""
    if why_not:
        return f"not called: {why_not}"
    if not _llm_on():
        return f"not called: llm.enabled is off (tools.{tool}: {gating.word(tool)} would send this {decision})"
    return f"called (tools.{tool}: {gating.word(tool)} reviews this {decision})"


def why_not(tool: str, decision: dict) -> str:
    """Why the hook sends a write or generic tool's static *decision* to no LLM; "" when it sends it."""
    word = gating.word(tool)
    if word not in gating.REVIEWS:
        return f"tools.{tool}: {word} never calls it"
    if decision.get("decision") == taxonomy.BLOCK:
        return "a static block is final"
    return llm_skip.write_ask(tool, decision, word)


def delivery_line(tool: str, decision: str) -> str:
    """How the hook hands *decision* to the agent: an ask or block always, an allow unless the word is defer."""
    if decision != taxonomy.ALLOW:
        return f"sent ({decision}s always reach the agent)"
    word = gating.word(tool)
    if word == gating.DEFER:
        return f"silent (tools.{tool}: defer): Claude Code's own permissions decide"
    return f"explicit allow (tools.{tool}: {word})"


def print_lines(tool: str, decision: dict, width: int, *, keys: list[str] | None = None,
                route_why_not: str | None = None) -> None:
    """The gating lines for *tool*'s static *decision*, labels padded to *width*.

    *keys* are the keys behind a Bash ask (gating.bash_keys); other tools
    read them off the decision. *route_why_not* is Bash's (bash_review.route).
    """
    d = decision.get("decision", taxonomy.ALLOW)
    rows = [("Word:", word_line(tool))]
    if d == taxonomy.ASK:
        rows.append(("Human-only:", keys_line(keys if keys is not None else gating.keys_of(decision))))
    reason = route_why_not if route_why_not is not None else why_not(tool, decision)
    rows += [("LLM call:", llm_line(tool, d, reason)), ("Delivery:", delivery_line(tool, d))]
    for label, text in rows:
        print(f"{label:<{width}}{text}")
