"""Variable uses nuh cannot know: found in a word, masked in a path, and the reason a stage asks."""

from __future__ import annotations

import os
import re

from nuh import word_quoting

from nuh.shell_state import _DIRECTORY_VARS, _NON_PATH_PARAMS, _shell_own, _VAR_REF_RE, State


# A parameter use, or (group ``other``) an expansion none covers, to the next ``/``:
# ``$[1+1]``, a ``$((x/2))`` left uncomputed, a ``${`` nuh cannot read.
_ANY_EXPANSION_RE = re.compile(_VAR_REF_RE.pattern + r"|(?P<other>\$[\[{(][^/]*)")


UNRESOLVED_COMPONENT = "__nuh_unresolved__"
# Shell variables that always hold a number: no path can come of them.
_NUMERIC_SHELL_VARS = frozenset({"RANDOM", "SRANDOM", "SECONDS", "EPOCHSECONDS", "EPOCHREALTIME", "LINENO",
                                 "PPID", "UID", "EUID", "BASHPID", "SHLVL", "HISTCMD", "OPTIND"})


def _set_here(name: str, state: State) -> bool:
    return name in state.variables() or name in state.opaque or name in state.row_names()


def _unknown_use(match: re.Match[str], state: State) -> bool:
    """True when the variable use *match* names a value nuh cannot know.

    Set in this command: when its value is unresolvable, or the use carries
    an operator; a loop variable left as written (``State.rows``) and an
    ``opaque`` one always. Not set in this command: when nuh's environment
    does not have it (the agent's shell may set it from rc files nuh never
    reads), or the use carries an operator nuh does not compute (``${X%/*}``,
    a default ``expand_words`` could not read off). The hook inherits the
    agent's environment, so one nuh sees (``$HOME``, ``$TMPDIR``) is known.
    The shell sets some itself: a positional parameter (``$1``, ``"$@"``)
    nuh holds no value for, ``$_``, and ``$!``/``$-`` (which can be empty)
    are unknown, ``$PWD``/``$OLDPWD`` are the directories nuh followed, and
    a number that is never empty (``$$``, ``$?``, ``$#``) names no path.
    """
    name, operator = match.group(1) or match.group(3), match.group(2)
    variables = state.variables()
    if name in variables:
        return variables[name] is None or bool(operator)
    if name in state.opaque or name in state.row_names() or _shell_own(name):
        return True
    if name in _DIRECTORY_VARS:
        return bool(operator) or state.shell_value(name) is None
    if (name in _NUMERIC_SHELL_VARS or name in _NON_PATH_PARAMS) and not operator:
        return False
    return bool(operator) or name not in os.environ


def unresolved_uses(word: str, state: State) -> list[str]:
    """The variables *word* uses at a value nuh cannot know (``_unknown_use``), in order.

    Any other expansion left in it (``$[…]``, a ``${…}`` nuh cannot read)
    counts too, by its opening bracket.
    """
    if "$" not in word:
        return []
    uses = [m.group(0)[1] if m.group("other") else m.group(1) or m.group(3)
            for m in _ANY_EXPANSION_RE.finditer(word_quoting.expansion(word))
            if m.group("other") or _unknown_use(m, state)]
    return list(dict.fromkeys(uses))


def unresolved_reason(names: list[str], state: State) -> str:
    """The reason a command using *names* (variables nuh cannot know in *state*) asks."""
    here = [name for name in names if _set_here(name, state)]
    shells = [name for name in names if name not in here and (
        _shell_own(name) or name in _DIRECTORY_VARS or name in _NON_PATH_PARAMS or name in ("[", "{", "("))]
    elsewhere = [name for name in names if name not in here and name not in shells]
    parts = []
    if here:
        parts.append(f"uses {', '.join(f'${name}' for name in here)}, "
                     "set in this command to a value nuh cannot resolve")
    if shells:
        parts.append(f"uses {', '.join(f'${name}' for name in shells)}, "
                     "a shell parameter or expansion nuh cannot resolve")
    if elsewhere:
        parts.append(f"uses {', '.join(f'${name}' for name in elsewhere)}, "
                     "not set in this command: a value nuh cannot resolve")
    return "; ".join(parts)


def mask_unresolved(word: str, state: State) -> str:
    """*word* with each use of a variable nuh cannot know (``_unknown_use``) replaced by an opaque path component.

    The value may be any path, absolute or climbing out with ``..``, so the
    working directory says nothing about where ``"$W"`` points: a word
    starting with such a use is rooted at ``/``, a use further in is one
    component. What the rest of the word names still counts (``~/.ssh/$W``,
    ``"$W/.env"``). Any other expansion (``$[…]``) masks up to the next ``/``.
    """
    if "$" not in word:
        return word

    def mask(match: re.Match[str]) -> str:
        if not match.group("other") and not _unknown_use(match, state):
            return match.group(0)
        return ("/" if match.start() == 0 else "") + UNRESOLVED_COMPONENT

    active = word_quoting.expansion(word)
    masked = word_quoting.decode(_ANY_EXPANSION_RE.sub(mask, active))
    if masked == word:
        return word  # keep its quoting record
    spelling = word_quoting.encoding(word)
    if spelling is not None:
        for match in reversed(list(_ANY_EXPANSION_RE.finditer(active))):
            if match.group("other") or _unknown_use(match, state):
                spelling = spelling[:match.start()] + mask(match) + spelling[match.end():]
        return word_quoting.spelled(masked, spelling)
    return masked
