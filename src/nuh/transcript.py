"""Conversation context for the LLM reviewer, read from the agent's transcript.

The context is split into sections with separate budgets, so tool noise cannot
starve what the user asked for:

- the user's first message of the session (usually states the task)
- the user's last few messages
- for a subagent, what the delegating agent told it (agent-written, labeled so)
- a little of the agent's own prose (it can steer the reviewer: kept small)
- the agent's recent tool calls, minus the call under review

Claude Code subagent calls carry ``agent_id``: the subagent's own transcript
sits at ``<session>/subagents/agent-<id>.jsonl`` and supplies the agent prose,
tool calls and brief, while the user's messages come from the parent session.

A Codex subagent's ``transcript_path`` is its own rollout. Its user-role
records are history forked from the parent (below the rollout's
``subagent_history_start_ordinal``) and its brief arrives as an inter-agent
message, so the user's messages are read from the root session instead, found
by following ``parent_thread_id`` links (see ``codex_lineage``) and cut off at
the subagent's newest record. If the root cannot be found, the subagent's
user-role records are shown as agent-written, as before.

Hot path: the transcript is read backward in chunks and the scan stops once
every section is full (or a byte cap is hit); lines are prefiltered by
substring (see ``transcript_records``) so large records are never parsed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, fields
from typing import Callable, Iterator

from nuh.codex_lineage import Lineage, LineageError, find_root, newest_timestamp
from nuh.transcript_records import AGENT, CLAUDE, CODEX, DELEGATOR, TOOL, USER, Event, Format, is_bare_command
from nuh.transcript_render import FIRST as _FIRST
from nuh.transcript_render import render

_CHUNK = 64 * 1024
_MAX_SCAN_BYTES = 32 * 1024 * 1024   # backward scan cap per file
_MAX_HEAD_BYTES = 4 * 1024 * 1024    # forward scan cap for the first message
_MAX_LINE_BYTES = 1024 * 1024        # longer lines are tool output or blobs: skipped
_MAX_AGENT_MESSAGES = 6
_MAX_DELEGATOR_MESSAGES = 2
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Budget:
    """Section budgets. ``total_chars`` <= 0 disables transcript context.

    The LLM layer lowers ``total_chars`` to what the prompt has left beside
    the action; ``transcript_render`` decides what gives way.
    """

    total_chars: int = 12000        # llm.context_chars
    first_prompt_chars: int = 1500  # llm.context.first_prompt_chars
    user_turns: int = 6             # llm.context.user_turns
    user_chars: int = 4000          # llm.context.user_chars
    tool_calls: int = 12            # llm.context.tool_calls
    assistant_chars: int = 1500     # llm.context.assistant_chars


@dataclass(frozen=True)
class TranscriptRef:
    """Where the conversation lives, from the hook payload."""

    path: str = ""
    agent_id: str = ""
    tool_use_id: str = ""


def _int_setting(raw: object, name: str, default: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        sys.stderr.write(f"nuh: config: {name} must be an integer; using {default}\n")
        return default
    return raw


def budget_from_config(llm_config: dict) -> Budget:
    """Read ``llm.context_chars`` and the ``llm.context`` section budgets."""
    default = Budget()
    raw = llm_config.get("context")
    if raw is None:
        raw = {}
    elif not isinstance(raw, dict):
        sys.stderr.write("nuh: config: llm.context must be a mapping; using defaults\n")
        raw = {}
    for key in raw:
        if key == "total_chars" or key not in {f.name for f in fields(Budget)}:
            sys.stderr.write(f"nuh: config: unknown key llm.context.{key}; ignored\n")
    values = {"total_chars": _int_setting(llm_config.get("context_chars"), "llm.context_chars", default.total_chars)}
    for f in fields(Budget):
        if f.name != "total_chars":
            values[f.name] = max(0, _int_setting(raw.get(f.name), f"llm.context.{f.name}", getattr(default, f.name)))
    return Budget(**values)


# -- Reading lines --


@dataclass
class _Stats:
    bytes_scanned: int = 0


def _lines_backward(path: str, stats: _Stats) -> Iterator[bytes]:
    """Complete lines from the end of the file toward the start, newest first."""
    with open(path, "rb") as f:
        pos = f.seek(0, os.SEEK_END)
        floor = max(0, pos - _MAX_SCAN_BYTES)
        pieces: list[bytes] = []  # fragments of the line being assembled, from its end backward
        size = 0
        while pos > floor:
            n = min(_CHUNK, pos - floor)
            pos -= n
            f.seek(pos)
            buf = f.read(n)
            stats.bytes_scanned += n
            cut = len(buf)
            nl = buf.rfind(b"\n", 0, cut)
            while nl >= 0:
                start = buf[nl + 1:cut]
                if size + len(start) <= _MAX_LINE_BYTES and (start or pieces):
                    yield b"".join([start, *reversed(pieces)])
                pieces, size = [], 0
                cut = nl
                nl = buf.rfind(b"\n", 0, cut)
            size += cut
            pieces = [] if size > _MAX_LINE_BYTES else [*pieces, buf[:cut]]
        if pos == 0 and pieces and size <= _MAX_LINE_BYTES:
            yield b"".join(reversed(pieces))


def _lines_forward(path: str, stats: _Stats) -> Iterator[bytes]:
    """Lines from the start of the file, skipping oversize ones, up to a byte cap."""
    read = 0
    with open(path, "rb") as f:
        while read < _MAX_HEAD_BYTES:
            line = f.readline(_MAX_LINE_BYTES + 1)
            if not line:
                return
            read += len(line)
            stats.bytes_scanned += len(line)
            if len(line) <= _MAX_LINE_BYTES:
                yield line
                continue
            while line and not line.endswith(b"\n") and read < _MAX_HEAD_BYTES:
                line = f.readline(_CHUNK)
                read += len(line)
                stats.bytes_scanned += len(line)


def _record(line: bytes) -> dict | None:
    try:
        rec = json.loads(line)
    except ValueError:
        # A torn last line (the agent is still writing) or a non-JSON line:
        # neither carries a message, and the next line is independent.
        return None
    return rec if isinstance(rec, dict) else None


# -- Collecting events --


class _Collector:
    """Newest-first events for the sections one file supplies."""

    def __init__(self, budget: Budget, subagent: bool, agent_role: bool, exclude_call: str = "") -> None:
        self.budget = budget
        self.subagent = subagent      # user-role records are the delegating agent's
        self.agent_role = agent_role  # this file supplies agent prose and tool calls
        self.exclude_call = exclude_call
        self.found: dict[str, list[str]] = {USER: [], DELEGATOR: [], AGENT: [], TOOL: []}
        self.agent_chars = 0

    def wants_user(self) -> bool:
        if self.subagent:
            return len(self.found[DELEGATOR]) < _MAX_DELEGATOR_MESSAGES
        return len(self.found[USER]) < self.budget.user_turns and self.budget.user_chars > 0

    def wants_agent(self) -> bool:
        b = self.budget
        prose_open = self.agent_chars < b.assistant_chars and len(self.found[AGENT]) < _MAX_AGENT_MESSAGES
        return self.agent_role and (len(self.found[TOOL]) < b.tool_calls or prose_open)

    def add(self, ev: Event) -> None:
        if ev.kind == TOOL and self.exclude_call and ev.call_id == self.exclude_call:
            return
        caps = {USER: self.budget.user_turns if self.budget.user_chars > 0 else 0,
                DELEGATOR: _MAX_DELEGATOR_MESSAGES, TOOL: self.budget.tool_calls,
                AGENT: _MAX_AGENT_MESSAGES if self.agent_chars < self.budget.assistant_chars else 0}
        items = self.found[ev.kind]
        if len(items) >= caps[ev.kind] or (items and items[-1] == ev.text):
            return  # full, or the same message recorded twice in a row
        items.append(ev.text)
        if ev.kind == AGENT:
            self.agent_chars += len(ev.text)


def _inherited(rec: dict, floor: int) -> bool:
    """A Codex subagent record forked from its parent's history (ordinal below *floor*)."""
    ordinal = rec.get("ordinal")
    return floor > 0 and isinstance(ordinal, int) and ordinal < floor


def _scan(path: str, fmt: Format, subagent: bool, col: _Collector, stats: _Stats,
          floor: int = 0, until: str = "") -> None:
    """Collect events newest first. Records below ordinal *floor* end the scan
    (all older ones are inherited too); records stamped after *until* are skipped."""
    for line in _lines_backward(path, stats):
        want_user, want_agent = col.wants_user(), col.wants_agent()
        if not (want_user or want_agent):
            return
        if not fmt.candidate(line, want_user, want_agent):
            continue
        rec = _record(line)
        if rec and _inherited(rec, floor):
            return
        if rec and until and isinstance(rec.get("timestamp"), str) and rec["timestamp"] > until:
            continue
        for ev in reversed(fmt.events(rec, subagent) if rec else []):
            col.add(ev)


def _first_message(path: str, fmt: Format, subagent: bool, stats: _Stats, floor: int = 0) -> str:
    """The first user-role message in the file (the task, or a subagent's brief)."""
    for line in _lines_forward(path, stats):
        if not fmt.candidate(line, True, False):
            continue
        rec = _record(line)
        if rec and _inherited(rec, floor):
            continue
        for ev in fmt.events(rec, subagent) if rec else []:
            if ev.kind in (USER, DELEGATOR) and not is_bare_command(ev.text):
                return ev.text
    return ""


# -- Building --


def _subagent_path(ref: TranscriptRef) -> str:
    if not ref.agent_id or not _SAFE_ID.match(ref.agent_id):
        return ""
    base = ref.path[:-len(".jsonl")] if ref.path.endswith(".jsonl") else ref.path
    cand = os.path.join(base, "subagents", f"agent-{ref.agent_id}.jsonl")
    return cand if os.path.isfile(cand) else ""


@dataclass(frozen=True)
class _Plan:
    """Which file supplies which sections."""

    fmt: Format
    main: str        # the acting agent's own transcript: its prose, tool calls, brief
    subagent: bool   # main's user-role records are agent-written
    user_path: str   # the user's own messages; '' when they cannot be found
    floor: int = 0   # main's records below this ordinal are forked parent history
    until: str = ""  # user_path records stamped later postdate the call under review
    hops: int = 0    # parent links followed to reach user_path


def _codex_lineage(path: str) -> Lineage | None:
    try:
        return find_root(path)
    except LineageError as exc:
        # The rollout names a parent that cannot be followed. The context
        # falls back to the subagent's own rollout (no user messages); say so,
        # because the reviewer then sees less than it should.
        sys.stderr.write(f"nuh: transcript: codex parent lookup: {exc}\n")
        return None


def _plan(ref: TranscriptRef) -> _Plan:
    if not os.path.basename(ref.path).startswith("rollout-"):
        sub_path = _subagent_path(ref)
        return _Plan(CLAUDE, sub_path or ref.path, bool(sub_path), ref.path)
    if not ref.agent_id:
        return _Plan(CODEX, ref.path, False, ref.path)
    lineage = _codex_lineage(ref.path)
    if lineage is None:
        return _Plan(CODEX, ref.path, True, "")
    return _Plan(CODEX, ref.path, True, lineage.root_path, lineage.history_start,
                 newest_timestamp(ref.path), lineage.hops)


def _sections(ref: TranscriptRef, budget: Budget, stats: dict, io: _Stats) -> dict[str, list[str]]:
    plan = _plan(ref)
    fmt, main = plan.fmt, plan.main
    stats["source"] = f"{fmt.name}-subagent" if plan.subagent else fmt.name
    separate_user = bool(plan.user_path) and plan.user_path != main
    if separate_user:
        stats["parent"] = plan.user_path
    if plan.hops:
        stats["parent_hops"] = plan.hops

    col = _Collector(budget, plan.subagent, agent_role=True, exclude_call=ref.tool_use_id)
    _scan(main, fmt, plan.subagent, col, io, floor=plan.floor)
    out = {key: col.found[key][::-1] for key in (DELEGATOR, AGENT, TOOL)}
    if plan.subagent and budget.first_prompt_chars > 0:
        brief = _first_message(main, fmt, True, io, plan.floor)
        out[DELEGATOR] = [brief, *(m for m in out[DELEGATOR] if m != brief)] if brief else out[DELEGATOR]

    # The user's own messages: the parent session for a subagent, cut off at
    # the call's time (a Codex parent keeps running while its subagents work).
    users = col.found[USER]
    if separate_user:
        parent = _Collector(budget, subagent=False, agent_role=False)
        _scan(plan.user_path, fmt, False, parent, io, until=plan.until)
        users = parent.found[USER]
    out[USER] = users[::-1]
    want_first = plan.user_path and budget.first_prompt_chars > 0
    first = _first_message(plan.user_path, fmt, False, io) if want_first else ""
    out[_FIRST] = [first] if first and first not in out[USER] else []
    return {key: out[key] for key in (_FIRST, USER, DELEGATOR, AGENT, TOOL)}


def build_context(
    ref: TranscriptRef, budget: Budget, redact: Callable[[str], str] | None = None,
) -> tuple[str, dict]:
    """Return (context text, per-section stats). Never raises: "" on failure.

    *redact* is applied to every message before it is cut to budget, so a
    secret cannot survive as a truncated fragment the patterns no longer match.
    """
    stats: dict = {"source": "", "chars": 0}
    if not ref.path or budget.total_chars <= 0:
        return "", stats
    t0 = time.perf_counter()
    io = _Stats()
    try:
        sections = _sections(ref, budget, stats, io)
        if redact is not None:
            sections = {k: [redact(t) for t in v] for k, v in sections.items()}
        text, dropped = render(sections, budget)
        stats.update({_STAT_NAMES[k]: len(v) for k, v in sections.items()})
        if dropped:
            stats["dropped"] = {_STAT_NAMES[k]: n for k, n in dropped.items()}
    except FileNotFoundError:
        # The agent named a transcript that is not there (a session that
        # never wrote one, or `nuh test`): no conversation to show, and the
        # review proceeds without context exactly as when no path is given.
        text = ""
    except Exception as exc:
        # Context is optional prompt enrichment on the hook's hot path: an
        # unreadable file or an unexpected record shape must not take the
        # LLM review (or the hook) down with it. Report it and send none.
        sys.stderr.write(f"nuh: transcript: {type(exc).__name__}: {exc}\n")
        text = ""
    stats.update(chars=len(text), bytes_scanned=io.bytes_scanned, ms=round((time.perf_counter() - t0) * 1000, 1))
    return text, stats


def user_messages(ref: TranscriptRef, budget: Budget) -> tuple[list[str], str, dict]:
    """(the user's recent messages oldest first, their first message, stats). Never raises.

    For a reviewer that takes the conversation as fields, not a prompt
    (system 1): the user's messages ``build_context`` would show, chosen
    by the same rules (``user_turns``; a subagent's come from the parent
    session), but whole rather than cut to the character budgets. The first
    message is "" when it is already among the recent ones. Nothing is read
    when ``total_chars`` <= 0.
    """
    stats: dict = {"source": "", "chars": 0}
    if not ref.path or budget.total_chars <= 0:
        return [], "", stats
    t0 = time.perf_counter()
    io = _Stats()
    try:
        sections = _sections(ref, budget, stats, io)
    except FileNotFoundError:
        # As in build_context: no transcript file means no conversation to send.
        sections = {}
    except Exception as exc:
        # As in build_context: context is optional on the hot path, so a bad
        # transcript sends none rather than failing the review. Reported.
        sys.stderr.write(f"nuh: transcript: {type(exc).__name__}: {exc}\n")
        sections = {}
    users = sections.get(USER, [])
    first = next(iter(sections.get(_FIRST, [])), "")
    stats.update(user_messages=len(users), first_message=int(bool(first)),
                 chars=sum(map(len, users)) + len(first), bytes_scanned=io.bytes_scanned,
                 ms=round((time.perf_counter() - t0) * 1000, 1))
    return users, first, stats


_STAT_NAMES = {_FIRST: "first_message", USER: "user_messages", DELEGATOR: "delegator_messages",
               AGENT: "agent_messages", TOOL: "tool_calls"}
