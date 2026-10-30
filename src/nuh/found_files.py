"""Commands that run the files ``find`` finds or ``xargs`` reads.

``find … -exec {} \\;``, ``-exec bash {} +``, ``-exec sh -c '"$0"' {} \\;`` and
``… | xargs sh`` execute files nuh cannot see until the command runs: the
program is unknown, not a missing script at ``<root>/{}``.
"""

import os
import re

from nuh import taxonomy

PLACEHOLDER = "{}"

_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish"})
# Interpreter flags whose argument is the program, so the file is only data.
_CODE_FLAGS = frozenset({"-c", "-e", "-m", "-E", "--eval", "-r"})
# Interpreter flags that take a value (not the program).
_VALUE_FLAGS = frozenset({"-X", "-W", "-O", "-o", "-I", "--rcfile", "--init-file"})
# Wrappers that run the rest of the line as a command.
_PREFIXES = frozenset({"env", "command", "exec", "nohup", "time"})

_POSITIONAL = r"""["']?\$\{?[0-9@*]"""
# A positional parameter where a command starts, or as a script handed to a runner.
_RUNS_ARG_RE = re.compile(
    r"(?:^|[;&|(\n`]|\b(?:then|do|else|exec|command|eval|source)\s|(?:^|\s)\.\s"
    r"|\b(?:sh|bash|dash|zsh|python[0-9.]*|node|perl|ruby)\s)\s*" + _POSITIONAL)


def runs_found_file(command: list[str], placeholder: str = PLACEHOLDER) -> bool:
    """True when *command* runs the file *placeholder* stands for (program or script position)."""
    command = _strip_prefixes(command)
    if not command:
        return False
    program = command[0]
    if placeholder in program:
        return True
    base = os.path.basename(program)
    if base == "eval":
        return any(placeholder in arg for arg in command[1:])
    if not taxonomy.is_exec_sink(base):
        return False
    args = command[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        if arg in _CODE_FLAGS or (base in _SHELLS and arg.startswith("-") and "c" in arg[1:] and not
                                  arg.startswith("--")):
            code = args[i + 1] if i + 1 < len(args) else ""
            return base in _SHELLS and bool(_RUNS_ARG_RE.search(code))
        i += 2 if arg in _VALUE_FLAGS else 1
    return i < len(args) and placeholder in args[i]


def _strip_prefixes(command: list[str]) -> list[str]:
    """*command* past ``env VAR=x``, ``command``, ``nohup`` and the like."""
    while command and os.path.basename(command[0]) in _PREFIXES:
        rest = command[1:]
        while rest and (rest[0].startswith("-") or "=" in rest[0]):
            rest = rest[2:] if rest[0] in ("-u", "--unset", "-C", "--chdir") else rest[1:]
        command = rest
    return command


def xargs_replace(tokens: list[str]) -> tuple[str | None, list[str]]:
    """(replace string, *tokens* without its ``-I``/``-J``/``--replace``/``-i`` option).

    The replace string is None when xargs appends the input instead.
    """
    out, replace = [tokens[0]], None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if replace is None and tok in ("-I", "-J") and i + 1 < len(tokens):
            replace, i = tokens[i + 1], i + 2
            continue
        if replace is None and tok[:2] in ("-I", "-J") and len(tok) > 2:
            replace = tok[2:]
        elif replace is None and tok in ("--replace", "-i"):
            replace = PLACEHOLDER
        elif replace is None and tok.startswith(("--replace=", "-i")):
            replace = tok.split("=", 1)[1] if tok.startswith("--replace=") else tok[2:]
        elif tok in _XARGS_VALUE_FLAGS and i + 1 < len(tokens):
            out.extend(tokens[i:i + 2])
            i += 2
            continue
        else:
            out.append(tok)
            if not tok.startswith("-") or tok == "--":
                out.extend(tokens[i + 1:])
                break
        i += 1
    return replace, out


_XARGS_VALUE_FLAGS = frozenset({"-d", "-E", "-L", "-n", "-P", "-R", "-S", "-s", "-a"})
