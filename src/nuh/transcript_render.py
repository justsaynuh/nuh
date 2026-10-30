"""Render conversation context sections for the LLM prompt, within a budget.

Each section is first fitted to its own budget (``llm.context.*``). If the
rendered whole is still over the total (``llm.context_chars``, lowered to
what the prompt has left beside the action), whole items are dropped in this
order, so what states the task survives longest:

1. the agent's tool calls, oldest first
2. the agent's own prose, oldest first
3. the user's recent messages, oldest first, keeping the newest one
4. the subagent brief section: later delegator messages, then the brief
5. last, the user's first message and newest message are cut to fit
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nuh.transcript_records import AGENT, DELEGATOR, TOOL, USER

if TYPE_CHECKING:
    from nuh.transcript import Budget

FIRST = "first"
HEADERS = {
    FIRST: "The user's first message in this session:",
    USER: "The user's most recent messages (oldest first):",
    DELEGATOR: "Instructions to this subagent from the agent that delegated to it"
               " (agent-written, not from the user):",
    AGENT: "The agent's own recent messages (agent-written, not from the user):",
    TOOL: "The agent's recent tool calls (oldest first; the call under review is not listed):",
}


def render(sections: dict[str, list[str]], budget: Budget) -> tuple[str, dict[str, int]]:
    """Sections (oldest first) as labeled blocks within *budget*; returns (text, items dropped per section)."""
    caps = {FIRST: budget.first_prompt_chars, USER: budget.user_chars,
            DELEGATOR: budget.first_prompt_chars, AGENT: budget.assistant_chars}
    fitted = {k: _fair_share(v, caps.get(k, sum(map(len, v)))) for k, v in sections.items() if v}
    dropped: dict[str, int] = {}
    total = budget.total_chars
    while len(_join(fitted)) > total:
        victim = _next_sacrifice(fitted)
        if victim is None:
            break
        key, index = victim
        fitted[key].pop(index)
        dropped[key] = dropped.get(key, 0) + 1
    text = _join(fitted)
    if len(text) > total:
        text = _cut_last_resort(fitted, len(text) - total)
    if len(text) > total:
        text = text[:max(total, 0)].rsplit("\n", 1)[0]
    return text, dropped


def _next_sacrifice(fitted: dict[str, list[str]]) -> tuple[str, int] | None:
    """(section, index) of the next item to drop, or None when only the last-resort items are left."""
    if fitted.get(TOOL):
        return TOOL, 0
    if fitted.get(AGENT):
        return AGENT, 0
    if len(fitted.get(USER, [])) > 1:
        return USER, 0
    delegator = fitted.get(DELEGATOR, [])
    if delegator:
        return DELEGATOR, 1 if len(delegator) > 1 else 0
    return None


def _cut_last_resort(fitted: dict[str, list[str]], excess: int) -> str:
    """Cut the first and newest user messages (all that is left) by *excess* chars between them."""
    keys = [k for k in (FIRST, USER) if fitted.get(k)]
    texts = [t for k in keys for t in fitted[k]]
    cut = _fair_share(texts, max(sum(map(len, texts)) - excess, 0))
    if len(cut) < len(texts):
        return ""
    fitted = {k: [cut[i]] for i, k in enumerate(keys)}
    return _join(fitted)


def _join(fitted: dict[str, list[str]]) -> str:
    blocks = ["\n".join([HEADERS[k], *map(_item, v)]) for k, v in fitted.items() if v]
    return "\n\n".join(blocks)


def _truncate(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:max(cap - 1, 0)].rstrip() + "…"


def _fair_share(texts: list[str], total: int) -> list[str]:
    """Fit texts into *total* chars: short ones whole, long ones cut evenly."""
    caps = [0] * len(texts)
    remaining = total
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    for k, i in enumerate(order):
        caps[i] = min(len(texts[i]), remaining // (len(texts) - k))
        remaining -= caps[i]
    return [_truncate(t, c) for t, c in zip(texts, caps, strict=True) if c > 0]


def _item(text: str) -> str:
    lines = [ln.rstrip() for ln in text.strip().splitlines()] or [""]
    return "\n".join(["- " + lines[0], *("  " + ln if ln else "" for ln in lines[1:])])
