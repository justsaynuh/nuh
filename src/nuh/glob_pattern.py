"""A glob word read as path components, each a regex for every name bash or zsh can match there, and maybe more.

The word arrives as its spelling (``word_quoting``): a quoted character is
encoded and matches only itself, so only unquoted characters are syntax.
Each component is one of:

- a literal name (no syntax in it), ``.`` and ``..`` included;
- ``**`` or ``***``: any number of directories (bash globstar, zsh
  recursive globbing), ``Part.any_depth``. A group spanning a ``/`` (zsh
  ``(dir/)#``) or never closing makes the component it starts in and every
  one after it any depth;
- a pattern: the union of how bash and zsh read it. bash: ``* ?``, bracket
  expressions and the extglob forms ``?( ) *( ) +( ) @( ) !( )``, every
  other character literal (a bare parenthesis would be a syntax error, so
  the literal reading only adds names). zsh: ``* ?``, bracket expressions,
  ``( | )`` grouping (a group ending the word may be glob qualifiers, so it
  is optional), and the extendedglob forms ``x#``, ``x##``, ``^x``, ``x~y``
  and ``<a-b>``; a stray ``)`` or ``|`` is a letter. A bracket expression,
  POSIX classes (``[[:alpha:]]``) included, matches any one character;
  case is ignored (nocaseglob, ``(#i)``). Matching ``.`` or ``..`` takes a
  component that could spell them out (it starts with ``.``, ``[`` or a
  group). Any other name starting with ``.`` takes a component starting
  with ``.`` or a group (``dot_names``), unless dotglob is on
  (``glob_options``): a leading ``*``, ``?`` or bracket expression never
  matches one by default, in bash or zsh.

zsh glob flags (``(#i)``, ``(#a1)``, ``(#q…)``) are not read: ``matcher``
gives None and the caller treats the word as unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nuh import word_quoting

_SYNTAX = frozenset("*?[()|^#<~")  # unquoted characters that make a component a pattern
_EXTGLOB = frozenset("?*+@!")  # bash extglob operators, each before a ( group
_RANGE_RE = re.compile(r"<\d*-\d*>")
_DOTS = (".", "..")


@dataclass(frozen=True)
class Part:
    """One path component of a glob word: a literal name, any depth, or a pattern."""

    literal: str | None = None       # the decoded name, for a literal component
    regex: re.Pattern[str] | None = None
    dots: bool = False                # the pattern may match ``.`` and ``..``
    dot_names: bool = False           # it may match other names starting with ``.`` with dotglob off
    raw: str = ""                     # the component as written (decoded), for display and representatives

    @property
    def any_depth(self) -> bool:
        return self.literal is None and self.regex is None


def has_syntax(spelling: str) -> bool:
    """True when *spelling* holds an unquoted glob or zsh pattern character."""
    return any(char in _SYNTAX for char in spelling)


def parts(spelling: str) -> list[Part] | None:
    """The components of the shell word *spelling* (``/``-separated, empty and ``.`` names dropped); None if unreadable.

    A group ending the word may be zsh glob qualifiers.
    """
    components, spans = _split(spelling)
    out: list[Part] = []
    for i, component in enumerate(components):
        found = matcher(component, qualifiers=i == len(components) - 1 and not spans)
        if found is None:
            return None
        out.append(found)
    if spans:
        out.append(Part(dot_names=True, raw="**"))  # what the group holds may spell a dot
    return out


def _split(spelling: str) -> tuple[list[str], bool]:
    """``/``-separated components; True when a group spans a ``/`` or never closes.

    The component the group starts in and the rest are left off: the caller
    adds an any-depth part for them. An unclosed group is one the parser cut short
    (``.ss(h|x)/i*`` arrives as ``.ss(h``, its ``|`` read as a pipe), so it
    may hold anything, a ``/`` included.
    """
    components: list[str] = []
    depth, start = 0, 0
    for i, char in enumerate(spelling):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "/":
            if depth:
                return _kept(components), True
            components.append(spelling[start:i])
            start = i + 1
    if depth:
        return _kept(components), True
    components.append(spelling[start:])
    return _kept(components), False


def _kept(components: list[str]) -> list[str]:
    return [c for c in components if c and c != "."]


def matcher(component: str, *, qualifiers: bool = False) -> Part | None:
    """The Part for one component's spelling; None when nuh cannot read it (see the module docstring)."""
    raw = word_quoting.decode(component)
    if component in ("**", "***"):
        return Part(raw=raw)
    if not has_syntax(component):
        return Part(literal=raw, raw=raw)
    bash = _Reader(component, zsh=False).read()
    zsh = _Reader(component, zsh=True, qualifiers=qualifiers).read()
    if bash is None or zsh is None:
        return None
    regex = re.compile(f"(?:{bash})|(?:{zsh})", re.IGNORECASE | re.DOTALL)
    leading_group = component[0] == "(" or (component[0] in _EXTGLOB and component[1:2] == "(")
    spells_dots = component[0] in ".[" or leading_group
    return Part(regex=regex, dots=spells_dots and any(regex.fullmatch(d) for d in _DOTS),
                dot_names=component[0] == "." or leading_group, raw=raw)


class _Reader:
    """One component read as bash (*zsh* False) or zsh does: a regex string, or None when nuh cannot read it."""

    def __init__(self, text: str, *, zsh: bool, qualifiers: bool = False) -> None:
        self.text, self.zsh, self.qualifiers = text, zsh, qualifiers
        self.i = 0

    def read(self) -> str | None:
        try:
            return self._sequence(stop="")
        except _Unread:
            return None

    def _peek(self, offset: int = 0) -> str:
        return self.text[self.i + offset:self.i + offset + 1]

    def _sequence(self, stop: str) -> str:
        """Atoms up to an unquoted character in *stop* (left unread) or the end."""
        atoms: list[str] = []
        while self.i < len(self.text) and not (stop and self._peek() in stop):
            atom = self._atom(atoms)
            if atom is not None:
                atoms.append(atom)
        return "".join(atoms)

    def _atom(self, atoms: list[str]) -> str | None:
        """The regex for the construct at the cursor (advancing past it); None when it adds no atom of its own."""
        char, nxt = self._peek(), self._peek(1)
        if not self.zsh and char in _EXTGLOB and nxt == "(":
            self.i += 1
            group = self._group()
            return {"?": f"(?:{group})?", "*": f"(?:{group})*", "+": f"(?:{group})+",
                    "@": f"(?:{group})", "!": ".*"}[char]
        if char in "*?":
            self.i += 1
            return ".*" if char == "*" else "."
        if char == "[":
            return self._bracket()
        if char == "(" and self.zsh:
            return self._zsh_group()
        if self.zsh:
            found = self._zsh_operator(char, atoms)
            if found is not False:
                return found
        self.i += 1
        return re.escape(word_quoting.decode(char))

    def _zsh_group(self) -> str:
        if self._peek(1) == "#":
            raise _Unread  # glob flags: (#i), (#a1), (#q…)
        group = f"(?:{self._group()})"
        return f"(?:{group})?" if self.qualifiers and self.i == len(self.text) else group

    def _group(self) -> str:
        """A ``( … | … )`` group at the cursor: its alternatives as one regex alternation."""
        self.i += 1
        alternatives = [self._sequence(stop="|)")]
        while self._peek() == "|":
            self.i += 1
            alternatives.append(self._sequence(stop="|)"))
        if self._peek() != ")":
            raise _Unread  # unbalanced
        self.i += 1
        return "|".join(alternatives)

    def _zsh_operator(self, char: str, atoms: list[str]) -> str | bool | None:
        """zsh extendedglob ``# ## ^ ~ <a-b>`` at the cursor; False when *char* is none of them here."""
        if char == "#" and atoms:
            self.i += 1
            repeat = "*"
            if self._peek() == "#":
                self.i += 1
                repeat = "+"
            atoms[-1] = f"(?:{atoms[-1]}){repeat}"
            return None
        if char == "^" or (char == "~" and atoms):
            # ^x: anything but x, from here on; x~y: x except y. Either way the rest
            # of this alternative only narrows what is matched: read past it.
            self._skip_alternative()
            return ".*" if char == "^" else None
        if char == "<":
            found = _RANGE_RE.match(self.text, self.i)
            if found:
                self.i = found.end()
                return "[0-9]+"
        return False

    def _skip_alternative(self) -> None:
        depth = 0
        while self.i < len(self.text):
            char = self._peek()
            if depth == 0 and char in "|)":
                return
            depth += {"(": 1, ")": -1}.get(char, 0)
            self.i += 1

    def _bracket(self) -> str:
        """A bracket expression at the cursor as any one character; an unclosed ``[`` as itself."""
        end = _bracket_end(self.text, self.i)
        if end < 0:
            self.i += 1
            return re.escape("[")
        self.i = end
        return "."


def _bracket_end(text: str, i: int) -> int:
    """Index just past the ``]`` closing the bracket expression at *i*; -1 when it is unclosed.

    A ``]`` first (after ``!`` or ``^``) is a member; ``[:alpha:]``,
    ``[.x.]`` and ``[=x=]`` inside are read whole.
    """
    j = i + 1
    if text[j:j + 1] in ("!", "^"):
        j += 1
    if text[j:j + 1] == "]":
        j += 1
    while j < len(text):
        if text[j] == "[" and text[j + 1:j + 2] in (":", ".", "="):
            close = text.find(text[j + 1] + "]", j + 2)
            if close < 0:
                return -1
            j = close + 2
            continue
        if text[j] == "]":
            return j + 1
        j += 1
    return -1


class _Unread(Exception):
    """A construct nuh does not read."""
