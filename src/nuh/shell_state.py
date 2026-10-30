"""The shell state a command changes as it runs: working directory and variables.

``cd ~ && rm -rf Documents`` deletes ``~/Documents``, not ``./Documents``, and
``F=~/.ssh/id_rsa; cat $F`` reads the key. ``shell_walk.walk`` follows a command's
stages in order and gives each one the state it runs in:

- the working directory after ``cd``/``chdir``/``pushd``/``popd``, or unknown
  after one nuh cannot follow (``cd "$X"``, ``cd -`` before any cd, a name
  CDPATH may resolve, ``popd`` of a stack nuh has not seen);
- the variables assigned earlier in the command, expanded in later words and
  redirect targets (``expand_target``); a
  value nuh cannot resolve (``F=$(cmd)``) makes its uses unresolved, except
  a whole ``$(ls …)`` that prints one path nuh can name (``shell_ls``);
- the values a ``for`` loop gives its variable, its list expanded here
  (``shell_loops``: globs, ``~``, braces, known variables): a stage using it
  is checked once per value (``Step.variants``), and so is one using what
  the body assigned from it (``f="$D/$p.pdf"``, ``s=$((n-40))``, see
  ``shell_arith``), each value paired with the loop value it came from
  (``State.rows``);
- variables holding values nuh never sees: a loop over a list it cannot
  expand (``$(cat list)``) and the names ``read`` sets (``State.opaque``).
  A use where a path goes is unknown; elsewhere it is text (``echo $f``).
  So is a variable neither set in the command nor in nuh's environment
  (the agent's shell may set it from rc files the hook never reads);
- an assigned value with whitespace inside a larger word (``"$D/x"``, quoting
  unknown): the stage is checked with the value joined and split.

Assignments follow the shells too (``shell_setters``): ``A=1 B=$A`` assigns
left to right, a builtin (``export X=new Y=$X``) expands every argument
first; a prefix assignment (``X=v cmd``) reaches the command
(``command_env``) and, before a special builtin, may stay set after it.
Where bash and zsh differ (``command export`` runs nothing in zsh), each
value is kept, one per row (``shell_walk.either``). A new shell sees the
exported variables (``fresh_shell``).

Scopes follow the shell. A subshell ``( … )`` (``Stage.opens``/``closes``),
a new shell (``bash -c``), a substitution, each element of a pipeline and a
background list (``&``) keep their changes to themselves; zsh runs the last
element of a pipeline in the current shell, so a change there leaves the
state either way (unknown where it differs). In an
and-or list a change holds only where it surely happened: after ``test -d x &&
cd x; rm y`` the directory is unknown for ``rm``, after ``cd x || exit 1`` it
is ``x``. Inside compound syntax (``if``, loops, ``{ … }``, functions) a change
may or may not run, so every change there is uncertain (``StageLike.compound``);
the commands before and after it run once, in order. The exception is an
assignment directly in a ``for`` loop's body: the commands after it in the
same pass see it. Each pass starts with what the last one left, so a variable
the body assigns is unresolved until it does, and after the loop it may not
have run.

``entered`` makes the state current while a stage is classified: the process
changes into a known working directory, so every relative path check
(``os.path.realpath`` and friends) resolves against it. With the directory
unknown it stays where it is and ``unknown_cwd`` says so; callers then treat
relative paths as unknown.

Adapted from manuelschipper/nah (MIT): c75ab0fa (``_stage_shell_cwd_update``,
``_parse_cd_target``, ``_cdpath_may_affect``, ``_path_for_shell_cwd``) and
817e6de1 (``_expand_intra_chain_vars``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Protocol

from nuh import shell_arith, shell_loops, word_quoting

class StageLike(Protocol):
    tokens: list[str]
    body_words: int
    operator: str
    opens: int
    closes: int
    heredoc_literal: str
    stdin_ambiguous: bool
    script_body: str | None
    compound: bool  # inside compound syntax: its changes may not run
    prefix_env: tuple[str, ...]  # the NAME=value words before its command, as written
    scopes: tuple[int, ...]  # ids of the compound constructs open around it, outermost first
    # Loop variables bound just before it, each with its list's words and the scope id of its loop's body.
    loop_vars: tuple[tuple[str, tuple[str, ...] | None, int], ...]

    @property
    def redirect_targets(self) -> list[str]: ...  # the files its output redirects write


Row = tuple[tuple[str, str], ...]  # one combination of loop values: (name, value) pairs, sorted
_MAX_ROWS = 1024


def _drop(rows: tuple[Row, ...], names: frozenset[str] | set[str]) -> tuple[Row, ...]:
    """*rows* without the columns in *names*, duplicates dropped (an empty row set when none is left)."""
    kept = tuple(dict.fromkeys(tuple(pair for pair in row if pair[0] not in names) for row in rows))
    return () if kept in ((), ((),)) else kept


@dataclass(frozen=True)
class State:
    """Working directory (None: unknown), the variables assigned so far and the loop values in play."""

    cwd: str | None
    oldpwd: str | None = None                 # set by a cd in this command, else the shell's own
    dirs: tuple[str | None, ...] = ()         # pushd stack pushed in this command
    vars: tuple[tuple[str, str | None], ...] = ()  # assigned in this command; None: unresolvable
    # Loop variables, and what a loop body computed from them: each row is one
    # combination they take together (``for n …; do s=$((n-40))`` pairs each n with its s).
    rows: tuple[Row, ...] = ()
    # Set in this command to values nuh cannot know (a loop over ``$(cmd)``):
    # unknown where they name a path, plain text elsewhere (``echo $f``).
    opaque: frozenset[str] = frozenset()
    # Marked for export in this command (``export X``, ``declare -x``, a prefix
    # assignment for the command it runs): a new shell sees them (``fresh_shell``).
    exported: frozenset[str] = frozenset()

    def variables(self) -> dict[str, str | None]:
        return dict(self.vars)

    def row_names(self) -> frozenset[str]:
        return frozenset(name for name, _value in self.rows[0]) if self.rows else frozenset()

    def with_vars(self, updates: dict[str, str | None]) -> State:
        merged = self.variables()
        merged.update(updates)
        return replace(self, vars=tuple(sorted(merged.items())), rows=_drop(self.rows, set(updates)),
                       opaque=self.opaque - set(updates))

    def with_opaque(self, names: set[str] | frozenset[str]) -> State:
        """*names* set to values nuh cannot know, used as text (see ``opaque``)."""
        variables = {name: value for name, value in self.vars if name not in names}
        return replace(self, vars=tuple(sorted(variables.items())), rows=_drop(self.rows, set(names)),
                       opaque=self.opaque | set(names))

    def renamed(self, old: str, new: str) -> State:
        """*old*'s value (plain, or one per row) held by *new*; *old* gone."""
        variables = self.variables()
        variables.pop(new, None)
        rows = _drop(self.rows, {new})
        if old in variables:
            variables[new] = variables.pop(old)
        else:
            rows = tuple(tuple(sorted((new if name == old else name, value) for name, value in row))
                         for row in rows)
        return replace(self, vars=tuple(sorted(variables.items())), rows=rows,
                       opaque=(self.opaque - {new, old}) | ({new} if old in self.opaque else set()))

    def or_unset(self, names: set[str] | frozenset[str]) -> State:
        """Each of *names* holding its value or nothing (unset, read as ""), every combination a row.

        A name with no value nuh knows stays as it is (unknown either way);
        more than _MAX_ROWS combinations make the rest opaque.
        """
        state = self
        for name in sorted(names):
            variables = state.variables()
            unknown = (name in variables and variables[name] is None) or name in state.opaque
            if unknown or (name not in variables and name not in state.row_names()):
                continue
            rows = tuple(dict.fromkeys(
                tuple(sorted({**dict(row), name: choice}.items()))
                for row in (state.rows or ((),))
                for choice in (dict(row).get(name, variables.get(name) or ""), "")))
            if len(rows) > _MAX_ROWS:
                return state.with_opaque({n for n in names if n >= name})
            variables.pop(name, None)
            state = replace(state, vars=tuple(sorted(variables.items())), rows=rows)
        return state

    def with_row(self, row: Row) -> State:
        """This state in one combination of loop values: they are plain variables."""
        return replace(self, rows=()).with_vars(dict(row))

    def with_column(self, name: str, values: list[str]) -> State:
        """*name* taking ``values[i]`` in row i."""
        variables = self.variables()
        variables.pop(name, None)
        rows = tuple(tuple(sorted((*(p for p in row if p[0] != name), (name, value))))
                     for row, value in zip(self.rows, values))
        return replace(self, vars=tuple(sorted(variables.items())), rows=rows)

    def bind_loop(self, name: str, words: tuple[str, ...] | None) -> State:
        """*name* set by a loop over *words* (``shell_loops.values``) to each value; None: no list nuh reads.

        A list using the loop values around it is expanded in each of their
        combinations, each value paired with the one it came from. Values
        nuh cannot list, or more than _MAX_ROWS combined with the loops
        around it, make *name* opaque: its path uses are unknown.
        """
        combined = self._loop_rows(name, words) if words is not None else None
        if combined is None:
            return self.with_opaque({name})
        variables = self.variables()
        variables.pop(name, None)
        return replace(self, vars=tuple(sorted(variables.items())), rows=combined, opaque=self.opaque - {name})

    def _loop_rows(self, name: str, words: tuple[str, ...]) -> tuple[Row, ...] | None:
        rows = _drop(self.rows, {name}) or ((),)
        row_names = self.row_names() - {name}
        dependent = any(_row_refs(word, row_names)[0] for word in words)
        combined: list[Row] = []
        for row in rows if dependent else [()]:
            state = self.with_row(row) if dependent else self
            values = shell_loops.values(words, state.known_value, state.cwd)
            if values is None:
                return None
            combined += [tuple(sorted((*paired, (name, value)))) for paired in ([row] if dependent else rows)
                         for value in values]
            if len(combined) > _MAX_ROWS:
                return None
        return tuple(dict.fromkeys(combined))

    def known_value(self, name: str) -> str | None:
        """The value of variable *name* here: set in this command, else nuh's environment; None when unknown.

        ``$PWD``/``$OLDPWD`` follow the cds nuh tracks (``shell_value``);
        ``$1``, ``"$@"`` and ``$_`` never come from the environment.
        """
        variables = self.variables()
        if name in variables:
            return variables[name]
        if name in self.opaque or name in self.row_names():
            return None
        if name in _DIRECTORY_VARS:
            return self.shell_value(name)
        if _shell_own(name):
            return None
        return os.environ.get(name)

    def shell_value(self, name: str) -> str | None:
        """``$PWD``/``$OLDPWD`` in this state: the directory nuh followed there, None when unknown.

        The environment nuh runs in holds the values from before the command,
        not the directory a ``cd`` in it left.
        """
        return self.cwd if name == "PWD" else self.oldpwd


@dataclass(frozen=True)
class Variant:
    """One reading of a stage's words: these variable values, the names in *split* split on whitespace."""

    values: dict[str, str]
    split: frozenset[str] = frozenset()


@dataclass
class Step:
    """What a stage runs with: its state, its words and redirect targets expanded, the variables it cannot resolve."""

    state: State
    tokens: list[str]
    unresolved: list[str] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)  # readings to check in turn (word_variants)
    targets: list[str] = field(default_factory=list)  # StageLike.redirect_targets expanded (expand_target)


# --- the state current while a stage is classified --------------------------

_current: State | None = None
_command: State | None = None  # the state the stage's command starts with (its prefix assignments set)


def current() -> State:
    """The state of the stage being classified (the process cwd outside any)."""
    return _current if _current is not None else State(cwd=os.getcwd())


def command_env() -> State:
    """The state the command of the stage being classified runs with: ``current`` plus its prefix assignments.

    ``X=v bash -c 'cat "$X"'``: the new shell sees ``X=v``, the stage's own
    words do not (they were expanded before the assignment).
    """
    return _command if _command is not None else current()


@contextmanager
def commanding(state: State) -> Iterator[None]:
    """Make *state* the ``command_env`` while the stage's command (and what it wraps) is classified."""
    global _command
    outer = _command
    _command = state
    try:
        yield
    finally:
        _command = outer


def unknown_cwd() -> bool:
    """True while a stage whose working directory is unknown is classified."""
    return _current is not None and _current.cwd is None


@contextmanager
def entered(state: State) -> Iterator[None]:
    """Classify in *state*: change into its directory, restore afterwards.

    A directory that does not exist yet (``mkdir b && cd b``) or cannot be
    entered is unknown: relative paths there cannot be checked.
    """
    global _current, _command
    outer_state, outer_command, outer_cwd = _current, _command, os.getcwd()
    if state.cwd is not None and not _chdir(state.cwd):
        state = replace(state, cwd=None)
    _current, _command = state, None
    try:
        yield
    finally:
        _current, _command = outer_state, outer_command
        if os.getcwd() != outer_cwd:
            os.chdir(outer_cwd)


def _chdir(path: str) -> bool:
    if path == os.getcwd():
        return True
    if not os.path.isdir(path):
        return False
    try:
        os.chdir(path)
    except OSError:
        # Permission or a race with deletion: the shell's cd would fail the
        # same way. False makes the directory unknown, so relative paths ask.
        return False
    return True


# A parameter use: a name, a positional parameter (``$1``, ``${10}``) or a
# special one (``$@``, ``$$``); in braces with what follows the name
# (``${F%/*}``, ``${#F}`` reads as ``#`` with ``F`` after it).
_VAR_REF_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])([^}]*)\}"
                         r"|([A-Za-z_][A-Za-z0-9_]*|[0-9]|[@*#?$!-]))")


# Set by the shell as the command runs, so nuh's environment says nothing of them.
_DIRECTORY_VARS = frozenset({"PWD", "OLDPWD"})
# Special parameters that always hold a number, never empty: no path comes of them.
_NON_PATH_PARAMS = frozenset({"$", "?", "#"})


def _shell_own(name: str) -> bool:
    """True for ``$0``, ``$1``…, ``$@``, ``$*``, ``$_``, ``$!`` and ``$-``: values nuh never has (the shell sets them).

    ``$!`` and ``$-`` can be empty: ``$!`` until a background job runs (each
    Bash call starts a fresh shell), ``$-`` in dash. Empty, ``"./$!"`` is
    ``./``, so neither is a fixed string a path can be built on.
    """
    return name == "_" or name.isdigit() or name in ("@", "*", "!", "-")


def _row_refs(text: str, row_names: frozenset[str]) -> tuple[set[str], bool]:
    """The loop values (row names) *text* uses, bare in ``$((…))`` too, and whether one carries an operator."""
    text = word_quoting.expansion(text)
    refs = [m for m in _VAR_REF_RE.finditer(text) if (m.group(1) or m.group(3)) in row_names]
    names = {m.group(1) or m.group(3) for m in refs} | (shell_arith.names(text) & row_names)
    return names, any(m.group(2) for m in refs)


SubValue = Callable[[int, State], "str | None"]  # what substitution N prints when run in a state; None: unknown


def fresh_shell(state: State) -> State:
    """The state a new shell (``bash -c``) starts in: same directory, the exported variables, no directory stack.

    A variable set in this command reaches the new shell when it is
    exported: marked (``State.exported``) or already in nuh's environment,
    where an assignment changes the exported value. Any other may be
    exported by an rc file nuh never reads, so it holds its value or
    nothing (``State.or_unset``). In the new shell every one is exported.
    """
    hidden = (state.variables().keys() | state.row_names()) - state.exported - os.environ.keys()
    started = replace(state, dirs=(), oldpwd=None).or_unset(hidden)
    return replace(started, exported=frozenset(started.variables().keys() | started.row_names()))
