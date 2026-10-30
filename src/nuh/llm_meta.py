"""An LLM review's result as the hook's metadata (``llm_*`` keys): what the log and the steps read.

One reading for Bash reviews (hook._try_llm), write reviews
(hook_write._try_llm_write) and ``nuh test``.
"""

import sys

from nuh import llm_cap, llm_failure, llm_fallback


def verdict(llm_call) -> str:
    """What the LLM answered, before the llm.can_block cap.

    allow, ask or block; ``uncertain`` when a model answered but reached no
    decision; "" when no answer came.
    """
    if llm_call.failed or not llm_call.cascade:
        return ""
    if llm_call.uncertain:
        return llm_fallback.UNCERTAIN
    return (llm_call.decision or {}).get("decision", "")


def build(llm_call, cfg) -> dict:
    """Build LLM metadata dict from an LLMCallResult."""
    llm_meta: dict = {}
    if llm_call.action:
        from nuh.llm_action import log_summary
        llm_meta["llm_action"] = log_summary(llm_call.action)
    if llm_call.too_large:
        llm_meta["llm_skipped"] = "too_large"
    if llm_call.failed:
        llm_meta["llm_skipped"] = llm_failure.ERROR
        llm_meta["llm_error"] = "; ".join(f"{a.provider}: {a.error}" for a in llm_call.cascade if a.error)
    if llm_call.steps:
        llm_meta["llm_steps"] = llm_call.steps  # the attempts' own steps (steps.review puts them first)
    if llm_call.cascade:
        said = verdict(llm_call)
        capped = llm_cap.capped_to(said, cfg.llm_can_block)
        if capped:
            llm_meta["llm_capped"] = capped  # the verdict after the llm.can_block cap
        llm_meta = {
            "llm_provider": llm_call.provider,
            "llm_model": llm_call.model,
            "llm_latency_ms": llm_call.latency_ms,
            "llm_reasoning": llm_call.reasoning,
            "llm_decision": said,
            "llm_cascade": [
                {"provider": a.provider, "status": a.status, "latency_ms": a.latency_ms,
                 **({"error": a.error} if a.error else {})}
                for a in llm_call.cascade
            ],
            **llm_meta,
        }
    try:
        if cfg.log and cfg.log.get("llm_prompt", False):
            llm_meta["llm_prompt"] = llm_call.prompt
    except Exception as exc:
        sys.stderr.write(f"nuh: config: log.llm_prompt: {exc}\n")
    return llm_meta
