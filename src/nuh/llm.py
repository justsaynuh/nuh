"""LLM layer — resolve ambiguous ask decisions via LLM providers.

The provider cascade and its entry points: ``try_llm`` (Bash),
``try_llm_write`` (Write, Edit, MultiEdit, NotebookEdit, apply_patch) and
``try_llm_generic``. The prompt prompted providers read is built in
llm_prompt.py, the provider calls are in llm_providers.py.

Providers run in ``llm.providers`` order. Each attempt ends in a decision
(allow, ask, block), uncertain (a model looked and could not decide), or a
failure (no usable answer). A decision or uncertain ends the cascade; a
failure moves on to the next provider. What uncertain, and a cascade in
which every provider failed, then do is ``llm.on_uncertain`` /
``llm.on_failure`` (llm_fallback.py), applied where the hook merges the
review with the static decision.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from nuh import cascade_budget, llm_action, llm_failure, llm_fallback, llm_heal, system1
from nuh import trace as tracing
from nuh.llm_prompt import (ExpendableSection, _bash_action, _build_generic_prompt, _build_prompt,
                            _build_write_prompt, _expendable_sections, _file_context_section, _fill_expendable,
                            generic_input)
from nuh.llm_providers import (_DEFAULT_MODELS, _TIMEOUT_LOCAL, _TIMEOUT_REMOTE, _call_provider,
                               _configured_providers, prompted_providers)
from nuh.llm_types import DECISION, FAILURE, UNCERTAIN, Answer, LLMCallResult, PromptParts, ProviderAttempt
from nuh.tool_call import ToolCall
from nuh.transcript import TranscriptRef

_STATUS = {DECISION: "success", UNCERTAIN: "uncertain", FAILURE: "error"}  # ProviderAttempt.status


@dataclass
class _Plan:
    """What prompted providers are sent: the prompt, or why nothing can be (too large)."""

    prompt: PromptParts | None = None
    action: dict = field(default_factory=dict)
    too_large: LLMCallResult | None = None


@dataclass
class _Review:
    """One review: what is reviewed, and how each kind of provider is shown it."""

    label: str
    llm_config: dict
    transcript: "str | TranscriptRef"
    trace: "tracing.Trace | None"
    call: ToolCall
    plan: Callable[[], _Plan]  # builds the prompted providers' prompt, on the first one reached


def _cascade(review: _Review) -> LLMCallResult:
    """Ask each provider in turn until one reaches a decision or is uncertain."""
    result = LLMCallResult()
    plan: _Plan | None = None
    sent = False
    for name, section in _configured_providers(review.llm_config):
        timeout = cascade_budget.capped(_provider_timeout(name, section))
        if timeout is None:
            # Too little of the hook's time is left to ask it: an error, so on_failure decides.
            model = str(section.get("model") or _DEFAULT_MODELS.get(name, "")) if isinstance(section, dict) else ""
            result.cascade.append(ProviderAttempt(name, _STATUS[FAILURE], 0, model, cascade_budget.skip_reason()))
            continue
        section = {**(section if isinstance(section, dict) else {}), "timeout": timeout}
        llm_heal.forget()
        if name == system1.NAME:
            answer, asked = system1.review(review.call, section, review.llm_config, review.transcript,
                                           review.trace), True
        else:
            plan = plan or review.plan()
            answer, asked = _prompted(name, section, plan, review.trace)
        sent = sent or asked
        result.steps += answer.steps
        result.cascade.append(ProviderAttempt(name, _STATUS[answer.outcome], answer.latency_ms, answer.model,
                                              answer.error))
        if answer.outcome != FAILURE:
            return _answered(result, name, answer, review, plan)
        # A timeout may be a model still loading: warm it in the background (llm.self_heal), never waited on.
        llm_heal.after_failure(name, answer.model, review.llm_config)
    if plan is not None and plan.too_large is not None and not sent:
        return plan.too_large  # nothing could be sent: ask, not a failure (llm_fallback)
    if plan is not None:
        result.prompt, result.action = _prompt_text(plan), plan.action
    return _unanswered(result, review)


def _prompted(name: str, section: dict, plan: _Plan, trace: "tracing.Trace | None") -> tuple[Answer, bool]:
    """(answer, whether it was sent) of prompted provider *name*; a failure when the prompt is too large for it."""
    model = section.get("model", _DEFAULT_MODELS.get(name, ""))
    if plan.prompt is None:
        reason = (plan.too_large.decision or {}).get("reason", "") if plan.too_large else ""
        return Answer(FAILURE, model=model, error=f"not sent: {reason}"), False
    result, elapsed, error = _call_provider(name, section, plan.prompt, trace)
    if result is None:
        return Answer(FAILURE, model=model, latency_ms=elapsed, error=error), True
    if result.decision == UNCERTAIN:
        reasoning = f"uncertain: {result.reasoning}" if result.reasoning else "uncertain"
        return Answer(UNCERTAIN, reasoning=reasoning, model=model, latency_ms=elapsed), True
    return Answer(DECISION, result.decision, result.reasoning, model, elapsed), True


def _prompt_text(plan: _Plan) -> str:
    return f"{plan.prompt.system}\n\n{plan.prompt.user}" if plan.prompt is not None else ""


def _answered(result: LLMCallResult, name: str, answer: Answer, review: _Review, plan: _Plan | None) -> LLMCallResult:
    """*result* of a cascade that provider *name* ended with a decision or uncertain."""
    result.provider, result.model, result.latency_ms = name, answer.model, answer.latency_ms
    result.prompt = answer.prompt or (_prompt_text(plan) if plan is not None else "")
    result.action = plan.action if plan is not None and not answer.prompt else {}
    if answer.outcome == UNCERTAIN:
        setting = llm_fallback.parse(review.llm_config, llm_fallback.ON_UNCERTAIN)
        result.uncertain = True
        result.reasoning = f"{answer.reasoning}; {llm_fallback.note(llm_fallback.ON_UNCERTAIN, setting)}"
        result.decision = {"decision": "ask", "reason": f"{review.label} (LLM): {result.reasoning}"}
        return result
    result.reasoning = answer.reasoning
    result.decision = _verdict_decision(answer.decision, answer.reasoning, review.label)
    return result


def _verdict_decision(decision: str, reasoning: str, label: str) -> dict:
    """The decision for a provider's verdict. Its ask is a permission prompt."""
    if decision == "allow":
        return {"decision": "allow", **({"reason": f"{label} (LLM): {reasoning}"} if reasoning else {})}
    if decision == "block":
        return {"decision": "block", "reason": f"{label} (LLM): {reasoning or 'LLM: blocked'}"}
    return {"decision": "ask", "reason": f"{label} (LLM): {reasoning or 'a human should decide'}"}


def _unanswered(result: LLMCallResult, review: _Review) -> LLMCallResult:
    """*result* of a cascade that ran out: when every provider errored, a failed review that asks."""
    setting = llm_fallback.parse(review.llm_config, llm_fallback.ON_FAILURE)
    ask = llm_failure.unanswered(review.label, result.cascade, setting)
    if ask is not None:
        last = result.cascade[-1]
        result.decision, result.failed = ask, True
        result.provider, result.model = last.provider, last.model
        result.latency_ms = sum(a.latency_ms for a in result.cascade)
    return result


def cascade_timeout_s(llm_config: dict) -> float:
    """Seconds the provider cascade may take at most: every provider's ``timeout`` added up.

    The cascade moves on after an error or timeout, so the worst case is each
    provider in turn running out. 0 when the LLM layer is off. Each
    ``timeout`` bounds its whole request (``llm_providers._exchange``).
    """
    if not llm_config.get("enabled", False):
        return 0.0
    return sum(_provider_timeout(name, section) for name, section in _configured_providers(llm_config))


def _provider_timeout(name: str, section: object) -> float:
    """Provider *name*'s request ``timeout`` (seconds), its default when unset or invalid."""
    default = _TIMEOUT_LOCAL if name == "ollama" else _TIMEOUT_REMOTE
    raw = section.get("timeout", default) if isinstance(section, dict) else default
    valid = isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0
    return float(raw) if valid else float(default)


def _prompt_char_limit(llm_config: dict, limits: "llm_action.Limits") -> int:
    """Largest prompt every prompted provider in the cascade can read whole (chars)."""
    limit = limits.max_prompt_chars
    for name, provider_config in prompted_providers(llm_config):
        if isinstance(provider_config, dict):
            tokens = llm_action.provider_token_limit(name, provider_config)
            limit = min(limit, llm_action.prompt_chars_fitting(tokens))
    return limit


def _plan(
    prompt: PromptParts, expendable: list[ExpendableSection], action_chars: int,
    action_stats: dict, llm_config: dict, label: str, trace: "tracing.Trace | None",
) -> _Plan:
    """The required *prompt* whole, then whatever *expendable* sections fit, or nothing.

    Only the required part (system text, call metadata, the action) can stop
    a review: over ``llm.max_action_chars``, or more than a provider in the
    cascade can read whole, nothing is sent and the decision is ask
    (``too_large``). Expendable sections fill the rest of the prompt budget
    in priority order; how much of them fits never affects the decision.
    """
    limits = llm_action.limits_from_config(llm_config)
    prompt_limit = _prompt_char_limit(llm_config, limits)
    prompt_chars = len(prompt.system) + len(prompt.user)
    action = {**action_stats, "chars": action_chars, "limit": limits.max_action_chars,
              "prompt_chars": prompt_chars, "prompt_limit": prompt_limit}
    if action_chars > limits.max_action_chars:
        return _Plan(too_large=_too_large(label, action, action_chars, limits.max_action_chars, trace))
    if prompt_chars > prompt_limit:
        return _Plan(too_large=_too_large(label, action, prompt_chars, prompt_limit, trace))
    extra = _fill_expendable(expendable, prompt_limit - prompt_chars)
    action["expendable_chars"] = len(extra)
    if trace is not None:
        trace.record_action(action)
    return _Plan(PromptParts(prompt.system, prompt.user + extra), action)


def _too_large(
    label: str, action: dict, chars: int, limit: int, trace: "tracing.Trace | None",
) -> LLMCallResult:
    """The not-reviewed result: ask, because the LLM could not be shown the whole action."""
    reason = llm_action.too_large_reason(label, chars, limit)
    action = {**action, "too_large": True}
    if trace is not None:
        trace.record_action(action)
    sys.stderr.write(f"nuh: LLM: {reason}; not sent\n")
    return LLMCallResult(decision={"decision": "ask", "reason": reason}, action=action, too_large=True)


def try_llm(
    classify_result, llm_config: dict, transcript: "str | TranscriptRef" = "",
    trace: "tracing.Trace | None" = None, call: "ToolCall | None" = None,
) -> LLMCallResult:
    """Try LLM providers in priority order. Returns LLMCallResult.

    ``result.decision`` is the LLM's verdict, {"decision": "allow" | "ask" |
    "block", ...}; {"decision": "ask", ...} with ``uncertain`` set when the
    model reached no decision, with ``too_large`` set when the action could
    not be sent whole, or with ``failed`` set when every provider errored; or
    None when no provider is configured.
    *transcript* is a transcript path or a TranscriptRef from the hook payload.
    *call* is the hook payload's tool call (the command alone when not given):
    what system 1 is shown.
    """
    call = call or ToolCall("Bash", {"command": classify_result.command}, os.getcwd())

    def plan() -> _Plan:
        action = _bash_action(classify_result, llm_action.limits_from_config(llm_config))
        expendable = _expendable_sections(transcript, llm_config, trace)
        return _plan(_build_prompt(classify_result, action), expendable, action.chars, action.stats,
                     llm_config, "Bash", trace)

    return _cascade(_Review("Bash", llm_config, transcript, trace, call, plan))


def try_llm_generic(
    tool_name: str, tool_input: dict, decision: dict, llm_config: dict,
    transcript: "str | TranscriptRef" = "",
    trace: "tracing.Trace | None" = None, call: "ToolCall | None" = None,
) -> LLMCallResult:
    """Try LLM providers for a tool that is neither Bash nor a write tool (hook_generic.py).

    Prompted providers are shown *tool_name* and *tool_input* as JSON, cut at
    ``llm.max_action_chars`` with a visible marker (``generic_input``), and
    the static *decision*'s reason. *call* is what system 1 is shown
    (*tool_name* and *tool_input* when not given).
    """
    call = call or ToolCall(tool_name, tool_input, os.getcwd())

    def plan() -> _Plan:
        limits = llm_action.limits_from_config(llm_config)
        shown, cut = generic_input(tool_input, limits.max_action_chars)
        expendable = _expendable_sections(transcript, llm_config, trace)
        return _plan(_build_generic_prompt(tool_name, shown, str(decision.get("reason") or "")), expendable,
                     min(len(shown), limits.max_action_chars), {"input_cut": cut}, llm_config, tool_name, trace)

    return _cascade(_Review(tool_name, llm_config, transcript, trace, call, plan))


def try_llm_write(
    tool_name: str,
    tool_input: dict,
    deterministic_decision: dict,
    llm_config: dict,
    transcript: "str | TranscriptRef" = "",
    trace: "tracing.Trace | None" = None,
    call: "ToolCall | None" = None,
) -> LLMCallResult:
    """Try LLM providers for Write/Edit content inspection (FD-080).

    *call* is the hook payload's tool call (*tool_name* and *tool_input* when
    not given): what system 1 is shown.
    """
    call = call or ToolCall(tool_name, tool_input, os.getcwd())

    def plan() -> _Plan:
        expendable = [*_expendable_sections(transcript, llm_config, trace),
                      _file_context_section(tool_name, tool_input, llm_config)]
        return _plan(_build_write_prompt(tool_name, tool_input, deterministic_decision), expendable,
                     llm_action.write_action_chars(tool_name, tool_input), {}, llm_config, tool_name, trace)

    return _cascade(_Review(tool_name, llm_config, transcript, trace, call, plan))
