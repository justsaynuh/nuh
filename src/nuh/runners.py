"""Package and environment runners: the command they run, so it is classified instead of the runner.

``uv run rm -rf ~`` is ``rm -rf ~`` with a different environment. ``unwrap``
strips the runner and its own options (value-taking options consume their
value) and returns the inner command. A runner that fetches a package before
running it (``npx``, ``uvx``, ``pipx run``, ``pnpm dlx``, ``bunx``, ``yarn dlx``)
is unwrapped only when the name is already a local program (on PATH, a path,
or in ``node_modules/.bin``); a package that must be fetched is ``fetched``,
and stays the runner's own classification (``package_run``) unless its name
classified as a command is stricter (``bash_stage._classify_runner``).

The unwrap list follows manuelschipper/nah (MIT), 0.x 952b5663 (uv run,
uv tool run/uvx, npx/npm exec) and the audit's mise exec item; the option
tables are written from each tool's documented options.
"""

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Runner:
    name: str                 # "uv run", "npx", ...
    inner: list[str] | None   # the command it runs; None when nuh cannot read one
    # A package fetched to run instead (its version spec dropped): what its name says it runs, unverified.
    fetched: list[str] | None = None


# Options that take a value, per runner (long forms also accept --opt=value).
_UV_GLOBAL = frozenset({"--directory", "--project", "--cache-dir", "--config-file", "--color",
                        "--allow-insecure-host", "--python-preference"})
_UV_RUN = frozenset({
    "--with", "--with-editable", "--with-requirements", "-w", "--python", "-p", "--project", "--directory",
    "--package", "--extra", "--group", "--only-group", "--no-group", "--index", "--index-url",
    "--extra-index-url", "--default-index", "-i", "--find-links", "-f", "--env-file", "--config-file",
    "--cache-dir", "--color", "--exclude-newer", "--exclude-newer-package", "--index-strategy",
    "--keyring-provider", "--resolution", "--prerelease", "--link-mode", "--refresh-package",
    "--reinstall-package", "--upgrade-package", "-P", "--no-binary-package", "--no-build-package",
    "--no-sources-package", "--config-setting", "-C", "--python-platform", "--allow-insecure-host",
    "--from", "--python-preference", "--build-constraints", "--constraints", "--overrides", "-c",
})
_POETRY = frozenset({"-C", "--directory", "-P", "--project"})
_PIPX_RUN = frozenset({"--spec", "--python", "--pip-args", "--index-url", "-i", "--backend"})
_NPM_GLOBAL = frozenset({"--prefix", "-w", "--workspace", "--userconfig", "--registry", "--cache",
                         "--loglevel", "-C"})
_NPX = frozenset({"-p", "--package", "-c", "--call", "-w", "--workspace", "--prefix", "--registry", "--cache",
                  "--userconfig", "--loglevel"})
_PNPM_GLOBAL = frozenset({"-C", "--dir", "-F", "--filter", "--filter-prod", "--workspace-concurrency",
                          "--reporter", "--loglevel"})
_PNPM_DLX = frozenset({"--package", "--allow-build"})
_BUNX = frozenset({"-p", "--package", "--cwd", "--config", "-c"})
_YARN = frozenset({"--cwd", "-p", "--package"})
_BUNDLE = frozenset({"--gemfile"})
_MISE = frozenset({"-C", "--cd", "-E", "--env", "-j", "--jobs", "-c", "--command"})

_PY_SCRIPT_SUFFIXES = (".py", ".pyw")


def _split(args: list[str], value_flags: frozenset[str]) -> tuple[dict[str, str], list[str], bool]:
    """(options seen with their values, the operands from the first non-option on, whether ``--`` ended them).

    An option nuh does not know is taken as a flag without a value.
    """
    seen: dict[str, str] = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return seen, args[i + 1:], True
        if not tok.startswith("-") or tok == "-":
            return seen, args[i:], False
        if tok.startswith("--") and "=" in tok:
            name, value = tok.split("=", 1)
            seen[name] = value
            i += 1
        elif tok in value_flags:
            seen[tok] = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif not tok.startswith("--") and tok[:2] in value_flags and len(tok) > 2:
            seen[tok[:2]] = tok[2:]  # attached short value: -p3.12
            i += 1
        else:
            seen[tok] = ""
            i += 1
    return seen, [], False


def _local_program(name: str) -> bool:
    """True when *name* runs without fetching a package: a path, on PATH, or in node_modules/.bin."""
    if "/" in name:
        return True
    if shutil.which(name):
        return True
    directory = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(directory, "node_modules", ".bin", name)):
            return True
        parent = os.path.dirname(directory)
        if parent == directory:
            return False
        directory = parent


def _fetching(name: str, operands: list[str]) -> Runner:
    """A runner that fetches *operands[0]* unless it is a local program."""
    if operands and _local_program(operands[0]):
        return Runner(name, operands)
    if not operands:
        return Runner(name, None)
    package = operands[0]
    at = package.rfind("@")  # rimraf@5 runs rimraf
    return Runner(name, None, [package[:at] if at > 0 else package, *operands[1:]])


def _uv(args: list[str]) -> Runner | None:
    _seen, rest, _dd = _split(args, _UV_GLOBAL)
    if rest[:1] == ["run"]:
        seen, operands, _dd = _split(rest[1:], _UV_RUN)
        if "-m" in seen or "--module" in seen:
            module = seen.get("-m") or seen.get("--module")
            return Runner("uv run", ["python", "-m", module, *operands] if module else ["python", "-m", *operands])
        if operands and (operands[0].endswith(_PY_SCRIPT_SUFFIXES) or "--script" in seen or "-s" in seen):
            return Runner("uv run", ["python", *operands])
        return Runner("uv run", operands or None)
    if rest[:2] == ["tool", "run"]:
        return _fetching("uv tool run", _split(rest[2:], _UV_RUN)[1])
    return None


def _npm(args: list[str]) -> Runner | None:
    _seen, rest, _dd = _split(args, _NPM_GLOBAL)
    if rest[:1] in (["exec"], ["x"]):
        return _npx("npm exec", rest[1:])
    return None


def _npx(name: str, args: list[str]) -> Runner:
    seen, operands, _dd = _split(args, _NPX)
    call = seen.get("-c") or seen.get("--call")
    if call:
        return Runner(name, ["sh", "-c", call])
    return _fetching(name, operands)


def _pnpm(args: list[str]) -> Runner | None:
    _seen, rest, _dd = _split(args, _PNPM_GLOBAL)
    if rest[:1] == ["exec"]:
        seen, operands, _dd = _split(rest[1:], _PNPM_GLOBAL)
        if "-c" in seen or "--shell-mode" in seen:
            return Runner("pnpm exec", ["sh", "-c", " ".join(operands)] if operands else None)
        return Runner("pnpm exec", operands or None)
    if rest[:1] == ["dlx"]:
        seen, operands, _dd = _split(rest[1:], _PNPM_DLX)
        if "-c" in seen or "--shell-mode" in seen:
            return Runner("pnpm dlx", None)
        return _fetching("pnpm dlx", operands)
    return None


def _yarn(args: list[str]) -> Runner | None:
    _seen, rest, _dd = _split(args, _YARN)
    if rest[:1] == ["exec"]:
        return Runner("yarn exec", _split(rest[1:], _YARN)[1] or None)
    if rest[:1] == ["dlx"]:
        return _fetching("yarn dlx", _split(rest[1:], _YARN)[1])
    return None


def _mise(args: list[str]) -> Runner | None:
    if args[:1] not in (["exec"], ["x"]):
        return None
    seen, operands, double_dash = _split(args[1:], _MISE)
    command = seen.get("-c") or seen.get("--command")
    if command:
        return Runner("mise exec", ["sh", "-c", command])
    if not double_dash:
        # Tool specs then a shell, or a -- we did not see: no command nuh can read.
        if "--" in operands:
            operands = operands[operands.index("--") + 1:]
        else:
            return Runner("mise exec", None)
    return Runner("mise exec", operands or None)


def unwrap(tokens: list[str]) -> Runner | None:
    """The runner *tokens* invoke and the command it runs; None when *tokens* are not a runner."""
    if not tokens:
        return None
    cmd, args = os.path.basename(tokens[0]), tokens[1:]
    if cmd == "uv":
        return _uv(args)
    if cmd == "uvx":
        return _fetching("uvx", _split(args, _UV_RUN)[1])
    if cmd == "poetry":
        _seen, rest, _dd = _split(args, _POETRY)
        return Runner("poetry run", _split(rest[1:], _POETRY)[1] or None) if rest[:1] == ["run"] else None
    if cmd == "pipx":
        return _fetching("pipx run", _split(args[1:], _PIPX_RUN)[1]) if args[:1] == ["run"] else None
    if cmd == "npx":
        return _npx("npx", args)
    if cmd == "npm":
        return _npm(args)
    if cmd == "pnpm":
        return _pnpm(args)
    if cmd == "bunx" or (cmd == "bun" and args[:1] == ["x"]):
        return _fetching("bunx", _split(args if cmd == "bunx" else args[1:], _BUNX)[1])
    if cmd == "yarn":
        return _yarn(args)
    if cmd == "bundle":
        return Runner("bundle exec", _split(args[1:], _BUNDLE)[1] or None) if args[:1] == ["exec"] else None
    if cmd == "mise":
        return _mise(args)
    return None
