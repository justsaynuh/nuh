"""Arithmetic expansion ``$((…))`` when every operand is a known integer.

``s=$((n-40))`` inside ``for n in 1018 1034`` sets ``s`` to 978, then 994.
Only the subset that cannot surprise: decimal integers (no leading zero,
which bash reads as octal), variable names (bare or ``$name``) whose value
is such an integer, ``+ - * / %`` (unary ``+``/``-`` too) and parentheses.
Division and remainder truncate toward zero as in C, and by zero fails.
Anything else (assignment operators, comparisons, bases, ``${…}``) gives
None: the expansion stays unknown.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_TOKEN_RE = re.compile(r"\s*(?:(\d+)|\$?([A-Za-z_][A-Za-z0-9_]*)|([-+*/%()]))")
_INTEGER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")
_NAME_RE = re.compile(r"(?<![\w$])\$?([A-Za-z_][A-Za-z0-9_]*)")

Lookup = Callable[[str], "str | None"]  # a variable's value, None when unknown


def spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each ``$((…))`` in *text*; an unclosed one runs to the end."""
    found: list[tuple[int, int]] = []
    i = text.find("$((")
    while i >= 0:
        depth, j = 0, i + 1
        while j < len(text):
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
            if depth == 0:
                break
        found.append((i, j))
        i = text.find("$((", j)
    return found


def names(text: str) -> set[str]:
    """The variable names the ``$((…))`` expansions in *text* read."""
    return {m.group(1) for start, end in spans(text) for m in _NAME_RE.finditer(text[start + 3:end - 2])}


def expand(text: str, lookup: Lookup) -> str | None:
    """*text* with each ``$((…))`` replaced by its value; None when one cannot be computed."""
    out: list[str] = []
    pos = 0
    for start, end in spans(text):
        if not text.endswith("))", start, end):
            return None
        value = evaluate(text[start + 3:end - 2], lookup)
        if value is None:
            return None
        out += [text[pos:start], str(value)]
        pos = end
    return "".join(out) + text[pos:]


def evaluate(expr: str, lookup: Lookup) -> int | None:
    """The integer *expr* computes, or None (see the module docstring)."""
    tokens: list[str | int] = []
    pos = 0
    while pos < len(expr):
        if expr[pos:].strip() == "":
            break
        match = _TOKEN_RE.match(expr, pos)
        if match is None:
            return None
        number, name, operator = match.groups()
        if number is not None:
            if len(number) > 1 and number.startswith("0"):
                return None  # octal (or invalid) in bash
            tokens.append(int(number))
        elif name is not None:
            value = lookup(name)
            if value is None or not _INTEGER_RE.fullmatch(value.strip()):
                return None
            tokens.append(int(value))
        else:
            tokens.append(operator)
        pos = match.end()
    parser = _Parser(tokens)
    result = parser.sum()
    return result if parser.done() else None


class _Parser:
    """Recursive descent over integer and operator tokens; None anywhere poisons the result."""

    def __init__(self, tokens: list[str | int]) -> None:
        self.tokens = tokens
        self.i = 0

    def done(self) -> bool:
        return self.i == len(self.tokens)

    def _peek(self) -> str | int | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def sum(self) -> int | None:
        left = self.product()
        while left is not None and self._peek() in ("+", "-"):
            op = self.tokens[self.i]
            self.i += 1
            right = self.product()
            left = None if right is None else (left + right if op == "+" else left - right)
        return left

    def product(self) -> int | None:
        left = self.unary()
        while left is not None and self._peek() in ("*", "/", "%"):
            op = self.tokens[self.i]
            self.i += 1
            right = self.unary()
            if right is None or (op != "*" and right == 0):
                return None
            if op == "*":
                left = left * right
            else:
                quotient = abs(left) // abs(right) * (1 if (left < 0) == (right < 0) else -1)
                left = quotient if op == "/" else left - right * quotient
        return left

    def unary(self) -> int | None:
        token = self._peek()
        if token in ("+", "-"):
            self.i += 1
            value = self.unary()
            return None if value is None else (value if token == "+" else -value)
        if token == "(":
            self.i += 1
            value = self.sum()
            if self._peek() != ")":
                return None
            self.i += 1
            return value
        if isinstance(token, int):
            self.i += 1
            return token
        return None
