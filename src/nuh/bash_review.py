"""Whether the hook sends a Bash call to the LLM, and what a script review makes of the allow.

One answer for the hook (hook.handle_bash) and ``nuh test``, so the dry run
reviews exactly the calls the hook reviews. Two kinds of Bash call go to
the LLM:

- ``ask``: under ``tools.Bash: decide``, a static ask no ``human_only``
  key is behind (gating.bash_keys; llm_skip.ask_skip);
- ``script``: under ``check`` or ``decide``, a static allow that runs a
  script file nuh scanned clean (``runs_clean_script``, FD-079). The allow
  rests on the LLM reading the script, so its ask or block raises the allow
  (``raised_allow``).

A static block is final, and ``approve`` or ``defer`` sends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nuh import gating, llm_cap, llm_fallback, llm_skip, steps, taxonomy

ASK, SCRIPT = "ask", "script"


@dataclass(frozen=True)
class Route:
    """What the hook does with a classified Bash call's LLM step."""

    kind: str = ""          # ASK, SCRIPT, or "" when the hook sends nothing
    skip: str = ""          # a static ask no verdict could change: why (llm_skip.ask_skip)
    keys: list[str] = field(default_factory=list)  # a static ask: the human_only keys behind it
    held: list[str] = field(default_factory=list)  # those of them that are human-only
    why_not: str = ""       # kind "": why the hook sends nothing


def route(result, word: str | None = None) -> Route:
    """Whether the hook reviews the Bash ClassifyResult *result*, and as what.

    *word* is the ``tools`` word for Bash; None reads the config.
    """
    word = word or gating.word("Bash")
    if result.final_decision == taxonomy.BLOCK:
        return Route(why_not="a static block is final")
    if result.final_decision == taxonomy.ASK:
        keys = gating.bash_keys(result)
        held = gating.held_keys(keys)
        skip = llm_skip.ask_skip("Bash", word, held)
        if skip:
            return Route(skip=skip, keys=keys, held=held, why_not=skip)
        return Route(ASK, keys=keys)
    if word not in gating.REVIEWS:
        return Route(why_not=f"tools.Bash: {word}")
    if runs_clean_script(result):
        return Route(SCRIPT)
    return Route(why_not="a static allow with no clean script file to review")


def raised_allow(decision: dict | None, llm_meta: dict) -> dict | None:
    """What a script review makes of the static allow: an ask or block, or None when the allow stands.

    A review with no decision (uncertain, failed) asks unless
    ``llm.on_uncertain`` / ``llm.on_failure`` is ``static``, which keeps the
    allow (``kept``); one too large to send whole always asks. An LLM ask
    raises the allow to ask. An LLM block raises it, capped to ask unless
    ``llm.can_block``. Only an LLM allow keeps it. Mutates *decision* like
    llm_cap.cap.
    """
    if decision is None:
        return None
    fallback = llm_fallback.of(llm_meta)
    if fallback is not None:
        return None if fallback.keeps_static else decision
    if steps.no_verdict(llm_meta):
        return decision
    verdict = llm_meta.get("llm_decision") or decision.get("decision", "")
    return llm_cap.raises(decision, verdict, taxonomy.ALLOW)


def raise_step(raised: dict, llm_meta: dict) -> dict:
    """The merge step by which a script review's *raised* decision replaced the static allow (raised_allow)."""
    failed = steps.no_verdict(llm_meta)
    if failed:
        return steps.merge(steps.FAIL_CLOSED, taxonomy.ALLOW, raised.get("decision", ""), f"llm.skipped: {failed}")
    fallback = llm_fallback.of(llm_meta)
    if fallback is not None:
        return steps.merge(steps.UNCERTAIN, taxonomy.ALLOW, raised.get("decision", ""), fallback.rule)
    return steps.merge(steps.ESCALATE, taxonomy.ALLOW, raised.get("decision", ""), "stricter")


def kept(llm_meta: dict, static: str) -> list[dict]:
    """The ``keep`` step when ``llm.on_uncertain`` / ``llm.on_failure: static`` kept the *static* decision; else []."""
    fallback = llm_fallback.of(llm_meta)
    return [llm_fallback.kept(fallback, static)] if fallback is not None and fallback.keeps_static else []


def ask_outcome(static: dict, llm_decision: dict | None, llm_meta: dict,
                word: str) -> tuple[dict, list[dict]]:
    """A static Bash ask after the LLM step: the final decision and the merge step that made it.

    *static* is the static ask, *llm_decision* and *llm_meta* the review's
    result (hook._try_llm). A review with no decision (uncertain, failed,
    too large) asks with its own reason, unless ``llm.on_uncertain`` /
    ``llm.on_failure: static`` keeps the static ask and its reason.
    Otherwise the verdict, after the ``llm.can_block`` cap, decides:
    an allow lowers the ask under ``decide`` (any other word holds it), a
    block escalates, and an ask takes the static ask's place, except one the
    cap made out of a block: then the static ask and its reason stand.
    Mutates *llm_decision* like llm_cap.cap.
    """
    if llm_decision is None:
        return static, []
    fallback = llm_fallback.of(llm_meta)
    if fallback is not None and fallback.keeps_static:
        return static, [llm_fallback.kept(fallback, taxonomy.ASK)]
    failed = steps.no_verdict(llm_meta)
    if failed:
        return llm_decision, [steps.merge(steps.FAIL_CLOSED, taxonomy.ASK, taxonomy.ASK, f"llm.skipped: {failed}")]
    if fallback is not None:
        return llm_decision, [steps.merge(steps.UNCERTAIN, taxonomy.ASK, taxonomy.ASK, fallback.rule)]
    verdict = llm_meta.get("llm_decision") or llm_decision.get("decision", "")
    capped = llm_cap.cap(llm_decision)
    after = capped.get("decision", "")
    rule = f"tools.Bash: {word}"
    if after == taxonomy.ALLOW:
        if word != gating.DECIDE:
            return static, [steps.merge(steps.TOOL, taxonomy.ALLOW, taxonomy.ASK, rule)]
        return capped, [steps.merge(steps.LOWER, taxonomy.ASK, taxonomy.ALLOW, rule)]
    if after == taxonomy.BLOCK:
        return capped, [steps.merge(steps.ESCALATE, taxonomy.ASK, taxonomy.BLOCK, "stricter")]
    if verdict != taxonomy.ASK:
        return static, []  # the cap made this ask: the static ask it equals stands
    return capped, [steps.merge(steps.ADOPT, taxonomy.ASK, taxonomy.ASK, rule)]


def runs_clean_script(result) -> bool:
    """Check if result has a lang_exec stage where content was inspected.

    Returns True when the context resolver scanned a script file clean
    ('script clean:'). Clean inline code asks instead, so the LLM sees it on
    the ask path. Returns False for nonexistent files and outside-project
    scripts. Stages folded into an unwrapped shell or a substitution count too.
    """
    return any(sr.action_type == taxonomy.LANG_EXEC and sr.reason.startswith("script clean:")
               for sr in result.all_stages())
