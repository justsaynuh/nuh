"""Split shell command text on its top-level command separators.

The separators are ``|``, ``|&``, ``||``, ``&&``, ``;``, ``&`` (with zsh
``&!`` / ``&|``) and an unquoted newline, which ends a command exactly like
``;``. Text is kept in one stage when it is:

- inside ``'…'``, ``"…"`` or ``$'…'`` (an unterminated quote runs to the end
  of the text, so nothing after it escapes as a separate stage);
- a heredoc body through its delimiter line. The body is attached to the
  stage holding its ``<<``, so in ``cat <<EOF | sh`` it is read by ``cat``,
  and the line after the delimiter starts a new stage;
- a backslash escape. A backslash-newline line continuation is removed, as
  the shell does, so ``gi\\<newline>t`` is the word ``git``.

A newline after ``|``, ``&&``, ``||``, ``&`` or ``;``, and a blank line, adds
no stage: ``a |<newline>b`` is a pipe.

An unquoted ``#`` that begins a word starts a comment through the end of the
line. It does not right after a backslash escape (``\\ #`` is a word) or
inside ``${…}``, ``$((…))``, ``$[…]`` or ``((…))``, which the shell reads as
one word: misreading either as a comment would hide the rest of the line.
The comment text is dropped (an apostrophe in it is not a quote), but
any ``__nuh_psub_N__`` substitution placeholders in it are kept: ``shell_lex``
extracts substitutions from comments as the stricter reading, and keeping
the placeholder keeps their classification attached to the stage.

``\\r`` is not a separator. In ``\\r\\n`` line endings it stays at the end of
the line's stage, where ``shlex`` reads it as whitespace, so CRLF text splits
exactly like LF text.

An unquoted ``(`` at command position opens a subshell and its matching
``)`` closes it (``split_commands`` counts them on the commands inside).
A paren glued to a word (``$((``, ``a=(``, ``*(.)``, ``@(a|b)``,
``name()``), ``((`` arithmetic, zsh's ``() { … }`` and a paren that is an
argument (``[[ x == (a|b) ]]``) stay text, as does the ``)`` closing one of
them; a case pattern's lone ``)`` is text too. Process and command
substitutions were taken out by the caller before splitting.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from nuh import shell_lex

# Placeholders bash_subs._replace_substitutions puts where a substitution was.
_PLACEHOLDER_RE = re.compile(r"__nuh_psub_\d+__")


class Command(NamedTuple):
    """One stage: its text, the separator after it, and the subshells around it.

    *opens* counts the ``(`` opened just before it, *closes* the ``)``
    closed just after it; when it closes one, *operator* is the separator
    after the subshell.
    """

    text: str
    operator: str
    opens: int = 0
    closes: int = 0


def split_on_operators(command: str) -> list[tuple[str, str]]:
    """Split *command* on top-level separators.

    Returns ``(stage_text, operator)`` pairs, where operator is the separator
    after the stage (``"|"``, ``"||"``, ``"&&"``, ``";"`` or ``"&"``; a newline
    is reported as ``";"``) and ``""`` for the last stage.
    """
    return [(cmd.text, cmd.operator) for cmd in split_commands(command)]


def split_commands(command: str) -> list[Command]:
    """``split_on_operators`` with the subshell parens counted on each command."""
    return _Splitter(command).run()


# Words after which the next word is still in command position: `if (a)`, `! (a)`.
_COMMAND_PREFIX_WORDS = frozenset({"!", "{", "then", "do", "else", "elif", "if", "while", "until", "time"})


def _background_operator_len(command: str, i: int, current: list[str]) -> int:
    """Length of a background operator at *i* (``&``, zsh ``&!`` / ``&|``), else 0.

    ``&`` that belongs to a redirect (``&>``, ``>&``, ``<&``) is not one.
    ``&&`` is handled by the caller before this check.
    """
    if command[i] != "&":
        return 0
    nxt = command[i + 1] if i + 1 < len(command) else ""
    if nxt == ">" or (current and current[-1] in ("<", ">")):
        return 0
    return 2 if nxt in ("!", "|") else 1


class _Splitter:
    """One left-to-right pass over the command text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.bodies = {doc.body_start: doc for doc in shell_lex.scan(text).heredocs}
        self.stages: list[list[str]] = []
        self.ops: list[str] = []
        self.starts: list[int] = []  # text index where each emitted stage began
        self.opens: list[int] = []
        self.closes: list[int] = []
        self.current: list[str] = []
        self.start = 0
        self.cur_opens = 0
        self.cur_closes = 0
        # One entry per unclosed `(`: the number of stages emitted when a
        # subshell opened, or None for a paren that is text.
        self.parens: list[int | None] = []
        # Index just past the last backslash escape. A `#` there continues a
        # word (`\ #` is the word " #"), so it never starts a comment; after a
        # line continuation that is also the stricter reading.
        self.escape_end = -1
        # End of the `${…}` / `$((…))` / `$[…]` / `((…))` word being read:
        # no comments inside. Separators there still split (stricter than
        # the shell, as before newline splitting).
        self.word_end = 0

    def run(self) -> list[Command]:
        i = 0
        while i < len(self.text):
            i = self._step(i)
        self._emit("", len(self.text))
        return [Command("".join(chunks), op, opens, closes)
                for chunks, op, opens, closes in zip(self.stages, self.ops, self.opens, self.closes)]

    def _step(self, i: int) -> int:
        """Consume the construct at *i*; return the index after it."""
        text = self.text
        c = text[i]
        if i in self.bodies:
            return self._heredoc_body(self.bodies[i])
        if c == "\n":
            return self._newline(i)
        if c in "$(" and i >= self.word_end:
            self.word_end = shell_lex.word_end(text, i) or 0
        if c == "#" and self._starts_comment(i):
            return self._comment(i)
        quoted_end = self._quoted_end(i)
        if quoted_end is not None:
            self.current.append(text[i:quoted_end])
            return quoted_end
        if c == "\\" and i + 1 < len(text):
            self.escape_end = i + 2
            if text[i + 1] != "\n":
                self.current.append(text[i:i + 2])
            return i + 2
        if c == "(":
            return self._open_paren(i)
        if c == ")" and self.parens:
            return self._close_paren(i)
        op, length = self._operator(i)
        if op:
            self._emit(op, i + length)
            return i + length
        self.current.append(c)
        return i + 1

    def _quoted_end(self, i: int) -> int | None:
        """Index just past a quoted string opening at *i*, or None."""
        text, c, n = self.text, self.text[i], len(self.text)
        if c == "'":
            close = text.find("'", i + 1)
            return n if close < 0 else close + 1
        if c == '"':
            close = shell_lex.escaped_quote_close(text, i + 1, n, '"')
        elif c == "$" and text.startswith("'", i + 1):
            close = shell_lex.escaped_quote_close(text, i + 2, n, "'")
        else:
            return None
        return n if close < 0 else close + 1

    def _operator(self, i: int) -> tuple[str, int]:
        """(operator, length) for a separator at *i*, else ("", 0)."""
        text = self.text
        c = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if c == "&" and nxt == "&":
            return "&&", 2
        background = _background_operator_len(text, i, self.current)
        if background:
            return "&", background
        if c == ";":
            return ";", 1
        if c != "|" or (self.current and self.current[-1] == ">"):
            return "", 0  # `>|` is a clobber redirect, not a pipe
        if nxt == "|":
            return "||", 2
        # `|&` pipes stdout and stderr: still a pipe into the next stage.
        return "|", 2 if nxt == "&" else 1

    def _emit(self, op: str, next_start: int) -> None:
        self.stages.append(self.current)
        self.ops.append(op)
        self.starts.append(self.start)
        self.opens.append(self.cur_opens)
        self.closes.append(self.cur_closes)
        self.current = []
        self.start = next_start
        self.cur_opens = self.cur_closes = 0

    def _command_position(self) -> bool:
        return all(word in _COMMAND_PREFIX_WORDS for word in "".join(self.current).split())

    def _open_paren(self, i: int) -> int:
        """A ``(`` at command position opens a subshell; any other is text."""
        if self._command_position() and not self.text.startswith(("()", "(("), i):
            self.parens.append(len(self.stages))
            self.cur_opens += 1
        else:
            self.parens.append(None)
            self.current.append("(")
        return i + 1

    def _close_paren(self, i: int) -> int:
        """Close the paren on top: a subshell ends the command inside it."""
        opened = self.parens.pop()
        if opened is None:
            self.current.append(")")
            return i + 1
        if not "".join(self.current).strip():
            if len(self.stages) <= opened:
                # `( )`: no command inside. The shell rejects it; nothing opens.
                self.cur_opens = max(0, self.cur_opens - 1)
                return i + 1
            self._reopen()
        self.cur_closes += 1
        j = i + 1
        while j < len(self.text) and self.text[j] in " \t":
            j += 1
        if j < len(self.text) and self.text[j] not in "\n);|&#":
            # `(a) > f` or a stray word: the rest is a command of its own.
            self._emit(";", i + 1)
        return i + 1

    def _reopen(self) -> None:
        """Resume the last emitted stage: the operator after its ``)`` is still to come."""
        self.current = self.stages.pop() + self.current
        self.ops.pop()
        self.start = self.starts.pop()
        self.cur_opens += self.opens.pop()
        self.cur_closes += self.closes.pop()

    def _newline(self, i: int) -> int:
        """A newline ends the current command, unless it is blank or a body follows."""
        if i + 1 in self.bodies:
            # The line carrying `<<` ends here; _heredoc_body adds this newline
            # back in front of the body, in the stage that owns the heredoc.
            return i + 1
        if "".join(self.current).strip():
            self._emit(";", i + 1)
        else:
            # Blank line, or a line ending in an operator: `a |\nb` is a pipe.
            self.current = []
            self.start = i + 1
        return i + 1

    def _heredoc_body(self, doc: shell_lex.Heredoc) -> int:
        """Attach *doc*'s body to the stage holding its ``<<``; resume after the delimiter line."""
        chunk = "\n" + self.text[doc.body_start:doc.end]
        if doc.operator >= self.start or not self.stages:
            self.current.append(chunk)
            return doc.end
        owner = 0
        for k, start in enumerate(self.starts):
            if start <= doc.operator:
                owner = k
        self.stages[owner].append(chunk)
        return doc.end

    def _starts_comment(self, i: int) -> bool:
        """True when the ``#`` at *i* starts a comment, as the shell reads it."""
        return (i >= self.word_end and i != self.escape_end
                and shell_lex.starts_comment(self.text, i))

    def _comment(self, i: int) -> int:
        """Drop the comment at *i* except its substitution placeholders."""
        line_end = self.text.find("\n", i)
        if line_end < 0:
            line_end = len(self.text)
        kept = _PLACEHOLDER_RE.findall(self.text, i, line_end)
        if kept:
            self.current.append(" " + " ".join(kept) + " ")
        return line_end
