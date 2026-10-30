"""The fixed places nuh knows (home, agent hooks, nuh config, its own package) and how a path is spelled and shown."""

import os

from nuh import word_quoting


_HOME = os.path.expanduser("~")
_HOOKS_DIR = os.path.realpath(os.path.join(_HOME, ".claude", "hooks"))
_CODEX_HOOKS_DIR = os.path.realpath(os.path.join(_HOME, ".codex", "hooks"))
_NUH_CONFIG_DIR = os.path.realpath(os.path.join(_HOME, ".config", "nuh"))
_CLAUDE_DIR = os.path.realpath(os.path.join(_HOME, ".claude"))

PROJECT_CONFIG_NAME = ".nuh.yaml"

# The directory nuh runs from (this module's package): for an editable install,
# the checkout's src/nuh, so other worktrees of the repo are other paths.
PACKAGE_DIR = os.path.dirname(os.path.realpath(__file__))
PACKAGE_RULE = "package_dir"


def friendly_path(resolved: str) -> str:
    """Replace home directory prefix with ~ for display."""
    if resolved.startswith(_HOME + os.sep):
        return "~" + resolved[len(_HOME):]
    if resolved == _HOME:
        return "~"
    return resolved


def is_hook_path(resolved: str) -> bool:
    """Check if path targets ~/.claude/hooks/ (self-protection)."""
    if not resolved:
        return False
    return any(resolved == directory or resolved.startswith(directory + os.sep)
               for directory in (_HOOKS_DIR, _CODEX_HOOKS_DIR))


def is_nuh_config_path(resolved: str) -> bool:
    """Check if path targets ~/.config/nuh/ (self-protection)."""
    if not resolved:
        return False
    return resolved == _NUH_CONFIG_DIR or resolved.startswith(_NUH_CONFIG_DIR + os.sep)


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + os.sep)


def _spellings(raw: str, *, literal: bool = False) -> list[str]:
    """*raw* as written (absolute, not symlink-resolved) and resolved, without duplicates.

    Checking both catches a protected name that is a symlink to an innocuous
    file and an innocuous name that is a symlink to a protected file.
    """
    expanded = raw if literal else word_quoting.expand_path(raw)
    written = os.path.abspath(expanded)
    resolved = os.path.realpath(expanded)
    return [written] if written == resolved else [written, resolved]
