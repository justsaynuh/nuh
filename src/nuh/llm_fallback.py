"""What a review that reached no decision does: ``llm.on_uncertain`` and ``llm.on_failure``.

- **uncertain**: a model looked and reached no confident decision (system 1:
  nothing cleared its thresholds; system 2: it said "uncertain").
- **failure**: nobody really looked: every provider errored, timed out or
  gave no usable reply, or nuh's own LLM step crashed (llm_failure).

Each setting is ``ask`` (the default) or ``static``. ``ask`` makes the call
an ask; ``static`` keeps the static decision. They differ only on a reviewed
static allow (a clean script file, a write): a static ask stays an ask either
way. ``static`` is the user's own loosening, global-only like all of ``llm``.

An action too large to show a model whole is neither: nuh refused to send
it, and it always asks (``llm.skipped: too_large``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from nuh import llm_failure, steps

ASK, STATIC = "ask", "static"
VALUES = (ASK, STATIC)
ON_UNCERTAIN, ON_FAILURE = "on_uncertain", "on_failure"
UNCERTAIN = "uncertain"  # the llm step's decision (llm.decision in the log) for an uncertain review


def parse(llm: dict, key: str, *, warn: bool = False) -> str:
    """``llm.<key>`` (on_uncertain or on_failure): ask or static; anything else is ask (with a warning when *warn*)."""
    raw = llm.get(key, ASK)
    if raw in VALUES:
        return raw
    if warn:
        sys.stderr.write(f"nuh: config: llm.{key} must be ask or static, got {raw!r}; using ask\n")
    return ASK


def configured(key: str) -> str:
    """``llm.<key>`` of the loaded config; ask when the config cannot be read (the strict reading)."""
    try:
        from nuh.config import get_config  # lazy import to avoid circular
        cfg = get_config()
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm.{key}: {exc}\n")
        return ASK
    return cfg.llm_on_uncertain if key == ON_UNCERTAIN else cfg.llm_on_failure


@dataclass(frozen=True)
class Fallback:
    """The setting that decides a review with no decision, and its value."""

    setting: str  # ON_UNCERTAIN or ON_FAILURE
    value: str    # ASK or STATIC

    @property
    def rule(self) -> str:
        return f"llm.{self.setting}: {self.value}"

    @property
    def keeps_static(self) -> bool:
        return self.value == STATIC


def of(llm_meta: dict) -> Fallback | None:
    """The fallback that applies to a review with *llm_meta* (llm_meta.build); None when it reached a decision.

    A review too large to send is not one of them: it asks whatever these say.
    """
    if llm_meta.get("llm_decision") == UNCERTAIN:
        return Fallback(ON_UNCERTAIN, configured(ON_UNCERTAIN))
    if steps.no_verdict(llm_meta) == llm_failure.ERROR:
        return Fallback(ON_FAILURE, configured(ON_FAILURE))
    return None


def kept(fallback: Fallback, static: str) -> dict:
    """The merge step by which *fallback* (``static``) kept the static decision *static*."""
    return steps.merge(steps.KEEP, static, static, fallback.rule)


def note(setting: str, value: str) -> str:
    """How a reason says which setting decided: ``on_uncertain: ask``."""
    return f"{setting}: {value}"
