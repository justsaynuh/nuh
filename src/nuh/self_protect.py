"""Self-protection: commands that reconfigure, remove or bypass nuh.

``nuh_admin`` covers mutating ``nuh`` subcommands (``nuh trace --prune`` among them; not their ``--help``, which
``prints_help`` types as a read), package operations that
remove, replace or pin the ``nuh`` package, and any pip or uv pip install into
the environment the hook runs from (``install_target``). ``unguarded_launch`` covers agent
launches that skip the hooks nuh runs in, or the runtime's own permission
checks. Both are floor rules (see ``floor``).

The launch-flag tables are adapted from manuelschipper/nah (MIT),
crates/nah-proto/src/runtime_protection.rs.
"""

import os
import re

from nuh import install_target

PACKAGE = "nuh"

# nuh subcommands that change rules, trust or the installed hook.
_MUTATING_SUBCOMMANDS = frozenset({
    "allow", "allow-path", "deny", "forget", "classify", "trust", "install", "uninstall", "update",
})

_PYTHON_RE = re.compile(r"^python[0-9.]*$")
_PIP_INSTALLERS = frozenset({"install", "uninstall"})
# pipx verbs that remove, replace or modify an installed package's environment.
_PIPX_NAMED = frozenset({"install", "uninstall", "reinstall", "upgrade", "inject", "uninject", "runpip"})
_PIPX_ALL = frozenset({"uninstall-all", "reinstall-all", "upgrade-all"})
_UV_TOOL = frozenset({"install", "uninstall", "upgrade"})
_REQUIREMENT_SEPARATORS = "=<>!~[ ;@"

# Environment variables that point an agent at a config tree without nuh's hook.
AGENT_HOME_VARS: dict[str, str] = {
    "CLAUDE_CONFIG_DIR": os.path.join("~", ".claude"),
    "CODEX_HOME": os.path.join("~", ".codex"),
}

_HELP_FLAGS = frozenset({"-h", "--help", "-V", "--version"})
_NUH_HELP = frozenset({"-h", "--help"})


def prints_help(tokens: list[str]) -> bool:
    """True for ``nuh ... -h`` or ``--help``: argparse prints the help and exits before any handler runs."""
    return bool(tokens) and os.path.basename(tokens[0]) == PACKAGE and _asks_help(tokens[1:])


def _asks_help(args: list[str]) -> bool:
    """Whether nuh's argparse reads ``-h``/``--help`` among *args* (the words after ``nuh``) as the help flag.

    Only a whole word before any ``--``: after it the word is an operand (``nuh trust -- -h`` trusts a host named
    ``-h``), and a quoted ``"cmd -h"`` is one word, a value. ``nuh claude`` hands its words to claude before
    argparse sees them (``cli.main``), so its ``--help`` is claude's.
    """
    return args[:1] != ["claude"] and any(arg in _NUH_HELP for arg in _before_double_dash(args))


def nuh_admin_reason(tokens: list[str]) -> str:
    """Why *tokens* administer nuh itself, or ""."""
    if not tokens:
        return ""
    cmd, args = os.path.basename(tokens[0]), tokens[1:]
    if _PYTHON_RE.match(cmd) and len(args) >= 2 and args[0] == "-m":
        if args[1] in (PACKAGE, f"{PACKAGE}.cli"):
            cmd, args = PACKAGE, args[2:]
        elif args[1] == "pip":
            cmd, args = "pip", args[2:]
    if cmd == PACKAGE:
        if _asks_help(args):
            return ""
        sub = _first_operand(args)
        if sub in _MUTATING_SUBCOMMANDS:
            return f"nuh {sub} changes nuh's own rules or install"
        if sub == "trace" and _prunes(args):
            return "nuh trace --prune deletes nuh's LLM traces"
        if sub == "system1" and _exports(args):
            return "nuh system1 export writes a system 1 definition (by default where nuh reads it)"
        return ""
    if cmd == "uv" and args[:1] == ["pip"]:
        return _pip_reason("uv pip", args[1:], lambda: install_target.uv_pip_env(args[1:]))
    if cmd in ("pip", "pip3"):
        return _pip_reason("pip", args, lambda: install_target.pip_env(tokens[0], args))
    if cmd == "pipx":
        return _pipx_reason(args)
    if cmd == "uv" and args[:1] == ["tool"]:
        verb = _first_operand(args[1:])
        if verb in _UV_TOOL and _names_package(args[args.index(verb) + 1:]):
            return f"uv tool {verb} of the nuh package"
    return ""


def _pip_reason(label: str, args: list[str], target) -> str:
    """nuh_admin for installing nuh anywhere, or anything into the hook's own environment.

    *target* gives where the install lands (``install_target``); it is asked
    only when the package is not nuh.
    """
    verb = _first_operand(args)
    if verb not in _PIP_INSTALLERS:
        return ""
    if _names_package(args[args.index(verb) + 1:]):
        return f"pip {verb} of the nuh package"
    if install_target.is_hook_env(target()):
        return f"{label} {verb} into the environment nuh runs from (its packages run at every hook start)"
    return ""


def _pipx_reason(args: list[str]) -> str:
    verb = _first_operand(args)
    if verb in _PIPX_ALL:
        return f"pipx {verb} includes the nuh package"
    if verb in _PIPX_NAMED and _names_package(args[args.index(verb) + 1:]):
        return f"pipx {verb} of the nuh package"
    return ""


def _prunes(args: list[str]) -> bool:
    """``nuh trace`` *args* that delete traces: ``--prune`` without ``--dry-run``, both before any ``--``.

    The trace parser takes no abbreviations (allow_abbrev=False), so
    ``--prune`` is the only spelling that prunes.
    """
    flags = args[:args.index("--")] if "--" in args else args
    return "--prune" in flags and "--dry-run" not in flags


def _exports(args: list[str]) -> bool:
    """``nuh system1`` *args* that write a file: ``export`` without ``--stdout``, or with any ``--`` at all.

    The export parser takes no abbreviations (allow_abbrev=False), so
    ``--stdout`` is its only spelling. After a ``--`` it would be a PATH,
    and where the subcommand name lands around one is argparse's business:
    any ``--`` counts as writing.
    """
    rest = args[args.index("system1") + 1:]
    return _first_operand(rest) == "export" and ("--stdout" not in rest or "--" in rest)


def _first_operand(args: list[str]) -> str:
    for arg in args:
        if not arg.startswith("-"):
            return arg
    return ""


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _names_package(args: list[str]) -> bool:
    """True when a requirement among *args* names the nuh package (any version or extras).

    A local path counts when it is nuh's source tree (``pipx install -e .``
    in a checkout) or a nuh wheel or sdist: installing it replaces the hook.
    """
    for arg in args:
        if arg.startswith(_EDITABLE_ATTACHED):
            arg = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            continue
        name = arg
        for sep in _REQUIREMENT_SEPARATORS:
            name = name.split(sep, 1)[0]
        if _normalize_name(name) == PACKAGE or _is_package_path(arg):
            return True
    return False


_EDITABLE_ATTACHED = ("--editable=", "-e=")
_DIST_SUFFIXES = (".whl", ".tar.gz", ".zip")
# A [project] table's name line, for Python 3.10 (no tomllib).
_PROJECT_NAME_RE = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_PROJECT_TABLE_RE = re.compile(r"^\s*\[project\]\s*$", re.MULTILINE)
_NEXT_TABLE_RE = re.compile(r"^\s*\[", re.MULTILINE)


def _is_package_path(arg: str) -> bool:
    """True when *arg* is a local path to nuh's source tree, wheel or sdist."""
    path = arg[len("file://"):] if arg.startswith("file://") else arg
    path = os.path.expanduser(path.split("[", 1)[0])  # `.[dev]` installs `.` with extras
    if not path:
        return False
    base = os.path.basename(path.rstrip("/"))
    if base.endswith(_DIST_SUFFIXES):
        return _normalize_name(base.split("-", 1)[0]) == PACKAGE
    if not os.path.isdir(path):
        return False
    return _normalize_name(project_name(path)) == PACKAGE


def project_name(directory: str) -> str:
    """The ``[project] name`` of *directory*'s pyproject.toml, "" when it has none or cannot be read."""
    try:
        with open(os.path.join(directory, "pyproject.toml"), "rb") as f:
            raw = f.read(1_000_000)
    except OSError:
        # No readable pyproject.toml: not a project nuh's own install could
        # come from (nuh builds from pyproject only), so nothing names it.
        return ""
    text = raw.decode("utf-8", errors="replace")
    try:
        import tomllib
    except ModuleNotFoundError:
        return _project_name_by_regex(text)
    try:
        project = tomllib.loads(text).get("project")
    except tomllib.TOMLDecodeError:
        # The installer would fail on it too, but a narrow read still says
        # whose project it claims to be; that is the safer answer.
        return _project_name_by_regex(text)
    name = project.get("name") if isinstance(project, dict) else None
    return name if isinstance(name, str) else ""


def _project_name_by_regex(text: str) -> str:
    """``name = "…"`` inside the ``[project]`` table, for Python 3.10, which has no tomllib.

    Only the plain one-line form is read; anything fancier gives "" (not nuh).
    """
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        return ""
    body = text[table.end():]
    following = _NEXT_TABLE_RE.search(body)
    match = _PROJECT_NAME_RE.search(body[:following.start()] if following else body)
    return match.group(1) if match else ""


def launch_reason(tokens: list[str]) -> str:
    """Why *tokens* launch an agent with hooks or permission checks off, or ""."""
    if not tokens:
        return ""
    cmd, args = tokens[0], tokens[1:]
    if cmd == PACKAGE and args[:1] == ["claude"]:
        cmd, args = "claude", args[1:]
    if any(arg in _HELP_FLAGS for arg in _before_double_dash(args)):
        return ""
    if cmd == "claude":
        return _claude_reason(args)
    if cmd == "codex":
        return _codex_reason(args)
    return ""


def _before_double_dash(args: list[str]) -> list[str]:
    return args[:args.index("--")] if "--" in args else args


def _option_values(args: list[str], options: tuple[str, ...]) -> list[str]:
    """Every value given to one of *options*: separate, after ``=``, or attached to a short option."""
    values = []
    for i, arg in enumerate(args):
        for option in options:
            if arg == option:
                if i + 1 < len(args):
                    values.append(args[i + 1])
            elif arg.startswith(option + "="):
                values.append(arg[len(option) + 1:])
            elif not option.startswith("--") and arg.startswith(option) and len(arg) > len(option):
                values.append(arg[len(option):])
    return values


def _claude_reason(args: list[str]) -> str:
    for flag in ("--dangerously-skip-permissions", "--bare", "--safe-mode"):
        if flag in args:
            return f"claude {flag} runs without nuh's hooks or permission checks"
    if "bypassPermissions" in _option_values(args, ("--permission-mode",)):
        return "claude --permission-mode bypassPermissions skips permission checks"
    for value in _option_values(args, ("--settings",)):
        if _settings_may_disable_hooks(value):
            return "claude --settings may set disableAllHooks"
    for value in _option_values(args, ("--setting-sources",)):
        if "user" not in (part.strip() for part in value.split(",")):
            return "claude --setting-sources without user skips the settings that load nuh"
    return ""


def _settings_may_disable_hooks(value: str) -> bool:
    """True when a ``--settings`` value (inline JSON or a file) may mention disableAllHooks.

    A file that cannot be read counts: what it would set is unknown.
    """
    text = value
    if not value.lstrip().startswith("{"):
        try:
            with open(os.path.expanduser(value), encoding="utf-8", errors="replace") as f:
                text = f.read(1_000_000)
        except OSError:
            return True
    return "disableAllHooks" in text or "\\u" in text


def _codex_reason(args: list[str]) -> str:
    for flag in ("--dangerously-bypass-approvals-and-sandbox", "--yolo"):
        if flag in args:
            return f"codex {flag} skips approvals and the sandbox"
    if any(name in ("hooks", "codex_hooks") for name in _option_values(args, ("--disable",))):
        return "codex --disable hooks turns nuh's hooks off"
    for assignment in _option_values(args, ("-c", "--config")):
        if _codex_override_disables_hooks(assignment):
            return f"codex -c {assignment} may turn hooks off"
    return ""


def _codex_override_disables_hooks(assignment: str) -> bool:
    """True for a ``-c KEY=VALUE`` that may switch the hooks feature off (any table that names hooks)."""
    key, sep, value = assignment.partition("=")
    if not sep:
        return False
    segments = [part.strip().strip("\"'") for part in key.split(".")]
    if segments[-2:] in (["features", "hooks"], ["features", "codex_hooks"]):
        return value.strip() != "true"
    if segments[-1] == "hooks" or segments[0] == "hooks":
        return True
    return value.lstrip().startswith("{") and ("hooks" in value or "\\u" in value)


def agent_home_reason(name: str, value: str) -> str:
    """Why assigning *value* to environment variable *name* moves an agent off nuh's hooks, or ""."""
    default = AGENT_HOME_VARS.get(name)
    if default is None:
        return ""
    chosen = os.path.normpath(os.path.expanduser(os.path.expandvars(value))) if value else ""
    if chosen in ("", os.path.normpath(os.path.expanduser(default))):
        return ""
    return f"{name}={value} points the agent at a config without nuh's hooks"
