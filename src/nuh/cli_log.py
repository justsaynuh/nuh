"""``nuh log`` and ``nuh tail``: filter, show and follow the decision log.

``nuh tail`` is ``nuh log --follow`` with a shorter backlog; both share one
set of filter flags (``cli_parser``). Compact view by default (one line per entry: time,
session, call reference, decision, tool, input, why, LLM verdict); ``-v``
expands each entry. Colour only on a terminal, and never with NO_COLOR set
or TERM=dumb.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from typing import BinaryIO

from nuh import log
from nuh.log_query import LogFilter, parse_lines, read_history
from nuh.log_view import LogView, use_color

POLL_S = 0.25
_DEFAULT_WIDTH = 120  # when stdout is not a terminal and COLUMNS is unset


def _filter(args: argparse.Namespace) -> LogFilter:
    return LogFilter(decision=args.decision, tool=args.tool, action_type=args.action_type,
                     project=args.project, grep=args.grep, llm=args.llm, errors=args.errors,
                     since=args.since, call=args.call, session=args.session)


def _view(args: argparse.Namespace) -> LogView:
    width = shutil.get_terminal_size((_DEFAULT_WIDTH, 24)).columns
    return LogView(color=use_color(sys.stdout), width=width, details=args.details,
                   show_full_input=args.full_input)


def _emit(entry: dict, view: LogView, as_json: bool) -> None:
    print(json.dumps(entry) if as_json else view.render(entry), flush=True)


def cmd_log(args: argparse.Namespace) -> None:
    """``nuh log``: matching entries, oldest first; with ``-f``, then every new one."""
    try:
        if getattr(args, "follow", False):
            _follow(args)
        else:
            _show(args)
    except BrokenPipeError:
        # The reader went away (``nuh log | head``): stop quietly. Point stdout
        # at devnull so the interpreter's exit flush does not raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except KeyboardInterrupt:
        # Ctrl-C is how a follow ends.
        pass


def cmd_tail(args: argparse.Namespace) -> None:
    """``nuh tail``: ``nuh log --follow``."""
    args.follow = True
    cmd_log(args)


def _show(args: argparse.Namespace) -> None:
    entries = read_history(_filter(args), args.limit, all_logs=args.all_logs)
    if not entries:
        print("No log entries found.")
        return
    if args.reverse:
        entries.reverse()
    view = _view(args)
    for entry in entries:
        _emit(entry, view, args.json)


def _follow(args: argparse.Namespace) -> None:
    if args.reverse:
        print("nuh log: --reverse cannot be combined with --follow", file=sys.stderr)
        raise SystemExit(2)
    flt = _filter(args)
    view = _view(args)
    handle = _open(log.LOG_PATH)
    raw = [] if handle is None else handle.readlines()
    if handle is not None and raw and not raw[-1].endswith(b"\n"):
        # A line still being written: leave it for the follow loop to finish.
        handle.seek(-len(raw.pop()), os.SEEK_CUR)
    live = [_decode(line) for line in raw]
    for entry in read_history(flt, args.limit, all_logs=args.all_logs, live_lines=live):
        _emit(entry, view, args.json)
    for entry in follow_entries(log.LOG_PATH, handle):
        if flt.matches(entry):
            _emit(entry, view, args.json)


def _open(path: str) -> BinaryIO | None:
    try:
        return open(path, "rb")
    except FileNotFoundError:
        # Not created yet (no decision logged), or between a rotation's rename
        # and the next write: the caller waits for it to appear.
        return None


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _replaced(path: str, handle: BinaryIO) -> bool:
    """*path* now names a different file than *handle* (log rotated)."""
    try:
        return os.stat(path).st_ino != os.fstat(handle.fileno()).st_ino
    except FileNotFoundError:
        # Rotated away and not recreated yet: keep the old handle until it is.
        return False


def follow_entries(path: str, handle: BinaryIO | None, *, poll_s: float = POLL_S,
                   sleep: Callable[[float], None] | None = None) -> Iterator[dict]:
    """Entries appended to *path* from *handle*'s position on, forever.

    Survives rotation: when *path* is a new file (inode changed) the rest of
    the old one is read, then the new one from its start. A truncated file
    is reread from its start. A line still being written waits for its end.
    *sleep* defaults to ``time.sleep``, looked up per call.
    """
    sleep = sleep or time.sleep
    pending = b""
    while True:
        if handle is None:
            handle = _open(path)
            if handle is None:
                sleep(poll_s)
                continue
        chunk = handle.readline()
        if chunk:
            pending += chunk
            if pending.endswith(b"\n"):
                yield from parse_lines([_decode(pending)])
                pending = b""
            continue
        if _replaced(path, handle):
            rest = pending + handle.read()
            handle.close()
            handle, pending = None, b""
            yield from parse_lines(_decode(rest).splitlines())
            continue
        try:
            truncated = os.fstat(handle.fileno()).st_size < handle.tell()
        except OSError as exc:
            sys.stderr.write(f"nuh: log: follow: {exc}\n")
            truncated = False
        if truncated:
            handle.seek(0)
            pending = b""
            continue
        sleep(poll_s)
