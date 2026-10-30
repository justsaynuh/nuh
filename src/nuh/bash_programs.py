"""The program text a Bash stage hands an interpreter or shell.

A heredoc a shell runs, the code ``source``/``.`` runs, ``eval`` with no
operand, and the program an interpreter reads (argv, stdin, a pipe), as
the remote-code checks need it.
"""

import os
import re
from dataclasses import replace

from nuh import exec_source, shell_state, shell_unresolved, shell_wrapper, taxonomy, word_quoting
from nuh.bash_policy import _StageProgram
from nuh.bash_subs import _PSUB_INDEX_RE, _PSUB_PREFIX
from nuh.bash_types import _shell_words, Stage
from nuh.passthrough_wrappers import _peel_wrappers


def _program_text(tokens: list[str]) -> tuple[str, str]:
    """Return (interpreter, program text) for a stage; ("", "") if not an interpreter.

    Unrecognized argv for an exec sink yields every argument, since any of
    them might be the program.
    """
    tokens = _peel_wrappers(tokens)
    if not tokens:
        return "", ""
    source = exec_source.program_source(tokens)
    if source is not None:
        return tokens[0], source.value
    if taxonomy.is_exec_sink(tokens[0]):
        return tokens[0], " ".join(tokens[1:])
    return "", ""


def _command_name_sub(tokens: list[str]) -> int | None:
    """Index of the substitution whose output starts the command name, else None.

    ``$(curl …)`` in command position runs whatever the substitution prints.
    """
    head = _peel_wrappers(tokens)
    match = _PSUB_INDEX_RE.match(head[0]) if head else None
    return int(match.group(1)) if match else None


def _reads_program_from_pipe(tokens: list[str]) -> bool:
    """True for an exec sink that may take its program from stdin."""
    return (bool(tokens) and (taxonomy.is_exec_sink(tokens[0]) or tokens[0] in _SOURCE_COMMANDS)
            and exec_source.reads_program_from_pipe(tokens, _PSUB_PREFIX))


_STDIN_HEREDOC_RE = re.compile(r"^0?<<(?!<)")


def _stdin_readings(stage: Stage) -> list[Stage]:
    """Inspect each stdin program separately: zsh MULTIOS can run earlier inputs too.

    Never join bodies: an unmatched quote in one must not hide code in another.
    Nonzero descriptors consume their own heredoc body but supply no stdin program.
    """
    words = _shell_words(stage)
    source = exec_source.program_source(words)
    if source is None or source.kind != exec_source.STDIN:
        return []
    argv: list[str] = []
    inputs: list[tuple[list[str], str]] = []
    bodies = iter(stage.heredoc_bodies)
    index = 0
    while index < len(words):
        word = words[index]
        match = re.fullmatch(r"(\d*)(<<<|<<-?|<&|<>|<)(.*)", word_quoting.encoding(word) or word)
        index += 1
        if match is None:
            argv.append(word)
            continue
        redirect = [word]
        if not match.group(3) and index < len(words):
            redirect.append(words[index])
            index += 1
        body = next(bodies, "") if match.group(2) in ("<<", "<<-") else ""
        if match.group(1) in ("", "0"):
            inputs.append((redirect, body))
    return [replace(stage, tokens=argv + redirect, heredoc_literal=body, heredoc_bodies=(),
                    body_words=0, stdin_ambiguous=False) for redirect, body in inputs]


def _heredoc_shell_program(stage: Stage) -> str | None:
    """Heredoc body a shell runs as its program (``bash <<'EOF' … EOF``), else None.

    A quoted delimiter keeps the outer shell from expanding the body, but the
    inner shell still executes it, so it is unwrapped like ``bash <<< "…"``.
    """
    tokens = _shell_words(stage)
    if not stage.heredoc_literal or not tokens:
        return None
    if exec_source.interpreter_name(tokens[0]) not in shell_wrapper._SHELL_WRAPPERS:
        return None
    if not any(_STDIN_HEREDOC_RE.match(tok) for tok in tokens[1:]):
        return None
    source = exec_source.program_source(tokens)
    if source is None or source.kind != exec_source.STDIN:
        return None
    found = exec_source.stdin_program(tokens, stage.heredoc_literal)
    return found[1] if found is not None else None


# Adapted from manuelschipper/nah (MIT), deca249f: `source` and `.` run a file
# in the current shell, so they are script execution with content inspection.
_SOURCE_COMMANDS = frozenset({"source", "."})


def _sourced_shell_code(stage: Stage) -> str | None:
    """Shell code a ``source``/``.`` stage runs from text nuh has, else None.

    ``source /dev/stdin <<'EOF' … EOF``, ``. /dev/stdin <<< "…"`` and a
    script written by a heredoc just before run that text in the current
    shell, so it is unwrapped like ``bash <<'EOF'``.
    """
    if not stage.tokens or stage.tokens[0] not in _SOURCE_COMMANDS:
        return None
    if stage.script_body is not None:
        return stage.script_body
    found = exec_source.stdin_program(_shell_words(stage), stage.heredoc_literal)
    return found[1] if found is not None else None


def _source_program(stage: Stage) -> "tuple[_StageProgram, str]":
    """``(program, unreadable reason)`` of a ``source``/``.`` stage; the reason is "" when readable.

    The program is the sourced file (``source x.sh``, ``. /dev/stdin < x.sh``).
    Shell code from a heredoc or here-string was unwrapped before this
    (``_sourced_shell_code``). Without a slash, bash looks the name up on
    PATH before the working directory, so a name found there is unreadable.
    """
    found = exec_source.program_source(_shell_words(stage))
    unknown: _StageProgram = (None, None, exec_source.FROM_ARGUMENT)
    if found is None:
        return unknown, "no file nuh can read"
    if found.kind == exec_source.STDIN:
        piped = exec_source.stdin_program(_shell_words(stage), stage.heredoc_literal)
        if piped is None or piped[0] is None:
            return unknown, "program on stdin nuh cannot read"
        return (piped[0], None, exec_source.FROM_STDIN), ""
    operand = found.value
    if _PSUB_PREFIX in operand:
        return unknown, "program comes from command output"
    state = shell_state.current()
    unresolved = shell_unresolved.unresolved_uses(operand, state)
    if unresolved:
        return unknown, shell_unresolved.unresolved_reason(unresolved, state)
    operand = word_quoting.expand_path(operand)  # active variables left are in nuh's environment ($VIRTUAL_ENV)
    if "/" not in operand and _on_search_path(operand):
        return unknown, f"{operand} is looked up on PATH"
    return (os.path.join(os.getcwd(), operand), None, exec_source.FROM_ARGUMENT), ""


_EVAL_STDIN_WORD_RE = re.compile(r"^\d*<<[-<]?(.*)$", re.DOTALL)


def _eval_runs_nothing(words: list[str]) -> bool:
    """True when ``eval`` has no operand once its heredocs and here-strings are dropped.

    eval never reads stdin, so ``eval <<EOF … EOF`` runs nothing. Any other
    word, including a ``<file`` redirect, keeps the normal unwrapping.
    """
    i = 1
    while i < len(words):
        match = _EVAL_STDIN_WORD_RE.match(words[i])
        if match is None:
            return False
        i += 1 if match.group(1) else 2
    return True


def _on_search_path(name: str) -> bool:
    """True when a file *name* exists in a ``$PATH`` directory."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    return any(os.path.isfile(os.path.join(d, name)) for d in dirs)
