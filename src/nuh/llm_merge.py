"""How a write or generic review's verdict merges with the static decision (Bash's is bash_review.ask_outcome).

- A review with no decision (too large, failed, uncertain) asks, unless
  ``llm.on_uncertain`` / ``llm.on_failure: static`` keeps the static
  decision; its ask raises an allow and never caps.
- A verdict stricter than the static decision, after the ``llm.can_block``
  cap, replaces it (``escalate``).
- An LLM allow lowers a static ask only under ``decide`` and only when no
  ``human_only`` key is behind the ask (``lower``); otherwise the ask holds,
  by the key (``human_only``) or by the tool's word (``tool``).
- Anything else agrees with the static decision, which stands with its reason.
"""

from nuh import gating, llm_cap, llm_fallback, steps, taxonomy


def merge(tool: str, word: str, static: dict, llm_decision: dict | None,
          llm_meta: dict) -> tuple[dict, list[dict]]:
    """The decision after *tool*'s review, and the merge step that made it ([] when the static one stands)."""
    if llm_decision is None:
        return static, []
    structural = static.get("decision", taxonomy.ALLOW)
    fallback = llm_fallback.of(llm_meta)
    if fallback is not None and fallback.keeps_static:
        return static, [llm_fallback.kept(fallback, structural)]
    failed = steps.no_verdict(llm_meta)
    if failed or fallback is not None:
        # Too large to review, no verdict, or uncertain: not an LLM decision, so no cap; its ask raises an allow.
        if not llm_cap.stricter(taxonomy.ASK, structural):
            return static, []
        llm_decision.setdefault("_meta", {}).update(llm_meta)
        if failed:
            return llm_decision, [steps.merge(steps.FAIL_CLOSED, structural, taxonomy.ASK, f"llm.skipped: {failed}")]
        return llm_decision, [steps.merge(steps.UNCERTAIN, structural, taxonomy.ASK, fallback.rule)]
    capped = llm_cap.cap(llm_decision)
    verdict = capped.get("decision", "")
    if llm_cap.stricter(verdict, structural):
        capped.setdefault("_meta", {}).update(llm_meta)
        return capped, [steps.merge(steps.ESCALATE, structural, verdict, "stricter")]
    if verdict == taxonomy.ALLOW and structural == taxonomy.ASK:
        held = gating.held_by(static)
        if word == gating.DECIDE and not held:
            return lowered(static), [steps.merge(steps.LOWER, structural, verdict, f"tools.{tool}: {word}")]
        return static, [hold(tool, word, held)]
    # The LLM agreed: the static decision and its reason stand.
    return static, []


def hold(tool: str, word: str, held: list[str]) -> dict:
    """The step by which an ask stood against an LLM allow: a human_only key behind it, else the tool's word."""
    if held:
        return steps.merge(steps.HUMAN_ONLY, taxonomy.ALLOW, taxonomy.ASK, f"human_only: {', '.join(held)}")
    return steps.merge(steps.TOOL, taxonomy.ALLOW, taxonomy.ASK, f"tools.{tool}: {word}")


def lowered(static: dict) -> dict:
    """The allow a confident LLM review gives a lowerable static ask.

    The static reason stays in the reason, so the log shows what was lowered;
    the LLM's own reasoning is in its llm fields.
    """
    return {"decision": taxonomy.ALLOW,
            "reason": f"{static.get('reason', '')} — lowered to allow by LLM review",
            "_meta": static.get("_meta", {})}
