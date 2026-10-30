"""The environment a stage's command runs with: its prefix assignments and its wrappers' (``env X=v``, ``sudo X=v``).

``X=v bash -c 'cat "$X"'`` and ``env X=v sh -c …`` hand ``X=v`` to the new
shell, and ``X=v eval …`` to the eval'd text, while the stage's own words
were expanded before the assignment. ``shell_state.command_env`` holds it
while the command (and each wrapper's command inside it) is classified;
``shell_state.fresh_shell`` keeps what a new shell inherits.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from nuh import shell_program_text, shell_state, word_quoting
from nuh.passthrough_wrappers import env_settings, EnvSettings
from nuh.shell_setters import prefixed
from nuh.shell_state import State

# Set by a shell as it starts, whatever environment it was given.
_SHELL_SET = frozenset({"PWD", "OLDPWD", "SHLVL", "PATH", "_"})


def _exported(state: State, words: tuple[str, ...] | list[str]) -> State:
    """*state* with the assignment *words* made for a command: assigned left to right and exported."""
    if not words:
        return state
    names = {word.split("=", 1)[0] for word in words}
    return replace(prefixed(state, tuple(words)), exported=state.exported | names)


def _with_env(state: State, found: EnvSettings) -> State:
    """*state* as ``env`` hands it on: ``-i`` empties every variable nuh knows of (but those a shell
    sets itself), ``-u NAME`` empties NAME, then its assignments."""
    if found.cleared:
        known = (os.environ.keys() | state.variables().keys() | state.row_names() | state.opaque) - _SHELL_SET
        state = state.with_vars(dict.fromkeys(known, ""))
    if found.unset:
        state = replace(state.with_vars(dict.fromkeys(found.unset, "")), exported=state.exported - set(found.unset))
    return _exported(state, found.assigned)


@contextmanager
def running(words: tuple[str, ...]) -> Iterator[None]:
    """Classify a stage's command with its prefix assignment *words* set (``shell_state.command_env``)."""
    if not words:
        yield
        return
    with shell_state.commanding(_exported(shell_state.command_env(), words)):
        yield


@contextmanager
def wrapped(tokens: list[str]) -> Iterator[str]:
    """Classify the command an ``env`` wrapper (*tokens*) runs with its settings; any other wrapper changes none."""
    found = env_settings(tokens)
    if found is None or not (found.cleared or found.unset or found.assigned or found.chdir is not None):
        yield ""
        return
    outer = shell_state.current()
    child = _with_env(shell_state.command_env(), found)
    if found.chdir is None:
        with shell_state.commanding(child):
            yield ""
    else:
        cwd, unreadable = _wrapper_cwd(found.chdir, outer)
        with shell_state.entered(replace(outer, cwd=cwd)):
            with shell_state.commanding(replace(child, cwd=shell_state.current().cwd)):
                yield unreadable


def _wrapper_cwd(target: str, outer: State) -> tuple[str | None, str]:
    """Resolve an env directory using outer argument expansion, before its assignments."""
    spelling = word_quoting.encoding(target) or target
    if any(char in spelling for char in "*?["):
        return None, "env chdir uses argument splitting or globbing nuh cannot resolve"
    directory, unreadable = shell_program_text.expand(target, outer)
    if spelling.startswith("~"):
        if directory == "~" or directory.startswith("~/"):
            home = outer.known_value("HOME")
            directory = home + directory[1:] if home is not None else "~"
        elif directory == "~+" or directory.startswith("~+/"):
            directory = outer.cwd + directory[2:] if outer.cwd is not None else "~+"
        elif directory == "~-" or directory.startswith("~-/"):
            directory = outer.oldpwd + directory[2:] if outer.oldpwd is not None else "~-"
        else:
            directory = os.path.expanduser(directory)
        if directory.startswith("~"):
            return None, "env chdir tilde nuh cannot resolve"
    if unreadable or not directory:
        return None, unreadable or "env chdir argument nuh cannot resolve"
    bare = word_quoting.unquoted_names(target)
    if ((bare or (bare is None and "$" in word_quoting.expansion(target)))
            and any(char.isspace() or char in "*?[" for char in directory)):
        return None, "env chdir uses argument splitting or globbing nuh cannot resolve"
    if not os.path.isabs(directory):
        if outer.cwd is None:
            return None, "env chdir runs in a directory nuh cannot follow"
        directory = os.path.join(outer.cwd, directory)
    return os.path.normpath(directory), ""
