"""Quote- and heredoc-aware scanning of shell command text.

Finds the substitutions a shell runs (``$(…)``, backticks, ``<(…)``, ``>(…)``,
zsh ``=(…)``) and the heredoc bodies it reads, following the quoting rules
bash and zsh share:

- single quotes and ``$'…'`` ANSI-C strings are literal;
- inside double quotes only ``$(…)``, ``$((…))`` and backticks expand;
  ``\\$`` and ``\\``` are literal and ``<(`` / ``>(`` are plain text;
- unquoted, a backslash escapes the next character;
- a heredoc body with a quoted delimiter (``<<'EOF'``, ``<<"EOF"``,
  ``<<\\EOF``, ``<<E"O"F``) is literal; with an unquoted delimiter only
  ``$(…)``, ``$((…))`` and backticks expand (quotes are plain text, and there
  is no globbing or process substitution);
- ``((…))``, ``$((…))`` and ``$[…]`` are arithmetic: ``<<`` there is a
  shift, not a heredoc, but ``$(…)`` and backticks inside still run. The
  shell reads the expression like a double-quoted string, so quotes there do
  not quote: ``$(( '$(cmd)' ))`` runs ``cmd`` in bash and zsh alike;
- ``<<`` inside ``${…}`` or a ``#`` comment is not a heredoc either;
- a backslash-newline is a line continuation, removed before the shell reads
  any token, except inside single quotes, ``$'…'``, a comment or a
  quoted-delimiter heredoc body (``shell_continuations``).

Unbalanced substitutions come back with kind ``FAILED`` so callers fail
closed. Heredoc detection errs toward "not a heredoc": text wrongly taken
for a body would hide the commands in it, so a body only counts once its
delimiter line is found, and an unterminated heredoc leaves the rest
scanned as commands.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from nuh.shell_heredoc import (Heredoc, PendingHeredoc, delimiter_line, delimiter_word,
                               escaped_quote_close)

FAILED = "failed"

# Deeper nesting than any real command uses; past it the whole command fails
# closed instead of exhausting Python's recursion limit in the hook.
_MAX_NESTING = 64

# (inner command, start, end, kind) — kind is "command", "backtick",
# "process_in", "process_out" or FAILED.
Sub = tuple[str, int, int, str]

_ZSH_EQUALS_PREV = frozenset(" \t\n;|&(")
_COMMENT_PREV = frozenset(" \t\n;|&")
_SUBSHELL_PREV = frozenset("$\n;|&")


@dataclass
class Scan:
    """Top-level substitutions and heredocs found in a command string."""

    subs: list[Sub] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)


def scan(text: str) -> Scan:
    """Return the substitutions and heredocs of *text* at top level.

    Substitutions nested inside another substitution are not listed; callers
    scan the inner command text themselves.
    """
    lexer = _Lexer(text)
    try:
        lexer.command(0, len(text), record=True)
    except _NestingTooDeep:
        # Unreadable by construction: one FAILED span makes callers block.
        return Scan(subs=[("", 0, len(text), FAILED)])
    return lexer.result


def expansions(text: str) -> list[Sub]:
    """Scan with quoted substitutions active, only for conservative written-body inspection."""
    lexer = _Lexer(text)
    try:
        lexer._expansions_only(0, len(text), record=True)
    except _NestingTooDeep:
        return [("", 0, len(text), FAILED)]
    return lexer.result.subs


def match_parens(text: str, start: int) -> int:
    """Index of the ``)`` closing the ``(`` at *start*, or -1 if unbalanced."""
    try:
        return _Lexer(text).command(start + 1, len(text), record=False, nested=True)
    except _NestingTooDeep:
        # Treated exactly like an unbalanced paren: callers fail closed on -1.
        return -1


def word_end(text: str, i: int) -> int | None:
    """Index just past a ``${…}``, ``$[…]``, ``$((…))`` or ``((…))`` at *i*, else None.

    The shell reads each as one word, so a ``#`` inside is no comment.
    """
    lexer = _Lexer(text, arithmetic_comments=False)
    end = len(text)
    try:
        if text.startswith("${", i):
            return lexer._braced_param(i, end, record=False)
        if text.startswith("$[", i):
            return lexer._bracket_arithmetic(i, end, record=False)
        if text.startswith("$((", i):
            inner = lexer.command(i + 3, end, record=False, nested=True, heredocs=False)
            return inner + 2 if inner >= 0 and text.startswith(")", inner + 1) else None
        if text.startswith("((", i):
            return lexer._arithmetic_command(i, end, record=False)
    except _NestingTooDeep:
        return end  # scan() already fails this text closed; no comment can hide anything
    return None


class _NestingTooDeep(Exception):
    """Raised past _MAX_NESTING levels of nested constructs."""


def continuations(text: str) -> tuple[list[int], set[int]] | None:
    """Line continuations of *text* in the context this scan reads: (backslash indices, those in heredoc bodies).

    None when *text* nests too deep to scan (``scan`` fails it closed).
    """
    lexer = _Lexer(text)
    try:
        lexer.command(0, len(text), record=False)
    except _NestingTooDeep:
        return None
    return sorted(set(lexer.continuations)), lexer.body_continuations


def blank_literal_heredocs(text: str) -> str:
    """*text* with quoted-delimiter heredoc bodies blanked out (they are literal)."""
    for doc in reversed(scan(text).heredocs):
        if doc.quoted:
            body = text[doc.body_start:doc.body_end]
            text = text[:doc.body_start] + re.sub(r"[^\n]", " ", body) + text[doc.body_end:]
    return text


class _Lexer:
    """Single-pass scanner; ``record`` decides whether findings are kept."""

    def __init__(self, text: str, *, arithmetic_comments: bool = True) -> None:
        self.text = text
        self.result = Scan()
        self._depth = 0
        # True while scanning an arithmetic expression, where quotes are plain characters.
        self._arithmetic = False
        # The shell has no comments in arithmetic; scan() keeps them, so `$((1 #))` fails closed.
        self._arithmetic_comments = arithmetic_comments
        # Closing index of non-recording nested scans, keyed by
        # (start, end, heredocs): keeps re-scans of nested text linear.
        self._closes: dict[tuple[int, int, bool], int] = {}
        # Index of each line continuation's backslash (``shell_continuations``),
        # recorded whether or not findings are; those in heredoc bodies again.
        self.continuations: list[int] = []
        self.body_continuations: set[int] = set()

    @contextmanager
    def _descend(self) -> Iterator[None]:
        self._depth += 1
        try:
            if self._depth > _MAX_NESTING:
                raise _NestingTooDeep(f"more than {_MAX_NESTING} nested shell constructs")
            yield
        finally:
            self._depth -= 1

    def command(self, i: int, end: int, *, record: bool,
                nested: bool = False, heredocs: bool = True) -> int:
        """Scan command text in ``[i, end)``.

        Nested: an opening paren is already open; return the index of its
        closing ``)``, or -1 when there is none. Otherwise return *end*.
        ``heredocs=False`` scans an arithmetic expression.
        """
        key = (i, end, heredocs)
        if nested and not record and key in self._closes:
            return self._closes[key]
        outer_arithmetic, self._arithmetic = self._arithmetic, not heredocs
        try:
            with self._descend():
                close = self._scan_command(i, end, record, nested, heredocs)
        finally:
            self._arithmetic = outer_arithmetic
        if nested and not record:
            self._closes[key] = close
        return close

    def _marks(self) -> tuple[int, int, int]:
        return len(self.result.subs), len(self.result.heredocs), len(self.continuations)

    def _rollback(self, subs: int, heredocs: int, continuations: int) -> None:
        """Drop what was recorded since ``_marks`` returned these counts.

        The continuations go too: a reading that failed (``$((a) b)`` read as
        arithmetic, where quotes do not quote) says nothing about them.
        """
        del self.result.subs[subs:]
        del self.result.heredocs[heredocs:]
        del self.continuations[continuations:]

    def _scan_command(self, i: int, end: int, record: bool, nested: bool, heredocs: bool) -> int:
        text = self.text
        pending: list[PendingHeredoc] = []
        depth = 1
        while i < end:
            c = text[i]
            if c == "\n" and pending:
                i = self._heredoc_bodies(i + 1, end, pending, record)
                pending = []
                continue
            if heredocs and text.startswith("<<", i):
                i = self._heredoc_operator(i, end, pending)
                continue
            # heredocs=False marks arithmetic, where the shell has no comments.
            comments = heredocs or self._arithmetic_comments
            nxt = self._unquoted_construct(i, end, record, comments)
            if nxt is not None:
                i = nxt
                continue
            if nested and c in "()":
                depth += 1 if c == "(" else -1
                if depth == 0:
                    return i
            i += 1
        return -1 if nested else end

    def _unquoted_construct(self, i: int, end: int, record: bool, comments: bool) -> int | None:
        """Skip a construct at *i* in unquoted command text; None when there is none.

        Comments, ``${…}``, ``$[…]`` and ``((…))`` are skipped as units so a
        ``<<`` inside them (a parameter word, a shift) is never taken for a
        heredoc: a false heredoc would hide the commands after it.
        """
        text = self.text
        c = text[i]
        if comments and c == "#" and starts_comment(text, i):
            newline = text.find("\n", i, end)
            line_end = end if newline < 0 else newline
            self._expansions_only(i, line_end, record)
            return line_end
        if text.startswith("${", i):
            return self._braced_param(i, end, record)
        if text.startswith("$[", i):
            return self._bracket_arithmetic(i, end, record)
        if text.startswith("((", i):
            nxt = self._arithmetic_command(i, end, record)
            if nxt is not None:
                return nxt
        return self._quoted_or_expansion(i, end, record)

    def _braced_param(self, i: int, end: int, record: bool) -> int:
        """Index just past the ``${…}`` at *i*; quotes and expansions apply inside.

        Unterminated ``${`` is a shell syntax error: recorded as FAILED.
        """
        j = i + 2
        with self._descend():
            while j < end:
                if self.text[j] == "}":
                    return j + 1
                if self.text.startswith("${", j):
                    j = self._braced_param(j, end, record)
                    continue
                nxt = self._quoted_or_expansion(j, end, record)
                j = nxt if nxt is not None else j + 1
        if record:
            self.result.subs.append(("", i, i + 2, FAILED))
        return end

    def _bracket_arithmetic(self, i: int, end: int, record: bool) -> int:
        """Index just past legacy ``$[expr]`` arithmetic at *i* (``$(…)`` inside runs)."""
        depth = 0
        for j in range(i + 1, end):
            ch = self.text[j]
            depth += (ch == "[") - (ch == "]")
            if depth == 0:
                self.command(i + 2, j, record=record, heredocs=False)
                return j + 1
        return i + 2

    # --- quoting and expansions ------------------------------------------

    def _quoted_or_expansion(self, i: int, end: int, record: bool) -> int | None:
        """Skip a quoted region or expansion at *i*; None when there is none."""
        text = self.text
        c = text[i]
        nxt = text[i + 1] if i + 1 < end else ""
        if self._arithmetic and (c in "'\"" or (c == "$" and nxt in "'\"")):
            return i + 1  # arithmetic is read like a double-quoted string: no quoting
        if c == "'":
            close = text.find("'", i + 1, end)
            return close + 1 if close >= 0 else end
        if c == "$" and nxt == "'":
            close = escaped_quote_close(text, i + 2, end, "'")
            return close + 1 if close >= 0 else end
        if c == '"':
            return self._double_quoted(i, end, record)
        if nxt == "(" and (c in "<>" or (c == "=" and (i == 0 or text[i - 1] in _ZSH_EQUALS_PREV))):
            kind = "process_out" if c == ">" else "process_in"
            close = self.command(i + 2, end, record=False, nested=True)
            return self._record_paren(i, close, kind, record)
        return self._expansion(i, end, record)

    def _expansion(self, i: int, end: int, record: bool) -> int | None:
        """Backslash escape, ``$(…)``/``$((…))`` or backtick at *i*, else None.

        These are the only constructs active in every context: unquoted,
        double-quoted and unquoted-delimiter heredoc bodies. A comment is
        scanned up to its newline, so a backslash ending one is no continuation.
        """
        text = self.text
        c = text[i]
        if c == "\\":
            if i + 1 < end and text[i + 1] == "\n":
                self.continuations.append(i)
            return min(i + 2, end)
        if c == "$" and i + 1 < end and text[i + 1] == "(":
            return self._dollar_paren(i, end, record)
        if c == "`":
            return self._backtick(i, end, record)
        return None

    def _double_quoted(self, i: int, end: int, record: bool) -> int:
        """Index just past the double-quoted string opening at *i*."""
        j = i + 1
        while j < end:
            if self.text[j] == '"':
                return j + 1
            nxt = self._expansion(j, end, record)
            j = nxt if nxt is not None else j + 1
        return end

    def _dollar_paren(self, i: int, end: int, record: bool) -> int:
        """Handle ``$(…)`` or arithmetic ``$((…))`` opening at *i*."""
        text = self.text
        if i + 2 < end and text[i + 2] == "(":
            # $((expr)) is arithmetic, not a command, but $(…) and backticks
            # in it run. `$((a) b)` is a command substitution after all.
            marks = self._marks()
            inner = self.command(i + 3, end, record=record, nested=True, heredocs=False)
            if inner >= 0 and inner + 1 < end and text[inner + 1] == ")":
                return inner + 2
            self._rollback(*marks)
        close = self.command(i + 2, end, record=False, nested=True)
        return self._record_paren(i, close, "command", record)

    def _arithmetic_command(self, i: int, end: int, record: bool) -> int | None:
        """Skip a ``((…))`` arithmetic command at *i*; None if it never closes."""
        marks = self._marks()
        close = self.command(i + 1, end, record=record, nested=True, heredocs=False)
        if close < 0:
            self._rollback(*marks)
            return None
        return close + 1

    def _record_paren(self, start: int, close: int, kind: str, record: bool) -> int:
        """Record a two-character-opener substitution; FAILED when unbalanced."""
        if close < 0:
            if record:
                self.result.subs.append(("", start, start + 2, FAILED))
            return start + 2
        if record:
            self.result.subs.append((self.text[start + 2:close].strip(), start, close + 1, kind))
        return close + 1

    def _backtick(self, i: int, end: int, record: bool) -> int:
        """Handle a backtick substitution opening at *i*."""
        text = self.text
        j = i + 1
        while j < end:
            if text[j] == "\\":
                if j + 1 < end and text[j + 1] == "\n":
                    self.continuations.append(j)
                j += 2
                continue
            if text[j] == "`":
                if record:
                    self.result.subs.append((text[i + 1:j], i, j + 1, "backtick"))
                return j + 1
            j += 1
        if record:
            self.result.subs.append(("", i, i + 1, FAILED))
        return end

    # --- heredocs --------------------------------------------------------

    def _heredoc_operator(self, i: int, end: int, pending: list[PendingHeredoc]) -> int:
        """Register the heredoc whose ``<<`` is at *i*; return where to resume.

        A here-string ``<<<`` or a ``<<`` without a readable delimiter word
        resumes one character later, so ``<(`` right after it is still seen.
        """
        text = self.text
        if text.startswith("<<<", i):
            return i + 2
        j = i + 2
        strip_tabs = j < end and text[j] == "-"
        if strip_tabs:
            j += 1
        while j < end and text[j] in " \t":
            j += 1
        word = delimiter_word(text, j, end)
        if word is None:
            return i + 1
        delimiter, quoted, after = word
        pending.append(PendingHeredoc(delimiter, quoted, strip_tabs, i))
        return after

    def _heredoc_bodies(self, pos: int, end: int, pending: list[PendingHeredoc], record: bool) -> int:
        """Consume the bodies of *pending* heredocs starting at *pos*.

        Returns where command scanning resumes. An unterminated body stops
        here: the shell would read the rest as data, nuh keeps scanning it as
        commands (the stricter reading).
        """
        resume = pos
        for doc in pending:
            found = delimiter_line(self.text, pos, end, doc) if pos <= end else None
            if found is None:
                return min(pos, end)
            body_end, line_end = found
            if record:
                self.result.heredocs.append(Heredoc(doc.delimiter, doc.quoted, pos, body_end, line_end, doc.operator))
            if not doc.quoted:
                mark = len(self.continuations)
                self._expansions_only(pos, body_end, record)
                self.body_continuations.update(self.continuations[mark:])
            resume = line_end
            pos = line_end + 1
        return resume

    def _expansions_only(self, i: int, end: int, record: bool) -> None:
        """Scan text where quotes are plain characters and only ``$(…)`` and backticks expand.

        Used for unquoted-delimiter heredoc bodies, and for comments: those
        never run, but a shell with comments off would expand them, so the
        substitutions are still extracted (the stricter reading).
        """
        while i < end:
            nxt = self._expansion(i, end, record)
            i = nxt if nxt is not None else i + 1


def starts_comment(text: str, i: int) -> bool:
    """True when the ``#`` at *i* begins a word and so a comment.

    After ``(`` it does only when that paren opens a subshell or ``$(``; in
    zsh ``*(#q…)``, ``(#i)x`` and ``(a)#`` the ``#`` is glob syntax.
    """
    if i == 0 or text[i - 1] in _COMMENT_PREV:
        return True
    return text[i - 1] == "(" and (i < 2 or text[i - 2] in _SUBSHELL_PREV)
