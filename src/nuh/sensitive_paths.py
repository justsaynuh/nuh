"""Sensitive paths: the built-in and configured tables, how a path matches them, and protected writes.

The tables merge the user's config once (``_ensure_sensitive_paths_merged``)
and reset for tests (``reset_sensitive_paths``). ``project_lifts_sensitive``
exempts paths inside the project from the user's own entries (``_lift``).
"""

import os
import sys

from nuh import floor, glob_reach, sensitive_catalog, taxonomy, word_paths, word_quoting
from nuh.path_names import _HOME, _is_under, _spellings, friendly_path, is_nuh_config_path, PROJECT_CONFIG_NAME
from nuh.protected_paths import _agent_wiring_verdict, _GLOBAL_GIT_CONFIGS, _package_verdict, REACH_TARGETS


# Sensitive paths: (resolved_dir, display_name, policy)
# Hook path (~/.claude/hooks) and nuh config (~/.config/nuh) are NOT in this list —
# checked separately via is_hook_path() / is_nuh_config_path() so they survive profile: none.
# These are hardcoded defaults for FD-004. FD-006 makes them configurable.
#
# ~/.ssh is not in this list: the directory and its key material block, its
# host files are protected for writes only (sensitive_catalog, _ssh_narrow).
# Credential entries are floored (rule credential_paths).
_CREDENTIAL_DIR_DEFAULTS: list[tuple[str, str, str]] = [
    (os.path.realpath(os.path.join(_HOME, ".gnupg")), "~/.gnupg", "block"),
    (os.path.realpath(os.path.join(_HOME, ".git-credentials")), "~/.git-credentials", "block"),
    (os.path.realpath(os.path.join(_HOME, ".netrc")), "~/.netrc", "block"),
    (os.path.realpath(os.path.join(_HOME, ".aws")), "~/.aws", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".azure")), "~/.azure", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".config", "gcloud")), "~/.config/gcloud", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".config", "gh")), "~/.config/gh", "ask"),
    # Gitea's CLI keeps bare 40-hex tokens here, the shape of a commit hash: only the path says secret.
    (os.path.realpath(os.path.join(_HOME, ".config", "tea")), "~/.config/tea", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".docker", "config.json")), "~/.docker/config.json", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".terraform.d", "credentials.tfrc.json")),
     "~/.terraform.d/credentials.tfrc.json", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".terraformrc")), "~/.terraformrc", "ask"),
    (os.path.realpath("/etc/shadow"), "/etc/shadow", "block"),
    *sensitive_catalog.CREDENTIAL_DIRS,
]
_SENSITIVE_DIRS: list[tuple[str, str, str]] = [
    *_CREDENTIAL_DIR_DEFAULTS,
    (os.path.realpath(os.path.join(_HOME, ".claude", "settings.json")), "~/.claude/settings.json", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".claude", "settings.local.json")), "~/.claude/settings.local.json", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".codex", "hooks.json")), "~/.codex/hooks.json", "ask"),
    (os.path.realpath(os.path.join(_HOME, ".codex", "config.toml")), "~/.codex/config.toml", "ask"),
    *sensitive_catalog.OTHER_DIRS,
]
_CREDENTIAL_DIRS = frozenset(entry[0] for entry in _CREDENTIAL_DIR_DEFAULTS)

# Basename patterns: (basename or glob, display_name, policy), matched anywhere.
_SENSITIVE_BASENAMES: list[tuple[str, str, str]] = [
    (".env", ".env", "ask"),
    (".env.local", ".env.local", "ask"),
    (".env.production", ".env.production", "ask"),
    (".npmrc", ".npmrc", "ask"),
    (".pypirc", ".pypirc", "ask"),
    *sensitive_catalog.CREDENTIAL_BASENAMES,
]
_CREDENTIAL_BASENAMES = frozenset(entry[0] for entry in _SENSITIVE_BASENAMES)
CREDENTIAL_RULE = "credential_paths"
# Every other sensitive-path match (user entries, the rest of the built-in list).
SENSITIVE_RULE = "sensitive_paths"


# Snapshot of hardcoded defaults for reset (testing).
_SENSITIVE_DIRS_DEFAULTS = list(_SENSITIVE_DIRS)
_SENSITIVE_BASENAMES_DEFAULTS = list(_SENSITIVE_BASENAMES)
_BUILTIN_DIR_PATHS = frozenset(entry[0] for entry in _SENSITIVE_DIRS_DEFAULTS)
_sensitive_paths_merged = False
# The user's own entries project_lifts_sensitive may lift (_liftable_user_dirs); empty when it is off.
_liftable_dirs: frozenset[str] = frozenset()
# The built-in ~/.ssh rule; a sensitive_paths ask or block entry for ~/.ssh replaces it.
_ssh_narrow = True


def check_protected_write(raw: str, *, parents: bool = False, literal: bool = False,
                          package: bool = True, shell_word: bool = True) -> floor.Verdict | None:
    """Floor verdict for modifying *raw*, or None; the strictest when it names several paths.

    Covers agent hook wiring and nuh's config (``agent_hooks``), a
    project's ``.nuh.yaml`` (``project_config``), ``.git/hooks`` and
    ``.git/config`` (``git_exec``) and ssh's config, authorized keys, rc and
    environment files (``credential_paths``, a block whatever ``sensitive_paths``
    says about ``~/.ssh``). With *parents*, renaming, removing or
    re-permissioning a directory above agent wiring counts too. With
    *package*, nuh's own package directory counts (``package_dir``). A
    *shell_word* (not *literal*) also names every path its glob can reach
    (``glob_reach``). No config switches one of these off.
    """
    if not raw:
        return None
    reached = reach(raw)[0] if shell_word and not literal else []
    verdicts = [v for path in dict.fromkeys((*_spellings(raw, literal=literal), *reached))
                if (v := _protected_verdict(path, parents, package))]
    return floor.strictest(verdicts) if verdicts else None


def _protected_verdict(path: str, parents: bool, package: bool) -> floor.Verdict | None:
    """``check_protected_write`` for one absolute *path*."""
    verdict = _agent_wiring_verdict(path, parents) or (_package_verdict(path, parents) if package else None)
    if verdict:
        return verdict
    if os.path.basename(path) == PROJECT_CONFIG_NAME:
        return floor.Verdict(taxonomy.ASK, f"targets nuh project config: {friendly_path(path)}", "project_config")
    reason = sensitive_catalog.git_exec_write_reason(path)
    if reason:
        return floor.Verdict(taxonomy.ASK, reason, "git_exec")
    if path in _GLOBAL_GIT_CONFIGS:
        return floor.Verdict(taxonomy.ASK, f"targets git config: {friendly_path(path)}", "git_exec")
    reason = sensitive_catalog.ssh_write_reason(path)
    if reason:
        # As before ~/.ssh was narrowed: these writes change who logs in or what ssh runs.
        # A user ~/.ssh entry (ask or allow) changes what reads ask, never this.
        return floor.Verdict(taxonomy.BLOCK, reason, CREDENTIAL_RULE)
    return None


def reach(raw: str, *, code: bool = False) -> tuple[list[str], str]:
    """``glob_reach.reach`` for the shell word *raw* against every sensitive and protected place."""
    _ensure_sensitive_paths_merged()
    targets = [*(entry[0] for entry in _SENSITIVE_DIRS), sensitive_catalog.SSH_DIR, *REACH_TARGETS]
    return glob_reach.reach(raw, targets, code=code)


def _liftable_user_dirs(cfg) -> frozenset[str]:
    """The user's own sensitive_paths entries ``project_lifts_sensitive`` may lift; none when it is off.

    Own means set in the global config with ask or block, at a path that is
    not a built-in entry (an override such as ``~/.aws: block`` stays
    built-in) and not in ``~/.ssh``. A project's ``.nuh.yaml`` entries never lift.
    """
    if not cfg.project_lifts_sensitive:
        return frozenset()
    dirs: set[str] = set()
    for path_str, policy in cfg.sensitive_paths_global.items():
        if policy not in ("ask", "block"):
            continue
        resolved = os.path.realpath(os.path.expanduser(str(path_str)))
        if resolved in _BUILTIN_DIR_PATHS or _is_under(resolved, sensitive_catalog.SSH_DIR):
            continue
        dirs.add(resolved)
    return frozenset(dirs)


def _lift(raw: str, spellings: list[str], *, shell_word: bool,
          code: bool = False) -> tuple[frozenset[str], list[str]]:
    """The user entries a path is exempt from under ``project_lifts_sensitive`` (usually none), and its glob matches.

    Only when every spelling of the path (as written and resolved) lies inside
    the project root, and only entries at or above the root: an entry inside
    the project still holds. For a shell word, every path the shell can
    expand it to must lie inside (``word_paths``): an unresolved ``$X`` is
    never exempt, and a glob or brace list only when each match or piece is
    (they come back, so callers check them for the entries that still hold). With
    *code* the word is a string literal of inline program text (``word_paths``).
    """
    if not _liftable_dirs:
        return frozenset(), []
    # Lazy import: the project root is paths' state, and paths' file-tool checks
    # are built on this module, so it cannot be imported here at load time.
    from nuh.paths import get_project_root
    root = get_project_root()
    if not root:
        return frozenset(), []
    named = word_paths.named_paths(raw, code=code) if shell_word else spellings
    if named is None:
        return frozenset(), []
    real_root = os.path.realpath(root)
    if not all(_is_under(path, real_root) for path in (*spellings, *named)):
        return frozenset(), []
    lifted = frozenset(entry for entry in _liftable_dirs if _is_under(real_root, entry))
    return lifted, [path for path in named if path not in spellings]


def _lifted_dirs(raw: str, spellings: list[str], *, shell_word: bool) -> frozenset[str]:
    """The user entries a path is exempt from under ``project_lifts_sensitive`` (see _lift)."""
    return _lift(raw, spellings, shell_word=shell_word)[0]


def _sensitive_match(resolved: str, lifted: frozenset[str] = frozenset()) -> tuple[str, str, bool] | None:
    """(display, policy, is a credential) of the first sensitive entry *resolved* matches.

    The credential flag is set when any credential entry matches, so a
    broader user entry (``~/Documents: ask``) does not unfloor a key file in it.
    Entries in *lifted* (directories, see _lifted_dirs; or basename patterns,
    see user_entries) are skipped.
    """
    _ensure_sensitive_paths_merged()
    if not resolved:
        return None
    first: tuple[str, str] | None = None
    credential = False
    for dir_path, display, policy in _SENSITIVE_DIRS:
        if dir_path in lifted:
            continue
        if _is_under(resolved, dir_path):
            first = first or (display, policy)
            credential = credential or dir_path in _CREDENTIAL_DIRS
    if _ssh_narrow and sensitive_catalog.ssh_read_match(resolved):
        first = first or (sensitive_catalog.SSH_DISPLAY, "block")
        credential = True
    basename = os.path.basename(resolved)
    for name, display, policy in _SENSITIVE_BASENAMES:
        if name not in lifted and sensitive_catalog.basename_matches(basename, name):
            first = first or (display, policy)
            credential = credential or name in _CREDENTIAL_BASENAMES
    return (*first, credential) if first else None


def is_sensitive_name(name: str) -> bool:
    """True when the bare file name *name* matches a sensitive basename (``server.pem``, ``.env``)."""
    _ensure_sensitive_paths_merged()
    return any(sensitive_catalog.basename_matches(name, entry) for entry, _d, _p in _SENSITIVE_BASENAMES)


def is_sensitive(resolved: str) -> tuple[bool, str, str]:
    """Check path against sensitive paths list.

    Returns (matched, pattern_display, policy) where policy is "ask" or "block".
    """
    match = _sensitive_match(resolved)
    if match is None:
        return False, "", ""
    return True, match[0], match[1]


def _split_path_parts(raw: str) -> list[str]:
    """Split a Unix-like path into normalized components.

    This is intentionally string-based so it can reason about wildcard and
    command-substitution-style segments without executing shell syntax.
    """
    return [part for part in raw.split("/") if part and part != "."]


def _home_relative_sensitive_entries(
        lifted: frozenset[str] = frozenset()) -> list[tuple[tuple[str, ...], str, str, bool]]:
    """Return sensitive entries relative to the current home dir, with their credential flag (*lifted* left out)."""
    _ensure_sensitive_paths_merged()
    entries: list[tuple[tuple[str, ...], str, str, bool]] = []
    for resolved, display, policy in _SENSITIVE_DIRS:
        if resolved == _HOME or resolved in lifted:
            continue
        if not resolved.startswith(_HOME + os.sep):
            continue
        rel = os.path.relpath(resolved, _HOME)
        parts = tuple(part for part in rel.split(os.sep) if part and part != ".")
        if parts:
            entries.append((parts, display, policy, resolved in _CREDENTIAL_DIRS))
    return entries


def _check_dynamic_home_sensitive_path(raw: str, lifted: frozenset[str] = frozenset()) -> floor.Verdict | None:
    """Conservatively detect sensitive home-style paths with dynamic user segments.

    Examples:
    - /home/*/.aws/credentials
    - /Users/$(whoami)/.ssh/id_rsa

    This does not execute shell syntax. It only matches sensitive home-relative
    suffixes immediately after a home-style prefix.
    """
    if not raw:
        return None

    expanded = word_quoting.expand_path(raw)
    parts = _split_path_parts(expanded)
    if not parts:
        return None

    tails: list[list[str]] = []
    if parts[0] in ("home", "Users") and len(parts) >= 3:
        tails.append(parts[2:])
    elif parts[0] == "root" and len(parts) >= 2:
        tails.append(parts[1:])

    if not tails:
        return None

    for tail in tails:
        for rel_parts, display, policy, credential in _home_relative_sensitive_entries(lifted):
            if len(tail) >= len(rel_parts) and tuple(tail[:len(rel_parts)]) == rel_parts:
                return floor.Verdict(policy, f"targets sensitive path: {display}",
                                     CREDENTIAL_RULE if credential else SENSITIVE_RULE)
        if _ssh_narrow and tail[:1] == [".ssh"] and sensitive_catalog.ssh_read_match(
                os.path.join(sensitive_catalog.SSH_DIR, *tail[1:])):
            return floor.Verdict(taxonomy.BLOCK, f"targets sensitive path: {sensitive_catalog.SSH_DISPLAY}",
                                 CREDENTIAL_RULE)
    return None


def check_path_basic_raw(raw: str, *, shell_word: bool = True, code: bool = False,
                         exempt: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """Core path check that preserves conservative matching on raw input.

    Both the path as written and its resolved path are checked, so a
    symlinked ``.env`` and a symlink to a key both count. *shell_word*:
    *raw* is a shell word the shell may still expand (globs, variables), not
    a file tool's literal path; *code*: it is a string literal of inline
    program text instead. A shell word or literal is also checked by every
    path its glob can reach (``glob_reach``); one nuh cannot follow asks.
    Entries in *exempt* are skipped (``user_entries`` under ``allow_paths``).
    """
    _ensure_sensitive_paths_merged()
    spellings = _spellings(raw)
    lifted, matches = _lift(raw, spellings, shell_word=shell_word, code=code)
    lifted |= exempt
    reached, unknown = reach(raw, code=code) if shell_word else ([], "")
    verdicts = [v for v in (*(check_path_basic(p, lifted) for p in dict.fromkeys((*spellings, *matches, *reached))),
                            _check_dynamic_home_sensitive_path(raw, lifted)) if v]
    if unknown:
        verdicts.append((taxonomy.ASK, unknown))
    return floor.strictest(verdicts) if verdicts else None


def check_path_basic(resolved: str, lifted: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """Core path check: nuh config → sensitive. Returns (decision, reason) or None.

    Note: hook self-protection (write-block, read-allow) is handled in
    check_path() which knows the tool name. This function is tool-agnostic
    and used by Bash token scanning where reads are fine. A built-in
    credential match carries floor rule ``credential_paths``, any other
    sensitive match ``sensitive_paths``. Sensitive entries in *lifted* are
    skipped (``project_lifts_sensitive``).
    """
    if is_nuh_config_path(resolved):
        return (taxonomy.ASK, f"targets nuh config: {friendly_path(resolved)}")
    match = _sensitive_match(resolved, lifted)
    if match:
        display, policy, credential = match
        rule = CREDENTIAL_RULE if credential else SENSITIVE_RULE
        return floor.Verdict(policy, f"targets sensitive path: {display}", rule)
    return None


def build_merged_sensitive_paths(config_paths: dict[str, str], config_default: str) -> None:
    """Merge user sensitive_paths with hardcoded lists. Modifies _SENSITIVE_DIRS in place.

    A block default raises built-in ask entries before explicit path overrides
    (adapted from manuelschipper/nah (MIT), 0.x c6a39e58); the normal ask
    default preserves built-in hard blocks. Global config can override
    hardcoded policies (e.g., ~/.gnupg block -> ask); an ask or block entry
    for ~/.ssh replaces the built-in ~/.ssh rule with a whole-directory one.
    An allow removes nothing: a built-in entry is a floor (it warns), and
    any other path is not listed.
    """
    global _ssh_narrow
    if config_default == "block":
        _SENSITIVE_DIRS[:] = [(d, display, "block") for d, display, _policy in _SENSITIVE_DIRS]
        _SENSITIVE_BASENAMES[:] = [(n, display, "block") for n, display, _policy in _SENSITIVE_BASENAMES]
    existing_resolved = {entry[0] for entry in _SENSITIVE_DIRS}
    for path_str, policy in config_paths.items():
        expanded = os.path.expanduser(path_str)
        resolved = os.path.realpath(expanded)
        if policy == "allow" and (resolved in _BUILTIN_DIR_PATHS or resolved == sensitive_catalog.SSH_DIR):
            _warn_builtin("sensitive_paths", path_str)
        if policy not in ("ask", "block"):
            continue
        if resolved == sensitive_catalog.SSH_DIR:
            _ssh_narrow = False
        if resolved in existing_resolved:
            # Override existing entry's policy
            for i, (dir_path, display, _old_policy) in enumerate(_SENSITIVE_DIRS):
                if dir_path == resolved:
                    _SENSITIVE_DIRS[i] = (dir_path, display, policy)
                    break
        else:
            display = path_str if path_str.startswith("~") else resolved
            _SENSITIVE_DIRS.append((resolved, display, policy))
            existing_resolved.add(resolved)


def _warn_builtin(key: str, entry: str) -> None:
    """Warn that *key*'s allow for the built-in *entry* is ignored, naming the floor it would lower.

    The built-in entries are floors (``credential_paths``, ``sensitive_paths``):
    config may move one between ask and block, never remove it.
    """
    resolved = os.path.realpath(os.path.expanduser(entry))
    credential = (entry in _CREDENTIAL_BASENAMES or resolved in _CREDENTIAL_DIRS
                  or resolved == sensitive_catalog.SSH_DIR)
    sys.stderr.write(f"nuh: config: {key}: {entry}: allow cannot remove a built-in entry (floor "
                     f"{CREDENTIAL_RULE if credential else SENSITIVE_RULE}); ignored, set ask or block instead\n")


def user_entries() -> frozenset[str]:
    """The sensitive entries the user's config added (directories and basename patterns).

    ``allow_paths`` exempts a path from these only: a built-in entry (or a
    user ``~/.ssh`` entry, which replaces the built-in ~/.ssh rule) still
    holds, since each is a floor.
    """
    _ensure_sensitive_paths_merged()
    dirs = {d for d, _display, _policy in _SENSITIVE_DIRS
            if d not in _BUILTIN_DIR_PATHS and d != sensitive_catalog.SSH_DIR}
    names = {n for n, _display, _policy in _SENSITIVE_BASENAMES if n not in _CREDENTIAL_BASENAMES}
    return frozenset(dirs | names)


def _merge_sensitive_basenames(config: dict) -> None:
    """Merge user sensitive_basenames config into _SENSITIVE_BASENAMES.

    Policies: ask/block = add or override. An allow removes nothing: a
    built-in basename is a floor (it warns), and any other is not listed.
    """
    existing = {e[0] for e in _SENSITIVE_BASENAMES}
    for basename, policy in config.items():
        name = str(basename)
        policy = str(policy)
        if policy == "allow" and name in _CREDENTIAL_BASENAMES:
            _warn_builtin("sensitive_basenames", name)
        elif policy in ("ask", "block"):
            if name in existing:
                for i, (n, d, _) in enumerate(_SENSITIVE_BASENAMES):
                    if n == name:
                        _SENSITIVE_BASENAMES[i] = (n, d, policy)
                        break
            else:
                _SENSITIVE_BASENAMES.append((name, name, policy))
                existing.add(name)


def _ensure_sensitive_paths_merged() -> None:
    """Lazy one-time merge of config sensitive_paths and basenames."""
    global _sensitive_paths_merged
    if _sensitive_paths_merged:
        return
    _sensitive_paths_merged = True
    from nuh.config import get_config  # lazy import to avoid circular
    cfg = get_config()
    global _liftable_dirs
    # Even under profile: none: the built-in entries are floors (credential_paths, sensitive_paths).
    _liftable_dirs = _liftable_user_dirs(cfg)
    build_merged_sensitive_paths(cfg.sensitive_paths, cfg.sensitive_paths_default)
    if cfg.sensitive_basenames:
        _merge_sensitive_basenames(cfg.sensitive_basenames)


def reset_sensitive_paths() -> None:
    """Restore _SENSITIVE_DIRS and _SENSITIVE_BASENAMES to hardcoded defaults (for testing)."""
    global _sensitive_paths_merged, _ssh_narrow, _liftable_dirs
    _sensitive_paths_merged = False
    _ssh_narrow = True
    _liftable_dirs = frozenset()
    _SENSITIVE_DIRS.clear()
    _SENSITIVE_DIRS.extend(_SENSITIVE_DIRS_DEFAULTS)
    _SENSITIVE_BASENAMES.clear()
    _SENSITIVE_BASENAMES.extend(_SENSITIVE_BASENAMES_DEFAULTS)
