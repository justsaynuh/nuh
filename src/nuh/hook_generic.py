"""The generic review: the LLM step for every tool that is neither Bash nor a write tool (gating.py).

Under ``check`` the LLM sees the tool's static allows and may raise one;
under ``decide`` it also sees asks no ``human_only`` key is behind, and may
lower one to allow. ``approve`` and ``defer`` send nothing. A static block
is final. The verdict merges with the static decision as a write review's
does (llm_merge.py). Prompted providers are shown the tool name and its
input as JSON, cut at ``llm.max_action_chars`` with a visible marker
(llm_prompt._build_generic_prompt); system 1 is shown the call as it is
shown any other.
"""

import sys

from nuh import deadline, gating, llm_failure, llm_fallback, llm_merge, llm_skip, steps, taxonomy, trace
from nuh.llm_meta import build as build_llm_meta


def review(tool: str, tool_input: dict, decision: dict) -> dict:
    """*decision* (the tool's static one) after its generic review, with the steps that made it."""
    word = gating.word(tool)
    if word not in gating.REVIEWS or decision.get("decision") == taxonomy.BLOCK:
        return decision
    skip = llm_skip.write_ask(tool, decision, word)
    if skip:
        return llm_skip.note(decision, skip)
    if not _llm_enabled():
        return decision
    trail = [steps.static(decision)]
    llm_decision, llm_meta = _try_llm_generic(tool, tool_input, decision)
    if llm_meta:
        decision.setdefault("_meta", {}).update(llm_meta)
    trail += steps.review(llm_meta, llm_decision)
    final, merged = llm_merge.merge(tool, word, decision, llm_decision, llm_meta)
    final[steps.KEY] = trail + merged
    return final


def _llm_enabled() -> bool:
    """Whether the LLM layer is on; a config that cannot be read counts as on (the review then fails closed)."""
    try:
        from nuh.config import get_config
        cfg = get_config()
        return bool(cfg.llm and cfg.llm.get("enabled", False))
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm: {exc}\n")
        return True


def _try_llm_generic(tool: str, tool_input: dict, decision: dict) -> tuple[dict | None, dict]:
    """The generic review of *tool*'s call: (verdict, llm_meta); (None, {}) when no provider ran.

    Any exception, or no provider answering, asks (llm_failure).
    """
    try:
        from nuh.config import get_config
        cfg = get_config()
        from nuh import hook
        from nuh.llm import try_llm_generic
        deadline.check(f"{tool} classification")
        tr = trace.start_review("generic", lambda: {"tool": tool, "deterministic_decision": decision})
        with deadline.suspended():
            llm_call = try_llm_generic(tool, tool_input, decision, cfg.llm, hook._transcript(), trace=tr,
                                       call=hook._tool_call())
        trace.record_call(llm_call, llm_call.decision)
        if llm_call.decision is None:
            sys.stderr.write(f"nuh: LLM generic: no providers responded for {tool}\n")
            return None, {}
        return llm_call.decision, build_llm_meta(llm_call, cfg)
    except Exception as exc:
        # A crashed review is no review: the call it was to inspect asks.
        trace.record_error(exc)
        return llm_failure.crashed(tool, exc, llm_fallback.configured(llm_fallback.ON_FAILURE))
