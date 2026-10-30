"""Reading and filtering the decision log for ``nuh log`` / ``nuh tail``.

Entries are the JSONL lines ``log.log_decision`` writes. Every field is read
defensively: old entries lack newer fields, and a display command must show
what is there rather than fail on what is not.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nuh import llm_cap, llm_skip, log, steps

# Scheme prefixes on tool call IDs (Claude Code ``toolu_…``, OpenAI-style
# ``call_…``). They carry no information, so the short reference drops them.
CALL_PREFIXES = ("toolu_", "call_")
SHORT_CALL_LEN = 10
SHORT_SESSION_LEN = 8

_SINCE = re.compile(r"(\d+)\s*([smhd])")
_SINCE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def parse_since(spec: str, now: datetime | None = None) -> datetime:
    """``10m``, ``2h``, ``1d``, ``30s`` ago, or ``today`` (local midnight), as an aware datetime.

    Raises ValueError for anything else.
    """
    now = now or datetime.now(timezone.utc)
    text = spec.strip().lower()
    if text == "today":
        return now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    match = _SINCE.fullmatch(text)
    if not match:
        raise ValueError(f"bad --since value {spec!r} (try 10m, 2h, 1d, today)")
    return now - timedelta(**{_SINCE_UNITS[match.group(2)]: int(match.group(1))})


def entry_time(entry: dict) -> datetime | None:
    """The entry's ``ts`` as an aware datetime (UTC when the stamp has no offset)."""
    stamp = entry.get("ts")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _str(entry: dict, key: str) -> str:
    value = entry.get(key)
    return value if isinstance(value, str) else ""


def call_ref(entry: dict) -> str:
    """The reference for a call: its ``tool_use_id``, else the log entry's own ``id``."""
    return _str(entry, "tool_use_id") or _str(entry, "id")


def short_call(ref: str) -> str:
    """*ref* without its scheme prefix, cut to SHORT_CALL_LEN characters."""
    return _bare(ref)[:SHORT_CALL_LEN]


def session_id(entry: dict) -> str:
    """The session UUID from ``session`` (the transcript filename), else the filename's stem."""
    name = _str(entry, "session")
    found = _UUID.findall(name)
    if found:
        return found[-1].lower()
    return name.rsplit(".", 1)[0] if "." in name else name


def subagent_label(entry: dict) -> str:
    """``sub:<agent type>`` (or 8 chars of agent_id) for a subagent's call, else ""."""
    agent_id = _str(entry, "agent_id")
    if not agent_id:
        return ""
    agent_type = _str(entry, "agent_type").rsplit(":", 1)[-1]
    return f"sub:{agent_type[:16] or agent_id[:8]}"


def llm_consulted(entry: dict) -> bool:
    """The entry had an LLM step: not one skipped because no verdict could change the ask."""
    llm = entry.get("llm")
    return isinstance(llm, dict) and llm.get("skipped") != llm_skip.SKIPPED


def llm_failed(entry: dict) -> bool:
    """The LLM step errored or timed out (a cascade attempt failed, or the step was skipped on error)."""
    llm = entry.get("llm")
    if not isinstance(llm, dict):
        return False
    if llm.get("skipped") == "error":
        return True
    cascade = llm.get("cascade")
    return isinstance(cascade, list) and any(
        isinstance(step, dict) and step.get("status") == "error" for step in cascade)


def outcome(entry: dict) -> steps.Outcome:
    """What the entry's steps say about its decision (steps.outcome): whose reason, what the LLM said and wanted.

    Entries logged before ``steps`` carry the LLM's verdict, reasoning and cap
    in ``llm``; their reason source is not recorded and reads as static.
    """
    final = _str(entry, "decision")
    trail = entry.get("steps")
    if isinstance(trail, list):
        return steps.outcome(trail, final)
    llm = entry.get("llm")
    if not isinstance(llm, dict):
        return steps.Outcome()
    said, capped = llm_cap.shown(llm)
    reasoning = llm.get("reasoning") if isinstance(llm.get("reasoning"), str) else ""
    return steps.Outcome(steps.STATIC, said, reasoning, capped, said if said and said != final else "")


def _bare(ref: str) -> str:
    """*ref* without its scheme prefix (``toolu_``, ``call_``)."""
    for prefix in CALL_PREFIXES:
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _call_matches(entry: dict, query: str) -> bool:
    """*query* names the entry's call: a whole tool call ID, its start or its end, with or without ``toolu_``.

    A leading ``#`` is dropped: the notice shows a call as ``#`` and the ID's last characters.

    ``SESSION:CALL`` (the call ID: session ID + tool call ID) also needs the
    session ID to start with SESSION. The log entry's own ``id`` matches the
    same way, for entries without a tool call ID.
    """
    session, sep, call = query.rpartition(":")
    if sep and not (session and session_id(entry).lower().startswith(session.lower())):
        return False
    wanted = _bare(call.strip().removeprefix("#"))
    if not wanted:
        return False
    for ref in (_str(entry, "tool_use_id"), _str(entry, "id")):
        bare = _bare(ref)
        if bare and (bare.startswith(wanted) or bare.endswith(wanted)):
            return True
    return False


@dataclass
class LogFilter:
    """Which entries to show. Unset fields do not filter."""

    decision: str | None = None
    tool: str | None = None
    action_type: str | None = None
    project: str | None = None
    grep: re.Pattern[str] | None = None
    llm: bool = False
    errors: bool = False
    since: datetime | None = None
    call: str | None = None
    session: str | None = None

    def matches(self, entry: dict) -> bool:
        checks = (
            self.decision is None or entry.get("decision") == self.decision,
            self.tool is None or _str(entry, "tool").casefold() == self.tool.casefold(),
            self.action_type is None or entry.get("action_type") == self.action_type,
            self.project is None or self.project in _str(entry, "project"),
            not self.llm or llm_consulted(entry),
            not self.errors or llm_failed(entry),
            self.call is None or _call_matches(entry, self.call),
            self.session is None or session_id(entry).lower().startswith(self.session.lower()),
        )
        if not all(checks):
            return False
        if self.grep is not None and not self.grep.search(_str(entry, "input")):
            return False
        if self.since is not None:
            when = entry_time(entry)
            return when is not None and when >= self.since
        return True


def parse_lines(lines: Iterable[str]) -> Iterator[dict]:
    """Parsed entries of *lines*, skipping blank and unparseable ones."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # A line cut short by a concurrent write or a corrupted file:
            # nothing to display for it, and the rest of the log still is.
            continue
        if isinstance(entry, dict):
            yield entry


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except FileNotFoundError:
        # No log yet, or a backup rotated away between listing and opening.
        return []
    except OSError as exc:
        sys.stderr.write(f"nuh: log: cannot read {path}: {exc}\n")
        return []


def read_history(flt: LogFilter, limit: int, *, all_logs: bool = False,
                 live_lines: list[str] | None = None) -> list[dict]:
    """The newest *limit* matching entries (0 = no limit), oldest first.

    Reads the live log, then rotated backups only while fewer than *limit*
    entries matched, or all of them with *all_logs*. A ``since`` filter stops
    at the first file that reaches back past it. *live_lines* stands in for
    the live log's contents (``--follow`` has already read them).
    """
    found: list[dict] = []
    for index, path in enumerate(log.log_paths()):
        if index and not all_logs and (limit <= 0 or len(found) >= limit):
            break
        lines = live_lines if index == 0 and live_lines is not None else _read_lines(path)
        oldest: datetime | None = None
        for entry in parse_lines(reversed(lines)):
            when = entry_time(entry)
            if when is not None and (oldest is None or when < oldest):
                oldest = when
            if flt.matches(entry):
                found.append(entry)
                if 0 < limit <= len(found):
                    found.reverse()
                    return found
        if flt.since is not None and oldest is not None and oldest < flt.since:
            break
    found.reverse()
    return found
