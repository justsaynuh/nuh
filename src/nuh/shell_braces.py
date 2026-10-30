"""Brace expansion, as bash and zsh do it before any other expansion.

``cat {,~/.aws/credentials}`` runs ``cat ~/.aws/credentials``: the shell
expands braces first, then tilde, variables and globs on each piece. nuh
expands each command word and redirect target the same way while it parses
a command (``bash_decompose``), so every check sees the words the shell
runs, not the brace syntax.

Expanded, left to right as bash does: ``{a,b}`` (nested, empty items, a
preamble and postscript), numeric sequences ``{1..9}``, ``{01..10}``,
``{1..9..2}`` and same-case letter sequences ``{a..e}``. A ``{x}`` with no
comma and no ``..`` is literal in both shells (``find -exec rm {} +``,
``HEAD@{1}``), and so is ``${…}``, a parameter expansion.

Not read (``pieces`` returns None, and the command asks): an unmatched
``{`` or ``}``, a sequence form beyond those above (``{a..e..2}``,
``{1..a}``, ``{+1..3}``, a step of 0: bash and zsh disagree or nuh does not
model it), a brace group inside ``${…}`` (``${x:-{a,b}}``) or right after a
``$name`` with a piece that would extend the name (``$X{1,2}``), where bash
and zsh expand differently, and more than _MAX_PIECES pieces.

Words arrive with their quoting encoded (``word_quoting``): a quoted ``{``,
``}`` or ``,`` is a letter, never brace syntax, so ``"{a,b}"`` and
``\\{a,b\\}`` stay one literal word, as in the shell.
"""

from __future__ import annotations

import re

from nuh import word_quoting

_MAX_PIECES = 256
_NUMBERS_RE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?")
_LETTERS_RE = re.compile(r"[a-z]\.\.[a-z]|[A-Z]\.\.[A-Z]")
_PADDED_RE = re.compile(r"-?0\d")
# A $name just before a group whose piece starts with a name character: bash expands the braces
# first, so the name grows ($X{1,2} is $X1 $X2); zsh expands the parameter first (${X}1 ${X}2).
_NAME_END_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*$")
_NAME_CHAR_RE = re.compile(r"[A-Za-z0-9_]")

Mark = tuple[int, str]  # (index, "{" "}" or ",") of brace syntax in a word


def pieces(word: str) -> list[str] | None:
    """The words the shell word *word* brace-expands to, empty ones dropped; None when nuh cannot tell.

    The quoting recorded for *word* (``word_quoting.encoding``) decides
    which braces and commas are syntax; each piece's own quoting is
    recorded, so later readings (globs, variables, project lifts) see the
    same quoted characters. A word nuh has no quoting for reads every
    brace and comma as syntax.
    """
    spelling = word_quoting.encoding(word)
    if spelling is None:
        spelling = word
    if "{" not in spelling and "}" not in spelling:
        return [word]
    expanded = expand(spelling)
    if expanded is None:
        return None
    out: list[str] = []
    for piece in expanded:
        text = word_quoting.decode(piece)
        if text:
            out.append(word_quoting.spelled(text, piece))
    return out


def expand(spelling: str) -> list[str] | None:
    """*spelling* (quoted characters encoded) brace-expanded in the shell's order; None for a form nuh cannot read."""
    marks = _marks(spelling)
    if marks is None or not _balanced(marks):
        return None
    return _expand(spelling)


def _expand(word: str) -> list[str] | None:
    """*word* expanded at its first brace group, the items and the rest of the word recursively."""
    marks = _marks(word)
    if marks is None:
        return None
    for n, (start, char) in enumerate(marks):
        group = _group(marks, n) if char == "{" else None
        if group is None:
            continue
        end, commas = group
        if commas:
            bounds = [start, *commas, end]
            items = [word[a + 1:b] for a, b in zip(bounds, bounds[1:])]
        else:
            items = _sequence(word[start + 1:end])
            if items is None:
                return None
            if not items:
                continue  # {x}: literal, a group inside it may still expand ({a{b,c}} is {ab} {ac})
        named = _NAME_END_RE.search(word[:start]) is not None
        tails = _expand(word[end + 1:])
        if tails is None:
            return None
        out: list[str] = []
        for item in items:
            heads = _expand(item) if commas else [item]
            if heads is None or named and any(_NAME_CHAR_RE.match(head) for head in heads):
                return None
            out += [word[:start] + head + tail for head in heads for tail in tails]
            if len(out) > _MAX_PIECES:
                return None
        return out
    return [word]


def _marks(word: str) -> list[Mark] | None:
    """The unquoted ``{ } ,`` of *word* outside ``${…}``; None when a ``${…}`` is unclosed or holds a ``{``."""
    marks: list[Mark] = []
    i = 0
    while i < len(word):
        if word.startswith("${", i):
            end = _parameter_end(word, i + 2)
            if end is None:
                return None
            i = end
            continue
        if word[i] in "{},":
            marks.append((i, word[i]))
        i += 1
    return marks


def _parameter_end(word: str, i: int) -> int | None:
    """Index just past the ``}`` closing the ``${`` before *i*; None when unclosed or a brace group sits inside."""
    depth = 1
    while i < len(word):
        if word.startswith("${", i):
            depth += 1
            i += 2
            continue
        if word[i] == "{":
            return None  # ${x:-{a,b}}: zsh expands the group, bash does not
        if word[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _balanced(marks: list[Mark]) -> bool:
    """Every ``{`` has its ``}``; an unmatched one is literal to bash but a parse error to zsh in places."""
    depth = 0
    for _index, char in marks:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _group(marks: list[Mark], n: int) -> tuple[int, list[int]] | None:
    """``(index of the closing }, indices of its top-level commas)`` for the ``{`` at ``marks[n]``; None if unclosed."""
    depth = 0
    commas: list[int] = []
    for index, char in marks[n:]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index, commas
        elif depth == 1:
            commas.append(index)
    return None


def _sequence(body: str) -> list[str] | None:
    """The items of a ``{body}`` with no top-level comma: a sequence, [] if literal, None for a form nuh cannot read."""
    numbers = _NUMBERS_RE.fullmatch(body)
    if numbers is not None:
        return _numbers(*numbers.groups())
    if _LETTERS_RE.fullmatch(body):
        first, last = ord(body[0]), ord(body[-1])
        step = 1 if first <= last else -1
        return [chr(code) for code in range(first, last + step, step)]
    return None if ".." in body else []


def _numbers(first: str, last: str, increment: str | None) -> list[str] | None:
    """``{first..last..increment}`` as bash expands it; None for a step of 0 (zsh differs) or too many items."""
    lo, hi = int(first), int(last)
    step = abs(int(increment)) if increment is not None else 1
    if step == 0 or abs(hi - lo) // step >= _MAX_PIECES:
        return None
    width = max(len(first), len(last)) if _PADDED_RE.match(first) or _PADDED_RE.match(last) else 0
    values = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
    return [f"{value:0{width}d}" for value in values]
