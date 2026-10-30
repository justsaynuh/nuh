"""The files and commands an awk program's own text reaches.

awk writes files only through output redirection in a ``print``/``printf``
statement (``print … > expr``, ``print … >> expr``) and runs commands through
pipes (``print … | cmd``, ``cmd | getline``) and ``system()``. A ``>`` anywhere
else (``NR>=5``, ``$1 > 5 {print}``, ``print (a > b)``) is a comparison.

The reading is conservative: inside a print statement every ``>`` outside
parentheses and brackets counts as a redirect (awk's own grammar reads it so),
and its target is a literal path only when it is one string literal standing
alone; any other target is an expression whose value nuh cannot know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GETLINE_INPUT_RE = re.compile(
    r"\bgetline\b\s*(?:\$?[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)?\s*<\s*(\"(?:[^\"\\\n]|\\.)*\")")
_STATEMENT_END = frozenset(";\n}")


@dataclass
class Reach:
    """What an awk program's text writes, reads and runs.

    ``writes`` holds a literal file name, or None for a file named by an
    expression; ``reads`` the literal names ``getline <`` reads. ``pipes`` is
    True when a print statement pipes to a command.
    """

    writes: list[str | None] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    pipes: bool = False


def _string_end(text: str, i: int) -> int:
    """Index just past the string literal opening at *i* (or the end of *text*)."""
    j = i + 1
    while j < len(text) and text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, len(text))


def _literal(text: str) -> str:
    """The value of the awk string literal *text* (quotes included), escapes decoded as awk does for names."""
    body = text[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def _target(text: str, i: int) -> tuple[str | None, int]:
    """The redirect target starting at *i*: (literal name or None, index where the statement goes on)."""
    while i < len(text) and text[i] in " \t":
        i += 1
    if i < len(text) and text[i] == '"':
        end = _string_end(text, i)
        rest = end
        while rest < len(text) and text[rest] in " \t":
            rest += 1
        if text[end - 1] == '"' and end - 1 > i and (rest == len(text) or text[rest] in _STATEMENT_END):
            return _literal(text[i:end]), rest
    return None, i


def _print_statement(text: str, i: int, reach: Reach) -> int:
    """Scan the print statement whose arguments start at *i*; record its redirects. Returns where it ends."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            i = _string_end(text, i)
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth < 0:
                return i
        elif depth == 0 and c in _STATEMENT_END:
            return i
        elif depth == 0 and c == ">":
            start = i + 2 if text.startswith(">>", i) else i + 1
            name, i = _target(text, start)
            reach.writes.append(name)
            continue
        elif depth == 0 and c == "|" and not text.startswith("||", i) and (i == 0 or text[i - 1] != "|"):
            reach.pipes = True
        i += 1
    return i


def reach(program: str) -> Reach:
    """What *program* (awk source text) writes, reads and runs, as far as its text shows."""
    found = Reach()
    i = 0
    while i < len(program):
        c = program[i]
        if c == '"':
            i = _string_end(program, i)
            continue
        if c == "#":
            newline = program.find("\n", i)
            i = len(program) if newline < 0 else newline
            continue
        match = _WORD_RE.match(program, i)
        if match:
            word_end = match.end()
            if match.group(0) in ("print", "printf") and (i == 0 or program[i - 1] not in "$."):
                i = _print_statement(program, word_end, found)
            else:
                i = word_end
            continue
        i += 1
    found.reads = [_literal(m.group(1)) for m in _GETLINE_INPUT_RE.finditer(program) if m.group(1)]
    return found
