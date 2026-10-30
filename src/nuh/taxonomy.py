"""Action taxonomy — command classification table and policy defaults.

Classification data and policies are loaded from JSON files in data/.
The flag classifiers of phase 2 are in ``flag_classify``, ``git_classify``
and ``http_classify``.
"""

import json
import os
import re
import sys
from pathlib import Path

from nuh import env_read, program_identity, self_protect


_DATA_DIR = Path(__file__).parent / "data"

# Action types
FILESYSTEM_READ = "filesystem_read"
FILESYSTEM_WRITE = "filesystem_write"
FILESYSTEM_DELETE = "filesystem_delete"
GIT_SAFE = "git_safe"
GIT_WRITE = "git_write"
GIT_REMOTE_WRITE = "git_remote_write"
GIT_DISCARD = "git_discard"
GIT_HISTORY_REWRITE = "git_history_rewrite"
NETWORK_OUTBOUND = "network_outbound"
NETWORK_WRITE = "network_write"
NETWORK_DIAGNOSTIC = "network_diagnostic"
PACKAGE_INSTALL = "package_install"
PACKAGE_RUN = "package_run"
PACKAGE_UNINSTALL = "package_uninstall"
LANG_EXEC = "lang_exec"
PROCESS_SIGNAL = "process_signal"
CONTAINER_DESTRUCTIVE = "container_destructive"
DB_READ = "db_read"
DB_WRITE = "db_write"
BEADS_SAFE = "beads_safe"
BEADS_WRITE = "beads_write"
BEADS_DESTRUCTIVE = "beads_destructive"
OBFUSCATED = "obfuscated"
NUH_ADMIN = "nuh_admin"
ENV_READ = "env_read"
UNKNOWN = "unknown"

# Decision constants
ALLOW = "allow"
ASK = "ask"
BLOCK = "block"
CONTEXT = "context"

# Valid profiles.
PROFILES = ("full", "minimal", "none")

# Strictness ordering — higher = more restrictive. Used for tighten-only merges.
STRICTNESS = {ALLOW: 0, CONTEXT: 1, ASK: 2, BLOCK: 3}


def _load_classify_table(profile: str = "full") -> list[tuple[tuple[str, ...], str]]:
    """Load classify table from JSON files for the given profile."""
    if profile == "none":
        return []
    subdir = "classify_minimal" if profile == "minimal" else "classify_full"
    classify_dir = _DATA_DIR / subdir
    table: list[tuple[tuple[str, ...], str]] = []
    for json_file in classify_dir.glob("*.json"):
        action_type = json_file.stem  # e.g. "git_safe" from "git_safe.json"
        with open(json_file) as f:
            prefixes = json.load(f)
        for prefix_str in prefixes:
            parts = prefix_str.split()
            if parts:
                parts[0] = os.path.basename(parts[0]) or parts[0]
            table.append((tuple(parts), action_type))
    table.sort(key=lambda entry: len(entry[0]), reverse=True)
    return table


def _load_policies() -> dict[str, str]:
    """Load default policies from data/policies.json."""
    with open(_DATA_DIR / "policies.json") as f:
        return json.load(f)


# Cached built-in tables keyed by profile name.
_BUILTIN_TABLES: dict[str, list[tuple[tuple[str, ...], str]]] = {}
_TYPE_DESCRIPTIONS: dict[str, str] | None = None
_POLICIES = _load_policies()
POLICIES = _POLICIES

# Pre-load full profile at module level (most common path).
_BUILTIN_TABLES["full"] = _load_classify_table("full")


def get_builtin_table(profile: str = "full") -> list[tuple[tuple[str, ...], str]]:
    """Get cached built-in classify table for a profile."""
    if profile not in _BUILTIN_TABLES:
        _BUILTIN_TABLES[profile] = _load_classify_table(profile)
    return _BUILTIN_TABLES[profile]


def build_user_table(user_classify: dict[str, list[str]]) -> list[tuple[tuple[str, ...], str]]:
    """Build a sorted classify table from user config entries."""
    table: list[tuple[tuple[str, ...], str]] = []
    for action_type, prefixes in user_classify.items():
        if not isinstance(prefixes, list):
            continue
        for prefix_str in prefixes:
            parts = prefix_str.split()
            if parts:
                parts[0] = os.path.basename(parts[0]) or parts[0]
                parts[0] = _normalize_interpreter(parts[0])
            table.append((tuple(parts), action_type))
    table.sort(key=lambda entry: len(entry[0]), reverse=True)
    return table


# Flags that mean inline code (already classified as lang_exec via classify table).
_INLINE_FLAGS: dict[str, set[str]] = {
    "python": {"-c"}, "python3": {"-c"},
    "node": {"-e", "-p", "--eval", "--print"},
    "ruby": {"-e"},
    "perl": {"-e", "-E"},
    "php": {"-r"},
    "bash": {"-c"}, "sh": {"-c"}, "dash": {"-c"}, "zsh": {"-c"},
}

# Flags that mean module mode (still lang_exec, but different path resolution).
_MODULE_FLAGS: dict[str, set[str]] = {
    "python": {"-m"}, "python3": {"-m"},
}

# Interpreter flags that consume the next token as a value argument.
# Must be skipped (along with their value) when searching for the script file.
_VALUE_FLAGS: dict[str, set[str]] = {
    "python": {"-W", "-X"},
    "python3": {"-W", "-X"},
    "node": {"-r", "--require", "--loader"},
    "ruby": {"-I", "-r"},
    "perl": {"-I", "-M"},
}


# Exec sinks for pipe composition.
_EXEC_SINKS_DEFAULTS = {"bash", "sh", "dash", "zsh", "eval", "python", "python3",
                         "node", "ruby", "perl", "php", "bun", "deno", "fish", "pwsh",
                         "env"}
EXEC_SINKS: set[str] = set(_EXEC_SINKS_DEFAULTS)
_exec_sinks_merged = False

# Versioned interpreter normalization (nah-1o5).
# Canonical names, longest first to avoid prefix ambiguity.
_CANONICAL_INTERPRETERS = [
    "python3", "python", "pip3", "pip",
    "node", "ruby", "perl", "php", "deno", "bun",
    "bash", "dash", "zsh", "sh", "fish", "pwsh",
]
_VERSION_SUFFIX_RE = re.compile(r"^\.?[0-9]+(?:\.[0-9]+)*$")


def _normalize_interpreter(name: str) -> str:
    """Strip version suffix from interpreter basename.

    python3.12 → python3, node22 → node, bash5.2 → bash.
    Returns name unchanged if not a versioned interpreter.
    Uses longest-prefix-first matching to correctly handle python3 vs python.
    """
    for canonical in _CANONICAL_INTERPRETERS:
        if name.startswith(canonical):
            suffix = name[len(canonical):]
            if not suffix:
                return name
            if _VERSION_SUFFIX_RE.match(suffix):
                return canonical
            return name
    return name


def _ensure_exec_sinks_merged():
    """Lazy one-time merge of config exec_sinks into EXEC_SINKS."""
    global _exec_sinks_merged
    if _exec_sinks_merged:
        return
    _exec_sinks_merged = True
    try:
        from nuh.config import get_config, _parse_add_remove
        cfg = get_config()
        if cfg.profile == "none":
            EXEC_SINKS.clear()
        add, remove = _parse_add_remove(cfg.exec_sinks)
        EXEC_SINKS.update(_normalize_interpreter(str(s)) for s in add)
        if remove:
            sys.stderr.write("nuh: warning: exec_sinks.remove weakens composition rules\n")
            EXEC_SINKS.difference_update(_normalize_interpreter(str(s)) for s in remove)
    except Exception as exc:
        sys.stderr.write(f"nuh: config: exec_sinks: {exc}\n")


def reset_exec_sinks():
    """Restore defaults and clear merge flag (for testing)."""
    global _exec_sinks_merged
    _exec_sinks_merged = False
    EXEC_SINKS.clear()
    EXEC_SINKS.update(_EXEC_SINKS_DEFAULTS)


# Decode commands for pipe composition (command, flag).
_DECODE_COMMANDS_DEFAULTS: list[tuple[str, str | None]] = [
    ("base64", "-d"),
    ("base64", "--decode"),
    ("xxd", "-r"),
    ("uudecode", None),
]
DECODE_COMMANDS: list[tuple[str, str | None]] = list(_DECODE_COMMANDS_DEFAULTS)
_decode_commands_merged = False


def _ensure_decode_commands_merged():
    """Lazy one-time merge of config decode_commands into DECODE_COMMANDS."""
    global _decode_commands_merged
    if _decode_commands_merged:
        return
    _decode_commands_merged = True
    try:
        from nuh.config import get_config, _parse_add_remove
        cfg = get_config()
        if cfg.profile == "none":
            DECODE_COMMANDS.clear()
        add, remove = _parse_add_remove(cfg.decode_commands)
        # Remove by command name (all flag variants)
        if remove:
            sys.stderr.write("nuh: warning: decode_commands.remove weakens composition rules\n")
            remove_cmds = {str(c) for c in remove}
            DECODE_COMMANDS[:] = [(c, f) for c, f in DECODE_COMMANDS if c not in remove_cmds]
        # Add: "command flag" or "command" (space-separated string)
        for entry in add:
            parts = str(entry).split(None, 1)
            if parts:
                cmd = parts[0]
                flag = parts[1] if len(parts) > 1 else None
                DECODE_COMMANDS.append((cmd, flag))
    except Exception as exc:
        sys.stderr.write(f"nuh: config: decode_commands: {exc}\n")


def reset_decode_commands():
    """Restore defaults and clear merge flag (for testing)."""
    global _decode_commands_merged
    _decode_commands_merged = False
    DECODE_COMMANDS.clear()
    DECODE_COMMANDS.extend(_DECODE_COMMANDS_DEFAULTS)


def _prefix_match(tokens: list[str], table: list[tuple[tuple[str, ...], str]]) -> str:
    """First prefix match in a single sorted table. Returns action type or UNKNOWN."""
    for prefix, action_type in table:
        if len(tokens) >= len(prefix) and tuple(tokens[:len(prefix)]) == prefix:
            return action_type
    return UNKNOWN


def classify_tokens(
    tokens: list[str],
    global_table: list | None = None,
    builtin_table: list | None = None,
    project_table: list | None = None,
    *,
    profile: str = "full",
) -> str:
    """Classify command tokens via three-phase lookup.

    Phase 1: Global table (trusted user config) — always runs.
    Phase 2: Flag classifiers (built-in opinions) — skipped when profile == "none".
    Phase 3: Remaining tables (project, builtin) — global already checked.
        A project table entry wins over the built-ins only when it does not
        loosen policy.
    """
    if not tokens:
        return UNKNOWN

    # Program identity: ./git is that file, not git.
    # A program file nuh cannot vouch for runs as a script, with content
    # inspection; nuh_admin still names a ./nuh admin command.
    if profile != "none" and not program_identity.is_tool(tokens[0]):
        return NUH_ADMIN if self_protect.nuh_admin_reason(tokens) else LANG_EXEC

    # Basename normalization — resolve /usr/bin/rm → rm (FD-065)
    base = os.path.basename(tokens[0])
    if base and base != tokens[0]:
        tokens = [base] + tokens[1:]

    # Version normalization — python3.12 → python3 (nah-1o5)
    normalized = _normalize_interpreter(tokens[0])
    if normalized != tokens[0]:
        tokens = [normalized] + tokens[1:]

    # --- Phase 0: self-protection (nuh_admin) ---
    # Before every table, so a broad user entry such as `pipx uninstall` or a
    # project entry cannot reclassify a command that reconfigures or removes nuh.
    # Its policy is still the user's to set (actions: nuh_admin).
    if self_protect.nuh_admin_reason(tokens):
        return NUH_ADMIN

    # Lazy import: the flag classifiers return this module's action types, so
    # they import taxonomy and cannot be imported here at load time.
    from nuh.flag_classify import (_classify_awk, _classify_find, _classify_global_install, _classify_script_exec,
                                   _classify_sed, _classify_tar)
    from nuh.git_classify import _classify_git, _strip_git_global_flags
    from nuh.http_classify import _classify_curl, _classify_httpie, _classify_wget
    from nuh.file_operands import write_mode

    # --- Phase 1: Global table override (trusted user config) ---
    # Non-git: check global table on raw tokens.
    if global_table and tokens[0] != "git":
        result = _prefix_match(tokens, global_table)
        if result != UNKNOWN:
            return result

    # Git: strip global flags first, then check global table on clean tokens.
    if tokens[0] == "git":
        tokens = _strip_git_global_flags(tokens)
        if global_table:
            result = _prefix_match(tokens, global_table)
            if result != UNKNOWN:
                return result

    # --- Phase 2: Flag classifiers (built-in opinions) ---
    # Skipped entirely when profile == "none".
    if profile != "none":
        # `nuh <command ...> --help` prints help and changes nothing. A prefix entry cannot say this: the flag
        # may follow any operand, and after `--` it is an operand itself.
        if self_protect.prints_help(tokens):
            return FILESYSTEM_READ
        if env_read.reason(tokens):
            return ENV_READ
        action = _classify_find(
            tokens,
            global_table=global_table,
            builtin_table=builtin_table,
            project_table=project_table,
            profile=profile,
        )
        if action is not None:
            return action
        file_action = write_mode(tokens)
        if file_action == UNKNOWN:
            return file_action
        action = _classify_sed(tokens)
        if action is not None:
            return action
        action = _classify_awk(tokens)
        if action is not None:
            return action
        if file_action is not None:
            return file_action
        action = _classify_tar(tokens)
        if action is not None:
            return action
        if tokens[0] == "git":
            action = _classify_git(tokens)
            if action is not None:
                return action
        action = _classify_curl(tokens)
        if action is not None:
            return action
        action = _classify_wget(tokens)
        if action is not None:
            return action
        action = _classify_httpie(tokens)
        if action is not None:
            return action
        action = _classify_global_install(tokens)
        if action is not None:
            return action
        action = _classify_script_exec(tokens)
        if action is not None:
            return action

    # --- Phase 3: Remaining tables (project, builtin) ---
    # Project table may override built-ins only when it does not weaken policy.
    # A command the built-ins call unknown counts as its policy (ask), so a
    # project can only tighten it too.
    project_result = _prefix_match(tokens, project_table) if project_table else UNKNOWN
    builtin_result = _prefix_match(tokens, builtin_table) if builtin_table else UNKNOWN

    if project_result == UNKNOWN:
        return builtin_result
    if project_result == builtin_result:
        return project_result

    project_policy = get_policy(project_result)
    builtin_policy = get_policy(builtin_result)
    if STRICTNESS.get(project_policy, 0) >= STRICTNESS.get(builtin_policy, 0):
        return project_result
    return builtin_result


def load_type_descriptions() -> dict[str, str]:
    """Load action type descriptions from types.json. Cached at module level."""
    global _TYPE_DESCRIPTIONS
    if _TYPE_DESCRIPTIONS is not None:
        return _TYPE_DESCRIPTIONS
    with open(_DATA_DIR / "types.json") as f:
        _TYPE_DESCRIPTIONS = json.load(f)
    return _TYPE_DESCRIPTIONS


def validate_action_type(name: str) -> tuple[bool, list[str]]:
    """Check if name is a valid action type. Returns (valid, close_matches)."""
    import difflib
    all_types = list(load_type_descriptions().keys())
    if name in all_types:
        return True, []
    matches = difflib.get_close_matches(name, all_types, n=3, cutoff=0.5)
    return False, matches


def get_policy(action_type: str, user_actions: dict[str, str] | None = None) -> str:
    """Return policy for an action type. Checks user overrides first, then built-in."""
    if user_actions and action_type in user_actions:
        return user_actions[action_type]
    return _POLICIES.get(action_type, ASK)


def is_exec_sink(token: str) -> bool:
    """Check if a token is an exec sink (for pipe composition rules)."""
    _ensure_exec_sinks_merged()
    return _normalize_interpreter(os.path.basename(token)) in EXEC_SINKS


def is_decode_stage(tokens: list[str]) -> bool:
    """Check if tokens represent a decode command (base64 -d, xxd -r, etc.)."""
    _ensure_decode_commands_merged()
    if not tokens:
        return False
    for cmd, flag in DECODE_COMMANDS:
        if tokens[0] == cmd:
            if flag is None:
                return True
            if flag in tokens[1:]:
                return True
    return False
