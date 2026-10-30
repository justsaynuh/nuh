"""The values a ``for`` loop gives its variable, when nuh can list them all.

``for p in A B; do cat "Shared Docs/$p/x.pdf"; done`` reads ``Shared Docs/A/x.pdf``
and ``Shared Docs/B/x.pdf``, nothing else. ``bindings`` reads loop headers
(``for NAME in WORDS``, zsh ``for NAME (WORDS)`` and ``foreach NAME (WORDS)``)
and keeps the words of the list; ``values`` expands them the way the shell
does, in the state the loop starts in (``shell_state`` expands the variable
to each value in turn):

- brace expansion as the shell does it (``shell_braces``: ``{a,b}``, ``{1..5}``);
- a leading ``~`` or ``~user``;
- variables nuh knows (``lookup``); a value with whitespace gives the whole
  value and each piece (unquoted it splits in bash, quoted or in zsh not);
- a glob gives every path it can match (``word_paths.glob_superset``: under
  nocaseglob and dotglob too); one that matches nothing stays as written,
  as bash passes it on (zsh would run nothing).

The words retain their quote masks after shlex, so ``'*.py'`` stays literal
while an unquoted ``*.py`` is globbed.

No values (nuh cannot list them) for: a substitution (``$(ls)``), a variable
nuh does not know or one with an operator (``${p%x}``), ``$@`` and other
special parameters, brace syntax ``shell_braces`` cannot read, ``~+``/``~-`` or a
``~`` inside a word, zsh glob syntax beyond ``* ? [ ]``, a glob nuh will not
list (``**``, more than 256 matches), a relative glob in a directory nuh
cannot follow, more than _MAX_VALUES values in all, an empty list, ``for p``
with no list (the positional parameters), arithmetic ``for ((…))`` and
zsh's several names (``for a b in …``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from nuh import shell_braces, word_paths, word_quoting

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOOP_WORDS = frozenset({"for", "foreach"})
# Words after which a loop header sits in command position.
_OPENERS = frozenset({"do", "then", "else", "{", "!", "if", "elif", "while", "until", "(){", "()", "time"})
_BODY_OPENERS = frozenset({"do", "{"})
_SUBSTITUTION_MARK = word_paths.SUBSTITUTION_MARK
# zsh extended glob, ksh/bash extglob, alternation, process substitution, NUL.
_UNREADABLE = frozenset("`^#<>|()\x00")
_GLOB = frozenset("*?[")
_MAX_VALUES = 256

_PARAM_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")

Binding = tuple[str, "tuple[str, ...] | None"]  # (name, the words of its list; None: no list nuh reads)
Lookup = Callable[[str], "str | None"]          # a variable's value; None: not known


def _short_list(words: list[str], start: int) -> list[str] | None:
    """The words of a zsh ``(A B)`` list opening at *start*; None when it does not close."""
    values: list[str] = []
    for word in words[start:]:
        values.append(word)
        if word.endswith(")"):
            break
    else:
        return None
    values[0] = values[0][1:]
    values[-1] = values[-1][:-1]
    return [v for v in values if v]


def _header(words: list[str], i: int) -> list[Binding]:
    """The bindings of the loop header whose keyword is at *i*."""
    names: list[str] = []
    j = i + 1
    while j < len(words) and _NAME_RE.fullmatch(words[j]) and words[j] != "in":
        names.append(words[j])
        j += 1
    if not names:
        return []  # for ((…)) or nothing nuh reads
    values: list[str] | None = None
    if j < len(words) and words[j] == "in":
        end = next((k for k in range(j + 1, len(words)) if words[k] in _BODY_OPENERS), len(words))
        values = words[j + 1:end]
    elif j < len(words) and words[j].startswith("("):
        values = _short_list(words, j)
    if len(names) == 1 and values:
        return [(names[0], tuple(values))]
    return [(name, None) for name in names]


def bindings(words: list[str]) -> list[Binding]:
    """The loop variables the headers in one command's *words* bind, with their list words, in order."""
    found: list[Binding] = []
    for i, word in enumerate(words):
        if word in _LOOP_WORDS and (i == 0 or words[i - 1] in _OPENERS):
            found += _header(words, i)
    return found


def values(words: tuple[str, ...], lookup: Lookup, cwd: str | None) -> tuple[str, ...] | None:
    """Every value a loop over *words* gives its variable, run in *cwd*; None when nuh cannot list them all."""
    out: list[str] = []
    for word in words:
        expanded = _word_values(word, lookup, cwd)
        if expanded is None:
            return None
        out += expanded
        if len(out) > _MAX_VALUES:
            return None
    return tuple(dict.fromkeys(out)) or None


def _word_values(word: str, lookup: Lookup, cwd: str | None) -> list[str] | None:
    syntax = word_quoting.encoding(word) or word
    if not word or _SUBSTITUTION_MARK in word or syntax.startswith("=") or any(c in _UNREADABLE for c in syntax):
        return None
    braced = shell_braces.pieces(word)
    if braced is None:
        return None
    out: list[str] = []
    for piece in braced:
        spelling = word_quoting.encoding(piece) or piece
        tilded = _tilde(spelling)
        tilded = word_quoting.spelled(word_quoting.decode(tilded), tilded) if tilded is not None else None
        substituted = _substitute(tilded, lookup) if tilded is not None else None
        if substituted is None:
            return None
        substituted = word_quoting.encoding(substituted) or substituted
        words = [substituted, *substituted.split()] if any(c.isspace() for c in substituted) else [substituted]
        for one in dict.fromkeys(words):
            matched = _glob(one, cwd)
            if matched is None:
                return None
            out += [word_quoting.decode(value) for value in matched]
        if len(out) > _MAX_VALUES:
            return None
    return out


def _tilde(word: str) -> str | None:
    """*word* with a leading ``~``/``~user`` expanded; None for ``~+``, ``~-`` or a ``~`` further in."""
    if "~" not in word:
        return word
    if not word.startswith("~") or "~" in word[1:] or word[1:2] in ("+", "-"):
        return None
    return os.path.expanduser(word)


def _substitute(word: str, lookup: Lookup) -> str | None:
    """*word* with ``$NAME``/``${NAME}`` replaced by their values; None when one is unknown or another ``$`` is left."""
    unknown = False

    def value(match: re.Match[str]) -> str:
        nonlocal unknown
        found = lookup(match.group(1) or match.group(2))
        if found is None:
            unknown = True
            return ""
        return word_quoting.value_spelling(word, match.start(), found)

    result = word_quoting.substitute(word, _PARAM_RE, value)
    return None if unknown or "$" in word_quoting.expansion(result) else result


def _glob(word: str, cwd: str | None) -> list[str] | None:
    """The paths the glob *word* names run in *cwd*, spelled as the shell does; *word* when no glob or no match."""
    if not any(c in _GLOB for c in word):
        return [word]
    if os.path.isabs(word):
        found = word_paths.glob_superset(word)
        return None if found is None else found or [word]
    if cwd is None or any(c in _GLOB for c in cwd):
        return None  # unknown directory, or one whose own name would read as a pattern
    prefix = cwd.rstrip("/") + "/"
    found = word_paths.glob_superset(prefix + word)
    if found is None:
        return None
    return [path[len(prefix):] for path in found] or [word]
