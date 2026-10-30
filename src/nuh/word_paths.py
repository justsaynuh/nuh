"""Every path a Bash word can name once the shell expands it, when nuh can list them.

``project_lifts_sensitive`` exempts a path only when it provably stays inside
the project. The words reach nuh after ``shlex``, so quotes and backslashes
are gone and the operators split off: a quote, ``!``, ``;``, ``&``, ``=``
inside a word is a literal character. What can still make a word name
something other than its spelling:

- parameter and command expansion: ``$``, backticks and the placeholder nuh
  puts where a substitution was. A variable nuh resolves arrives here
  already expanded (``shell_expand``); one it cannot resolve is refused;
- brace expansion, ``{a,../../x}`` and zsh's ``{ab}`` (brace_ccl): ``{``,
  ``}``. Command words arrive already brace-expanded (``shell_braces``); a
  word from elsewhere whose quoting nuh recorded is expanded here the same
  way and each piece read on. A ``{`` or ``}`` left in a piece is refused;
- ``~user``, ``~+``, ``~-`` and the ``~`` bash expands after ``=`` in an
  argument: any ``~`` but a leading ``~/``; zsh ``=cmd`` at the start;
- zsh glob syntax beyond ``* ? [ ]`` (extendedglob ``^ # ~``, ``<a-b>``
  ranges, ``( | )`` alternation, ksh and bash extglob ``@( )``): ``^ # < > | ( )``;
- ``* ? [ ]``: a glob names the existing paths it matches, and a match can
  be a symlink out of the project (``cat *.txt`` reading ``link.txt``), or
  ``..`` itself (bash before 5.2 matches ``.*`` against ``..``). These are
  listed (``glob_superset``), never assumed to stay under their literal parent.

Only unquoted characters are syntax. The Bash parser records the quoting
that shlex drops (``word_quoting.encoding``), so in ``"Sub Dir (x)"``,
``'a[1]'`` or ``a\\*`` every such character is a letter and the word names
its spelling, while ``"Sub Dir (x)/"*.pdf`` globs only its ``*``. The
spelling survives the variables nuh expands (``"$S/Sub Dir (x)"`` with
``S=A``) when they were quoted or their values hold nothing the shell
splits or globs. A word nuh has no spelling for reads every such character
as syntax. An active ``$`` left in the word, an active backtick or a
substitution placeholder refuses it; single-quoted ones are literal.

A string literal in inline program text (``open('a</b>.txt')``) is no shell
word: the shell never expands it, so only what the program itself may expand
counts (``$`` and ``{…}`` interpolation, ``~`` and globs, as above). ``<``,
``>``, ``|``, ``( )``, ``^`` and ``#`` are plain characters there; a Perl open
mode in front of a name (``">../x"``) is the caller's concern.
"""

from __future__ import annotations

import os
import re

from nuh import shell_braces, word_quoting

SUBSTITUTION_MARK = "__nuh_psub_"  # bash_subs._PSUB_PREFIX: where a $(…) or <(…) was
_EXPANDING = frozenset("$`{}~^#<>|()")
_CODE_EXPANDING = frozenset("$`{}~")  # in a code string literal (see the module docstring)
_GLOB = frozenset("*?[]")
_MAX_MATCHES = 256


def spellings(path: str) -> list[str]:
    """*path* absolute as written and resolved (symlinks followed), without duplicates."""
    written = os.path.abspath(path)
    resolved = os.path.realpath(path)
    return [written] if written == resolved else [written, resolved]


def named_paths(word: str, *, code: bool = False) -> list[str] | None:
    """Every path the shell word *word* can name, each as written and resolved; None when nuh cannot list them.

    A glob that matches nothing names itself: bash passes it on as written
    (zsh runs nothing at all). *code*: *word* is a string literal of inline
    program text, not a shell word.
    """
    if SUBSTITUTION_MARK in word:
        return None
    if code:
        return _pattern_paths(word, _CODE_EXPANDING)
    spelling = word_quoting.encoding(word)
    if any(char in "$`" for char in word_quoting.expansion(word)) or spelling is None and word.startswith("="):
        return None
    pieces = [word] if spelling is None else shell_braces.expand(spelling)
    if pieces is None:
        return None
    named: list[str] = []
    for piece in pieces:
        found = None if piece.startswith("=") else _pattern_paths(piece, _EXPANDING)
        if found is None:
            return None
        named += found
    return list(dict.fromkeys(named))


def _pattern_paths(pattern: str, expanding: frozenset[str]) -> list[str] | None:
    """Every path *pattern* names (quoted characters encoded, ``word_quoting``); None if it holds one of *expanding*.

    A leading ``~/`` is expanded. A quoted leading ``~`` names a literal directory.
    """
    body = pattern[2:] if pattern.startswith("~/") else pattern
    if any(char in expanding for char in body):
        return None
    path = os.path.expanduser(pattern)
    text = word_quoting.decode(path)
    if not any(char in _GLOB for char in body):
        return spellings(text)
    matches = glob_superset(path)
    if matches is None:
        return None
    return [s for match in (matches or [text]) for s in spellings(match)]


def _bracket_end(part: str, i: int) -> int | None:
    """Index of the ``]`` closing the bracket expression at *i*; -1 when unclosed; None when unsure.

    A ``]`` first (after ``!`` or ``^``) is a member. A ``[`` inside
    (``[[:alpha:]]``, ``[[.x.]]``) is a class nuh does not read.
    """
    start = i + 1
    if part[start:start + 1] in ("!", "^"):
        start += 1
    if part[start:start + 1] == "]":
        start += 1
    close = part.find("]", start)
    if "[" in part[i + 1:close if close >= 0 else len(part)]:
        return None
    return close


def _component_regex(part: str) -> re.Pattern[str] | None:
    """A regex matching every name the glob component *part* can match in bash or zsh, and maybe more; None if unsure.

    ``*`` and ``?`` as the shell reads them; a bracket expression (or an
    unclosed ``[``) as any one character; case ignored (nocaseglob).
    """
    out: list[str] = []
    i = 0
    while i < len(part):
        char = part[i]
        if char == "*":
            out.append(".*")
        elif char == "?":
            out.append(".")
        elif char == "[":
            close = _bracket_end(part, i)
            if close is None:
                return None
            out.append(".")
            i = close if close >= 0 else i
        else:
            out.append(re.escape(word_quoting.decode(char)))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def glob_superset(pattern: str) -> list[str] | None:
    """Every existing path the glob *pattern* can match, and maybe more; None when nuh will not list them.

    Hidden names count (dotglob, globdots), and ``.`` and ``..`` count for a
    component starting with ``.`` or ``[``. Refused: ``**`` (recursive
    globbing), a backslash (an escape nuh cannot see), a bracket class
    (``[[:alpha:]]``) and more than _MAX_MATCHES matches. A character
    ``word_quoting`` encoded (it was quoted) matches only itself.
    """
    if "**" in pattern or "\\" in pattern:
        return None
    current = ["/" if pattern.startswith("/") else ""]
    for part in pattern.split("/"):
        if not part:
            continue
        if not any(char in _GLOB for char in part):
            current = [os.path.join(prefix, word_quoting.decode(part)) for prefix in current]
            continue
        regex = _component_regex(part)
        if regex is None:
            return None
        matched: list[str] = []
        for prefix in current:
            try:
                names = os.listdir(prefix or ".")
            except OSError:
                # Not a directory or not readable: the shell's glob matches
                # nothing there either, so no path comes from this prefix.
                continue
            if part[0] in ".[":
                names += [".", ".."]
            matched += [os.path.join(prefix, name) for name in names if regex.fullmatch(name)]
            if len(matched) > _MAX_MATCHES:
                return None
        current = matched
    return [path for path in current if os.path.lexists(path)]
