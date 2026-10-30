"""Heredoc delimiter words and closing lines, as bash and zsh read them.

``shell_lex`` finds the ``<<`` operators; this module parses the delimiter
word after one and finds the line that closes its body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_END = frozenset(" \t\n;&|<>()")
_DQ_DELIM_ESCAPE = re.compile(r"\\([\\$`\"\n])")
_BODY_ESCAPE = re.compile(r"\\([\\$`\n])")


@dataclass(frozen=True)
class Heredoc:
    """A terminated heredoc: body is ``text[body_start:body_end]``.

    ``end`` is the index just past the closing delimiter line (its newline
    excluded). ``operator`` is the index of its ``<<``.
    """

    delimiter: str
    quoted: bool
    body_start: int
    body_end: int
    end: int
    operator: int


def heredoc_text(text: str, doc: Heredoc) -> str:
    """Text the command reads from heredoc *doc* of *text* (no newline before the delimiter).

    With an unquoted delimiter the shell removes ``\\$``, ``\\``` and ``\\\\``
    escapes and joins backslash-newline lines first, so ``bash <<EOF`` given
    ``\\$(cmd)`` really runs ``$(cmd)``.
    """
    body = text[doc.body_start:doc.body_end]
    body = body[:-1] if body.endswith("\n") else body
    if doc.quoted:
        return body
    return _BODY_ESCAPE.sub(lambda m: "" if m.group(1) == "\n" else m.group(1), body)


@dataclass(frozen=True)
class PendingHeredoc:
    """A ``<<`` operator whose body starts after the current line."""

    delimiter: str
    quoted: bool
    strip_tabs: bool
    operator: int


def escaped_quote_close(text: str, j: int, end: int, quote: str) -> int:
    """Index of the first *quote* from *j* not escaped by a backslash, or -1.

    Closes ``"…"`` and ``$'…'``, where a backslash escapes the next character.
    """
    while j < end:
        if text[j] == "\\":
            j += 2
        elif text[j] == quote:
            return j
        else:
            j += 1
    return -1


def delimiter_word(text: str, j: int, end: int) -> tuple[str, bool, int] | None:
    """Parse a heredoc delimiter word at *j*: (delimiter, quoted, index after).

    Any quoting makes the body literal. None for a missing word or a form
    nuh does not model (``$'…'`` / ``$"…"``), so the text after it stays
    scanned as commands.
    """
    parts: list[str] = []
    quoted = False
    while j < end and text[j] not in _WORD_END:
        c = text[j]
        if c == "$" and j + 1 < end and text[j + 1] in "'\"":
            return None
        if c == "'":
            close = text.find("'", j + 1, end)
            if close < 0:
                return None
            parts.append(text[j + 1:close])
            quoted, j = True, close + 1
        elif c == '"':
            close = escaped_quote_close(text, j + 1, end, '"')
            if close < 0:
                return None
            parts.append(_DQ_DELIM_ESCAPE.sub(r"\1", text[j + 1:close]))
            quoted, j = True, close + 1
        elif c == "\\" and j + 1 < end:
            parts.append(text[j + 1])
            quoted, j = True, j + 2
        else:
            parts.append(c)
            j += 1
    if not parts and not quoted:
        return None
    return "".join(parts), quoted, j


def delimiter_line(text: str, pos: int, end: int, doc: PendingHeredoc) -> tuple[int, int] | None:
    """Find the line closing *doc*'s body: (body end, line end), or None.

    With an unquoted delimiter a line ending in an unescaped backslash joins
    the next line, so that next line cannot close the body.
    """
    continued = False
    line_start = pos
    while True:
        newline = text.find("\n", line_start, end)
        line_end = end if newline < 0 else newline
        line = text[line_start:line_end]
        candidate = line.lstrip("\t") if doc.strip_tabs else line
        if candidate == doc.delimiter and not continued:
            return line_start, line_end
        if newline < 0:
            return None
        trailing = len(line) - len(line.rstrip("\\"))
        continued = not doc.quoted and trailing % 2 == 1
        line_start = newline + 1
