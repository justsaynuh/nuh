"""Git configuration that makes git run a program (floor rule ``git_exec``).

A key can be set for one command (``git -c``, ``--config-env``), for every
later git command in the repo or account (``git config <key> <value>``), or
through the environment (``GIT_SSH_COMMAND=…``, ``GIT_CONFIG_*``). All of
them run a program the next time git reaches that key, so all of them are a
floored ask. Values that name a well-known harmless program (``cat`` as a
pager, ``true`` as an editor) or turn the feature off are exempt, since
agents use them to keep git non-interactive.
"""

import os
import re

# (section, subsection, name) patterns, lowercase; "*" matches any subsection.
# A pattern without a subsection matches only keys without one.
_EXEC_KEYS: frozenset[tuple[str, str | None, str]] = frozenset({
    ("core", None, "sshcommand"), ("core", None, "fsmonitor"), ("core", None, "pager"),
    ("core", None, "editor"), ("core", None, "hookspath"), ("core", None, "askpass"),
    ("core", None, "gitproxy"), ("credential", None, "helper"), ("credential", "*", "helper"),
    ("sequence", None, "editor"), ("gpg", None, "program"), ("gpg", "*", "program"),
    ("diff", None, "external"), ("diff", "*", "textconv"), ("diff", "*", "command"),
    ("filter", "*", "clean"), ("filter", "*", "smudge"), ("filter", "*", "process"),
    ("merge", "*", "driver"), ("uploadpack", None, "packobjectshook"),
    ("protocol", None, "allow"), ("protocol", "*", "allow"),
    ("include", None, "path"), ("includeif", "*", "path"),
    ("remote", "*", "uploadpack"), ("remote", "*", "receivepack"),
    ("web", None, "browser"), ("browser", "*", "cmd"), ("man", "*", "cmd"),
})
_PAGER_VALUES = frozenset({"", "cat", "less", "more", "less -r", "less -R", "less -FRX", "less -FRSX"})
_EDITOR_VALUES = frozenset({"", "true", ":", "cat", "vi", "vim", "nvim", "nano", "emacs"})
_BOOLEANS = frozenset({"true", "false", "yes", "no", "on", "off", "1", "0"})
# Values that are harmless for a key, by its (section, name).
_HARMLESS: dict[tuple[str, str], frozenset[str]] = {
    ("core", "pager"): _PAGER_VALUES,
    ("pager", "*"): _PAGER_VALUES | _BOOLEANS,
    ("core", "editor"): _EDITOR_VALUES,
    ("sequence", "editor"): _EDITOR_VALUES,
    ("core", "fsmonitor"): _BOOLEANS,
    ("core", "hookspath"): frozenset({"/dev/null"}),
    ("credential", "helper"): frozenset({""}),
    ("core", "askpass"): frozenset({""}),
    ("protocol", "allow"): frozenset({"never"}),
}

# Environment variables that make git run a program or read other config.
_EXEC_ENV = frozenset({"GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND", "GIT_ASKPASS", "GIT_EXTERNAL_DIFF",
                       "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_CONFIG_PARAMETERS",
                       "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_TEMPLATE_DIR",
                       "GIT_EXEC_PATH"})
_EXEC_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_HARMLESS_ENV: dict[str, frozenset[str]] = {
    "GIT_PAGER": _PAGER_VALUES,
    "GIT_EDITOR": _EDITOR_VALUES,
    "GIT_SEQUENCE_EDITOR": _EDITOR_VALUES,
    "GIT_CONFIG_GLOBAL": frozenset({"/dev/null"}),
    "GIT_CONFIG_SYSTEM": frozenset({"/dev/null"}),
    "GIT_CONFIG_PARAMETERS": frozenset({""}),
}

_CONFIG_VALUE_FLAGS = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment"})
_CONFIG_READ_FLAGS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l",
                                "--get-color", "--get-colorbool", "--show-origin", "--show-scope",
                                "--name-only"})
_CONFIG_UNSET_FLAGS = frozenset({"--unset", "--unset-all", "--remove-section", "--rename-section"})
_CONFIG_EDIT_FLAGS = frozenset({"-e", "--edit"})
_CONFIG_SUBCOMMANDS = frozenset({"get", "list", "unset", "rename-section", "remove-section", "set", "edit"})
_GIT_VALUE_FLAGS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"})
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(\..*)?\.[A-Za-z][A-Za-z0-9-]*$")


def _split_key(key: str) -> tuple[str, str | None, str] | None:
    if not _KEY_RE.match(key):
        return None
    section, _, rest = key.partition(".")
    subsection, _, name = rest.rpartition(".")
    return section.lower(), (subsection or None), name.lower()


def _exec_key(key: str) -> tuple[str, str] | None:
    """(section, name) when *key* makes git run a program, else None. ``alias.*`` is separate."""
    parts = _split_key(key)
    if parts is None:
        return None
    section, subsection, name = parts
    if section == "pager" and subsection is None:
        return ("pager", "*")
    for want_section, want_sub, want_name in _EXEC_KEYS:
        if section == want_section and name == want_name and (
                (want_sub is None) == (subsection is None)):
            return (section, name)
    return None


def key_reason(key: str, value: str | None) -> str:
    """Why setting git config *key* to *value* runs a program, or "". *value* None: unknown."""
    parts = _split_key(key)
    if parts and parts[0] == "alias":
        if value is None or value.lstrip().startswith("!"):
            return f"git alias {key} runs a shell command"
        return ""
    found = _exec_key(key)
    if found is None:
        return ""
    if value is not None and value.strip() in _HARMLESS.get(found, frozenset()):
        return ""
    return f"git config {key} runs a program"


def env_reason(name: str, value: str) -> str:
    """Why environment variable *name*=*value* makes git run a program, or ""."""
    if name not in _EXEC_ENV and not name.startswith(_EXEC_ENV_PREFIXES):
        return ""
    if value.strip() in _HARMLESS_ENV.get(name, frozenset()):
        return ""
    return f"{name} changes what git runs"


def _config_reasons(args: list[str]) -> list[str]:
    """Reasons from a ``git config`` invocation (*args* after ``config``) that sets an exec key."""
    positionals: list[str] = []
    subcommand = ""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _CONFIG_READ_FLAGS or arg in _CONFIG_UNSET_FLAGS:
            return []
        if arg in _CONFIG_EDIT_FLAGS:
            return ["git config --edit opens the config in an editor"]
        if arg in _CONFIG_VALUE_FLAGS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        if not positionals and not subcommand and arg in _CONFIG_SUBCOMMANDS:
            subcommand = arg
        else:
            positionals.append(arg)
        i += 1
    if subcommand == "edit":
        return ["git config edit opens the config in an editor"]
    if subcommand not in ("", "set") or len(positionals) < 2:
        return []
    reason = key_reason(positionals[0], positionals[1])
    return [reason] if reason else []


def command_reasons(tokens: list[str]) -> list[str]:
    """Reasons a git command line (``tokens[0]`` is git) runs a configured program."""
    if not tokens or os.path.basename(tokens[0]) != "git":
        return []
    reasons: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-c" and i + 1 < len(tokens):
            key, sep, value = tokens[i + 1].partition("=")
            reasons.append(key_reason(key, value if sep else "true"))
            i += 2
        elif tok.startswith("--config-env"):
            spec = tok.partition("=")[2] if "=" in tok else (tokens[i + 1] if i + 1 < len(tokens) else "")
            reasons.append(key_reason(spec.partition("=")[0], None))
            i += 1 if "=" in tok else 2
        elif tok.startswith("--exec-path="):
            reasons.append("git --exec-path runs git commands from another directory")
            i += 1
        elif tok in _GIT_VALUE_FLAGS:
            i += 2
        elif tok.startswith("-"):
            i += 1
        else:
            if tok == "config":
                reasons += _config_reasons(tokens[i + 1:])
            break
    return [r for r in reasons if r]
