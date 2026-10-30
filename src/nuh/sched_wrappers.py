"""Scheduling and resource-limit wrappers stripped off a command: nice, ionice, taskset, chrt, prlimit.

Each ``_strip_*`` returns the command the wrapper runs in command mode, or
None when the tokens are not that wrapper, act on an existing process, or
use a flag nuh does not know (fail-closed).
"""

import os


def _strip_nice_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip nice wrapper and supported flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "nice":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-n", "--adjustment"}:
            i += 2
            continue

        if tok.startswith("--adjustment="):
            i += 1
            continue

        if tok.startswith("-n") and len(tok) > 2:
            i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_ionice_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip ionice wrapper and supported command-mode flags, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "ionice":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-t", "--ignore"}:
            i += 1
            continue

        if tok in {"-c", "-n", "--class", "--classdata"}:
            if i + 1 >= n:
                return None
            i += 2
            continue

        if tok.startswith(("-c", "-n")) and len(tok) > 2:
            i += 1
            continue

        if tok.startswith(("--class=", "--classdata=")):
            i += 1
            continue

        if tok in {"-p", "-P", "-u", "--pid", "--pgid", "--uid"}:
            return None

        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2:
            cluster = tok[1:]
            j = 0
            while j < len(cluster):
                flag = cluster[j]
                if flag == "t":
                    j += 1
                    continue
                if flag in {"c", "n"}:
                    if j + 1 == len(cluster):
                        if i + 1 >= n:
                            return None
                        i += 2
                    else:
                        i += 1
                    break
                if flag in {"p", "P", "u"}:
                    return None
                return None
            else:
                i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None


def _strip_taskset_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip command-mode taskset wrapper, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "taskset":
        return None

    i = 1
    n = len(tokens)
    expect_mask = True
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-p", "--pid", "-a", "--all-tasks"}:
            return None

        if tok in {"-c", "--cpu-list"}:
            if i + 1 >= n:
                return None
            i += 2
            expect_mask = False
            continue

        if tok.startswith("--cpu-list="):
            i += 1
            expect_mask = False
            continue

        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2:
            cluster = tok[1:]
            if cluster[0] == "c" and len(cluster) > 1:
                i += 1
                expect_mask = False
                continue
            return None

        if tok.startswith("-"):
            return None

        break

    if i >= n:
        return None

    if expect_mask:
        i += 1
        if i >= n:
            return None

    if i < n and tokens[i] == "--":
        i += 1

    inner = tokens[i:]
    return inner if inner else None


def _strip_chrt_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip command-mode chrt wrapper, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "chrt":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in {"-a", "--all-tasks", "-m", "--max", "-p", "--pid", "-h", "--help", "-V", "--version"}:
            return None

        if tok in {"-b", "--batch", "-d", "--deadline", "-f", "--fifo", "-i", "--idle", "-o", "--other",
                   "-r", "--rr", "-R", "--reset-on-fork", "-v", "--verbose"}:
            i += 1
            continue

        if tok in {"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"}:
            if i + 1 >= n:
                return None
            i += 2
            continue

        if tok.startswith(("--sched-runtime=", "--sched-period=", "--sched-deadline=")):
            i += 1
            continue

        if tok.startswith("-"):
            return None

        break

    if i >= n:
        return None

    i += 1  # priority
    if i < n and tokens[i] == "--":
        i += 1

    inner = tokens[i:]
    return inner if inner else None


_PRLIMIT_NOARG_FLAGS = {"--noheadings", "--raw", "--verbose"}
_PRLIMIT_ARG_FLAGS = {"-o", "--output"}
_PRLIMIT_PID_FLAGS = {"-p", "--pid"}
_PRLIMIT_RESOURCE_SHORT_FLAGS = {"-c", "-d", "-e", "-f", "-i", "-l", "-m", "-n", "-q", "-r", "-s", "-t", "-u", "-v",
                                 "-x", "-y"}
_PRLIMIT_RESOURCE_LONG_FLAGS = {
    "--core",
    "--data",
    "--nice",
    "--fsize",
    "--sigpending",
    "--memlock",
    "--rss",
    "--nofile",
    "--msgqueue",
    "--rtprio",
    "--stack",
    "--cpu",
    "--nproc",
    "--as",
    "--locks",
    "--rttime",
}


def _strip_prlimit_wrapper(tokens: list[str]) -> list[str] | None:
    """Strip command-mode prlimit wrapper, returning inner command tokens."""
    if not tokens or os.path.basename(tokens[0]) != "prlimit":
        return None

    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        if tok == "--":
            i += 1
            break

        if tok in _PRLIMIT_NOARG_FLAGS:
            i += 1
            continue

        if tok in _PRLIMIT_PID_FLAGS or tok.startswith("--pid="):
            return None

        if tok in _PRLIMIT_ARG_FLAGS | _PRLIMIT_RESOURCE_SHORT_FLAGS | _PRLIMIT_RESOURCE_LONG_FLAGS:
            if i + 1 >= n:
                return None
            i += 2
            continue

        if tok.startswith("--output=") or any(
            tok.startswith(flag + "=") for flag in _PRLIMIT_RESOURCE_LONG_FLAGS
        ):
            i += 1
            continue

        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2:
            flag = tok[:2]
            if flag == "-p":
                return None
            if flag in _PRLIMIT_ARG_FLAGS | _PRLIMIT_RESOURCE_SHORT_FLAGS:
                i += 1
                continue
            return None

        if tok.startswith("-"):
            return None

        break

    inner = tokens[i:]
    return inner if inner else None
