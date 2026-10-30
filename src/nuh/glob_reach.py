"""Where a glob word can reach, so the sensitive and protected checks see the paths the shell would hand over.

``cat ../.ss*/i*`` reads ``~/.ssh/id_rsa``, though no protected name is
spelled out in it. For a word in a path position that holds glob syntax
(``glob_pattern``) or starts with ``~+``/``~-``, ``reach`` gives the paths
to check, two ways, both conservative:

1. its matches now: the glob expanded against the filesystem from the
   working directory the command runs in (``shell_state``), ``..``, ``~``,
   ``~+`` ($PWD), ``~-`` ($OLDPWD) and ``~user`` expanded, each match as
   written and resolved. A name starting with ``.`` counts where the
   pattern spells the dot out, or everywhere when the command may turn
   dotglob or globdots on (``glob_options``) or the glob carries zsh's
   ``(D)`` qualifier; ``**`` descends (globstar). More than _MAX_MATCHES matches, or a tree too large
   to walk, lists nothing here and leaves the word to 2;
2. what it could match: each protected place (``targets``: the sensitive
   directories, ``~/.ssh``, agent wiring, nuh's code) the pattern can reach
   from its literal prefix gives a representative path: the place with the
   rest of the pattern as written below it (``~/.ssh/i*``), or, when the
   pattern ends above the place, the directory it names (a parent of it,
   for removals). A glob that matches nothing today still gets the
   verdict of every place it could match.

Unknown, and so an ask (the reason is returned): ``~-`` before any ``cd``
nuh followed, ``~+`` in a directory it could not follow, a ``..`` after a
glob component (the match may be a symlink to anywhere), a brace expansion
or a pattern form nuh does not read (``glob_pattern``). A relative word in
a directory nuh could not follow is left to the stage's own check
(``bash_stage``). A word still holding ``$`` or a substitution is the
unresolved-variable checks' concern.
"""

from __future__ import annotations

import os
import re

from nuh import glob_options, glob_pattern, shell_braces, shell_state, word_quoting
from nuh.glob_pattern import Part
from nuh.word_paths import SUBSTITUTION_MARK, spellings

_TRIGGER = frozenset("*?[(|^#<~")
_QUALIFIERS_RE = re.compile(r"\(([^()|#][^()|]*)\)$")  # zsh glob qualifiers ending a word: (D), (.D), (/)
_MAX_MATCHES = 256
_MAX_WALK = 2048  # entries a ** may visit before nuh gives up listing


def reach(word: str, targets: list[str], *, code: bool = False) -> tuple[list[str], str]:
    """``(paths to check, why nuh cannot tell where it reaches or "")`` for the shell word *word*.

    *targets* are the protected places, resolved (see the module docstring).
    *code*: *word* is a string literal of inline program text, where only
    ``* ? [ ]`` and a leading ``~`` are syntax.
    """
    tilde_dir = word.startswith(("~+", "~-"))
    body = word if tilde_dir or not word.startswith("~") else word[1:]
    active = word if code else word_quoting.expansion(word)
    if not any(char in _TRIGGER for char in body) or SUBSTITUTION_MARK in word or "$" in active or "`" in active:
        return [], ""
    if code:
        pieces: list[str] | None = [_code_spelling(word)]
    else:
        spelling = word_quoting.encoding(word)
        pieces = shell_braces.expand(word if spelling is None else spelling)
    if pieces is None:
        return [], f"brace expansion nuh cannot read: {word}"
    qualifiers = _QUALIFIERS_RE.search(pieces[-1]) if pieces else None
    hidden = glob_options.dotglob() or (qualifiers is not None and "D" in qualifiers.group(1))
    paths: list[str] = []
    for piece in pieces:
        found, why = _piece(piece, targets, hidden)
        if why:
            return [], why
        paths += found
    return list(dict.fromkeys(paths)), ""


def _code_spelling(word: str) -> str:
    """A program string's spelling: every character literal but ``* ? [ ]`` and a leading ``~``."""
    return "".join(char if char in "*?[]" or (i == 0 and char == "~") else word_quoting.encode(char)
                   for i, char in enumerate(word))


def _piece(piece: str, targets: list[str], hidden: bool) -> tuple[list[str], str]:
    """``reach`` for one brace-expanded piece (its spelling); *hidden*: dotglob may be on."""
    expanded, why = _tilde(piece)
    if expanded is None:
        return [], why
    if not glob_pattern.has_syntax(expanded[1:] if expanded.startswith("~") else expanded):
        # No pattern: the path as the tilde expansion spells it (a quoted glob character is a letter).
        return ([] if expanded == piece else spellings(word_quoting.decode(expanded))), ""
    if not expanded.startswith("/"):
        if shell_state.unknown_cwd():
            return [], ""  # bash_stage asks for a relative path in a directory nuh could not follow
        expanded = word_quoting.encode(os.getcwd()).rstrip("/") + "/" + expanded
    parts = glob_pattern.parts(expanded)
    if parts is None:
        return [], f"glob nuh cannot read: {word_quoting.decode(piece)}"
    split = next((i for i, part in enumerate(parts) if part.literal is None), len(parts))
    if ".." in (part.literal for part in parts[split:]):
        return [], f"'..' after a glob component (a match may be a link to anywhere): {word_quoting.decode(piece)}"
    prefix = os.path.realpath(os.path.join("/", *(part.literal or "" for part in parts[:split])))
    rest = parts[split:]
    found = _matches(prefix, rest, hidden)
    paths = [os.path.join(prefix, *(part.raw for part in rest))]
    paths += [s for match in found or [] for s in spellings(match)]
    return paths + _representatives(prefix, rest, targets, hidden), ""


def _tilde(piece: str) -> tuple[str | None, str]:
    """*piece* with its tilde prefix expanded as the shell does; ``(None, why)`` when nuh cannot know the directory.

    ``~`` and ``~user`` are home directories (an unknown user leaves the
    word literal, as in the shell); ``~+`` is the working directory and
    ``~-`` the one before the last ``cd`` nuh followed.
    """
    if not piece.startswith("~"):
        return piece, ""
    head, sep, tail = piece.partition("/")
    shown = word_quoting.decode(piece)
    if head == "~+":
        if shell_state.unknown_cwd():
            return None, f"~+ in a directory nuh could not follow: {shown}"
        directory = os.getcwd()
    elif head == "~-":
        directory = shell_state.current().oldpwd
        if directory is None:
            return None, f"~- names the directory before a cd nuh did not see: {shown}"
    elif head[1:2] in ("+", "-") or head[1:].isdigit():
        return None, f"directory stack entry nuh cannot know: {shown}"
    else:
        expanded = os.path.expanduser(word_quoting.decode(head))
        return (piece if expanded == word_quoting.decode(head)
                else word_quoting.encode(expanded) + sep + tail), ""
    return word_quoting.encode(directory) + sep + tail, ""


def _visible(name: str, part: Part, hidden: bool) -> bool:
    """Whether the pattern *part* may match *name*: a dot-name needs dotglob or a dot it spells out."""
    return hidden or part.dot_names or not name.startswith(".")


def _matches(prefix: str, rest: list[Part], hidden: bool) -> list[str] | None:
    """Every existing path *rest* matches below *prefix* (and maybe more); None when there are too many to list."""
    current = [prefix]
    for part in rest:
        if part.literal is not None:
            current = [os.path.join(path, part.literal) for path in current]
            continue
        nxt: list[str] = []
        for path in current:
            names = _below(path, hidden or part.dot_names) if part.any_depth else _entries(path, part, hidden)
            if names is None:
                return None
            nxt += names
            if len(nxt) > _MAX_MATCHES:
                return None
        current = nxt
    return [path for path in current if os.path.lexists(path)]


def _entries(directory: str, part: Part, hidden: bool) -> list[str]:
    """The names in *directory* the pattern *part* matches, joined to it."""
    try:
        names = os.listdir(directory)
    except OSError:
        # Not a directory or not readable: the shell's glob matches nothing
        # there either, so no path comes from it.
        return []
    if part.dots:
        names += [".", ".."]
    regex = part.regex
    return [os.path.join(directory, name) for name in names
            if regex is not None and _visible(name, part, hidden) and regex.fullmatch(name)]


def _below(directory: str, hidden: bool) -> list[str] | None:
    """*directory* and everything under it (symlinks not followed; dot-names only if *hidden*); None past _MAX_WALK."""
    found = [directory]
    for root, dirs, files in os.walk(directory):
        if not hidden:
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            files = [name for name in files if not name.startswith(".")]
        found += [os.path.join(root, name) for name in (*dirs, *files)]
        if len(found) > _MAX_WALK:
            return None
    return found


def _representatives(prefix: str, rest: list[Part], targets: list[str], hidden: bool) -> list[str]:
    """A path for each protected place in *targets* the pattern *rest* below *prefix* could name (module docstring)."""
    reps: list[str] = []
    seen: set[tuple[str, int]] = set()
    todo = [(prefix, 0)]
    while todo:
        path, i = todo.pop(0)
        if (path, i) in seen:
            continue
        seen.add((path, i))
        if any(path == t or path.startswith(t.rstrip("/") + "/") for t in targets):
            reps.append(os.path.join(path, *(part.raw for part in rest[i:])))
            continue
        if i == len(rest):
            reps.append(path)
            continue
        todo += _steps(path, i, rest[i], targets, hidden)
    return reps


def _steps(path: str, i: int, part: Part, targets: list[str], hidden: bool) -> list[tuple[str, int]]:
    """Where component *part* (the i-th of the pattern's rest) can lead from *path* toward a target."""
    if part.literal is not None:
        return [(os.path.realpath(os.path.join(path, part.literal)), i + 1)]
    children = [child for child in _children(path, targets) if _visible(os.path.basename(child), part, hidden)]
    if part.any_depth:
        return [(path, i + 1), *((child, i) for child in children)]
    regex = part.regex
    steps = [(child, i + 1) for child in children if regex is not None and regex.fullmatch(os.path.basename(child))]
    if part.dots and (hidden or part.dot_names):
        steps += [(path, i + 1), (os.path.dirname(path), i + 1)]
    return steps


def _children(path: str, targets: list[str]) -> list[str]:
    """The entries of *path* on the way to a target below it, in the targets' order."""
    base = path.rstrip("/") + "/"
    return list(dict.fromkeys(base + target[len(base):].split("/", 1)[0]
                              for target in targets if target.startswith(base)))
