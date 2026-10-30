"""Whether a command may run its globs with dotglob (bash) or globdots (zsh) on.

Both are off by default, in the agent's shell too: a leading ``*``, ``?`` or
bracket expression never matches a name starting with ``.``. Each tool call
is a fresh shell, so only the command itself can turn them on. nuh assumes
it does, for every glob in the command, when anything in it can, before or
after the glob (order is not modelled):

- ``dotglob`` or ``globdots`` anywhere, case and underscores ignored
  (``shopt -s dotglob``, ``setopt GLOB_DOTS``, ``unsetopt NO_GLOB_DOTS``,
  ``bash -O dotglob``, ``zsh -o globdots``, ``zsh --globdots``);
- a ``shopt``, ``setopt``, ``unsetopt`` or ``set`` whose arguments hold
  something nuh cannot read (``setopt $O``, ``shopt -s $(cat opts)``);
  ``set --`` only sets the positional parameters;
- a ``GLOBIGNORE`` assignment (bash turns dotglob on when it is non-null);
- ``emulate`` with arguments (it may reset any option).

The zsh ``(D)`` qualifier turns globdots on for its own glob (``glob_reach``).
``scope`` holds the command being classified; a nested command (``bash -c``)
inherits what the outer one may have set, and its text is part of the
outer one's anyway.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

_SETTER_RE = re.compile(r"(?:^|[\s;&|({`])(shopt|setopt|unsetopt|set)\s+([^;&|\n]*)")
_OTHER_RE = re.compile(r"\bGLOBIGNORE\+?=|(?:^|[\s;&|({`])emulate\s+[^\s;&|]")
_UNREAD_RE = re.compile(r"[$`]|__nuh_psub_")  # a parameter, a substitution or nuh's placeholder for one
_scopes: list[bool] = []


def enables_dotglob(command: str) -> bool:
    """True when *command* may turn dotglob or globdots on (see the module docstring)."""
    flat = command.lower().replace("_", "")
    if "dotglob" in flat or "globdots" in flat or _OTHER_RE.search(command):
        return True
    for match in _SETTER_RE.finditer(command):
        name, args = match.groups()
        if name == "set" and args.lstrip().startswith("--"):
            continue
        if _UNREAD_RE.search(args):
            return True
    return False


@contextmanager
def scope(command: str) -> Iterator[None]:
    """Classify *command*: its globs see dotglob when it, or a command around it, may turn it on."""
    _scopes.append(enables_dotglob(command) or bool(_scopes and _scopes[-1]))
    try:
        yield
    finally:
        _scopes.pop()


def dotglob() -> bool:
    """True while classifying a command that may run its globs with dotglob or globdots on."""
    return bool(_scopes and _scopes[-1])
