"""git argv: global flags stripped, and the subcommands whose flags decide the action type.

Phase 2 of ``taxonomy.classify_tokens`` for git; prefix matching covers the rest.
"""

from nuh.taxonomy import GIT_DISCARD, GIT_HISTORY_REWRITE, GIT_REMOTE_WRITE, GIT_SAFE, GIT_WRITE


# Git global flags that take a value argument (must consume next token too).
_GIT_VALUE_FLAGS = {"-C", "--git-dir", "--work-tree", "--namespace", "-c", "--config-env"}
_GIT_VALUE_FLAG_PREFIXES = ("--git-dir=", "--work-tree=", "--namespace=", "--exec-path=", "--config-env=")

# Git global flags that are standalone (no value argument).
_GIT_BOOLEAN_FLAGS = {
    "-p", "--paginate", "-P", "--no-pager", "--no-replace-objects",
    "--no-lazy-fetch", "--no-optional-locks", "--no-advice", "--bare",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs",
}


def _is_valid_git_config_key(name: str) -> bool:
    """Return True for plausible git config keys like section.name or section.sub.key."""
    section, dot, remainder = name.partition(".")
    return bool(dot and section and remainder and not remainder.startswith("."))


def _is_valid_git_config_arg(value: str) -> bool:
    """Return True for values accepted by `git -c`, including implicit boolean keys."""
    name = value.split("=", 1)[0]
    return _is_valid_git_config_key(name)


def _is_valid_git_config_env(value: str) -> bool:
    """Return True for NAME=ENVVAR values accepted by --config-env."""
    name, sep, env = value.partition("=")
    return bool(sep and env and _is_valid_git_config_key(name))


def _git_has_short_flag(args: list[str], flag: str) -> bool:
    """Return True if args contain a short git flag, including combined clusters."""
    needle = f"-{flag}"
    for arg in args:
        if arg == needle:
            return True
        if arg.startswith("-") and not arg.startswith("--") and flag in arg[1:]:
            return True
    return False


def _strip_git_global_flags(tokens: list[str]) -> list[str]:
    """Strip git global flags (e.g. -C <dir>, --no-pager) from token list.

    Preserves 'git' as first token followed by the subcommand and its args.
    Malformed value-taking flags stop stripping so classification fails closed.
    """
    result = [tokens[0]]  # keep "git"
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GIT_VALUE_FLAGS:
            if i + 1 >= len(tokens):
                result.extend(tokens[i:])
                break
            if tok == "-c" and not _is_valid_git_config_arg(tokens[i + 1]):
                result.extend(tokens[i:])
                break
            if tok == "--config-env" and not _is_valid_git_config_env(tokens[i + 1]):
                result.extend(tokens[i:])
                break
            i += 2  # skip flag + its value
        elif tok.startswith("--config-env="):
            if not _is_valid_git_config_env(tok.split("=", 1)[1]):
                result.extend(tokens[i:])
                break
            i += 1  # skip =joined config-env value flag
        elif any(tok.startswith(prefix) for prefix in _GIT_VALUE_FLAG_PREFIXES):
            i += 1  # skip =joined value flag
        elif tok in _GIT_BOOLEAN_FLAGS:
            i += 1  # skip flag only
        else:
            # Reached the subcommand — append rest as-is.
            result.extend(tokens[i:])
            break
    return result


def _classify_git(tokens: list[str]) -> str | None:
    """Flag-dependent classification for 12 git subcommands.

    Returns action type or None (fall through to prefix matching).
    Called after _strip_git_global_flags(), so tokens are clean.
    """
    if len(tokens) < 2 or tokens[0] != "git":
        return None

    sub = tokens[1]
    args = tokens[2:]

    if sub == "tag":
        if not args:
            return GIT_SAFE
        has_force = "--force" in args or _git_has_short_flag(args, "f")
        has_delete = "--delete" in args or _git_has_short_flag(args, "d")
        if has_force:
            return GIT_HISTORY_REWRITE
        if has_delete:
            return GIT_DISCARD
        listing_flags = {"-l", "--list", "-v", "--verify", "--contains", "--no-contains",
                         "--merged", "--no-merged", "--points-at"}
        if any(a in listing_flags or a.startswith("-n") for a in args):
            return GIT_SAFE
        return GIT_WRITE

    if sub == "branch":
        if not args:
            return GIT_SAFE
        has_force = "--force" in args or _git_has_short_flag(args, "f")
        has_force_delete = _git_has_short_flag(args, "D")
        has_delete = "--delete" in args or _git_has_short_flag(args, "d")
        if has_force_delete or (has_delete and has_force):
            return GIT_HISTORY_REWRITE
        if has_delete:
            return GIT_DISCARD
        for a in args:
            if a in ("-a", "-r", "--list", "-v", "-vv"):
                return GIT_SAFE
        return GIT_WRITE

    if sub == "config":
        for a in args:
            if a in ("--get", "--list", "--get-all", "--get-regexp"):
                return GIT_SAFE
            if a in ("--unset", "--unset-all", "--replace-all"):
                return GIT_WRITE
        # Count non-flag args: 0-1 = read (get), 2+ = write (set)
        non_flag = [a for a in args if not a.startswith("-")]
        return GIT_SAFE if len(non_flag) <= 1 else GIT_WRITE

    if sub == "reset":
        return GIT_DISCARD if "--hard" in args else GIT_WRITE

    if sub == "push":
        _FORCE_FLAGS = {"--force", "-f", "--force-with-lease", "--force-if-includes"}
        if "--mirror" in args or "--prune" in args:
            return GIT_HISTORY_REWRITE
        if _git_has_short_flag(args, "f") or _git_has_short_flag(args, "d"):
            return GIT_HISTORY_REWRITE
        for a in args:
            if a in _FORCE_FLAGS or a.startswith("--force-with-lease="):
                return GIT_HISTORY_REWRITE
            if a in ("--delete", "-d"):
                return GIT_HISTORY_REWRITE
            # +refspec means force push; :refspec deletes a remote ref.
            if (a.startswith("+") or a.startswith(":")) and len(a) > 1:
                return GIT_HISTORY_REWRITE
        return GIT_REMOTE_WRITE

    if sub == "add":
        return GIT_SAFE if ("--dry-run" in args or _git_has_short_flag(args, "n")) else GIT_WRITE

    if sub == "rm":
        return GIT_WRITE if "--cached" in args else GIT_DISCARD

    if sub == "clean":
        return GIT_SAFE if ("--dry-run" in args or _git_has_short_flag(args, "n")) else GIT_HISTORY_REWRITE

    if sub == "reflog":
        if args and args[0] in ("delete", "expire"):
            return GIT_DISCARD
        return GIT_SAFE

    if sub == "checkout":
        _DISCARD = {".", "--", "HEAD", "--force", "-f", "--ours", "--theirs", "-B"}
        for a in args:
            if a in _DISCARD:
                return GIT_DISCARD
        return GIT_WRITE

    if sub == "switch":
        _DISCARD = {"--discard-changes", "--force", "-f"}
        for a in args:
            if a in _DISCARD:
                return GIT_DISCARD
        return GIT_WRITE

    if sub == "restore":
        return GIT_WRITE if "--staged" in args else GIT_DISCARD

    return None
