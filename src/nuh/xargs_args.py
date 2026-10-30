"""xargs argv: the command it runs (FD-089), and whether that command deletes."""

import os

from nuh import found_files


# xargs flags: bail-out triggers, no-arg flags, arg flags (short prefix → consumes value)
_XARGS_BAILOUT_SHORT = {"-I", "-J", "-a"}
_XARGS_BAILOUT_LONG = {"--replace", "--arg-file"}  # also checked as prefix for =value form
_XARGS_NOARG_SHORT = {"-0", "-o", "-p", "-r", "-t", "-x"}
_XARGS_NOARG_LONG = {"--null", "--interactive", "--no-run-if-empty", "--verbose", "--exit"}
# Short flags that take an argument (next token or glued): -n1, -P 4, -d '\n', etc.
_XARGS_ARG_SHORT = {"-d", "-E", "-L", "-n", "-P", "-R", "-S", "-s"}
_XARGS_ARG_LONG_PREFIX = (
    "--delimiter=", "--max-lines=", "--max-args=", "--max-procs=", "--max-chars=",
)


def _strip_xargs(tokens: list[str]) -> list[str] | None:
    """Strip xargs wrapper and flags, returning inner command tokens (FD-089).

    Returns None if:
    - bare xargs (no inner command)
    - -I/-J/--replace/-a/--arg-file present (placeholder semantics, Phase 2)
    - unrecognized flag (fail-closed → unknown → ask)
    """
    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        # End of options
        if tok == "--":
            i += 1
            break

        # Not a flag → start of inner command
        if not tok.startswith("-"):
            break

        # Bail-out: exact short flags
        if tok in _XARGS_BAILOUT_SHORT:
            return None

        # Bail-out: long flags (exact or =value form)
        for prefix in _XARGS_BAILOUT_LONG:
            if tok == prefix or tok.startswith(prefix + "="):
                return None

        # No-arg flags
        if tok in _XARGS_NOARG_SHORT or tok in _XARGS_NOARG_LONG:
            i += 1
            continue

        # Arg flags: check exact match (consume next token) or glued form
        matched = False
        for flag in _XARGS_ARG_SHORT:
            if tok == flag:
                # Exact: consume next token as value
                i += 2
                matched = True
                break
            if tok.startswith(flag) and len(tok) > len(flag):
                # Glued: -n1, -P4, -d'\n'
                i += 1
                matched = True
                break
        if matched:
            continue

        # Arg long flags with =value
        if any(tok.startswith(p) for p in _XARGS_ARG_LONG_PREFIX):
            i += 1
            continue

        # Unknown flag → fail-closed
        return None

    inner = tokens[i:]
    return inner if inner else None


# Programs that remove the files find hands them.
_DELETE_PROGRAMS = frozenset({"rm", "rmdir", "unlink", "shred"})


def _xargs_deletes(tokens: list[str]) -> bool:
    """True for ``xargs [opts] rm|rmdir|unlink|shred …``."""
    if not tokens or os.path.basename(tokens[0]) != "xargs":
        return False
    inner = _strip_xargs(found_files.xargs_replace(tokens)[1])
    return bool(inner) and os.path.basename(inner[0]) in _DELETE_PROGRAMS
