"""PreToolUse hook entry point — reads JSON from stdin, returns decision on stdout."""

import dataclasses
import json
import os
import sys
import time
from typing import TYPE_CHECKING

from nuh import (agents, bash_hint, bash_review, cascade_budget, deadline, floor, gating, hook_error, hook_generic,
                 llm_failure, llm_fallback, llm_skip, notice, paths, steps, taxonomy, trace)
from nuh.bash import classify_command
from nuh.hook_tools import _classify_unknown_tool, handle_glob, handle_grep, handle_read
from nuh.hook_write import _checked, handle_edit, handle_multiedit, handle_notebookedit, handle_write
from nuh.llm_meta import build as build_llm_meta
from nuh.transcript import TranscriptRef

if TYPE_CHECKING:
    from nuh.tool_call import ToolCall

_transcript_path: str = ""  # set per-invocation by main()
_agent_id: str = ""         # payload agent_id (subagent calls), set by main()
_tool_use_id: str = ""      # payload tool_use_id (the call under review), set by main()
_agent_type: str = ""       # payload agent_type (subagent name), set by main()
_payload: dict = {}         # the validated hook payload, set by main()
_agent: str = ""            # the detected agent, set by main()
_call: "ToolCall | None" = None  # system 1's view of _payload, built by _tool_call()
_answered: dict | None = None  # the decision already on stdout this invocation, set by _emit()


def _transcript() -> TranscriptRef:
    """Where the LLM reviewer reads conversation context for this call."""
    return TranscriptRef(_transcript_path, _agent_id, _tool_use_id)


def _tool_call() -> "ToolCall | None":
    """The payload's tool call for system 1, built once per invocation (None outside main())."""
    global _call
    if _call is None and _payload:
        from nuh.tool_call import ToolCall
        cwd = _payload.get("cwd")
        _call = ToolCall(
            agents.normalize_tool(_payload["tool_name"]), _payload.get("tool_input", {}),
            cwd if isinstance(cwd, str) and os.path.isabs(cwd) else os.getcwd(), _agent or agents.CLAUDE,
        )
    return _call


def _format_bash_reason(result) -> str:
    """Build the human-readable reason string from a ClassifyResult."""
    reason = result.reason
    if result.composition_rule:
        reason = f"[{result.composition_rule}] {reason}"
    return f"Bash: {reason}"


def _try_llm(classify_result) -> tuple[dict | None, dict]:
    """Attempt LLM resolution for bash ClassifyResult. Returns (decision, llm_meta).

    (None, {}) when the LLM is off. A review that raised asks (llm_failure):
    on a static allow that rests on it, the ask raises the allow.
    """
    try:
        from nuh.config import get_config
        cfg = get_config()
        if not cfg.llm or not cfg.llm.get("enabled", False):
            return None, {}
        from nuh.llm import try_llm
        deadline.check("Bash classification")
        tr = trace.start_review("bash", lambda: {"tool": "Bash", **dataclasses.asdict(classify_result)})
        with deadline.suspended():
            llm_call = try_llm(classify_result, cfg.llm, _transcript(), trace=tr, call=_tool_call())
        trace.record_call(llm_call, llm_call.decision)
        return llm_call.decision, build_llm_meta(llm_call, cfg)
    except Exception as exc:
        trace.record_error(exc)
        return llm_failure.crashed("Bash", exc, llm_fallback.configured(llm_fallback.ON_FAILURE))


def _classify_meta(result) -> dict:
    """Build classification metadata from ClassifyResult."""
    meta = {
        "stages": [
            {"action_type": sr.action_type, "decision": sr.decision,
             "policy": sr.default_policy, "reason": sr.reason}
            for sr in result.stages
        ],
    }
    if result.composition_rule:
        meta["composition_rule"] = result.composition_rule
    return meta


def handle_bash(tool_input: dict) -> dict:
    """Full Bash handler: structural classification -> LLM layer -> decision."""
    command = tool_input.get("command", "")
    if not command:
        return {"decision": taxonomy.ALLOW}

    result = classify_command(command)
    meta = _classify_meta(result)
    floor_rule = floor.enforced(result.floor_rules())
    word = gating.word("Bash")
    route = bash_review.route(result, word)

    if result.final_decision == taxonomy.BLOCK:
        decision = _with_floor({"decision": taxonomy.BLOCK, "reason": _format_bash_reason(result), "_meta": meta},
                               floor_rule)
        decision[steps.KEY] = [_bash_static_step(result, decision)]
        return decision

    if result.final_decision == taxonomy.ASK:
        return _handle_bash_ask(result, meta, floor_rule, word, route)

    decision = {"decision": taxonomy.ALLOW, "_meta": meta}
    trail = [_bash_static_step(result, decision)]
    # LLM veto gate for lang_exec scripts (FD-079): even when the deterministic
    # layer allows, the LLM inspects script content and its block raises the allow.
    if route.kind == bash_review.SCRIPT:
        llm_decision, llm_meta = _try_llm(result)
        meta.update(llm_meta)
        trail += steps.review(llm_meta, llm_decision)
        raised = bash_review.raised_allow(llm_decision, llm_meta)
        if raised:
            raised["_meta"] = meta
            raised[steps.KEY] = trail + [bash_review.raise_step(raised, llm_meta)]
            return raised
        # The LLM allowed it, or on_uncertain / on_failure kept it: the structural allow stands
        trail += bash_review.kept(llm_meta, taxonomy.ALLOW)
    decision[steps.KEY] = trail
    return decision


def _handle_bash_ask(result, meta: dict, floor_rule: str, word: str, route: bash_review.Route) -> dict:
    """A static Bash ask, after the LLM step when the route sends one (bash_review.ask_outcome)."""
    hint = bash_hint.build(result)
    if hint:
        meta["hint"] = hint
    # No call when no verdict could change the ask (a floored one, say).
    meta.update(llm_skip.meta(route.skip))
    static = {"decision": taxonomy.ASK, "reason": _format_bash_reason(result), "_meta": meta}
    if hint:
        static["_hint"] = hint
    static = _with_floor(static, floor_rule)
    trail = [_bash_static_step(result, static)]
    if route.kind != bash_review.ASK:
        static[steps.KEY] = trail
        return static
    llm_decision, llm_meta = _try_llm(result)
    meta.update(llm_meta)
    trail += steps.review(llm_meta, llm_decision)
    final, merged = bash_review.ask_outcome(static, llm_decision, llm_meta, word)
    if final is not static:
        if hint and final.get("decision") == taxonomy.ASK:
            final.setdefault("_hint", hint)
        final["_meta"] = meta
    final[steps.KEY] = trail + merged
    return final


def _bash_static_step(result, decision: dict) -> dict:
    """The static step of a Bash call: its decision, each stage's result, and the composition rule."""
    step = steps.static(decision, [dict(stage) for stage in _classify_meta(result)["stages"]])
    if result.composition_rule:
        step["composition"] = result.composition_rule
    return step


def _with_floor(decision: dict, rule: str) -> dict:
    """Mark *decision* as made by floor *rule* (for the log); unchanged when *rule* is ""."""
    if rule:
        decision["_floor"] = rule
    return decision


HANDLERS = {
    "Bash": handle_bash,
    "Read": handle_read,
    "Write": handle_write,
    "Edit": handle_edit,
    "MultiEdit": handle_multiedit,
    "NotebookEdit": handle_notebookedit,
    "Glob": handle_glob,
    "Grep": handle_grep,
}


def handle_apply_patch(tool_input: dict) -> dict:
    """Evaluate Codex patches without adapting away their multi-file structure."""
    from nuh.patch import handle_apply_patch as evaluate_patch
    return evaluate_patch(tool_input)


HANDLERS["apply_patch"] = handle_apply_patch


def _to_hook_output(decision: dict, agent: str) -> dict:
    """Convert internal decision to agent-appropriate output format."""
    d = decision.get("decision", taxonomy.ALLOW)
    reason = decision.get("reason", "")
    tool = agents.normalize_tool(_payload_str(_payload, "tool_name"))
    tool_input = _payload.get("tool_input") if isinstance(_payload.get("tool_input"), dict) else {}
    if d == taxonomy.BLOCK:
        shown = notice.for_decision(notice.BLOCK, decision, tool, tool_input, _tool_use_id)
        return agents.format_block(reason, agent, notice=shown)
    if d == taxonomy.ASK:
        hint = decision.get("_hint")
        if hint:
            reason = f"{reason}\n     {hint}"
        shown = notice.for_decision(notice.ASK, decision, tool, tool_input, _tool_use_id)
        said = steps.outcome(decision.get(steps.KEY), d)
        # The LLM's reasoning when the reason is not already its own (Codex shows it in the deny reason).
        warning = said.llm_reason if said.reason_source == steps.STATIC else ""
        return agents.format_ask(reason, agent, notice=shown, warning=warning)
    return agents.format_allow(agent)


def _log_hook_decision(
    tool: str, tool_input: dict, decision: dict,
    agent: str, total_ms: int, extra: dict | None = None,
) -> None:
    """Build and write the log entry, with *extra* top-level fields. Never raises."""
    try:
        from nuh.log import log_decision, input_summary, build_entry
        from nuh import __version__

        meta = decision.pop("_meta", None) or {}
        floor_rule = decision.pop("_floor", "")
        trail = decision.pop(steps.KEY, None) or []

        log_config = None
        try:
            from nuh.config import get_config
            log_config = get_config().log or None
        except Exception as exc:
            sys.stderr.write(f"nuh: config: log: {exc}\n")

        summary = input_summary(tool, tool_input)

        entry = build_entry(
            tool=tool, input_summary=summary,
            decision=decision.get("decision", "allow"),
            reason=decision.get("reason", ""),
            agent=agent, hook_version=__version__,
            total_ms=total_ms, meta=meta, steps=trail,
            transcript_path=_transcript_path,
            tool_input=tool_input,
            subagent={"agent_id": _agent_id, "agent_type": _agent_type,
                      "tool_use_id": _tool_use_id},
        )
        if floor.is_enforced(floor_rule):
            # The LLM was not asked; a replay can ask it about this call later.
            entry["floor"] = floor_rule
            if entry["decision"] == taxonomy.ASK:
                entry["llm_replayable"] = True
        trace_name = trace.current_name()
        if trace_name:
            # The trace directory this decision wrote, so a log line can be
            # matched to its trace exactly instead of by timing.
            entry["trace_dir"] = trace_name
        entry.update(extra or {})

        log_decision(entry, log_config)
    except Exception as exc:
        sys.stderr.write(f"nuh: log error: {exc}\n")


def _validate_input(data: object, agent: str) -> dict:
    """Reject malformed hook envelopes before they reach policy handlers."""
    if not isinstance(data, dict):
        raise ValueError("hook input must be an object")
    if not isinstance(data.get("tool_name"), str) or not data["tool_name"].strip():
        raise ValueError("tool_name must be a nonempty string")
    if not isinstance(data.get("tool_input", {}), dict):
        raise ValueError("tool_input must be an object")
    if "hook_event_name" in data and data["hook_event_name"] != "PreToolUse":
        raise ValueError("only PreToolUse events are supported")
    if agent == agents.CODEX:
        if "turn_id" in data and (
            not isinstance(data["turn_id"], str) or not data["turn_id"].strip()
        ):
            raise ValueError("turn_id must be a nonempty string")
        if "cwd" in data and (
            not isinstance(data["cwd"], str) or not os.path.isabs(data["cwd"])
        ):
            raise ValueError("cwd must be an absolute path")
        if data["tool_name"] == "Bash":
            command = data.get("tool_input", {}).get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Bash command must be a nonempty string")
    return data


def _payload_str(data: dict, key: str) -> str:
    """An optional string field of the hook payload; '' when absent or not a string."""
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _start_deadline(started_at: float) -> None:
    """Start the static inspection budget (hook.deadline_s) and the LLM cascade's, counted from *started_at*."""
    cascade_budget.start(started_at)
    try:
        from nuh.config import get_config
        budget = get_config().hook_deadline_s
    except Exception as exc:
        # A broken config must not leave the hook without a budget; the
        # handlers report the config error themselves where it matters.
        sys.stderr.write(f"nuh: config: hook.deadline_s: {exc}\n")
        budget = deadline.DEFAULT_DEADLINE_S
    deadline.start(budget, started_at)


def _decide(canonical: str, tool_input: dict) -> dict:
    """Run the tool's handler, then the generic review for tools that are neither Bash nor a write tool.

    A static budget that runs out asks, never allows.
    """
    try:
        handler = HANDLERS.get(canonical)
        decision = _classify_unknown_tool(canonical, tool_input) if handler is None else handler(tool_input)
        if gating.kind(canonical) == gating.GENERIC:
            decision = hook_generic.review(canonical, tool_input, decision)
        return decision
    except deadline.InspectionTimeout as exc:
        sys.stderr.write(f"{deadline.TIMEOUT_REASON} during {exc}\n")
        blocked = deadline.blocked_reason()
        if blocked:
            # A stage already blocked before time ran out: the timeout can't weaken that.
            return _checked("deadline", {"decision": taxonomy.BLOCK,
                                         "reason": f"{blocked} ({deadline.TIMEOUT_REASON} during {exc})"})
        return _checked("deadline", {"decision": taxonomy.ASK, "reason": f"{deadline.TIMEOUT_REASON} during {exc}"})


def _failed_call() -> tuple[str, dict]:
    """(tool, tool_input) of the call the hook failed on; ("unknown", {}) when its payload was never read."""
    tool_input = _payload.get("tool_input")
    return (agents.normalize_tool(_payload_str(_payload, "tool_name")) or "unknown",
            tool_input if isinstance(tool_input, dict) else {})


def _write(text: str) -> None:
    """Put one serialized hook answer on stdout."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _emit(decision: dict, answer: dict) -> None:
    """Write *answer*, the hook output for *decision*: the one decision object this invocation prints."""
    global _answered
    text = json.dumps(answer) + "\n"  # serialized first: a bad value raises before stdout holds anything
    # Recorded before the write: once any of it may be on stdout, a second object
    # (the error path's) would make the whole hook output invalid.
    _answered = decision
    _write(text)


def _answer_internal_error(exc: Exception, agent: str, t0: float) -> None:
    """Answer per ``hook.on_error`` (hook_error.py), then log what was answered. Never raises.

    When the decision was already written, the error reaches only stderr, the
    trace and the log: Claude Code rejects hook output holding two JSON objects.
    """
    on_error = hook_error.mode()
    tool, tool_input = _failed_call()
    emitted = None
    if _answered is None:
        emitted = hook_error.output(on_error, exc, agent, tool, tool_input, _tool_use_id)
    else:
        sys.stderr.write("nuh: the error came after the decision was sent; nothing more printed\n")
    try:
        trace.finish(None, emitted, 0, error=str(exc))
    except Exception as trace_exc:
        # The trace may be what raised; the answer below must still go out.
        sys.stderr.write(f"nuh: trace error: {trace_exc}\n")
    if emitted is not None:
        try:
            _write(json.dumps(emitted) + "\n")
        except BrokenPipeError:
            # The agent stopped reading: there is no one left to answer.
            pass
    try:
        if _answered is None:
            decision, extra = hook_error.log_decision(on_error, exc)
        else:
            decision = _answered
            extra = {"internal_error": {"error": hook_error.describe(exc), "after_answer": True}}
        _log_hook_decision(tool, tool_input, decision, agent, int((time.monotonic() - t0) * 1000), extra)
    except Exception as log_exc:
        # The answer is already on stdout; a failure here must not replace it
        # with a crash, so stderr is the one place left to report it.
        sys.stderr.write(f"nuh: log error: {log_exc}\n")


def main() -> None:
    agent = agents.CLAUDE  # default until we can detect
    original_cwd: str | None = None
    t0 = time.monotonic()
    try:
        global _transcript_path, _agent_id, _tool_use_id, _agent_type, _payload, _agent, _call, _answered
        _payload, _call, _answered = {}, None, None
        agent = agents.detect_agent(None)
        data = json.loads(sys.stdin.read())
        agent = agents.detect_agent(data)
        data = _validate_input(data, agent)
        _payload, _agent = data, agent
        if agent == agents.CODEX:
            # Codex has no $CLAUDE_PROJECT_DIR: its launch dir is the payload's cwd.
            paths.set_launch_dir(data.get("cwd", ""))
            paths.reset_project_root()
            if "cwd" in data:
                original_cwd = os.getcwd()
                os.chdir(data["cwd"])
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        _transcript_path = data.get("transcript_path", "")
        _agent_id = _payload_str(data, "agent_id")
        _tool_use_id = _payload_str(data, "tool_use_id")
        _agent_type = _payload_str(data, "agent_type")
        trace.begin(agent, data)
        _start_deadline(t0)

        canonical = agents.normalize_tool(tool_name)
        decision = steps.ensure(_decide(canonical, tool_input))

        d = decision.get("decision", taxonomy.ALLOW)

        emitted = None
        if d != taxonomy.ALLOW or gating.sends_allow(canonical):
            emitted = _to_hook_output(decision, agent)
            _emit(decision, emitted)

        total_ms = int((time.monotonic() - t0) * 1000)
        trace.finish(decision, emitted, total_ms)
        _log_hook_decision(canonical, tool_input, decision, agent, total_ms)

    except Exception as e:
        sys.stderr.write(f"nuh: error: {e}\n")
        _answer_internal_error(e, agent, t0)
    finally:
        deadline.clear()
        cascade_budget.clear()
        trace.end()
        if original_cwd is not None:
            os.chdir(original_cwd)
        if agent == agents.CODEX:
            paths.set_launch_dir(None)
            paths.reset_project_root()


if __name__ == "__main__":
    main()
