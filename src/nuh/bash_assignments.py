"""A Bash stage that only sets shell variables: allowed, unless the variable changes what later commands run."""

from nuh import taxonomy
from nuh.bash_policy import _apply_policy
from nuh.bash_types import StageResult


# Variables whose value changes what later commands run, or where they look
# for code and config. Assigning one asks; any other assignment is shell state.
# git's own variables are the git_exec floor (Stage.env_floor).
_HIJACK_VARS = frozenset({
    "PATH", "path", "HOME", "ENV", "BASH_ENV", "PROMPT_COMMAND", "PS0", "PS1", "PS2", "PS3", "PS4", "IFS",
    "PAGER", "MANPAGER", "EDITOR", "VISUAL", "BROWSER", "LESSOPEN", "LESSCLOSE", "SHELLOPTS", "BASHOPTS",
    "ZDOTDIR", "FPATH", "fpath", "module_path", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "NODE_OPTIONS",
    "NODE_PATH", "PERL5OPT", "PERL5LIB", "PERLLIB", "RUBYOPT", "RUBYLIB", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
    "XDG_CONFIG_HOME", "GIT_DIR", "GIT_WORK_TREE", "SSH_ASKPASS", "SUDO_ASKPASS",
})
_HIJACK_PREFIXES = ("LD_", "DYLD_")


def _is_hijack_var(name: str) -> bool:
    return (name in _HIJACK_VARS or name.startswith(_HIJACK_PREFIXES)
            or (name.startswith("GIT_") and name.endswith("_COMMAND")))


def _classify_assignments(tokens: list[str], assigned: list[tuple[str, str]],
                          user_actions: dict[str, str] | None) -> StageResult:
    """A stage that only sets shell variables (``X=1``, ``export X=1``).

    Adapted from manuelschipper/nah (MIT), adc4f995 and 4ffe1644: it is shell
    state and runs nothing; later stages see the value where they use it
    (``shell_state``). Assigning a variable that changes what later commands
    run is unknown, so it asks.
    """
    hijacked = [name for name, _value in assigned if _is_hijack_var(name)]
    if not hijacked:
        return StageResult(tokens=tokens, action_type=taxonomy.FILESYSTEM_READ, default_policy=taxonomy.ALLOW,
                           decision=taxonomy.ALLOW, reason="shell variable assignment")
    sr = StageResult(tokens=tokens, action_type=taxonomy.UNKNOWN,
                     default_policy=taxonomy.get_policy(taxonomy.UNKNOWN, user_actions))
    _apply_policy(sr)
    if sr.decision != taxonomy.ALLOW:
        sr.reason = f"assignment to {', '.join(hijacked)} changes what later commands run"
    return sr
