"""The one path an ``ls`` substitution prints, when nuh can tell exactly.

``W=$(ls 02-white-book/current/*PRINT.txt)`` sets ``W`` to the file the glob
matches, when it matches one: ``ls`` given a file prints its name as the
shell passed it, and the substitution drops the newline. ``shell_state``
then knows ``W`` like any literal assignment.

Read only when the answer cannot depend on what nuh does not see:

- ``ls`` with no options but ``-1`` and ``-d``, and one operand that is a
  plain word: no quotes, variables, braces, ``~`` or zsh glob syntax, and
  no bracket expression (bash and Python read ``[^…]`` differently);
- exactly one existing path matches, under any of the shell options that
  widen a glob (nocaseglob, dotglob, globdots): ``word_paths.glob_superset``
  lists every candidate, and the one left must match case-sensitively with
  no wildcard matching a leading dot;
- it is no directory (``ls`` would list what is in it), unless ``-d``.

No match (``ls`` prints nothing) and several (one word with newlines in
``"$W"``) are unresolved.
"""

from __future__ import annotations

import fnmatch
import os
import re

from nuh import word_paths

_LS_RE = re.compile(r"\s*ls((?:\s+-(?:1|d|1d|d1))*)\s+([^\s'\"\\$`{}\[\]~^#<>|();&!=]+)\s*")
_WILDCARDS = frozenset("*?")


def single_path(text: str, cwd: str | None) -> str | None:
    """What the ``ls`` command *text* prints, run in *cwd*, when that is one path nuh can name; else None."""
    match = _LS_RE.fullmatch(text)
    if match is None or cwd is None:
        return None
    word = match.group(2)
    pattern = word if os.path.isabs(word) else os.path.join(cwd, word)
    if any(char in _WILDCARDS for char in word):
        found = word_paths.glob_superset(pattern)
        if found is None:
            return None
    else:
        found = [pattern] if os.path.lexists(pattern) else []
    if len(found) != 1 or not _matches_exactly(found[0], pattern):
        return None
    if "d" not in match.group(1) and os.path.isdir(found[0]):
        return None
    if os.path.isabs(word):
        return found[0]
    prefix = cwd.rstrip("/") + "/"
    return found[0][len(prefix):] if found[0].startswith(prefix) else None


def _matches_exactly(path: str, pattern: str) -> bool:
    """True when every component of *path* matches *pattern*'s as the shell's default glob reads it."""
    names = [part for part in path.split("/") if part]
    parts = [part for part in pattern.split("/") if part]
    if len(names) != len(parts):
        return False
    for name, part in zip(names, parts):
        if not any(char in _WILDCARDS for char in part):
            if name != part:
                return False
        elif not fnmatch.fnmatchcase(name, part) or (name.startswith(".") and not part.startswith(".")):
            return False
    return True
