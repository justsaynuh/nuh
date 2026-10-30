"""What the file a Write or Edit changes holds now, for the LLM review (want 8b).

A small edit reviewed alone is hard to judge; the lines around it say what
the file is and what the change does. An Edit or MultiEdit gets the whole
current file when it fits the budget, otherwise ``llm.file_context_lines``
lines either side of each edit's ``old_string`` (first occurrence). A Write
over an existing file gets the file's current content from the top. A new
file gets nothing. ``llm.file_context_chars`` caps the excerpt; windows
shrink first, then the excerpt is cut at a line.

Never for a path the read rules protect (sensitive or credential paths, nuh's
config): the excerpt would carry the very contents those rules guard off the
machine. Binary files and files nuh cannot read are left out.
"""

import os
import stat
import sys
from dataclasses import dataclass
from typing import Callable

from nuh import paths
from nuh.transcript import _int_setting

DEFAULT_LINES = 30
DEFAULT_CHARS = 8000
# Bytes read from the file at most; a larger file is excerpted from this head.
READ_LIMIT = 1024 * 1024
TOOLS = frozenset({"Write", "Edit", "MultiEdit"})
_GAP = "\n...\n"  # between two windows of the excerpt

Span = tuple[int, int]  # first and last line index, inclusive


@dataclass(frozen=True)
class FileContext:
    path: str
    excerpt: str
    truncated: bool  # the excerpt is not the whole current file
    lines: str       # 1-based inclusive ranges in the excerpt, "a-b" or "a-b,c-d"


def _setting(llm_config: dict, key: str, default: int) -> int:
    value = _int_setting(llm_config.get(key), f"llm.{key}", default)
    if value < 0:
        sys.stderr.write(f"nuh: config: llm.{key} must be 0 or more; using 0 (off)\n")
        return 0
    return value


def _read(path: str) -> tuple[str, bool] | None:
    """(text, cut at READ_LIMIT) of the file at *path*, or None: missing, not a file, binary, unreadable."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None  # a FIFO or device would block or never end; only regular files have content to show
        with open(path, "rb") as f:
            data = f.read(READ_LIMIT + 1)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None  # a new file (or no file at all): there is nothing to show
    except OSError as exc:
        # Context is optional: the review runs without it, but say why it is missing.
        sys.stderr.write(f"nuh: file context: {path}: {type(exc).__name__}: {exc}\n")
        return None
    cut = len(data) > READ_LIMIT
    if cut:
        data = data[:data.rfind(b"\n", 0, READ_LIMIT) + 1] or data[:READ_LIMIT]
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace"), cut


def _edit_spans(tool_name: str, tool_input: dict, text: str) -> list[Span]:
    """Line spans of each edit's old_string in *text* (edits whose text is not there are skipped)."""
    edits = tool_input.get("edits") if tool_name == "MultiEdit" else [tool_input]
    spans = []
    for edit in edits if isinstance(edits, list) else []:
        old = edit.get("old_string") if isinstance(edit, dict) else None
        index = text.find(old) if isinstance(old, str) and old else -1
        if index >= 0:
            # The line of old_string's last character: a trailing newline ends its line.
            spans.append((text.count("\n", 0, index), text.count("\n", 0, index + len(old) - 1)))
    return spans


def _windows(spans: list[Span], radius: int, last: int) -> list[Span]:
    """*spans* widened by *radius* lines, clamped to the file, overlapping or touching ones merged."""
    merged: list[Span] = []
    for first, end in sorted((max(0, a - radius), min(last, b + radius)) for a, b in spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((first, end))
    return merged


def _render(lines: list[str], windows: list[Span]) -> str:
    return _GAP.join("\n".join(lines[a:b + 1]) for a, b in windows)


def _fit_windows(lines: list[str], spans: list[Span], radius: int, budget: int) -> list[Span]:
    """The widest windows around *spans*, up to *radius* lines, whose excerpt fits *budget*."""
    last = len(lines) - 1
    for r in range(radius, 0, -1):
        windows = _windows(spans, r, last)
        if len(_render(lines, windows)) <= budget:
            return windows
    return _windows(spans, 0, last)


def _take(lines: list[str], windows: list[Span], budget: int) -> tuple[str, list[Span]]:
    """The longest prefix of *windows* within *budget* chars, in whole lines (a first line alone is cut)."""
    out: list[str] = []
    taken: list[Span] = []
    used = 0
    for first, end in windows:
        for i in range(first, end + 1):
            sep = "" if not out else (_GAP if i == first else "\n")
            if used + len(sep) + len(lines[i]) > budget:
                if not out:
                    return lines[i][:budget], [(i, i)]
                return "".join(out), taken
            out.append(sep + lines[i])
            used += len(sep) + len(lines[i])
            if i == first:
                taken.append((i, i))
            else:
                taken[-1] = (taken[-1][0], i)
    return "".join(out), taken


def build(
    tool_name: str, tool_input: object, llm_config: dict, *,
    max_chars: int | None = None, redact: Callable[[str], str] | None = None,
) -> FileContext | None:
    """The file context for this call, or None (off, not a Write/Edit, new, protected, binary, empty).

    *max_chars* lowers ``llm.file_context_chars`` (the room a prompt has
    left). *redact* masks secrets line by line before the excerpt is cut.
    """
    if tool_name not in TOOLS or not isinstance(tool_input, dict):
        return None
    radius = _setting(llm_config, "file_context_lines", DEFAULT_LINES)
    budget = _setting(llm_config, "file_context_chars", DEFAULT_CHARS)
    if max_chars is not None:
        budget = min(budget, max_chars)
    raw = tool_input.get("file_path")
    if radius == 0 or budget <= 0 or not isinstance(raw, str) or not raw:
        return None
    if paths.check_path("Read", raw):
        return None
    resolved = paths.resolve_path(raw)
    read = _read(resolved)
    if read is None or not read[0]:
        return None
    text, cut = read
    lines = (redact(text) if redact else text).split("\n")
    if text.endswith("\n"):
        lines.pop()
    whole = [(0, len(lines) - 1)]
    if tool_name == "Write" or (len(text) <= budget and not cut):
        windows = whole
    else:
        spans = _edit_spans(tool_name, tool_input, text)
        if not spans:
            return None
        windows = _fit_windows(lines, spans, radius, budget)
    excerpt, taken = _take(lines, windows, budget)
    lines_field = ",".join(f"{a + 1}-{b + 1}" for a, b in taken)
    return FileContext(raw, excerpt, cut or taken != whole, lines_field)
