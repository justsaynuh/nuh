"""The walk: each stage of a command given the shell state it runs in (see ``shell_state``).

Frames follow subshells and and-or lists, loops their bodies; the builtins
that change state are in ``shell_builtins``.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from nuh import shell_setters
from nuh.shell_builtins import _cd, _popd, _pushd, _read, _READERS, _same_shell, Outcome
from nuh.shell_expand import _assign_one, assignments, expand_target, expand_words, word_variants
from nuh.shell_state import _drop, _MAX_ROWS, StageLike, State, Step, SubValue


# --- the walk ---------------------------------------------------------------

_NEVER_RETURN = frozenset({"exit", "return", "logout"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)


def merge(a: State | None, b: State | None) -> State | None:
    """A state that holds whichever of *a* and *b* is real (None: never reached)."""
    if a is None or a == b:
        return b
    if b is None:
        return a
    va, vb = a.variables(), b.variables()
    names = va.keys() | vb.keys()
    # A loop variable bound on one side only may be unset on the other: left as written.
    common = a.row_names() & b.row_names()
    rows = _drop(a.rows, a.row_names() - common) + _drop(b.rows, b.row_names() - common)
    return State(
        cwd=a.cwd if a.cwd == b.cwd else None,
        oldpwd=a.oldpwd if a.oldpwd == b.oldpwd else None,
        dirs=a.dirs if a.dirs == b.dirs else (),
        vars=tuple(sorted((n, va[n] if n in va and n in vb and va[n] == vb[n] else None) for n in names)),
        rows=tuple(dict.fromkeys(rows)) if common else (),
        opaque=(a.opaque | b.opaque) - names,
        exported=a.exported & b.exported,
    )


def either(states: Sequence[State]) -> State:
    """A state holding each of *states* as one reading: where their variables differ, every value, one per row.

    For readings of one command that differ by shell (``command export X=v``
    assigns in bash, not in zsh): a later use is checked with each value.
    A value nuh cannot know in any reading, or more than _MAX_ROWS rows,
    leaves the name unresolved, as ``merge`` does.
    """
    unique = list(dict.fromkeys(states))
    merged = functools.reduce(merge, unique)
    if len(unique) == 1 or merged is None:
        return unique[0]
    readings = [(s, {**s.variables(), **dict(row)}) for s in unique for row in (s.rows or ((),))]

    def value(state: State, reading: dict[str, str | None], name: str) -> str | None:
        return reading[name] if name in reading else state.known_value(name)  # not set there: as before

    names = set().union(*(reading for _s, reading in readings)) - merged.opaque
    settled: dict[str, str | None] = {}
    columns: list[str] = []
    for name in sorted(names):
        values = {value(s, reading, name) for s, reading in readings}
        if None in values:
            settled[name] = None
        elif len(values) == 1:
            settled[name] = values.pop()
        else:
            columns.append(name)
    rows = tuple(dict.fromkeys(tuple((name, value(s, reading, name) or "") for name in columns)
                               for s, reading in readings))
    if len(rows) > _MAX_ROWS:
        settled.update(dict.fromkeys(columns))
        columns, rows = [], ()
    return replace(merged, vars=tuple(sorted(settled.items())), rows=rows if columns else ())


@dataclass
class _Frame:
    """One shell (the command, or a subshell): the and-or list being followed."""

    cur: State | None              # state the next and-or list starts in
    entered: State | None = None   # the state a subshell was entered in
    ok: State | None = None        # after the list so far succeeded
    fail: State | None = None      # after it failed
    prev_op: str = ""              # operator before the next element
    unit_op: str = ""              # operator before the current pipeline
    unit_run: State | None = None  # state the current pipeline's elements run in
    list_start: State | None = None


@dataclass
class _Loop:
    """A ``for`` loop whose body the walk is in."""

    scope: int              # the scope id of its body (StageLike.scopes)
    before: State           # the state it started in
    names: frozenset[str]   # variables its body assigns
    frames: int             # shells open when it started


class _Walker:
    def __init__(self, stages: Sequence[StageLike], start: State, sub_value: SubValue | None = None) -> None:
        self.stages = stages
        self.frames = [_Frame(cur=start)]
        self.last = start  # last reachable state, for stages that never run
        self.sub_value = sub_value
        self.loops: list[_Loop] = []

    def step(self, index: int) -> Step:
        stage = self.stages[index]
        self._leave_loops(stage.scopes)
        for _ in range(stage.opens):
            entered = self._begin(self.frames[-1])
            self.frames.append(_Frame(cur=entered, entered=entered))
        frame = self.frames[-1]
        run = self._begin(frame)
        if run is not None and stage.loop_vars and frame.prev_op != "|":
            run = self._enter_loops(index, run)
            frame.unit_run = run
        if run is not None:
            self.last = run
        targets_state = run if run is not None else self.last
        words = stage.tokens[:len(stage.tokens) - stage.body_words]
        state = targets_state
        spaced: list[str] = []
        expanded, unresolved = (words, []) if assignments(words) is not None else expand_words(words, state, spaced)
        targets = [expand_target(target, targets_state, unresolved) for target in stage.redirect_targets]
        # A loop value in a redirect target needs its readings too; with no command, none names it.
        variants, left = word_variants((expanded or [""]) + targets if targets else expanded, state,
                                       list(dict.fromkeys(spaced)))
        ok, fail = self._effects(stage, words, expanded, run)
        self._end(frame, ok, fail, "" if stage.closes else stage.operator)
        for k in range(stage.closes):
            if len(self.frames) == 1:
                break  # a `)` with no `(`: the shell rejects it
            inner = self.frames.pop()
            op = stage.operator if k == stage.closes - 1 else ""
            # Nothing done inside leaves the subshell.
            self._end(self.frames[-1], inner.entered, inner.entered, op)
        return Step(state, expanded + stage.tokens[len(words):], unresolved + left, variants, targets)

    def _enter_loops(self, index: int, run: State) -> State:
        """*run* as the body of each loop whose header stage *index* follows begins.

        A variable the body assigns (other than the loop's own) holds the
        previous iteration's value when the body starts again, so it is
        unresolved until the body assigns it.
        """
        stage = self.stages[index]
        for scope, group in itertools.groupby(stage.loop_vars, key=lambda binding: binding[2]):
            bindings = list(group)
            names = frozenset(self._body_assignments(index, scope) - {name for name, _values, _s in bindings})
            self.loops.append(_Loop(scope, run, names, len(self.frames)))
            for name, values, _scope in bindings:
                run = run.bind_loop(name, values)
            run = run.with_vars(dict.fromkeys(names)) if names else run
        return run

    def _body_assignments(self, index: int, scope: int) -> set[str]:
        """Names assigned by the stages from *index* on inside *scope* (one loop body)."""
        names: set[str] = set()
        for stage in self.stages[index:]:
            if scope not in stage.scopes:
                break
            words = stage.tokens[:len(stage.tokens) - stage.body_words]
            names |= shell_setters.assigned_names(words)
            if _keeps_prefix(stage, shell_setters.precommands(words)[0]):
                names |= {word.split("=", 1)[0] for word in stage.prefix_env}
        return names

    def _leave_loops(self, scopes: tuple[int, ...]) -> None:
        """Close the loops whose body a command inside *scopes* is outside of (see ``_after_loop``)."""
        while self.loops and self.loops[-1].scope not in scopes:
            loop = self.loops.pop()
            if len(self.frames) < loop.frames:
                continue  # it ran in a subshell that has closed: its changes left with it
            frame = self.frames[-1]
            frame.cur, frame.ok, frame.fail = (_after_loop(loop, s) for s in (frame.cur, frame.ok, frame.fail))

    def _in_loop_body(self, stage: StageLike) -> bool:
        """True for a command directly in the innermost loop's body, not nested in other syntax there."""
        return bool(self.loops) and stage.scopes[-1:] == (self.loops[-1].scope,)

    def _begin(self, frame: _Frame) -> State | None:
        if frame.prev_op == "|":
            return frame.unit_run
        if frame.prev_op not in ("&&", "||"):
            frame.list_start = frame.cur
        frame.unit_op = frame.prev_op
        run = {"&&": frame.ok, "||": frame.fail}.get(frame.prev_op, frame.cur)
        frame.unit_run = run
        return run

    def _end(self, frame: _Frame, ok: State | None, fail: State | None, op: str) -> None:
        if op == "|":
            frame.prev_op = "|"
            return
        if frame.prev_op == "|":
            # Last element of a pipeline: a subshell in bash, the current shell in zsh.
            ok, fail = merge(frame.unit_run, ok), merge(frame.unit_run, fail)
        if frame.unit_run is None:
            ok = fail = None
        if frame.unit_op == "&&":
            frame.ok, frame.fail = ok, merge(frame.fail, fail)
        elif frame.unit_op == "||":
            frame.ok, frame.fail = merge(frame.ok, ok), fail
        else:
            frame.ok, frame.fail = ok, fail
        frame.prev_op = op
        if op not in ("&&", "||"):
            frame.cur = frame.list_start if op == "&" else merge(frame.ok, frame.fail)

    def _effects(self, stage: StageLike, words: list[str], expanded: list[str], run: State | None) -> Outcome:
        if run is None:
            return None, None
        if words and all(_ASSIGNMENT_RE.fullmatch(word) for word in words):
            assigned_state = run
            for name, value in assignments(words) or []:  # left to right: `A=1 B=$A` sets B to 1
                assigned_state = _assign_one(assigned_state, name, value, self.sub_value)
            if stage.compound and not self._in_loop_body(stage):
                return merge(run, assigned_state), run
            return assigned_state, None
        cmd, partial = shell_setters.precommands(expanded)
        if not cmd:
            return run, run
        if cmd[0] in _NEVER_RETURN:
            return None, None
        call = shell_setters.parse(cmd)
        if call is not None:
            # The words as written, when they name the same builtin: each reading expands the values itself.
            written = shell_setters.parse(shell_setters.precommands(words)[0])
            ok, fail = self._setter(stage, written if written is not None and written.name == call.name else call,
                                    run, partial)
            if stage.compound and not self._in_loop_body(stage):
                return merge(run, ok), run
            return ok, fail
        ok, fail = _builtin(stage, cmd, run)
        if _keeps_prefix(stage, cmd):
            # POSIX mode keeps the prefix assignment after a special builtin; bash and zsh do not.
            kept_ok, kept_fail = _builtin(stage, cmd, shell_setters.prefixed(run, stage.prefix_env, self.sub_value))
            ok, fail = _either(ok, kept_ok), _either(fail, kept_fail)
        if partial:
            ok, fail = _either(ok, run), _either(fail, run)  # one shell runs nothing (zsh `command cd`)
        if stage.compound:
            return merge(run, ok), run
        return ok, fail

    def _setter(self, stage: StageLike, call: shell_setters.Call, run: State, partial: bool) -> Outcome:
        """The state after setter *call*: every reading bash and zsh (and POSIX sh) give it, kept (``either``).

        Without prefix assignments: its values expanded before any is set.
        With them: bash expands before the prefix and keeps it only for a
        name the builtin exports (``X=v export X``), POSIX sh keeps it, zsh
        expands after it. A shell that runs nothing (*partial*: ``command
        export`` in zsh; ``local`` outside a function in bash) leaves *run*.
        """
        readings = [shell_setters.apply(run, call, run, self.sub_value)]
        if stage.prefix_env:
            with_prefix = shell_setters.prefixed(run, stage.prefix_env, self.sub_value)
            readings += [shell_setters.apply(with_prefix, call, run, self.sub_value),
                         shell_setters.apply(run, call, with_prefix, self.sub_value)]
        idle = partial or call.name == "local"
        if idle:
            readings.append(run)
        after = either(readings)
        return after, (after if idle else None)  # where nothing ran, it fails: still each reading


def _keeps_prefix(stage: StageLike, cmd: list[str]) -> bool:
    """True when *stage*'s prefix assignments may stay set after its command: a special builtin (POSIX mode)."""
    return bool(stage.prefix_env) and bool(cmd) and cmd[0] in shell_setters.SPECIAL


def _either(*states: State | None) -> State | None:
    """``either`` of the reachable *states* (None: none is)."""
    reached = [s for s in states if s is not None]
    return either(reached) if reached else None


def _builtin(stage: StageLike, cmd: list[str], run: State) -> Outcome:
    """The outcome of *cmd* (precommands stripped) run in *run*: the builtins that change state, else none."""
    handler = {"cd": _cd, "chdir": _cd, "pushd": _pushd, "popd": _popd}.get(cmd[0])
    if handler is not None:
        return handler(cmd[1:], run)
    if cmd[0] in ("eval", "source", "."):
        return _same_shell(stage, cmd, run)
    if cmd[0] in _READERS:
        return _read(cmd[1:], run)
    if cmd[0] == "unset":
        after = shell_setters.unset(run, cmd[1:])
        return after, after
    return run, run


def _after_loop(loop: _Loop, end: State | None) -> State | None:
    """The state after *loop*, from *end* (where its body left off): what the body assigned may not have run.

    A variable the body assigned keeps its value only when it is the one it
    had before the loop; any other is unresolved. The loop variable keeps
    every value it took.
    """
    if end is None:
        return None
    before, after = loop.before.variables(), end.variables()
    maybe = [name for name in loop.names if name not in before or name not in after or before[name] != after[name]]
    return end.with_vars(dict.fromkeys(maybe)) if maybe else end


def walk(stages: Sequence[StageLike], start: State, sub_value: SubValue | None = None) -> list[Step]:
    """The state each of *stages* runs in, starting from *start*.

    A change made by a stage marked ``compound`` may not run, except an
    assignment directly in a ``for`` loop's body: later commands in the body
    see it, each with the loop values it was computed from; after the loop it
    may not have run (see the module docstring). *sub_value* reads what a
    substitution assigned whole prints.
    """
    walker = _Walker(stages, start, sub_value)
    return [walker.step(index) for index in range(len(stages))]
