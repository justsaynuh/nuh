"""What an LLM review returns: one provider's verdict, each cascade attempt, and the review's result."""

from dataclasses import dataclass, field
from typing import NamedTuple


class PromptParts(NamedTuple):
    """Structured prompt with system and user components."""

    system: str
    user: str


@dataclass
class LLMResult:
    decision: str      # "allow", "ask", "block", or "uncertain" (the model looked and could not decide)
    reasoning: str = ""


@dataclass
class ProviderAttempt:
    provider: str
    status: str       # "success" (a decision came back), "uncertain" (no decision), or "error" (no answer)
    latency_ms: int
    model: str = ""
    error: str = ""


DECISION, UNCERTAIN, FAILURE = "decision", "uncertain", "failure"  # Answer.outcome


@dataclass
class Answer:
    """One provider attempt as the cascade reads it (llm._cascade).

    *outcome* is DECISION (*decision* is allow, ask or block), UNCERTAIN (a
    model looked and could not decide: it ends the cascade) or FAILURE (no
    usable answer, *error* says why: the next provider is asked).
    """

    outcome: str
    decision: str = ""
    reasoning: str = ""   # what explains the outcome (system 1 builds it from its triggers)
    model: str = ""
    latency_ms: int = 0
    error: str = ""
    steps: list[dict] = field(default_factory=list)  # the attempt's own steps (steps.py)
    prompt: str = ""      # what the provider read, when it is not the cascade's prompted prompt


@dataclass
class LLMCallResult:
    decision: dict | None = None
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    reasoning: str = ""
    prompt: str = ""
    cascade: list[ProviderAttempt] = field(default_factory=list)
    action: dict = field(default_factory=dict)  # size of the action reviewed, its limit, inline programs
    too_large: bool = False  # not sent: the action (or the prompt it needs) is over a limit
    failed: bool = False  # no verdict came back (llm_failure); its ask is not an LLM verdict to cap
    uncertain: bool = False  # a model answered but reached no decision; llm.on_uncertain applies (llm_fallback)
    steps: list[dict] = field(default_factory=list)  # the attempts' own steps (system 1: questions, gates, provider)
