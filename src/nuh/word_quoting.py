"""How each Bash word was quoted, so its glob and expansion characters read as the shell reads them.

shlex drops the quotes, so ``"Sub Dir (x)"`` and an unquoted ``Sub Dir (x)``
(zsh glob grouping) become the same word. While a command is classified
(``scope``), every split of shell words is recorded here (``record``) as an
encoded spelling (``encoding``): each ``( ) [ ] * ? { } ~ ^ # < > | = $ ,``
inside quotes or after a backslash becomes a private-use character of its
own, so the ones left as they were are shell syntax. ``word_paths`` reads
that spelling: ``"Shared Docs/"*.pdf`` globs only the ``*``, and
``"R-"{a,b}"_P.pdf"`` expands only the unquoted braces.

When nuh expands a variable (``shell_expand``), the result carries the
word's spelling. A quoted value is neither split nor globbed, so it is
encoded whole: ``"$S/x (y)"`` with ``S=A`` names ``A/x (y)`` and nothing
else, whatever S holds. Unquoted values retain active glob characters,
while dollars supplied by any value are literal; ``unquoted_names`` tells
the split readings which uses those are.

Each parsed word carries its spelling, including slices and expansions.
Single-quoted or escaped dollars/backticks have a separate encoding from
double-quoted dollars: only the latter expand. Derived plain strings also
have a text-keyed fallback; conflicting spellings there keep only their
shared quoting. A text nuh cannot spell has none: every special character
in it is syntax.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from nuh import shell_quote_marks

_SPECIAL = frozenset("()[]*?{}~^#<>|=")
_ENCODED = _SPECIAL | frozenset("$`,")  # a quoted , separates no brace items
_VALUE_LITERAL = frozenset("$`~<>")  # never syntax when a variable's value supplies them (value_spelling)
_SHIFT = 0xF0000  # quoted character c is chr(_SHIFT + ord(c)), in Supplementary Private Use Area-A
_LITERAL_SHIFT = _SHIFT + 128  # literal $/`: single quotes, escapes, or an expansion's value
_MARK = ""  # what mark_quoted puts for a quoted character while recording
_LITERAL_MARK = ""
_NAME_RE = re.compile(r"\{?([A-Za-z_][A-Za-z0-9_]*)")  # the variable used by the $ just before the match
_REF_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")  # a use with no operator
_scopes: list[dict[str, str | None]] = []  # word text -> its spelling (None: none), innermost scope last
_active_quoted = False  # main's conservative reading, only during written-script inspection


@contextmanager
def active_quoted() -> Iterator[None]:
    """Also inspect written code with quoted dollars, tildes and backticks active, as on main."""
    global _active_quoted
    previous = _active_quoted
    _active_quoted = True
    try:
        yield
    finally:
        _active_quoted = previous


def quoted_active() -> bool:
    """Whether the additional conservative written-body reading is in progress."""
    return _active_quoted


class _Word(str):
    """One token's spelling: identical text elsewhere must not change whether its dollars expand."""

    spelling: str | None

    def __new__(cls, text: str, spelling: str | None = None) -> _Word:
        word = super().__new__(cls, text)
        word.spelling = spelling
        return word

    def __getitem__(self, key: int | slice) -> str:
        text = super().__getitem__(key)
        if isinstance(key, slice):
            return _Word(text, self.spelling[key] if self.spelling is not None else None)
        return text


@contextmanager
def scope() -> Iterator[None]:
    """Record the words split while one command is classified; a nested command gets its own record."""
    _scopes.append({})
    try:
        yield
    finally:
        _scopes.pop()


def encode(text: str) -> str:
    """*text* with each special character, ``$`` and ``,`` as its quoted counterpart."""
    return "".join(chr((_LITERAL_SHIFT if char in "$`" else _SHIFT) + ord(char))
                   if char in _ENCODED else char for char in text)


def _encoded(char: str) -> bool:
    return _SHIFT <= ord(char) < _LITERAL_SHIFT + 128


def decode(text: str) -> str:
    """*text* with each quoted counterpart back as the character it stands for."""
    return "".join(chr((ord(char) - _SHIFT) % 128) if _encoded(char) else char for char in text)


def _kept(word: str) -> bool:
    """A word with a character whose quoting matters."""
    return any(char in _ENCODED for char in word)


def _store(text: str, spelling: str | None) -> None:
    """Record *spelling* for *text*: a character stays encoded only where every spelling of *text* had it so."""
    record = _scopes[-1]
    if text in record:
        old = record[text]
        spelling = None if old is None or spelling is None else "".join(
            a if a == b else plain for a, b, plain in zip(old, spelling, text))
    record[text] = spelling


def record(text: str, words: list[str]) -> None:
    """Record the spelling of each of *words* (the shlex words of *text*).

    Quoted ``>`` arrive as ``shell_quote_marks.QUOTED_GT`` in both *text* and *words*
    and count as quoted. Does nothing outside a ``scope``.
    """
    if not _scopes:
        return
    restored = [word.replace(shell_quote_marks.QUOTED_GT, ">") for word in words]
    if not any(_kept(word) for word in restored):
        return
    try:
        marked = shell_quote_marks.split(shell_quote_marks.mark_quoted(text, _ENCODED, _MARK), source=text)
        literal = shell_quote_marks.split(
            shell_quote_marks.mark_quoted(text, frozenset("$`"), _LITERAL_MARK, literal_only=True), source=text)
    except ValueError:
        marked = []
    usable = (len(marked) == len(literal) == len(restored)
              and all(len(w) == len(m) == len(lit) for w, m, lit in zip(restored, marked, literal)))
    for i, word in enumerate(restored):
        if not _kept(word):
            continue
        if not usable or any(_encoded(char) for char in word):
            # The marked text should split the same way, and a word cannot already hold encoded
            # characters; when either fails, none of its characters is taken for quoted.
            _store(word, None)
            words[i] = _Word(words[i], None)
            continue
        quoted = (_MARK, shell_quote_marks.QUOTED_GT)
        spelling = "".join(
            chr(_LITERAL_SHIFT + ord(char)) if lit == _LITERAL_MARK and char in "$`" else
            chr(_SHIFT + ord(char)) if mark in quoted and char in _ENCODED else char
            for char, mark, lit in zip(word, marked[i], literal[i]))
        _store(word, spelling)
        words[i] = _Word(words[i], spelling)


def encoding(word: str) -> str | None:
    """*word* with the characters quoted in this command encoded; None when nuh cannot tell which were."""
    if isinstance(word, _Word):
        return word.spelling
    if _scopes and word in _scopes[-1]:
        return _scopes[-1][word]
    return None if _kept(word) or any(_encoded(char) for char in word) else word


def joined(words: list[str], separator: str = " ") -> str:
    """Join words while retaining each operand's outer expansion mask (eval and heredocs)."""
    text = separator.join(words)
    spellings = [encoding(word) for word in words]
    spelling = None if any(s is None for s in spellings) else separator.join(spellings)
    if _scopes:
        _store(text, spelling)
    return _Word(text, spelling)


def expansion(word: str) -> str:
    """*word* with literal dollars/backticks and quoted tildes masked; active double-quoted dollars stay active.

    Same length as *word*, so regex match offsets still address its spelling.
    Decode only after expansion; replacement values must never be expanded again.
    """
    spelling = encoding(word)
    if _active_quoted or spelling is None:
        return word
    return "".join(mark if (_LITERAL_SHIFT <= ord(mark) < _LITERAL_SHIFT + 128)
                   or (char == "~" and mark != char) else char for char, mark in zip(word, spelling))


def unsplit(word: str) -> str:
    """A here-string operand expands without field splitting or globbing.

    Preserve literal dollars from explicit quoting, while active dollars
    behave as double-quoted ones even when the operand was unquoted.
    """
    spelling = encoding(word) or word
    return spelled(word, "".join(
        mark if _LITERAL_SHIFT <= ord(mark) < _LITERAL_SHIFT + 128 else
        chr(_SHIFT + ord(char)) if char in _ENCODED else char
        for char, mark in zip(word, spelling)))


def expand_path(word: str) -> str:
    """Expand active environment variables and a leading tilde, respecting the recorded shell quoting."""
    return decode(os.path.expanduser(os.path.expandvars(expansion(word))))


def part(word: str, start: int) -> str:
    """An assignment's value, with the quoting of its original NAME=value word."""
    text, spelling = word[start:], encoding(word)
    return spelled(text, spelling[start:]) if spelling is not None else text


def unquoted_names(word: str) -> frozenset[str] | None:
    """The variables *word* uses outside quotes (split and globbed); None when nuh cannot tell."""
    spelling = encoding(word)
    if spelling is None:
        return None
    names: set[str] = set()
    for i, char in enumerate(spelling):
        found = _NAME_RE.match(word, i + 1) if char == "$" else None
        if found:
            names.add(found.group(1))
    return frozenset(names)


def value_spelling(word: str, start: int, value: str) -> str:
    """A substituted value: dollars never expand again; unquoted glob characters stay active.

    A value's ``<`` or ``>`` is never redirection syntax: the shells parse
    redirects before they expand words (``op='<'; printf %s $op f`` prints).
    """
    spelling = encoding(word)
    if spelling is not None and spelling[start] != "$":
        return encode(value)
    return "".join(encode(char) if char in _VALUE_LITERAL else char for char in value)


def substitute(word: str, pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str | None]) -> str:
    """Replace original reference spans with spellings; None retains a reference and its name boundary."""
    active, spelling = expansion(word), encoding(word) or word
    replacements = [(match, replace(match)) for match in pattern.finditer(active)]
    if not any(value is not None for _match, value in replacements):
        return word
    parts: list[str] = []
    last = 0
    for match, value in replacements:
        if value is None:
            value = spelling[match.start():match.end()]
            # $d followed by a value beginning "etc" remains ${d}etc, never $detc.
            if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", match.group(0)):
                value = value[0] + encode("{") + value[1:] + encode("}")
        parts += [spelling[last:match.start()], value]
        last = match.end()
    result = "".join(parts) + spelling[last:]
    # A value split/globbed outside quotes does not establish an exact path
    # spelling. Keep expansion masks (especially produced dollars), while
    # leaving path syntax conservative as it was before variable resolution.
    if any(value is not None and spelling[match.start()] == "$"
           and any(char in _SPECIAL or char == "\\" or char.isspace() for char in value)
           for match, value in replacements):
        result = "".join(decode(char) if decode(char) in _SPECIAL - _VALUE_LITERAL else char for char in result)
    return spelled(decode(result), result)


def expanded(word: str, result: str, values: Mapping[str, str]) -> str:
    """Record *result*'s spelling: *word* with its uses of the variables in *values* replaced by them.

    *result* has none when *word* has none or when *result* is not what
    replacing the uses gives (an operator nuh read off, ``$$``).
    """
    if result == word and not values:
        return word
    if not _scopes:
        return result
    expected = _REF_RE.sub(lambda m: values.get(m.group(1) or m.group(2), m.group(0)), expansion(word))
    if encoding(word) is not None and decode(expected) == result:
        return substitute(word, _REF_RE, lambda m: value_spelling(word, m.start(), values[name])
                          if (name := m.group(1) or m.group(2)) in values else None)
    _store(result, None)
    return _Word(result, None)


def spelled(text: str, spelling: str) -> str:
    """Record *spelling* for *text*, a word nuh derived from a recorded one (a brace piece, ``shell_braces``)."""
    if _scopes:
        _store(text, spelling)
        return _Word(text, spelling)
    return text


def unknown(text: str) -> None:
    """Record that nuh cannot spell *text* (arithmetic it computed): every special character in it is syntax."""
    if _scopes:
        _store(text, None)
