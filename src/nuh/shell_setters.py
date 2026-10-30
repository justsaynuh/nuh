"""The builtins that assign variables (export, declare, typeset, local, readonly, unset) as bash and zsh run them.

``A=1 B=$A`` assigns left to right, but a builtin expands all its words
before it runs: ``export X=new Y=$X`` sets Y to X's old value. A prefix
assignment (``X=v export Y=$X``) is seen by zsh's expansion, not bash's,
and stays set after a special builtin in POSIX mode (dash as ``sh``, and
bash's ``X=v export X``). ``command export`` runs the builtin in bash and
looks for a program in zsh; ``noglob export`` the other way round. Verified
against bash 5.2 and zsh 5.9 (each reading is one state; ``shell_walk``
keeps them all).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from nuh import word_quoting
from nuh.shell_expand import _assign_one
from nuh.shell_state import _drop, State, SubValue


SETTERS = frozenset({"export", "declare", "typeset", "local", "readonly"})
# POSIX special builtins (and source, bash's `.`): a prefix assignment before one stays set in POSIX mode.
SPECIAL = frozenset({":", ".", "source", "eval", "exec", "set", "shift", "times", "trap", "unset", "break",
                     "continue", "return", "exit", "export", "readonly"})
_BOTH = frozenset({"builtin"})  # precommands both shells run as the builtin
_ZSH_ONLY = frozenset({"noglob", "nocorrect", "-"})  # bash has no such command: nothing runs
_PLAIN_OPTIONS = frozenset("xrgp")  # export, readonly, global, print: the value is assigned as written
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)


def precommands(words: list[str]) -> tuple[list[str], bool]:
    """*words* without the precommands that still run a builtin in this shell, and whether one shell runs nothing.

    ``command`` (and ``command -p``) runs the builtin in bash, a program of
    that name in zsh; ``noglob``/``nocorrect``/``-`` are zsh's alone.
    ``time`` and ``builtin`` run it in both.
    """
    partial = False
    while words:
        if words[0] in _BOTH or words[0] in _ZSH_ONLY:
            partial = partial or words[0] in _ZSH_ONLY
            words = words[1:]
        elif words[0] == "command" and len(words) > 1:
            rest = words[1:]
            while rest and rest[0] == "-p":
                rest = rest[1:]
            if not rest or rest[0].startswith("-"):
                return words, partial  # command -v/-V: a lookup, nothing runs
            words, partial = rest, True
        elif words[0] == "time" and len(words) > 1 and not words[1].startswith("-"):
            words = words[1:]
        elif words[:2] == ["time", "-p"]:
            words = words[2:]
        else:
            return words, partial
    return words, partial


@dataclass(frozen=True)
class Call:
    """What one setter command assigns."""

    name: str                             # export, declare, …
    pairs: tuple[tuple[str, str], ...]    # NAME=value words, in order
    marked: tuple[str, ...]               # bare NAME words
    export: bool                          # the names become exported
    unknown: frozenset[str]               # names set to a value nuh does not model (an option, an array)


def parse(words: list[str]) -> Call | None:
    """The setter command *words* (precommands already stripped), or None when they are not one.

    An option other than -x/-r/-g/-p (``-i``, ``-a``, ``-n``, zsh's ``-u``…)
    changes how the value reads, so every name it sets is unknown; a nameref
    (``-n R=X``) makes the name it refers to unknown too.
    """
    if not words or words[0] not in SETTERS:
        return None
    options, args = "", words[1:]
    while args and args[0][:1] in "-+" and len(args[0]) > 1:
        if args[0] == "--":
            args = args[1:]
            break
        options += args[0] if args[0][0] == "+" else args[0][1:]
        args = args[1:]
    plain = set(options) <= _PLAIN_OPTIONS
    pairs: list[tuple[str, str]] = []
    marked: list[str] = []
    unknown: set[str] = set()
    for arg in args:
        pair, name = _PAIR_RE.fullmatch(arg), _NAME_RE.match(arg)
        if pair is not None and plain:
            pairs.append((pair.group(1), word_quoting.part(arg, pair.start(2))))
        elif _NAME_RE.fullmatch(arg) and plain:
            marked.append(arg)
        elif name is not None:
            unknown.add(name.group(0))
            if "n" in options and pair is not None and _NAME_RE.fullmatch(pair.group(2)):
                unknown.add(pair.group(2))
    return Call(words[0], tuple(pairs), tuple(marked), words[0] == "export" or "x" in options, frozenset(unknown))


def assigned_names(words: list[str]) -> set[str]:
    """Every name a stage's *words* may assign: plain assignments and setter arguments."""
    if words and all(_PAIR_RE.fullmatch(word) for word in words):
        return {word.split("=", 1)[0] for word in words}
    call = parse(precommands(words)[0])
    return set() if call is None else {name for name, _value in call.pairs} | set(call.marked) | call.unknown


def prefixed(state: State, words: tuple[str, ...], sub_value: SubValue | None = None) -> State:
    """*state* with the prefix assignment *words* (``X=1 Y=$X cmd``) made, left to right."""
    for word in words:
        name = word.split("=", 1)[0]
        value = word_quoting.part(word, len(name) + 1)
        state = _assign_one(state, name, value, sub_value)
    return state


def apply(base: State, call: Call, values_from: State, sub_value: SubValue | None = None) -> State:
    """*base* after *call*, its values expanded in *values_from* (all before any is assigned).

    Each value is first assigned to a name no word can use, so no value sees
    another; then the names take them in order.
    """
    temps = [f"\0{i}" for i in range(len(call.pairs))]
    staged = values_from
    for temp, (_name, value) in zip(temps, call.pairs):
        staged = _assign_one(staged, temp, value, sub_value)
    state = staged if base == values_from else _carry(staged, base, temps)
    for temp, (name, _value) in zip(temps, call.pairs):
        state = state.renamed(temp, name)
    state = state.with_vars(dict.fromkeys(call.unknown)) if call.unknown else state
    if call.export:
        names = {name for name, _value in call.pairs} | set(call.marked)
        state = _set_exported(state, (state.exported | names) - call.unknown)
    return state


def _carry(staged: State, base: State, temps: list[str]) -> State:
    """*base* holding the *temps* *staged* computed; a temp that varies with rows *base* lacks is unknown."""
    variables = staged.variables()
    same_rows = _drop(staged.rows, set(temps)) == base.rows
    state = base
    for temp in temps:
        if temp in variables:
            state = state.with_vars({temp: variables[temp]})
        elif same_rows and temp in staged.row_names():
            state = state.with_column(temp, [dict(row)[temp] for row in staged.rows])
        else:
            state = state.with_vars({temp: None})
    return state


def unset(state: State, args: list[str]) -> State:
    """*state* after ``unset NAME…``: each name empty and no longer exported.

    ``-v`` is the default and ``-f`` names functions; any other option
    (zsh's ``-m`` pattern, bash's ``-n``) may unset anything: every
    variable set in this command is unknown.
    """
    options = "".join(arg[1:] for arg in args if arg.startswith("-"))
    if "f" in options:
        return state
    if set(options) - {"v"}:
        return state.with_vars(dict.fromkeys(state.variables().keys() | state.row_names()))
    names = {arg for arg in args if _NAME_RE.fullmatch(arg)}
    return _set_exported(state.with_vars(dict.fromkeys(names, "")), state.exported - names)


def _set_exported(state: State, exported: frozenset[str] | set[str]) -> State:
    return replace(state, exported=frozenset(exported))
