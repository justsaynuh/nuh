"""Transcript records -> conversation events, for Claude Code and Codex JSONL.

An event is one thing that happened in the session before the call under
review: a message the user typed, a message from the agent that delegated to a
subagent, the agent's own prose, or a tool call the agent made. Harness
injections (system reminders, task notifications, AGENTS.md, environment
context) are not user messages and are dropped.

Each format also has a byte-level prefilter: transcripts are dominated by tool
output, API blobs and reasoning records, so lines are checked by substring
before ``json.loads``. Inside a JSON string every quote is escaped, so a needle
like ``"type":"user"`` can only match the record's own structure, never text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, NamedTuple

USER = "user"            # typed by the human
DELEGATOR = "delegator"  # written by the agent that delegated to a subagent
AGENT = "agent"          # the agent's own prose
TOOL = "tool"            # a tool call the agent made

_SUMMARY_CHARS = 120

_HARNESS_PREFIXES = (
    "<task-notification", "<local-command", "<command-message", "<system-reminder",
    "<bash-stdout", "<bash-stderr", "Caveat:", "[Request interrupted",
    "Another Claude session sent", "<agent-message", "This session is being continued from a previous conversation",
    "# AGENTS.md", "<environment_context", "<user_instructions", "<INSTRUCTIONS",
    "<turn_aborted", "<user_shell_command", "# Files mentioned by the user", "<image", "</image>",
    "<skill>", "<subagent_notification",
)
_COMMAND_NAME_RE = re.compile(r"<command-name>(?P<name>[^<]+)</command-name>")
_COMMAND_ARGS_CLOSE = "</command-args>"
_COMMAND_ARGS_RE = re.compile(r"<command-args>(?P<args>.*?)</command-args>", re.DOTALL)
_BARE_COMMAND_RE = re.compile(r"^\(invoked \S+\)$")


@dataclass(frozen=True)
class Event:
    kind: str
    text: str
    call_id: str = ""


class Format(NamedTuple):
    """How to read one transcript format."""

    name: str
    candidate: Callable[[bytes, bool, bool], bool]  # (line, want_user, want_agent)
    events: Callable[[dict, bool], list[Event]]     # (record, is_subagent)


# -- Prefilters --


def _kv(key: str, value: str | bool) -> tuple[bytes, ...]:
    """Compact and spaced spellings of one JSON key/value pair."""
    v = "true" if value is True else f'"{value}"'
    return (f'"{key}":{v}'.encode(), f'"{key}": {v}'.encode())


def _has(line: bytes, *needle_sets: tuple[bytes, ...]) -> bool:
    return any(n in line for needles in needle_sets for n in needles)


_FULL_PROBE_BYTES = 16 * 1024
_EDGE_BYTES = 4 * 1024
_CODEX_HEAD_BYTES = 1024


def _probe(line: bytes) -> bytes:
    """The part of a line the prefilter searches.

    Both formats put a record's own keys (type, role, the first content
    block's type) before or after its bulky content, so for a long line the
    first and last few KB hold everything the prefilter looks for. Searching
    only those keeps megabyte-sized tool output from costing a full scan per
    needle. A record that passes is parsed and checked again in full.
    """
    if len(line) <= _FULL_PROBE_BYTES:
        return line
    return line[:_EDGE_BYTES] + line[-_EDGE_BYTES:]


_C_USER = _kv("type", "user")
_C_ASSISTANT = _kv("type", "assistant")
_C_TOOL_RESULT = _kv("type", "tool_result")
_C_META = _kv("isMeta", True)
_C_QUEUED = _kv("type", "queued_command")
_C_TEXT = _kv("type", "text")
_C_TOOL_USE = _kv("type", "tool_use")

_X_RESPONSE_ITEM = _kv("type", "response_item")
_X_EVENT_MSG = _kv("type", "event_msg")
_X_MESSAGE = _kv("type", "message")
_X_CALL = _kv("type", "function_call") + _kv("type", "custom_tool_call")
_X_ROLE_USER = _kv("role", "user")
_X_ROLE_ASSISTANT = _kv("role", "assistant")
_X_USER_MESSAGE = _kv("type", "user_message")
_X_AGENT_MESSAGE = _kv("type", "agent_message")


def _claude_candidate(line: bytes, want_user: bool, want_agent: bool) -> bool:
    line = _probe(line)
    if want_user:
        if _has(line, _C_USER) and not _has(line, _C_TOOL_RESULT, _C_META):
            return True
        if _has(line, _C_QUEUED):
            return True
    return want_agent and _has(line, _C_ASSISTANT) and _has(line, _C_TEXT, _C_TOOL_USE)


def _codex_candidate(line: bytes, want_user: bool, want_agent: bool) -> bool:
    # Rollout records open with timestamp, type, then the payload's type and
    # role (fixed field order): the head alone decides.
    line = line[:_CODEX_HEAD_BYTES]
    if _has(line, _X_RESPONSE_ITEM):
        if _has(line, _X_MESSAGE):
            return (want_user and _has(line, _X_ROLE_USER)) or (want_agent and _has(line, _X_ROLE_ASSISTANT))
        if _has(line, _X_AGENT_MESSAGE):  # a message between agents (multi-agent v2)
            return want_user
        return want_agent and _has(line, _X_CALL)
    if _has(line, _X_EVENT_MSG):
        return (want_user and _has(line, _X_USER_MESSAGE)) or (want_agent and _has(line, _X_AGENT_MESSAGE))
    return False


# -- Text cleanup and summaries --


def clean_user_text(text: str) -> str:
    """User-role text as the reviewer should see it: '' for harness injections."""
    text = text.strip()
    name = _COMMAND_NAME_RE.search(text[:500]) if text.startswith("<command-") else None
    if name:
        # Bounded at the last closing tag: an opening tag with no close after
        # it cannot match, and would otherwise rescan to the end (quadratic).
        close = text.rfind(_COMMAND_ARGS_CLOSE)
        args = _COMMAND_ARGS_RE.search(text, 0, close + len(_COMMAND_ARGS_CLOSE)) if close >= 0 else None
        arg_text = args.group("args").strip() if args else ""
        return f"(invoked {name.group('name').strip()}" + (f" {arg_text})" if arg_text else ")")
    if text.startswith(_HARNESS_PREFIXES):
        return ""
    return text


def is_bare_command(text: str) -> bool:
    """A slash command without arguments (``/clear``): says nothing about the task."""
    return bool(_BARE_COMMAND_RE.match(text))


def _collapse(value: object, limit: int = _SUMMARY_CHARS) -> str:
    return " ".join(str(value).split())[:limit]


_DETAIL_KEYS = ("command", "cmd", "file_path", "notebook_path", "path", "pattern", "url", "query", "description")


def _detail(inp: dict) -> str:
    """The most telling string argument of a tool input, or ''."""
    for key in _DETAIL_KEYS:
        val = inp.get(key)
        if isinstance(val, list) and val:
            val = " ".join(map(str, val))
        if isinstance(val, str) and val:
            return _collapse(val)
    return ""


def format_tool_use_summary(block: dict) -> str:
    """Format a tool_use content block as a compact one-line summary."""
    name = block.get("name", "")
    if not name:
        return ""
    inp = block.get("input", {})
    if not isinstance(inp, dict):
        return f"[{name}]"
    if name in ("Bash", "Shell", "execute_bash", "shell"):
        cmd = str(inp.get("command", ""))[:80]
        return f"[Bash: {cmd}]" if cmd else "[Bash]"
    labels = {"Read": "Read", "fs_read": "Read", "Write": "Write", "fs_write": "Write",
              "write_to_file": "Write", "Edit": "Edit", "MultiEdit": "MultiEdit"}
    if name in labels:
        return f"[{labels[name]}: {inp.get('file_path', '')}]"
    if name == "NotebookEdit":
        return f"[NotebookEdit: {inp.get('notebook_path', '')}]"
    if name in ("Glob", "glob", "Grep", "grep"):
        return f"[{name.capitalize()}: {inp.get('pattern', '')}]"
    if name.startswith("mcp__"):
        for key, val in inp.items():
            return f"[{name}: {key}={str(val)[:60]}]"
    detail = _detail(inp)
    return f"[{name}: {detail}]" if detail else f"[{name}]"


def _codex_call_summary(payload: dict) -> str:
    name = str(payload.get("name") or "tool")
    if payload.get("type") == "function_call":
        raw = payload.get("arguments")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            parsed = raw
        detail = _detail(parsed) if isinstance(parsed, dict) else ""
        detail = detail or _collapse(raw or "")
    else:
        detail = _collapse(payload.get("input") or "")
    return f"[{name}: {detail}]" if detail else f"[{name}]"


# -- Claude Code records --


def _claude_user_texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    blocks = [b for b in content if isinstance(b, dict)]
    if any(b.get("type") == "tool_result" for b in blocks):
        return []
    return [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]


def _user_events(texts: list[str], subagent: bool) -> list[Event]:
    kind = DELEGATOR if subagent else USER
    cleaned = (clean_user_text(t) for t in texts)
    return [Event(kind, t) for t in cleaned if t]


def _claude_assistant_events(content: object) -> list[Event]:
    if isinstance(content, str):
        return [Event(AGENT, content.strip())] if content.strip() else []
    events = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
            events.append(Event(AGENT, block["text"].strip()))
        elif block.get("type") == "tool_use":
            summary = format_tool_use_summary(block)
            if summary:
                events.append(Event(TOOL, summary, str(block.get("id") or "")))
    return events


def claude_events(rec: dict, subagent: bool) -> list[Event]:
    """Events from one Claude Code transcript record, in record order."""
    rtype = rec.get("type")
    if rtype == "attachment":
        att = rec.get("attachment")
        # Messages typed while the agent is busy arrive as queued prompts.
        if isinstance(att, dict) and att.get("type") == "queued_command" \
                and att.get("commandMode") == "prompt" and isinstance(att.get("prompt"), str):
            return _user_events([att["prompt"]], subagent)
        return []
    msg = rec.get("message")
    if rtype not in ("user", "assistant") or not isinstance(msg, dict):
        return []
    if rtype == "assistant":
        return _claude_assistant_events(msg.get("content"))
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return []
    return _user_events(_claude_user_texts(msg.get("content")), subagent)


# -- Codex rollout records --


def _codex_texts(content: object, block_type: str) -> list[str]:
    if not isinstance(content, list):
        return []
    return [b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == block_type and isinstance(b.get("text"), str)]


def _codex_agent_message(payload: dict) -> list[Event]:
    """A task or message another agent sent this subagent: agent-written.

    Only a plaintext header (message type, task name, sender) is readable;
    Codex stores the body as ``encrypted_content``, which is noted, not shown.
    In the user's own session these records are subagent reports, never the
    user's words, so they are dropped there (see ``codex_events``).
    """
    content = payload.get("content")
    header = "\n".join(_codex_texts(content, "input_text")).strip()
    sealed = isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "encrypted_content" for b in content)
    text = f"{header} (encrypted, not readable)" if header and sealed else header
    return [Event(DELEGATOR, text)] if text else []


def codex_events(rec: dict, subagent: bool) -> list[Event]:
    """Events from one Codex rollout record."""
    payload = rec.get("payload")
    if not isinstance(payload, dict):
        return []
    kind = payload.get("type")
    if rec.get("type") == "event_msg":
        message = payload.get("message")
        if not isinstance(message, str):
            return []
        if kind == "user_message":
            return _user_events([message], subagent)
        return [Event(AGENT, message.strip())] if kind == "agent_message" and message.strip() else []
    if rec.get("type") != "response_item":
        return []
    if kind == "message" and payload.get("role") == "user":
        texts = [clean_user_text(t) for t in _codex_texts(payload.get("content"), "input_text")]
        joined = "\n".join(t for t in texts if t)
        return [Event(DELEGATOR if subagent else USER, joined)] if joined else []
    if kind == "message" and payload.get("role") == "assistant":
        joined = "\n".join(_codex_texts(payload.get("content"), "output_text")).strip()
        return [Event(AGENT, joined)] if joined else []
    if kind == "agent_message":
        return _codex_agent_message(payload) if subagent else []
    if kind in ("function_call", "custom_tool_call"):
        return [Event(TOOL, _codex_call_summary(payload), str(payload.get("call_id") or ""))]
    return []


CLAUDE = Format("claude", _claude_candidate, claude_events)
CODEX = Format("codex", _codex_candidate, codex_events)
