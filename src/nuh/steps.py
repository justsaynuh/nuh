"""The steps of a hook decision, in the order they ran (the decision log's ``steps``).

Every value that feeds the final decision is recorded as one step:

- ``static``: the static verdict for the whole call, before any LLM step,
  with its ``breakdown``: Bash's per-stage results, or for other tools each
  check that objected (path, project boundary, content, hidden characters,
  credential search, the classify table).
- System 1 (the ``system1`` provider), per attempt, before the ``llm`` step:
  a ``question`` step per question asked (its answer, the decision it
  triggered and, as ``rule``, the threshold that fired), a ``gate`` step per
  gate evaluated (``fired``), and a ``provider`` step with the attempt's
  ``model``, ``ms`` and outcome (``decision``, ``uncertain`` or ``failure``).
  Prompted providers' attempts are in the log's ``llm.cascade`` only.
- ``llm``: the LLM's own verdict, before any cap, and its reasoning:
  allow, ask, block, or ``uncertain`` (a model looked and reached no
  decision). A review that gave no verdict (it failed, or the action was
  too large to send whole) has ``decision`` "" and ``error`` (the log's
  ``llm.skipped``).
- Merge steps, ``{"step", "from", "to", "rule"}``:

  - ``cap``: the LLM's verdict past what it may assign (a block under
    ``llm.can_block: false``) becomes ask.
  - ``escalate``: the LLM's verdict, after the cap, is stricter than the
    decision so far and replaces it.
  - ``lower``: under the ``tools`` word ``decide``, a confident LLM allow
    lowers an ask no ``human_only`` key is behind.
  - ``adopt``: a Bash LLM ask takes the place of the static ask; its reason
    explains the ask.
  - ``human_only``: the LLM allowed an ask a ``human_only`` key is behind;
    the ask holds (``rule`` names the keys).
  - ``tool``: the LLM allowed an ask its tool's ``tools`` word does not let
    it lower; the ask holds. (Log lines written before 2026-10-20 call
    both of these ``floor``; they read the same, as text.)
  - ``fail_closed``: the review gave no verdict, so the call asks
    (``llm.on_failure: ask``).
  - ``uncertain``: the review was uncertain, so the call asks
    (``llm.on_uncertain: ask``); its reason explains the ask.
  - ``keep``: the review reached no decision and ``llm.on_uncertain`` or
    ``llm.on_failure`` is ``static``: the static decision stands.
  - ``strictest``: apply_patch decides the strictest of its operations;
    ``target`` names the operation whose outcome won. Steps from each
    operation's review carry that operation's ``target``.

A kind added later is one more element; readers skip kinds they do not
know. The in-process decision holds its steps under ``KEY`` with every
reason whole (the LLM trace keeps them so); ``for_log`` bounds them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nuh import llm_failure

KEY = "_steps"
BREAKDOWN = "_breakdown"  # a handler's static breakdown, consumed when its static step is built

STATIC, LLM = "static", "llm"
QUESTION, GATE, PROVIDER = "question", "gate", "provider"
CAP, ESCALATE, LOWER, ADOPT, HUMAN_ONLY, TOOL, FAIL_CLOSED, UNCERTAIN, KEEP, STRICTEST = (
    "cap", "escalate", "lower", "adopt", "human_only", "tool", "fail_closed", "uncertain", "keep", "strictest")
DECISIONS = ("allow", "ask", "block")
# Merges after which the LLM step's reason is the one that explains the decision.
_TAKES_LLM_REASON = frozenset({ESCALATE, LOWER, ADOPT, FAIL_CLOSED, UNCERTAIN})
_NO_VERDICT = ("too_large", llm_failure.ERROR)  # llm.skipped values: the review gave no verdict

# Log bounds: a step's reason, a breakdown item's reason, breakdown items kept.
REASON_MAX = 500
BREAKDOWN_REASON_MAX = 300
BREAKDOWN_MAX = 32

# An LLM decision's reason: "[LLM suggested block: ]<label> (<who>): <text>" (llm.py, llm_failure.py).
_LLM_REASON = re.compile(r"(?:LLM suggested block: )?\S+ \([^()]*\): (?P<text>.*)", re.DOTALL)


def check(name: str, decision: dict) -> dict:
    """A breakdown item: the check *name* objected with *decision*."""
    item = {"check": name, "decision": decision.get("decision", ""), "reason": decision.get("reason", "")}
    if decision.get("_floor"):
        item["rule"] = decision["_floor"]
    return item


def static(decision: dict, breakdown: list[dict] | None = None) -> dict:
    """The static step for *decision*; the breakdown is *breakdown*, else the one its handler attached."""
    attached = decision.pop(BREAKDOWN, None)
    return {"step": STATIC, "decision": decision.get("decision", "allow"), "reason": decision.get("reason", ""),
            "breakdown": list(breakdown if breakdown is not None else attached or [])}


def ensure(decision: dict) -> dict:
    """Give *decision* its steps when no LLM step built them: the static step alone. Returns *decision*."""
    if not isinstance(decision.get(KEY), list):
        decision[KEY] = [static(decision)]
    else:
        decision.pop(BREAKDOWN, None)
    return decision


def merge(kind: str, frm: str, to: str, rule: str) -> dict:
    return {"step": kind, "from": frm, "to": to, "rule": rule}


def no_verdict(llm_meta: dict) -> str:
    """The ``llm.skipped`` value of a review that gave no verdict; "" when it gave one.

    Either the action was too large to send whole, or the review failed: no
    provider answered, or nuh's own LLM step raised
    (llm_failure). Both fail closed, and nothing caps or lowers that ask.
    """
    skipped = llm_meta.get("llm_skipped", "")
    return skipped if skipped in _NO_VERDICT else ""


def review(llm_meta: dict, llm_decision: dict | None) -> list[dict]:
    """The steps of a review: its providers' own (system 1), the ``llm`` step, its ``cap``; [] when none ran.

    *llm_meta* is the hook's LLM metadata (llm_meta.build): its
    ``llm_steps`` are the attempts' steps, ``llm_decision`` the verdict before
    the cap, ``llm_capped`` what the cap made of it.
    """
    attempts = [s for s in llm_meta.get("llm_steps") or [] if isinstance(s, dict)]
    failed = no_verdict(llm_meta)
    fallback = llm_text(str((llm_decision or {}).get("reason") or ""))
    if failed:
        return [*attempts, {"step": LLM, "decision": "", "reason": fallback or str(llm_meta.get("llm_error") or ""),
                            "error": failed}]
    verdict = llm_meta.get("llm_decision") or ""
    if not verdict:
        return attempts
    out = [*attempts,
           {"step": LLM, "decision": verdict, "reason": str(llm_meta.get("llm_reasoning") or "") or fallback}]
    capped = llm_meta.get("llm_capped")
    if capped:
        out.append(merge(CAP, verdict, capped, "llm.can_block: false"))
    return out


def llm_text(reason: str) -> str:
    """An LLM decision's *reason* without its ``<label> (<who>): `` lead."""
    match = _LLM_REASON.fullmatch(reason)
    return match["text"] if match else reason


@dataclass(frozen=True)
class Outcome:
    """What the steps say about the final decision."""

    reason_source: str = STATIC  # whose reason explains the final decision: "static" or "llm"
    llm_decision: str = ""       # the LLM's own verdict, before any cap: a decision or uncertain ("" for none)
    llm_reason: str = ""         # the LLM step's reason
    capped: str = ""             # what the cap made of the LLM's verdict ("" when it left it alone)
    wanted: str = ""             # the LLM's verdict when it differs from the final decision


def outcome(steps: object, final: str) -> Outcome:
    """Whose reason explains the *final* decision, and what the LLM wanted, from *steps*.

    One reading for the notice (notice.py) and ``nuh log -v`` (log_view.py).
    The reason is the static step's until a merge that takes the LLM's
    (escalate, lower, adopt, fail_closed). In an apply_patch only the
    winning operation's steps count.
    """
    items = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
    winner = next((s.get("target", "") for s in items if s.get("step") == STRICTEST), None)
    source, llm, capped = STATIC, {}, ""
    for step in items:
        if winner is not None and "target" in step and step.get("target") != winner:
            continue
        kind = step.get("step")
        if kind == LLM:
            llm = step
        elif kind == CAP:
            capped = str(step.get("to") or "")
        elif kind in _TAKES_LLM_REASON:
            source = LLM
    said = str(llm.get("decision") or "")
    # An uncertain review wanted no decision of its own: nothing to say it "wanted".
    wanted = said if said in DECISIONS and said != final else ""
    return Outcome(source, said, str(llm.get("reason") or ""), capped, wanted)


def tagged(steps: list[dict], target: str) -> list[dict]:
    """*steps* each marked as belonging to the apply_patch operation on *target*."""
    return [{**step, "target": target} for step in steps]


def for_log(steps: object) -> list[dict]:
    """*steps* bounded for the decision log: reasons cut (REASON_MAX, BREAKDOWN_REASON_MAX), BREAKDOWN_MAX items."""
    out = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        step = dict(step)
        if isinstance(step.get("reason"), str):
            step["reason"] = _cut(step["reason"], REASON_MAX)
        breakdown = step.get("breakdown")
        if isinstance(breakdown, list):
            kept = [_bounded_item(item) for item in breakdown[:BREAKDOWN_MAX]]
            step["breakdown"] = kept
            if len(breakdown) > BREAKDOWN_MAX:
                step["breakdown_omitted"] = len(breakdown) - BREAKDOWN_MAX
        out.append(step)
    return out


def _bounded_item(item: object) -> object:
    if not isinstance(item, dict) or not isinstance(item.get("reason"), str):
        return item
    return {**item, "reason": _cut(item["reason"], BREAKDOWN_REASON_MAX)}


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[{len(text) - limit} more chars]"
