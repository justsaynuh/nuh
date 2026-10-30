"""Which Python environment a ``pip`` or ``uv pip`` command installs into.

Any package installed into the environment the hook runs from changes the
hook: a ``.pth`` file it drops runs at every hook start. Installs into other
environments are ordinary package installs (installing nuh itself anywhere is
``nuh_admin`` regardless; see ``self_protect``).

An environment is a venv or conda directory (its real path), ``SYSTEM`` for
a Python outside one, or ``USER`` for ``pip --user``'s site.
"""

import os
import shutil
import sys

SYSTEM = "<system>"
USER = "<user>"

_PIP_DIR_OPTIONS = ("--target", "-t", "--prefix", "--root")
_PYTHON_OPTIONS = ("--python", "-p")


def _env_dir(directory: str) -> bool:
    return os.path.exists(os.path.join(directory, "pyvenv.cfg")) or os.path.isdir(
        os.path.join(directory, "conda-meta"))


def hook_envs() -> frozenset[str]:
    """The hook's environments: this interpreter's, and pipx's ``nuh`` venv when it exists.

    ``nuh install`` writes ``sys.executable`` into the guard's hook command,
    so the interpreter deciding now is the one the installed guard runs.
    """
    envs = {os.path.realpath(sys.prefix) if _env_dir(sys.prefix) else SYSTEM}
    pipx_home = os.environ.get("PIPX_HOME") or os.path.expanduser(os.path.join("~", ".local", "share", "pipx"))
    for home in (pipx_home, os.path.expanduser(os.path.join("~", ".local", "pipx"))):
        venv = os.path.join(home, "venvs", "nuh")
        if _env_dir(venv):
            envs.add(os.path.realpath(venv))
    return frozenset(envs)


def program_env(program: str) -> str | None:
    """The environment *program* (a pip or python, path-spelled or found on PATH) belongs to; None if not found."""
    path = os.path.expanduser(program) if "/" in program else shutil.which(program)
    if not path:
        return None
    # Not realpath on the file: a venv's python is a symlink to the base interpreter.
    root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    return os.path.realpath(root) if _env_dir(root) else SYSTEM


def _option_value(args: list[str], options: tuple[str, ...]) -> str | None:
    for i, arg in enumerate(args):
        for option in options:
            if arg == option and i + 1 < len(args):
                return args[i + 1]
            if option.startswith("--") and arg.startswith(option + "="):
                return arg[len(option) + 1:]
    return None


def _python_env(value: str) -> str | None:
    """The environment a ``--python`` value names: a venv dir, an interpreter path, or a name on PATH."""
    path = os.path.expanduser(value)
    if os.path.isdir(path):
        return os.path.realpath(path) if _env_dir(path) else None
    if "/" in value or not value[:1].isdigit():
        return program_env(value)
    return None  # a version request (3.12): uv picks an interpreter nuh cannot predict


def pip_env(program: str, args: list[str]) -> str | None:
    """Where ``<program> [-m pip] <args>`` installs; None when nuh cannot tell."""
    target = _option_value(args, _PIP_DIR_OPTIONS)
    if target is not None:
        return os.path.realpath(os.path.expanduser(target))
    if "--user" in args:
        return USER
    python = _option_value(args, ("--python",))
    return _python_env(python) if python is not None else program_env(program)


def uv_pip_env(args: list[str]) -> str | None:
    """Where ``uv pip <args>`` installs: --python, --system, $VIRTUAL_ENV, else the nearest .venv."""
    target = _option_value(args, ("--target", "--prefix"))
    if target is not None:
        return os.path.realpath(os.path.expanduser(target))
    python = _option_value(args, _PYTHON_OPTIONS)
    if python is not None:
        return _python_env(python)
    if "--system" in args:
        return program_env("python3")
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        return os.path.realpath(active)
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, ".venv")
        if _env_dir(candidate):
            return os.path.realpath(candidate)
        parent = os.path.dirname(directory)
        if parent == directory:
            return None  # uv finds no environment and refuses to install
        directory = parent


def is_hook_env(env: str | None) -> bool:
    """True when *env* is an environment this hook runs from (``--user`` counts for a system hook)."""
    if env is None:
        return False
    hooks = hook_envs()
    return env in hooks or (env == USER and SYSTEM in hooks)
