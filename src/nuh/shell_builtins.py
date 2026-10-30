"""The builtins that change shell state: cd, chdir, pushd, popd, read, eval and source.

Each returns an ``Outcome``: the state after it succeeds and after it fails.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

from nuh import exec_source, shell_program_text, word_quoting
from nuh.shell_state import _VAR_REF_RE, StageLike, State


# --- cd, pushd, popd ---------------------------------------------------------

_CD_FLAG_RE = re.compile(r"-[LPeqs@]+")  # bash -L -P -e -@, zsh -q -s


def _cd_target(args: list[str]) -> tuple[str, bool] | None:
    """``(target, physical)`` of a cd; "" for home; None when nuh cannot read it.

    zsh ``cd old new`` and ``cd ±N`` (directory stack) are unreadable.
    """
    targets: list[str] = []
    physical = False
    options = True
    for arg in args:
        if options and arg == "--":
            options = False
        elif options and _CD_FLAG_RE.fullmatch(arg):
            physical = physical or "P" in arg
        elif options and arg.startswith("-") and arg != "-":
            return None
        else:
            targets.append(arg)
    if len(targets) > 1 or (targets and re.fullmatch(r"[+-]\d+", targets[0])):
        return None
    return (targets[0] if targets else ""), physical


def _destination(target: str, physical: bool, run: State) -> str | None:
    """The directory a cd to *target* enters from *run*, or None when unknown."""
    if target == "":
        return os.path.expanduser("~")
    if target == "-":
        return run.oldpwd
    target = word_quoting.expansion(target)
    if any(ch in target for ch in "`*?[") or "__nuh_psub_" in target:
        return None
    if "$" in target:
        target = _expand_shell_vars(target, run)
        if target is None:
            return None
    expanded = os.path.expanduser(target)
    if expanded.startswith("~"):
        return None  # ~user that does not exist
    if not os.path.isabs(expanded):
        if run.cwd is None or _cdpath_applies(target, run):
            return None
        expanded = os.path.join(run.cwd, expanded)
    expanded = word_quoting.decode(expanded)
    return os.path.realpath(expanded) if physical else os.path.normpath(expanded)


def _expand_shell_vars(target: str, run: State) -> str | None:
    """Expand ``$HOME``, ``$PWD`` and ``$OLDPWD`` in a cd target; None for anything else."""
    values = {"HOME": os.path.expanduser("~"), "PWD": run.cwd, "OLDPWD": run.oldpwd}
    assigned = run.variables()  # set in this command but unresolved: not the shell's own value
    unknown = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal unknown
        name = match.group(1) or match.group(3) or ""
        value = None if name in assigned else values.get(name)
        if value is None or match.group(2):
            unknown = True
            return ""
        return value

    text = _VAR_REF_RE.sub(substitute, target)
    return None if unknown or "$" in text else text


def _cdpath_applies(target: str, run: State) -> bool:
    """True when CDPATH may send a relative cd elsewhere (from c75ab0fa ``_cdpath_may_affect``)."""
    variables = run.variables()
    cdpath = variables["CDPATH"] if "CDPATH" in variables else os.environ.get("CDPATH", "")
    if cdpath == "":
        return False
    return not (target in (".", "..") or target.startswith(("./", "../")))


def _enterable(path: str) -> bool:
    return os.path.isdir(path) and os.access(path, os.X_OK)


Outcome = tuple["State | None", "State | None"]  # (after success, after failure); None: never


def _unknown(run: State) -> State:
    return replace(run, cwd=None, oldpwd=run.cwd)


def _cd(args: list[str], run: State) -> Outcome:
    parsed = _cd_target(args)
    dest = _destination(*parsed, run) if parsed is not None else None
    if dest is None:
        return _unknown(run), run
    return replace(run, cwd=dest, oldpwd=run.cwd), (None if _enterable(dest) else run)


def _pushd(args: list[str], run: State) -> Outcome:
    if not args:
        if not run.dirs:
            return _unknown(run), run
        return replace(run, cwd=run.dirs[-1], oldpwd=run.cwd, dirs=(*run.dirs[:-1], run.cwd)), run
    ok, fail = _cd(args, run)
    if ok is None or ok.cwd is None or args[0].startswith(("+", "-")):
        return replace(_unknown(run), dirs=()), run
    return replace(ok, dirs=(*run.dirs, run.cwd)), fail


def _popd(args: list[str], run: State) -> Outcome:
    if args or not run.dirs:
        return replace(_unknown(run), dirs=()), run
    dest = run.dirs[-1]
    ok = replace(run, cwd=dest, oldpwd=run.cwd, dirs=run.dirs[:-1])
    return ok, (None if dest is not None and _enterable(dest) else run)


# --- read --------------------------------------------------------------------

_READERS = frozenset({"read", "mapfile", "readarray"})
_READ_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\?.*)?", re.DOTALL)  # zsh `read 'name?prompt'`


def _read(args: list[str], run: State) -> Outcome:
    """``read``/``mapfile``: the names it sets hold input nuh never sees (``State.opaque``).

    Every word that could be a name counts, option values (``-p PROMPT``)
    too: marking one more name opaque only makes its path uses ask. With no
    name, read sets ``REPLY`` and mapfile ``MAPFILE``.
    """
    names = {m.group(1) for arg in args if (m := _READ_NAME_RE.fullmatch(arg))} | {"REPLY", "MAPFILE"}
    after = run.with_opaque(names)
    return after, after  # at end of input it fails, the names set to what was left


# --- sourced and eval'd text ------------------------------------------------

_CD_WORD_RE = re.compile(r"(?<![\w-])(?:cd|chdir|pushd|popd)(?![\w-])")
_ASSIGNED_NAME_RE = re.compile(r"(?<![\w$])([A-Za-z_][A-Za-z0-9_]*)\+?=")
_MAX_SOURCE_CHARS = 1_000_000


def _same_shell_text(stage: StageLike, words: list[str], run: State) -> str | None:
    """Text ``eval``/``source`` run in the current shell; None when unreadable."""
    if stage.stdin_ambiguous and words[0] != "eval":
        return None
    if words[0] == "eval":
        text, unreadable = shell_program_text.expand(word_quoting.joined(words[1:]), run)
        return None if unreadable else text
    if stage.script_body is not None:
        return stage.script_body
    operand = next((w for w in words[1:] if not w.startswith("-")), "")
    if operand in ("/dev/stdin", "/dev/fd/0", "/proc/self/fd/0", ""):
        found = exec_source.stdin_program(words, stage.heredoc_literal)
        if found is None or found[1] is None:
            return None
        text, unreadable = shell_program_text.expand(found[1], run)
        return None if unreadable else text
    path = word_quoting.expand_path(operand)
    if not os.path.isabs(path):
        if run.cwd is None:
            return None
        path = os.path.join(run.cwd, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(_MAX_SOURCE_CHARS)
    except OSError:
        # Unreadable means unknown effects; the caller takes the strict
        # reading (any cd, any variable), so nothing is hidden.
        return None


def _same_shell(stage: StageLike, words: list[str], run: State) -> Outcome:
    """Outcome of ``eval``/``source``: a cd in the text makes the cwd unknown, its assignments unresolved.

    Unreadable text may do either.
    """
    text = _same_shell_text(stage, words, run)
    ok = run
    if text is None or _CD_WORD_RE.search(text):
        ok = _unknown(run)
    names = run.variables() if text is None else _ASSIGNED_NAME_RE.findall(text)
    return ok.with_vars({name: None for name in names}), run
