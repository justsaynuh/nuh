"""Writes that can switch nuh off or run code: agent hook wiring, nuh's config and code, the account's git config.

Each helper gives a floored verdict for one kind of protected place;
``sensitive_paths.check_protected_write`` combines them.
"""

import fnmatch
import os

from nuh import floor, taxonomy
from nuh.path_names import (_CLAUDE_DIR, _CODEX_HOOKS_DIR, _HOME, _HOOKS_DIR, _is_under, _NUH_CONFIG_DIR, friendly_path,
                            PACKAGE_DIR, PACKAGE_RULE)


# Agent hook wiring and nuh's own config: modifying any of them, or renaming or
# removing a directory above them, can switch nuh off (floor rule agent_hooks).
# ~/.claude/settings*.json is matched by pattern in _agent_wiring_verdict.
_AGENT_PROTECTED: list[tuple[str, str]] = [
    (_HOOKS_DIR, "~/.claude/hooks"),
    (_CODEX_HOOKS_DIR, "~/.codex/hooks"),
    (os.path.realpath(os.path.join(_HOME, ".codex", "hooks.json")), "~/.codex/hooks.json"),
    (os.path.realpath(os.path.join(_HOME, ".codex", "config.toml")), "~/.codex/config.toml"),
    (_NUH_CONFIG_DIR, "~/.config/nuh"),
]
_CLAUDE_SETTINGS_GLOB = "settings*.json"
# The account's git config: an exec key written there runs on every git command (floor rule git_exec).
_GLOBAL_GIT_CONFIGS = frozenset({os.path.join(_HOME, ".gitconfig"),
                                 os.path.realpath(os.path.join(_HOME, ".gitconfig")),
                                 os.path.join(_HOME, ".config", "git", "config"),
                                 os.path.realpath(os.path.join(_HOME, ".config", "git", "config"))})
# Every place above as a path, for what a glob could match (glob_reach): ~/.claude/settings*.json by its usual names.
REACH_TARGETS: tuple[str, ...] = (*(path for path, _display in _AGENT_PROTECTED),
                                  os.path.join(_CLAUDE_DIR, "settings.json"),
                                  os.path.join(_CLAUDE_DIR, "settings.local.json"),
                                  *sorted(_GLOBAL_GIT_CONFIGS), PACKAGE_DIR)


def _agent_wiring_verdict(path: str, parents: bool) -> floor.Verdict | None:
    for protected, display in _AGENT_PROTECTED:
        if _is_under(path, protected):
            what = ("nuh config: ~/.config/nuh/ (guard self-protection)" if protected == _NUH_CONFIG_DIR
                    else f"agent hook wiring: {display}")
            return floor.Verdict(taxonomy.ASK, f"targets {what}", "agent_hooks")
        if parents and protected.startswith(path + os.sep):
            return floor.Verdict(taxonomy.ASK, f"renames or removes a parent of {display}", "agent_hooks")
    if os.path.dirname(path) == _CLAUDE_DIR and fnmatch.fnmatchcase(os.path.basename(path),
                                                                      _CLAUDE_SETTINGS_GLOB):
        return floor.Verdict(taxonomy.ASK, f"targets agent settings: {friendly_path(path)}", "agent_hooks")
    return None


def _package_verdict(path: str, parents: bool) -> floor.Verdict | None:
    """Floored ask for modifying nuh's own code (rule ``package_dir``), or None."""
    if _is_under(path, PACKAGE_DIR):
        return floor.Verdict(taxonomy.ASK, f"targets nuh's own code: {friendly_path(path)}", PACKAGE_RULE)
    if parents and PACKAGE_DIR.startswith(path + os.sep):  # "/" itself is the catastrophic rule's
        return floor.Verdict(taxonomy.ASK, f"renames or removes a parent of nuh's own code: {friendly_path(path)}",
                             PACKAGE_RULE)
    return None
