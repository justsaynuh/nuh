"""Path checks for the file tools, the project boundary, and project root detection.

The sensitive-path tables and matching are in ``sensitive_paths``, the
protected places in ``protected_paths``, path spelling and display in
``path_names``.
"""

import os
import subprocess
import sys

from nuh import floor, gating, taxonomy, word_quoting
from nuh.path_names import _HOME, _is_under, _spellings, friendly_path, is_hook_path, is_nuh_config_path
from nuh.sensitive_paths import (_ensure_sensitive_paths_merged, _lifted_dirs, check_path_basic, check_path_basic_raw,
                                 check_protected_write, user_entries)


# Tools that modify the file they name.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})


_project_root: str | None = None
_project_root_resolved = False
_launch_dir: str | None = None  # see set_launch_dir


def resolve_path(raw: str) -> str:
    """Expand ~ and env vars, then resolve to absolute canonical path."""
    if not raw:
        return ""
    expanded = word_quoting.expand_path(raw)
    return os.path.realpath(expanded)


def check_path(tool_name: str, raw_path: str, *, literal: bool = False) -> dict | None:
    """Check a path for hook/sensitive violations. Returns decision dict or None (= allow)."""
    if not raw_path:
        return None

    resolved = os.path.realpath(raw_path) if literal else resolve_path(raw_path)

    # Hook self-protection — Write/Edit blocked, Read/Glob/Grep allowed.
    # Reading hooks is harmless and useful for debugging. Only modification
    # is dangerous (self-protection).
    if is_hook_path(resolved):
        if tool_name in WRITE_TOOLS:
            return {
                "decision": taxonomy.BLOCK,
                "reason": f"{tool_name} targets hook directory: {friendly_path(resolved)} (self-modification blocked)",
            }
        return None  # Read/Glob/Grep on hooks is fine

    # Agent wiring, nuh, project and git config, ssh host files: a floored ask for writes.
    protected = (check_protected_write(raw_path, literal=literal, shell_word=False)
                 if tool_name in WRITE_TOOLS else None)
    protected_result = None
    if protected:
        decision, reason = protected
        protected_result = {"decision": decision, "reason": f"{tool_name} {reason}",
                            "_floor": floor.rule_of(protected)}

    # Config self-protection — ASK for all tools (users legitimately edit config)
    if is_nuh_config_path(resolved):
        return protected_result or {
            "decision": taxonomy.ASK,
            "reason": f"{tool_name} targets nuh config: ~/.config/nuh/ (guard self-protection)",
        }

    # Core check: sensitive paths
    sensitive = _sensitive_result(tool_name, raw_path, resolved, literal)
    if sensitive and (protected_result is None or sensitive["decision"] == taxonomy.BLOCK):
        return sensitive
    return protected_result


def _sensitive_result(tool_name: str, raw_path: str, resolved: str, literal: bool) -> dict | None:
    """``check_path``'s sensitive-path decision, after the allow_paths exemption.

    ``allow_paths`` exempts a path from the user's own entries only: a
    built-in entry is a floor and still holds (``user_entries``).
    """
    from nuh.config import is_path_allowed  # lazy import to avoid circular
    allowed = is_path_allowed(resolved if literal else raw_path, get_project_root(), literal=literal)
    basic = _basic_verdict(raw_path, literal, user_entries() if allowed else frozenset())
    if not basic:
        return None
    decision, reason = basic

    result = {"decision": taxonomy.BLOCK if decision == taxonomy.BLOCK else taxonomy.ASK,
              "reason": f"{tool_name} {reason}"}
    if decision != taxonomy.BLOCK and not allowed and not _basic_verdict(raw_path, literal, user_entries()):
        # Only the user's own entries matched: allow_paths can exempt the path from them.
        result["_hint"] = f"To always allow: nuh allow-path {friendly_path(resolved)}"
    if floor.rule_of(basic):
        result["_floor"] = floor.rule_of(basic)
    return result


def _basic_verdict(raw_path: str, literal: bool, exempt: frozenset[str]) -> tuple[str, str] | None:
    """The sensitive-path verdict for a file tool's path, entries in *exempt* skipped."""
    if not literal:
        return check_path_basic_raw(raw_path, shell_word=False, exempt=exempt)
    _ensure_sensitive_paths_merged()
    spellings = _spellings(raw_path, literal=True)
    lifted = _lifted_dirs(raw_path, spellings, shell_word=False) | exempt
    verdicts = [v for v in (check_path_basic(p, lifted) for p in spellings) if v]
    return floor.strictest(verdicts) if verdicts else None


def is_trusted_path(resolved: str) -> bool:
    """Check if resolved path is inside a trusted_paths directory."""
    from nuh.config import get_config  # lazy import to avoid circular
    cfg = get_config()
    if cfg.profile == "none":
        return True  # boundary check disabled; defense in depth
    for entry in cfg.trusted_paths:
        trust_dir = resolve_path(entry)
        if resolved == trust_dir or resolved.startswith(trust_dir + os.sep):
            return True
    return False


def _suggest_trust_dir(raw_path: str) -> str:
    """Suggest a directory to trust for a given path.

    Under $HOME: ~/first_component (e.g. ~/builds).
    Elsewhere: parent directory (e.g. /tmp/foo/bar.txt → /tmp/foo).
    Special: if parent is root (/), return the resolved path itself.
    """
    resolved = resolve_path(raw_path)
    home = os.path.realpath(os.path.expanduser("~"))
    if resolved.startswith(home + os.sep):
        # Under home: return ~/first_component
        rel = resolved[len(home) + 1:]  # strip home + /
        first_component = rel.split(os.sep)[0]
        return f"~/{first_component}"
    # Outside home: return parent directory
    parent = os.path.dirname(resolved)
    if parent == "/":
        return resolved
    return parent


def check_project_boundary(tool_name: str, raw_path: str, *, literal: bool = False) -> dict | None:
    """Check if path is outside project root + trusted_paths. Returns dict or None (= allow)."""
    if not raw_path:
        return None
    from nuh.config import get_config  # lazy import to avoid circular
    if get_config().profile == "none":
        return None  # boundary check disabled (D9)
    resolved = os.path.realpath(raw_path) if literal else resolve_path(raw_path)
    if is_trusted_path(resolved):
        return None  # trusted — allow regardless of git root (FD-107)
    project_root = get_project_root()
    if project_root is None:
        return {
            "decision": taxonomy.ASK,
            "reason": f"{tool_name} outside project (no git root): {friendly_path(resolved)}",
            "_hint": f"To always allow: nuh trust {_suggest_trust_dir(raw_path)}",
            gating.KEYS: [taxonomy.FILESYSTEM_WRITE],  # the human_only key behind it
        }
    real_root = os.path.realpath(project_root)
    if resolved == real_root or resolved.startswith(real_root + os.sep):
        return None  # inside project
    if is_trusted_path(resolved):
        return None  # inside trusted directory
    return {
        "decision": taxonomy.ASK,
        "reason": f"{tool_name} outside project: {friendly_path(resolved)}",
        "_hint": f"To always allow: nuh trust {_suggest_trust_dir(raw_path)}",
        gating.KEYS: [taxonomy.FILESYSTEM_WRITE],  # the human_only key behind it
    }


def set_project_root(path: str) -> None:
    """Override project root (for testing). Bypasses git auto-detection."""
    global _project_root, _project_root_resolved
    _project_root = path
    _project_root_resolved = True


def reset_project_root() -> None:
    """Clear project root override, restoring auto-detection."""
    global _project_root, _project_root_resolved
    _project_root = None
    _project_root_resolved = False


def set_launch_dir(path: str | None) -> None:
    """Set the launch dir ``project_root: git_or_launch_dir`` falls back to.

    The hook sets it for Codex from the payload's ``cwd`` ("" for none);
    None (the default) reads ``$CLAUDE_PROJECT_DIR``, which Claude Code sets
    for hooks to the directory it was launched in.
    """
    global _launch_dir
    _launch_dir = path


def _launch_dir_root() -> str | None:
    """The launch dir as a project root, or None when unset or refused.

    It must be an absolute, existing directory. ``/``, ``$HOME`` and any
    directory above ``$HOME`` are refused: each would make the whole home
    directory "the project".
    """
    raw = _launch_dir if _launch_dir is not None else os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not raw:
        return None
    if not os.path.isabs(raw) or not os.path.isdir(raw):
        sys.stderr.write(f"nuh: project_root: launch dir is not an absolute directory: {raw!r}; no project root\n")
        return None
    real = os.path.realpath(raw)
    if real == os.sep or _is_under(os.path.realpath(_HOME), real):
        sys.stderr.write(f"nuh: project_root: launch dir {real} is / or contains $HOME; no project root\n")
        return None
    return real


def _git_root() -> str | None:
    """The git toplevel of the current directory, or None outside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        sys.stderr.write("nuh: git not available, project root detection skipped\n")
    return None


def get_project_root() -> str | None:
    """The project root, cached for the process lifetime.

    The git toplevel of the current directory; with the global
    ``project_root: git_or_launch_dir`` and no git root, the launch dir.
    """
    global _project_root, _project_root_resolved
    if _project_root_resolved:
        return _project_root
    _project_root_resolved = True
    _project_root = _git_root()
    if _project_root is None:
        from nuh.config import project_root_mode  # lazy import to avoid circular
        if project_root_mode() == "git_or_launch_dir":
            _project_root = _launch_dir_root()
    return _project_root
