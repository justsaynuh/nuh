"""Passthrough wrappers stripped off a command: command, env, time, nohup, stdbuf, setsid, timeout.

Each ``_strip_*`` returns the command the wrapper runs, or None when the
tokens are not that wrapper or use a flag nuh does not know (fail-closed).
The scheduling wrappers (nice, ionice, taskset, chrt, prlimit) are in
``sched_wrappers``.
"""

import os
from dataclasses import dataclass, field

from nuh import zsh_syntax
from nuh.bash_types import _MAX_UNWRAP_DEPTH
from nuh.floor_rules import is_env_assignment as _is_env_assignment
from nuh.sched_wrappers import (_strip_chrt_wrapper, _strip_ionice_wrapper, _strip_nice_wrapper, _strip_prlimit_wrapper,
                                _strip_taskset_wrapper)


def _strip_command_builtin(tokens: list[str]) -> list[str] | None:
    """Strip 'command' builtin wrapper, returning inner tokens.

    Returns None for introspection forms (-v/-V) or bare 'command'."""
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        if "v" in flag or "V" in flag:
            return None  # Introspection
        if flag == "-p":
            i += 1
            continue
        break
    if i < len(tokens):
        return tokens[i:]
    return None


_ENV_NOARG_FLAGS = {"-i", "--ignore-environment"}
_ENV_ARG_FLAGS = {"-u", "--unset", "-C", "--chdir", "--argv0"}
_ENV_ARG_FLAG_PREFIXES = ("--unset=", "--chdir=", "--argv0=")


@dataclass
class EnvSettings:
    """What an ``env`` wrapper does to the environment of the command it runs (``tokens[start:]``)."""

    start: int
    cleared: bool = False                                 # -i: nothing inherited
    unset: list[str] = field(default_factory=list)        # -u NAME
    assigned: list[str] = field(default_factory=list)     # NAME=value words, in order
    chdir: str | None = None                             # -C/--chdir; None leaves cwd unchanged


def env_settings(tokens: list[str]) -> EnvSettings | None:
    """The ``env`` wrapper's settings, or None when *tokens* are not env or use a flag nuh does not know."""
    if not tokens or os.path.basename(tokens[0]) != "env":
        return None
    found = EnvSettings(start=1)
    n = len(tokens)
    while found.start < n:
        tok = tokens[found.start]
        if tok == "--":
            found.start += 1
            break
        if _is_env_assignment(tok):
            found.assigned.append(tok)
        elif tok in _ENV_NOARG_FLAGS:
            found.cleared = True
        elif tok in _ENV_ARG_FLAGS:
            if found.start + 1 >= n:
                return None
            if tok in ("-u", "--unset"):
                found.unset.append(tokens[found.start + 1])
            elif tok in ("-C", "--chdir"):
                found.chdir = tokens[found.start + 1]
            found.start += 1
        elif any(tok.startswith(prefix) for prefix in _ENV_ARG_FLAG_PREFIXES):
            if tok.startswith("--unset="):
                found.unset.append(tok.split("=", 1)[1])
            elif tok.startswith("--chdir="):
                found.chdir = tok[len("--chdir="):]
        elif tok.startswith("-"):
            return None
        else:
            break
        found.start += 1
    return found


def _strip_env_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip env wrapper and supported flags, returning inner command tokens."""
    found = env_settings(tokens)
    inner = tokens[found.start:] if found is not None else []
    return inner if inner else None


def _strip_time_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip time wrapper and supported flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "time":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok == "-p":
            i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_nohup_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip nohup wrapper, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "nohup":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_stdbuf_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip stdbuf wrapper and supported flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "stdbuf":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-i", "-o", "-e"}:
            i += 2
            continue

        if tok.startswith(("-i", "-o", "-e")) and len(tok) > 2:
            i += 1
            continue

        if tok.startswith(("--input=", "--output=", "--error=")):
            i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_setsid_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip setsid wrapper and supported flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "setsid":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-c", "-f", "-w", "--ctty", "--fork", "--wait"}:
            i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_timeout_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip timeout wrapper and supported flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "timeout":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-f", "-p", "-v", "--foreground", "--preserve-status", "--verbose"}:
            i += 1
            continue

        if tok in {"-k", "-s"}:
            if i + 1 >= n:
                return None
            i += 2
            continue

        if tok.startswith(("-k", "-s")) and len(tok) > 2:
            i += 1
            continue

        if tok.startswith(("--kill-after=", "--signal=")):
            i += 1
            continue

        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2:
            cluster = tok[1:]
            j = 0
            while j < len(cluster):
                flag = cluster[j]
                if flag in {"f", "p", "v"}:
                    j += 1
                    continue
                if flag in {"k", "s"}:
                    if j + 1 == len(cluster):
                        if i + 1 >= n:
                            return None
                        i += 2
                    else:
                        i += 1
                    break
                return None
            else:
                i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    if i >= n:
        return None

    i += 1  # duration
    if i < n and tokens[i] == "--":
        i += 1

    inner = tokens[i:]
    return inner if inner else None


def _strip_passthrough_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip one supported passthrough wrapper layer, if present."""
    if not tokens:
        return None

    if tokens[0] == "command":
        return _strip_command_builtin(tokens)

    return (
        zsh_syntax.strip_precommand(tokens)
        or _strip_env_wrapper(tokens)
        or _strip_nice_wrapper(tokens)
        or _strip_time_wrapper(tokens)
        or _strip_nohup_wrapper(tokens)
        or _strip_stdbuf_wrapper(tokens)
        or _strip_setsid_wrapper(tokens)
        or _strip_timeout_wrapper(tokens)
        or _strip_ionice_wrapper(tokens)
        or _strip_taskset_wrapper(tokens)
        or _strip_chrt_wrapper(tokens)
        or _strip_prlimit_wrapper(tokens)
    )


def _peel_wrappers(tokens: list[str]) -> list[str]:
    """Strip passthrough wrappers (env, timeout, nice, command, …) off *tokens*."""
    for _ in range(_MAX_UNWRAP_DEPTH):
        inner = _strip_passthrough_wrapper(tokens)
        if not inner:
            break
        tokens = inner
    return tokens
