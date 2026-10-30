"""Retention for LLM traces (``llm.trace_keep_days``): delete trace directories older than the cutoff.

What may be deleted, all of it checked per directory:

- its name parses as a nuh trace name (trace_names.parse), with a timestamp
  before the cutoff (a future timestamp is never old);
- it is a real directory, not a symlink (opened with O_NOFOLLOW);
- it holds only trace files (input.json, classification.json, result.json,
  request-NN-*.json, response-NN-*.json), each a regular file. A directory
  holding anything else is kept whole and reported.

Files are unlinked one by one through directory descriptors and the empty
directory removed; nothing is deleted recursively.

From the hook (trace.finish, after the decision is printed), ``maybe_prune``
costs one stat of the marker file: ``.<trace dir name>.pruned`` beside the
trace directory, outside it so nothing reading the traces sees it. The first
trace only stamps the marker. Once the marker is a day old, it is restamped
and the prune runs in a detached child process, which records its outcome in
the marker; ``nuh trace --prune`` shows it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from nuh import trace_names

if TYPE_CHECKING:
    import subprocess

INTERVAL_S = 24 * 3600
_TRACE_FILE = re.compile(r"(?:input|classification|result)\.json|(?:request|response)-\d{2,}-[A-Za-z0-9_.-]+\.json")
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_KEPT_SHOWN = 5


@dataclass
class Outcome:
    """What a prune did (or, dry, would do)."""

    cutoff: datetime
    deleted: list[str] = field(default_factory=list)
    bytes: int = 0
    kept: list[tuple[str, str]] = field(default_factory=list)  # (name, why it was not deleted)
    errors: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"finished": _iso(time.time()), "cutoff": self.cutoff.isoformat(), "deleted": len(self.deleted),
                "bytes": self.bytes, "kept": [f"{n}: {why}" for n, why in self.kept[:_KEPT_SHOWN]],
                "kept_count": len(self.kept), "errors": self.errors[:_KEPT_SHOWN]}


def marker_path(root: str) -> str:
    """The marker beside *root*: ``<parent>/.<root name>.pruned``."""
    root = os.path.normpath(root)
    return os.path.join(os.path.dirname(root), f".{os.path.basename(root)}.pruned")


def cutoff_for(keep_days: int, now: float | None = None) -> datetime:
    when = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    return when - timedelta(days=keep_days)


def prune(root: str, keep_days: int, *, dry_run: bool = False, now: float | None = None) -> Outcome:
    """Delete (with *dry_run*, only find) the trace directories under *root* older than *keep_days*."""
    outcome = Outcome(cutoff_for(keep_days, now))
    if keep_days <= 0:
        return outcome
    if os.open not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd:
        outcome.errors.append("this platform cannot delete through directory descriptors; nothing deleted")
        return outcome
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for name in _expired(root_fd, outcome.cutoff):
            why = _remove(root_fd, name, outcome, dry_run)
            if why:
                outcome.kept.append((name, why))
            else:
                outcome.deleted.append(name)
    finally:
        os.close(root_fd)
    return outcome


def _expired(root_fd: int, cutoff: datetime) -> list[str]:
    """Names under the root that parse as trace names older than *cutoff* and are directories, oldest first."""
    found = []
    with os.scandir(root_fd) as entries:
        for entry in entries:
            parsed = trace_names.parse(entry.name)
            if parsed is not None and parsed.stamp < cutoff and entry.is_dir(follow_symlinks=False):
                found.append(entry.name)
    return sorted(found)


def _remove(root_fd: int, name: str, outcome: Outcome, dry_run: bool) -> str:
    """Delete trace directory *name*; "" when deleted (or, dry, deletable), else why it was kept."""
    try:
        dir_fd = os.open(name, _DIR_FLAGS, dir_fd=root_fd)
    except OSError as exc:
        # Replaced by a symlink or a file since the scan (O_NOFOLLOW refuses
        # it), or deleted by another prune: either way, not deleted here.
        return f"cannot open: {exc.strerror or exc}"
    try:
        files, size = [], 0
        with os.scandir(dir_fd) as entries:
            for entry in entries:
                if not _TRACE_FILE.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                    return f"holds {entry.name!r}, not a trace file"
                files.append(entry.name)
                size += entry.stat(follow_symlinks=False).st_size
        if dry_run:
            outcome.bytes += size
            return ""
        for file in files:
            try:
                os.unlink(file, dir_fd=dir_fd)
            except FileNotFoundError:
                # Another prune (two hook calls past the day mark at once) got it first.
                continue
    except OSError as exc:
        outcome.errors.append(f"{name}: {exc.strerror or exc}")
        return "error while deleting"
    finally:
        os.close(dir_fd)
    try:
        os.rmdir(name, dir_fd=root_fd)
    except FileNotFoundError:
        # Another prune removed the emptied directory first: it is gone either way.
        pass
    except OSError as exc:
        # A file appeared between the unlinks and the rmdir: the directory stays.
        outcome.errors.append(f"{name}: {exc.strerror or exc}")
        return "not empty after deleting its trace files"
    outcome.bytes += size
    return ""


# -- From the hook --


def maybe_prune(root: str, keep_days: int, *, now: float | None = None) -> subprocess.Popen[bytes] | None:
    """Start a prune when the marker is a day old; the child process when one started, else None.

    The first trace under *root* only stamps the marker. Errors propagate to
    the caller (Trace.guard reports them on stderr).
    """
    if keep_days <= 0 or not root:
        return None
    marker = marker_path(root)
    now = time.time() if now is None else now
    try:
        age = now - os.stat(marker).st_mtime
    except FileNotFoundError:
        write_marker(marker, {"armed": _iso(now)})
        return None
    if 0 <= age < INTERVAL_S:
        return None
    write_marker(marker, {"started": _iso(now), "keep_days": keep_days})
    return _spawn(root, keep_days)


def _spawn(root: str, keep_days: int) -> subprocess.Popen[bytes]:
    """Run the prune in a detached child: no inherited stdio, so the hook's caller never waits on it."""
    import subprocess  # lazy import: needed once a day at most
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # -I: neither the hook's cwd (a project, maybe untrusted) nor PYTHON* variables shape what the child imports.
    code = "import sys; sys.path.insert(0, sys.argv[1]); from nuh.trace_prune import main; sys.exit(main(sys.argv[2:]))"
    return subprocess.Popen(
        [sys.executable, "-I", "-c", code, package_parent, root, str(keep_days)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd="/", start_new_session=True, close_fds=True)


def main(argv: list[str]) -> int:
    """The child's entry point: prune ``argv[0]`` keeping ``argv[1]`` days, and record the outcome in the marker."""
    root = argv[0]
    try:
        keep_days = int(argv[1])
        record = {**prune(root, keep_days).as_json(), "keep_days": keep_days}
    except Exception as exc:
        # The child's stderr goes nowhere (the hook's caller must not wait
        # on it): the marker is the only place a failure can be seen, by
        # `nuh trace --prune`. Record it there whatever it was.
        record = {"finished": _iso(time.time()), "error": f"{type(exc).__name__}: {exc}"}
    write_marker(marker_path(root), {**record, "automatic": True})
    return 1 if record.get("error") or record.get("errors") else 0


def write_marker(path: str, record: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "w") as f:
        json.dump(record, f)


def read_marker(path: str) -> dict:
    """The last prune's record, or {} when there is none or it cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"nuh: trace: prune marker {path}: {exc}\n")
        return {}
    return record if isinstance(record, dict) else {}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# -- nuh trace --prune --


def run_cli(root: str, keep_days: int, *, dry_run: bool) -> int:
    """``nuh trace --prune [--dry-run]``: prune now and say what happened."""
    if not root:
        print("nuh trace: llm.trace_dir is not set (tracing is off); pass --dir", file=sys.stderr)
        return 2
    last = read_marker(marker_path(root))
    if last.get("finished"):
        print(f"Last prune: {last['finished']}, deleted {last.get('deleted', 0)} "
              f"({_size(last.get('bytes', 0))})" + (f"; error: {last['error']}" if last.get("error") else ""))
    elif last.get("started"):
        print(f"Last automatic prune started {last['started']} and has not recorded an outcome.")
    if keep_days <= 0:
        print("llm.trace_keep_days is 0: traces are kept forever; nothing to prune.")
        return 0
    try:
        outcome = prune(root, keep_days, dry_run=dry_run)
    except OSError as exc:
        print(f"nuh trace: cannot prune {root}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    when = outcome.cutoff.astimezone().strftime("%Y-%m-%d %H:%M")
    verb = "Would delete" if dry_run else "Deleted"
    print(f"{verb} {len(outcome.deleted)} trace directories older than {keep_days} days (before {when}), "
          f"{_size(outcome.bytes)}.")
    if outcome.deleted:
        print(f"  oldest {outcome.deleted[0]}, newest {outcome.deleted[-1]}")
    for name, why in outcome.kept:
        print(f"  kept {name}: {why}")
    for error in outcome.errors:
        print(f"nuh trace: prune: {error}", file=sys.stderr)
    if not dry_run:
        write_marker(marker_path(root), {**outcome.as_json(), "keep_days": keep_days})
    return 1 if outcome.errors else 0


def _size(n: object) -> str:
    value = float(n) if isinstance(n, (int, float)) else 0.0
    units = ("B", "KB", "MB", "GB")
    step = 0
    while value >= 1024 and step < len(units) - 1:
        value /= 1024
        step += 1
    return f"{value:.0f} B" if step == 0 else f"{value:.1f} {units[step]}"
