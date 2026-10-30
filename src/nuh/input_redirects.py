"""Input redirects among a command's shell words: the files the shell opens for reading.

``< FILE``, ``N< FILE`` and ``<> FILE`` open FILE whatever the command does
with it (``<>`` opens it for writing too), and zsh reads it with no command
at all: ``< FILE`` alone runs ``$READNULLCMD`` on it. A here-string or
heredoc (``<<<``, ``<<``, ``<<-``) names no file, nor does a descriptor
duplication (``<&N``) or a process substitution (``<(…)``, a word of its own).

``bash_decompose`` gives every input redirect the spaced spelling, the
operator word and then the target word (``wc -l <f`` reads as ``wc -l < f``,
``cmd 3<f`` as ``cmd 3< f``, ``echo ok<f`` as ``echo ok < f``), one pair per
word a brace target expands to, after the command's arguments. ``find``
then reads each redirect off the words. Only a literal, unquoted ``<`` is
syntax: a quoted one, one a variable's value supplies (``word_quoting``) and
one inside ``${…}``, ``$((…))``, ``$[…]`` or ``((…))`` are plain characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nuh import shell_lex, word_quoting

# Descriptor digits, the operator, then whatever is glued after it.
_WORD_RE = re.compile(r"(\d*)(<<<|<<-|<<|<>|<&|<)(.*)", re.DOTALL)
_FILE_OPERATORS = frozenset({"<", "<>"})  # the operators whose target is a file the shell opens
_READ_WRITE = "<>"
_NUMERIC_GLOB_RE = re.compile(r"<\d*-\d*>")  # zsh: a glob matching numbers, not a redirect
_OPENERS = ("${", "$((", "$[", "((")
_MASK = "_"  # stands in for a character inside an expansion: never syntax here


@dataclass(frozen=True)
class InputRedirect:
    """One input redirect among a command's words: how many words it takes, its descriptor, operator and file."""

    width: int
    fd: str
    operator: str
    file: str | None  # the file a `<` or `<>` opens; None for the others


def masked(tokens: list[str]) -> list[str]:
    """Each of *tokens* as ``word_quoting`` spells it, characters inside an expansion masked out.

    An expansion may span several words (``$(( 1 <2 ))``), so the words are
    read together; each mask has its word's length.
    """
    spellings = [word_quoting.encoding(tok) or tok for tok in tokens]
    text = " ".join(spellings)
    chars = list(text)
    at = 0
    while at < len(text):
        end = shell_lex.word_end(text, at) if text.startswith(_OPENERS, at) else None
        if end is None or end <= at:
            at += 1
            continue
        chars[at:end] = _MASK * (end - at)
        at = end
    masks: list[str] = []
    pos = 0
    for spelling in spellings:
        masks.append("".join(chars[pos:pos + len(spelling)]))
        pos += len(spelling) + 1
    return masks


def _slice(word: str, start: int, end: int | None = None) -> str:
    """``word[start:end]``, keeping its quoting."""
    spelling = word_quoting.encoding(word)
    piece = word[start:end]
    return word_quoting.spelled(piece, spelling[start:end]) if spelling is not None else piece


def _operator(mask: str, at: int = 0) -> re.Match[str] | None:
    """The input redirect operator starting at *at* in *mask*; None for none or a process substitution."""
    match = _WORD_RE.fullmatch(mask, at)
    if match is None or (match.group(2) == "<" and match.group(3).startswith("(")):
        return None
    return match


def operator_only(word: str, mask: str) -> bool:
    """True for a ``<`` or ``<>`` operator word whose file is the next word (``<``, ``3<``, ``<>``)."""
    match = _operator(mask)
    return match is not None and match.group(2) in _FILE_OPERATORS and match.end(2) == len(word)


def spaced(word: str, mask: str) -> list[str]:
    """*word* with each ``<`` or ``<>`` redirect in it split off: the text before, the operator word, the rest.

    ``3<f`` gives ``["3<", "f"]``, ``ok<f`` ``["ok", "<", "f"]`` and ``<a<b``
    ``["<", "a", "<", "b"]``: digits are the descriptor only when they start
    the word. A heredoc, here-string, descriptor duplication or process
    substitution ends the splitting, and so does a zsh numeric glob (``a<1-5>``).
    """
    pieces: list[str] = []
    start = 0
    while (at := mask.find("<", start)) >= 0 and not _NUMERIC_GLOB_RE.match(mask, at):
        match = _operator(mask, at)
        if match is None or match.group(2) not in _FILE_OPERATORS:
            break
        begin = start if mask[start:at].isdigit() else at
        end = match.end(2)
        if begin == start and end == len(word):
            break
        pieces += [piece for piece in (_slice(word, start, begin), _slice(word, begin, end)) if piece]
        start = end
    if start < len(word) or not pieces:
        pieces.append(_slice(word, start) if start else word)
    return pieces


def find(tokens: list[str]) -> dict[int, InputRedirect]:
    """Each input redirect among *tokens*, by the index of its operator word."""
    masks = masked(tokens)
    found: dict[int, InputRedirect] = {}
    i = 0
    while i < len(tokens):
        match = _operator(masks[i])
        if match is None:
            i += 1
            continue
        glued = match.end(2) < len(tokens[i])
        # An operator with another operator for its word (`<> 2<>f`) is a syntax
        # error in both shells; the second one still names its own file.
        width = 1 if glued or i + 1 == len(tokens) or _operator(masks[i + 1]) is not None else 2
        name = None
        if match.group(2) in _FILE_OPERATORS:
            name = _slice(tokens[i], match.end(2)) if glued else (tokens[i + 1] if width == 2 else None)
        found[i] = InputRedirect(width, match.group(1), match.group(2), name)
        i += width
    return found


def files(tokens: list[str]) -> list[str]:
    """The files the ``<`` and ``<>`` redirects among *tokens* open, in order."""
    return [redirect.file for redirect in find(tokens).values() if redirect.file]


def read_writes(tokens: list[str]) -> list[tuple[str, str]]:
    """``(descriptor, file)`` for each ``<>`` redirect among *tokens*: the descriptor defaults to 0."""
    return [(redirect.fd or "0", redirect.file) for redirect in find(tokens).values()
            if redirect.operator == _READ_WRITE and redirect.file]
