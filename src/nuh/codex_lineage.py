"""Find the session the user types into, starting from a Codex subagent rollout.

Codex writes each thread to ``<sessions>/YYYY/MM/DD/rollout-<local time>-<thread id>.jsonl``.
The first line of a rollout is its ``session_meta`` record. A subagent's names
its parent thread (``parent_thread_id``, repeated under
``source.subagent.thread_spawn``) and ``subagent_history_start_ordinal``:
records below that ordinal are history forked from the parent (the user's
messages among them), not the subagent's own work. A top-level thread has no
parent link.

Thread ids are UUIDv7, so the first 48 bits of a parent's id give its creation
time and with it the date directory holding its rollout. Lookup cost per hop:
one bounded first-line read, and a filename glob in a few day directories
(that date and its neighbors, then the child's date backward), capped overall.
Nothing is cached: each hook run is a fresh process.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
from dataclasses import dataclass

_MAX_HOPS = 4                 # subagent -> parent -> ... -> the user's session
_MAX_DAYS_PROBED = 48         # day directories considered per lookup, all hops together
_WIDEN_DAYS = 31              # how far back from the child's date the last resort looks
_MAX_META_BYTES = 1024 * 1024
_MAX_TAIL_BYTES = 1024 * 1024
_CHUNK = 64 * 1024
_THREAD_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# A record's own timestamp opens its line (Codex writes it first, compact);
# inside a JSON string quotes are escaped, so this cannot match message text.
_LINE_TIMESTAMP = re.compile(rb'\n\{"timestamp": ?"([0-9T:.+\-Z]{10,40})"')


class LineageError(Exception):
    """The rollout says it has a parent, but the chain cannot be followed."""


@dataclass(frozen=True)
class Lineage:
    """Where a Codex subagent's user turns live."""

    root_path: str      # rollout of the thread the user types into
    root_id: str
    hops: int           # parent links followed to reach it
    history_start: int  # the subagent's records below this ordinal are inherited


def read_meta(path: str) -> dict:
    """The ``session_meta`` payload from a rollout's first line.

    OSError propagates (the caller decides whether a missing file is normal);
    a first line that is not a session_meta record raises LineageError.
    """
    with open(path, "rb") as f:
        line = f.readline(_MAX_META_BYTES + 1)
    if len(line) > _MAX_META_BYTES:
        raise LineageError(f"session_meta line over {_MAX_META_BYTES} bytes in {path}")
    try:
        rec = json.loads(line)
    except ValueError as exc:
        raise LineageError(f"unreadable session_meta in {path}: {exc}") from exc
    payload = rec.get("payload") if isinstance(rec, dict) and rec.get("type") == "session_meta" else None
    if not isinstance(payload, dict):
        raise LineageError(f"first record of {path} is not session_meta")
    return payload


def parent_id(meta: dict) -> str:
    """The thread that spawned this one, or '' for a top-level thread."""
    direct = meta.get("parent_thread_id")
    if isinstance(direct, str) and direct:
        return direct
    source = meta.get("source")
    spawn = source.get("subagent") if isinstance(source, dict) else None
    spawn = spawn.get("thread_spawn") if isinstance(spawn, dict) else None
    nested = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return nested if isinstance(nested, str) else ""


def _uuid7_day(thread_id: str) -> datetime.date | None:
    """Local creation date encoded in a UUIDv7 thread id (the rollout's directory)."""
    if thread_id[14] != "7":
        return None
    millis = int(thread_id[:8] + thread_id[9:13], 16)
    try:
        return datetime.date.fromtimestamp(millis / 1000)
    except (OverflowError, OSError, ValueError):
        # A version-7 id with a timestamp the platform cannot represent:
        # no date hint, so the lookup falls back to the child's own date.
        return None


def _dated_layout(child_path: str) -> tuple[str, datetime.date | None]:
    """(sessions root, child's date) for ``<root>/YYYY/MM/DD/<file>``; else (its directory, None)."""
    day_dir = os.path.dirname(os.path.abspath(child_path))
    month_dir = os.path.dirname(day_dir)
    year_dir = os.path.dirname(month_dir)
    try:
        day = datetime.date(int(os.path.basename(year_dir)), int(os.path.basename(month_dir)),
                            int(os.path.basename(day_dir)))
    except ValueError:
        return day_dir, None
    return os.path.dirname(year_dir), day


class _Locator:
    """Finds rollouts by thread id, probing a bounded number of day directories."""

    def __init__(self, child_path: str) -> None:
        self.root, self.child_day = _dated_layout(child_path)
        self.probed = 0

    def _days(self, thread_id: str) -> list[datetime.date]:
        one = datetime.timedelta(days=1)
        order: list[datetime.date] = []
        for anchor in (_uuid7_day(thread_id), self.child_day):
            if anchor is not None:
                order += [anchor, anchor - one, anchor + one]
        if self.child_day is not None:
            order += [self.child_day - one * k for k in range(2, _WIDEN_DAYS + 1)]
        return list(dict.fromkeys(order))

    def _dirs(self, thread_id: str) -> list[str]:
        if self.child_day is None:
            return [self.root]
        return [os.path.join(self.root, f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}")
                for d in self._days(thread_id)]

    def locate(self, thread_id: str) -> str:
        if not _THREAD_ID.match(thread_id):
            raise LineageError(f"unexpected thread id {thread_id[:80]!r}")
        for directory in self._dirs(thread_id):
            if self.probed >= _MAX_DAYS_PROBED:
                raise LineageError(f"gave up looking for thread {thread_id} after {self.probed} directories")
            self.probed += 1
            hits = glob.glob(os.path.join(glob.escape(directory), f"rollout-*-{thread_id}.jsonl"))
            if hits:
                return sorted(hits)[0]
        return ""


def find_root(child_path: str) -> Lineage | None:
    """Follow parent links from a subagent rollout to the user's session.

    None for a top-level thread (no parent link): that is the normal case and
    not an error. Raises LineageError when a parent is named but cannot be
    found or read, or the chain is deeper than the cap. OSError reading the
    child itself propagates.
    """
    meta = read_meta(child_path)
    parent = parent_id(meta)
    if not parent:
        return None
    start = meta.get("subagent_history_start_ordinal")
    history_start = start if isinstance(start, int) and not isinstance(start, bool) else 0
    locator = _Locator(child_path)
    for hop in range(1, _MAX_HOPS + 1):
        path = locator.locate(parent)
        if not path:
            raise LineageError(f"parent thread {parent} not found under {locator.root}")
        try:
            meta = read_meta(path)
        except OSError as exc:
            raise LineageError(f"cannot read parent thread {parent}: {exc}") from exc
        if meta.get("id") != parent:
            raise LineageError(f"{path} holds thread {str(meta.get('id'))[:80]!r}, not {parent}")
        nxt = parent_id(meta)
        if not nxt:
            return Lineage(path, parent, hop, history_start)
        parent = nxt
    raise LineageError(f"thread chain longer than {_MAX_HOPS} hops")


def newest_timestamp(path: str) -> str:
    """Timestamp of the newest record whose line start is in the last MB, or ''."""
    with open(path, "rb") as f:
        pos = f.seek(0, os.SEEK_END)
        stop = max(0, pos - _MAX_TAIL_BYTES)
        carry = b""
        while pos > stop:
            n = min(_CHUNK, pos - stop)
            pos -= n
            f.seek(pos)
            buf = f.read(n) + carry
            hits = _LINE_TIMESTAMP.findall((b"\n" if pos == 0 else b"") + buf)
            if hits:
                return hits[-1].decode("ascii")
            carry = buf[:64]  # a match may straddle the chunk boundary
    return ""
