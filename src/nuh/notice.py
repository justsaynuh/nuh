"""The one-line notice a Claude client shows the user for an ask or a block (the hook's ``systemMessage``).

One fixed shape, one line (Claude Code shows each line of a systemMessage as
its own notice); an empty part drops out with its separator:

    {icon} **{verdict}** {tool} · {target} · {reason} · {call}

    ⚠️ **nuh?** Bash · `curl -sI https://evil.example/` · unknown host: `evil.example` · `#a7Kq2x`
    ⚠️ **nuh?** Bash · `frobnicate --sync` · 🤖 posts a dotfile to an outside host · `#Qm3v8L`
    ⚠️ **nuh?** Bash · `curl -sI https://x.example/` · unknown host: `x.example` (🤖 wanted block) · `#Hx52rD`
    ⛔ **nuh!** Bash · `cat ~/.ssh/id_ed25519` · targets sensitive path: `~/.ssh` · `#Lp0xT3`
    💥 **nuh: internal error** · blocked for safety · … · To bypass: `nuh uninstall` · …

{reason} is the reason that set the final decision (steps.outcome): the
static one, or after 🤖 the LLM's. When the LLM's own verdict differs from
the final decision, "(🤖 wanted X)" follows it ("(wanted X)" after an LLM
reason). {call} is the end of the tool call ID; ``nuh log --call <it> -v``
shows the rest: every step, the floor rule, hints, the cap, confidence.

Claude clients render the notice as Markdown, and a reason carries text nuh
did not write. The rule here: a notice is nuh's own fixed text (the
constants and literals in this module) joined with ``md.code_span`` and
``md.escape`` output, never a raw value. The reason as a whole goes through
``md.escape``; values nuh can cut out of it cleanly (the hidden-character
excerpt), the call's own target (its command or path) and the call ID are
code spans.
"""

import os

from nuh import hidden_chars, md, steps

ASK = "⚠️ **nuh?**"
BLOCK = "⛔ **nuh!**"
ERROR = "💥 **nuh: internal error**"
LLM = "🤖"
SEP = " · "
CALL_CHARS = 6  # how much of the tool call ID the notice shows (``nuh log --call`` matches an ID's end)

# The tool_input field that names what the call acts on, shown in code; paths keep their end when cut.
_TARGET_FIELDS = {
    "Bash": "command", "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "MultiEdit": "file_path", "NotebookEdit": "notebook_path", "Glob": "pattern", "Grep": "pattern",
}
_PATH_TOOLS = frozenset({"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"})
_TARGET_MAX = 80


def for_decision(head: str, decision: dict, tool: str = "", tool_input: dict | None = None,
                 call_id: str = "") -> str:
    """The notice for an internal *decision* on a *tool* call with *tool_input*; *head* is ASK or BLOCK."""
    said = steps.outcome(decision.get(steps.KEY), str(decision.get("decision") or ""))
    by_llm = said.reason_source == steps.LLM
    reason = said.llm_reason if by_llm else str(decision.get("reason") or "")
    target = _target(tool, tool_input or {})
    if not by_llm and (target in reason or (tool == "Bash" and _hidden_parts(reason))):
        target = ""  # the reason already shows it
    return render(head, reason, tool=tool, target=target, call_id=call_id, by_llm=by_llm, wanted=said.wanted)


def render(head: str, reason: str, *, tool: str = "", target: str = "", call_id: str = "",
           by_llm: bool = False, wanted: str = "") -> str:
    """One notice line.

    *head* (ASK or BLOCK), the *tool*, its *target* in code, the *reason*
    (the LLM's when *by_llm*), what the LLM *wanted* when the final decision
    differs from its verdict, and the end of the tool call ID *call_id*.
    """
    parts = []
    if target:
        parts.append(md.code_span(_cut(target, path=tool in _PATH_TOOLS)))
    shown = _llm_reason(reason) if by_llm else _reason(reason, tool)
    if wanted:
        shown = f"{shown} ({'' if by_llm else LLM + ' '}wanted {md.escape(wanted)})".lstrip()
    if shown:
        parts.append(shown)
    if call_id:
        parts.append(md.code_span("#" + call_id[-CALL_CHARS:]))
    # The tool is named unless the static reason names it ("unrecognized tool: mcp__x").
    if tool and (by_llm or tool not in _without_tool(reason.strip(), tool)):
        return SEP.join([f"{head} {_tool(tool)}", *parts])
    return f"{head} {SEP.join(parts)}" if parts else head


def error(message: str) -> str:
    """The notice for an internal error that blocked the call."""
    return SEP.join([ERROR, "blocked for safety", md.escape(message) or md.EMPTY,
                     f"To bypass: {md.code_span('nuh uninstall')}", f"To debug: {md.code_span('nuh log')}"])


def _tool(tool: str) -> str:
    return tool if tool.isalnum() and tool.isascii() else md.code_span(tool)


def _target(tool: str, tool_input: dict) -> str:
    """What the call acts on (its command, or its path with home as ``~``); "" when the tool names none."""
    field = _TARGET_FIELDS.get(tool, "")
    value = tool_input.get(field)
    if not isinstance(value, str):
        return ""
    return _home_short(value.strip()) if tool in _PATH_TOOLS else value.strip()


def _cut(target: str, *, path: bool) -> str:
    """*target* cut to _TARGET_MAX characters: a path keeps its end, a command its start."""
    if len(target) <= _TARGET_MAX:
        return target
    return "…" + target[-(_TARGET_MAX - 1):] if path else target[:_TARGET_MAX - 1] + "…"


def _home_short(path: str) -> str:
    """*path* with the home directory written ``~``, as nuh's reasons write paths."""
    home = os.path.expanduser("~").rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def _llm_reason(reasoning: str) -> str:
    """🤖 and the LLM's reasoning as prose."""
    text = md.escape(reasoning)
    return f"{LLM} {text}" if text else LLM


def _hidden_parts(reason: str) -> tuple[str, str] | None:
    """(what, excerpt) of a hidden-characters reason (hidden_chars.reason), or None."""
    what, mark, excerpt = reason.partition(hidden_chars.MARK)
    return (what, excerpt) if mark else None


def _reason(reason: str, tool: str) -> str:
    """*reason* as Markdown, without the tool name the notice already shows; a hidden-characters excerpt in code."""
    reason = _without_tool(reason.strip(), tool)
    hidden = _hidden_parts(reason)
    if hidden is None:
        return md.escape(reason)
    what, excerpt = hidden
    label = "hidden characters"
    if what not in ("command", "content"):
        label = f"{md.escape(what)} holds hidden characters"
    return f"{label}{SEP}{md.code_span(excerpt)}"


def _without_tool(reason: str, tool: str) -> str:
    """*reason* without a leading "<tool>: " or "<tool> " (the notice names the tool)."""
    if tool:
        for prefix in (f"{tool}: ", f"{tool} "):
            if reason.startswith(prefix):
                return reason[len(prefix):]
    return reason
