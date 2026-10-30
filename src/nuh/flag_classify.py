"""Phase 2 flag classifiers of ``taxonomy.classify_tokens``: commands whose flags decide the action type.

find, sed, awk, tar, global package installs and interpreter + script
execution. git is in ``git_classify``, HTTP clients in ``http_classify``.
Each returns an action type, or None to fall through to prefix matching.
"""

import os
import re

from nuh import awk_prog, operands
from nuh.taxonomy import (_INLINE_FLAGS, _MODULE_FLAGS, _VALUE_FLAGS, classify_tokens, FILESYSTEM_DELETE,
                          FILESYSTEM_READ, FILESYSTEM_WRITE, LANG_EXEC, UNKNOWN)


# Global-install flags that escalate to unknown (ask).
_GLOBAL_INSTALL_FLAGS = {"-g", "--global", "--system", "--target", "--root"}
_GLOBAL_INSTALL_CMDS = {"npm", "pnpm", "bun", "pip", "pip3", "cargo", "gem"}


# Script execution detection — interpreters and their flags.
_SCRIPT_INTERPRETERS = {
    "python", "python3", "node", "ruby", "perl",
    "bash", "sh", "dash", "zsh", "php", "tsx",
}


# Script file extensions for shebang/extension detection.
_SCRIPT_EXTENSIONS = {".py", ".js", ".rb", ".sh", ".pl", ".ts", ".php", ".tsx"}


# find actions that write the file they name.
_FIND_WRITE_FLAGS = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})


def _classify_find(
    tokens: list[str],
    *,
    global_table: list | None = None,
    builtin_table: list | None = None,
    project_table: list | None = None,
    profile: str = "full",
) -> str | None:
    """Special classifier for find: -delete deletes; -fprint and the like write; an -exec payload is its own type.

    The Bash classifier classifies -exec payloads as full commands first
    (``bash_stage._classify_find_exec``); this table answer is for callers that
    only have tokens. An unknown or missing payload is unknown, never a delete.
    """
    if not tokens or tokens[0] != "find":
        return None
    if "-delete" not in tokens and _FIND_WRITE_FLAGS.intersection(tokens):
        return FILESYSTEM_WRITE
    for i, tok in enumerate(tokens[1:], start=1):
        if tok == "-delete":
            return FILESYSTEM_DELETE
        if tok in ("-exec", "-execdir", "-ok", "-okdir"):
            inner_tokens = _extract_find_exec_tokens(tokens, i + 1)
            if not inner_tokens:
                return UNKNOWN
            return classify_tokens(
                inner_tokens,
                global_table=global_table,
                builtin_table=builtin_table,
                project_table=project_table,
                profile=profile,
            )
    return FILESYSTEM_READ


def _extract_find_exec_tokens(tokens: list[str], start: int) -> list[str]:
    """Extract the command payload following find -exec/-execdir/-ok until ; or +."""
    inner: list[str] = []
    for tok in tokens[start:]:
        if tok in (";", "+"):
            break
        inner.append(tok)
    return inner


def _classify_sed(tokens: list[str]) -> str | None:
    """Flag-dependent: sed -i/-I → filesystem_write; else → filesystem_read."""
    if not tokens or tokens[0] != "sed":
        return None
    for tok in tokens[1:]:
        if tok == "--":
            break
        # -i/-I or -i.bak/-I.bak (GNU lowercase, BSD uppercase)
        if tok == "-i" or tok.startswith("-i") or tok == "-I" or tok.startswith("-I"):
            return FILESYSTEM_WRITE
        # --in-place or --in-place=.bak (GNU long form)
        if tok.startswith("--in-place"):
            return FILESYSTEM_WRITE
        # Combined short flags: -ni, -nI, -ein, etc.
        if tok.startswith("-") and not tok.startswith("--") and ("i" in tok or "I" in tok):
            return FILESYSTEM_WRITE
    return FILESYSTEM_READ


def _classify_awk(tokens: list[str]) -> str | None:
    """Flag-dependent: awk with system()/getline/pipes → lang_exec.

    Output redirection (``print > "f"``) is a file write, not code: the Bash
    classifier checks its target (``awk_prog``).
    """
    if not tokens or tokens[0] not in ("awk", "gawk", "mawk", "nawk"):
        return None
    for tok in operands.script_texts(tokens):
        if (re.search(r"\bsystem\s*\(", tok) or any(p in tok for p in ("| getline", "|&", "| \""))
                or awk_prog.reach(tok).pipes):
            return LANG_EXEC
    return None


def _classify_tar(tokens: list[str]) -> str | None:
    """Flag-dependent: tar mode detection. Write takes precedence. Default: write."""
    if not tokens or tokens[0] != "tar":
        return None
    found_read = False
    found_write = False
    args = tokens[1:]
    if not args:
        return FILESYSTEM_WRITE  # Conservative default
    # Check if first arg is a bare mode string (no leading dash): tf, czf, xf
    first = args[0]
    if first and not first.startswith("-"):
        if any(c in first for c in "cxru"):
            found_write = True
        elif "t" in first:
            found_read = True
    # Check all flag arguments
    for tok in args:
        if tok.startswith("-") and len(tok) > 1 and tok[1] != "-":
            # Short flags: -tf, -czf, -xf, etc.
            letters = tok[1:]
            if "t" in letters:
                found_read = True
            if any(c in letters for c in "cxru"):
                found_write = True
        elif tok.startswith("--"):
            if tok == "--list":
                found_read = True
            if tok in ("--create", "--extract", "--append", "--update",
                       "--get", "--delete"):
                found_write = True
    if found_write:
        return FILESYSTEM_WRITE
    if found_read:
        return FILESYSTEM_READ
    return FILESYSTEM_WRITE  # Conservative default


def _classify_global_install(tokens: list[str]) -> str | None:
    """Flag-dependent: global-install flags escalate to unknown (ask)."""
    if not tokens or tokens[0] not in _GLOBAL_INSTALL_CMDS:
        return None
    for tok in tokens[1:]:
        if tok in _GLOBAL_INSTALL_FLAGS:
            return UNKNOWN
        if tok.startswith(("--global=", "--system=", "--target=", "--root=")):
            return UNKNOWN
        if tokens[0] in {"pip", "pip3"} and tok == "-t":
            return UNKNOWN
    return None


def _classify_script_exec(tokens: list[str]) -> str | None:
    """Flag-dependent: detect interpreter + script file execution → lang_exec.

    Returns LANG_EXEC when a known interpreter is invoked with a script file.
    Returns None for bare REPL (python), inline code (python -c), and
    commands handled by the classify table or shell wrapper unwrapping.
    """
    if not tokens:
        return None

    cmd = tokens[0]

    # Shebang / extension detection: ./script.py, /path/to/script.sh
    # Note: classify_tokens() normalizes paths via basename before calling
    # flag classifiers, so ./script.py becomes script.py. Check extension
    # on the (possibly normalized) command name.
    if cmd not in _SCRIPT_INTERPRETERS:
        _, ext = os.path.splitext(cmd)
        if ext in _SCRIPT_EXTENSIONS:
            return LANG_EXEC
        return None

    if len(tokens) < 2:
        return None  # bare REPL (python, node) — fall through

    inline = _INLINE_FLAGS.get(cmd, set())
    module = _MODULE_FLAGS.get(cmd, set())

    # Inline code flags → fall through to classify table (already lang_exec)
    if tokens[1] in inline:
        return None

    # Module mode (python -m) → fall through to Phase 3 classify table.
    # Phase 3 has more specific prefixes (python -m pytest → package_run)
    # and python -m → lang_exec as a catch-all.
    if tokens[1] in module:
        return None

    # First non-flag argument = script file.
    # Skip value-taking flags (e.g. -W ignore) and their arguments.
    value_flags = _VALUE_FLAGS.get(cmd, set())
    skip_next = False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in value_flags:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        return LANG_EXEC  # found script file argument

    return None  # all args are flags — fall through
