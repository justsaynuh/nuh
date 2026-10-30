"""What the hook answers when it fails inside: ``hook.on_error`` (global config only).

- ``ask`` (default): an ask whose reason names the error. A crash means nuh
  could not classify the call, which nuh treats like any other call it
  cannot settle.
- ``block``: a deny ("internal error — blocked for safety").
- ``defer``: no decision at all, so the agent's own permission flow decides,
  as the ``defer`` word in ``tools:`` does for an allow. nuh never sends an
  allow for a call it failed on.

Codex gets the same three: its ask is the block it already gets for any ask.
The config may be what broke, so a config that fails to load gives the
default. Claude Code's ``onFailure: block`` in the installed hook is a
different case: a hook that dies before it can answer at all.
"""

import sys

from nuh import agents, notice, taxonomy

ASK, BLOCK, DEFER = "ask", "block", "defer"
MODES = (ASK, BLOCK, DEFER)
DEFAULT = ASK


def mode() -> str:
    """``hook.on_error`` from the config; the default when the config cannot load."""
    try:
        from nuh.config import get_config  # lazy: config may be the module that broke
        return get_config().hook_on_error
    except Exception as exc:
        # The error path must not depend on the config that may have caused it.
        sys.stderr.write(f"nuh: config: hook.on_error: {exc}; using {DEFAULT}\n")
        return DEFAULT


def describe(exc: BaseException) -> str:
    """The error as the reason and log show it: ``RuntimeError: message``."""
    return f"{type(exc).__name__}: {exc}"


def output(on_error: str, exc: BaseException, agent: str, tool: str, tool_input: dict, call_id: str) -> dict | None:
    """What the hook prints for *on_error*; None for defer (no output: the agent decides)."""
    if on_error == DEFER:
        return None
    if on_error == BLOCK:
        return agents.format_error(str(exc), agent)
    reason = f"internal error: {describe(exc)}"
    try:
        shown = notice.for_decision(notice.ASK, {"decision": taxonomy.ASK, "reason": reason}, tool, tool_input,
                                    call_id)
    except Exception as notice_exc:
        # The ask must go out even when the notice cannot be built; its reason still names the error.
        sys.stderr.write(f"nuh: error notice: {notice_exc}\n")
        shown = ""
    return agents.format_ask(reason, agent, notice=shown)


def log_decision(on_error: str, exc: BaseException) -> tuple[dict, dict]:
    """(decision, extra log fields) for the row that records what *on_error* emitted."""
    error = describe(exc)
    extra = {"internal_error": {"error": error, "on_error": on_error}}
    if on_error == DEFER:
        reason = f"internal error, deferred (hook.on_error: defer, no decision sent): {error}"
        return {"decision": taxonomy.ALLOW, "reason": reason}, extra
    if on_error == BLOCK:
        return {"decision": taxonomy.BLOCK, "reason": f"nuh: internal error: {error}"}, extra
    return {"decision": taxonomy.ASK, "reason": f"internal error: {error}"}, extra
