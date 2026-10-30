"""Program identity: a program spelled with a slash is that file, not the tool its basename names.

``./git status`` runs whatever ``./git`` is, so it is classified as running
that file (script execution with content inspection), not as ``git``. A
slash-spelled program keeps its tool identity when it is that tool:

- it resolves (realpath) to the same file as the PATH lookup of its basename;
- it lives in a standard executable directory (system and per-user install
  locations: /usr/bin, ~/.local/bin, ~/.cargo/bin, toolchain managers);
- the user's global ``classify:`` names that exact path;
- it is a Python virtualenv's entry point: a console-script wrapper pip or
  uv generated, a compiled program, or a link into a standard directory.

The venv case is deliberate: everything a venv entry point runs already
comes from the venv's site-packages, which nuh does not inspect for
``python -m pytest`` either, so treating ``.venv/bin/pytest`` as pytest
opens nothing new. A hand-written text script in a venv's bin is still a
script.

Idea from manuelschipper/nah (MIT), crates/nah-proto/src/labels.rs
(``standard_executable_directory``).
"""

import ast
import os
import shutil

_HOME = os.path.expanduser("~")

_SYSTEM_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/local/sbin",
                "/usr/libexec", "/usr/lib", "/usr/local/lib", "/usr/local/Cellar", "/opt/homebrew",
                "/nix/store", "/run/current-system/sw/bin", "/snap/bin", "/usr/games")
_HOME_DIRS = (".local/bin", ".local/share/uv/python", ".local/share/uv/tools", ".local/share/mise/installs",
              ".local/share/pnpm", ".local/pipx/venvs", ".cargo/bin", ".rustup/toolchains", "go/bin",
              ".pyenv/versions", ".rbenv/versions", ".nvm/versions", ".volta", ".bun/bin", ".deno/bin",
              ".asdf/installs", ".sdkman/candidates")

_MAX_WRAPPER_BYTES = 4096
_BINARY_MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                 b"\xca\xfe\xba\xbe", b"MZ")


def _standard_dirs() -> list[str]:
    dirs = [os.path.realpath(d) for d in _SYSTEM_DIRS]
    dirs += [os.path.realpath(os.path.join(_HOME, d)) for d in _HOME_DIRS]
    return dirs


_STANDARD_DIRS = _standard_dirs()


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def is_path_spelled(program: str) -> bool:
    """True for a program named by a path (``./git``, ``bin/rails``, ``/tmp/x/git``)."""
    return "/" in program


def _expand(program: str) -> str:
    return os.path.expanduser(os.path.expandvars(program))


def is_tool(program: str) -> bool:
    """True when *program* is the tool its basename names (see the module docstring).

    A program not spelled with a slash is always its own name.
    """
    if not is_path_spelled(program):
        return True
    written = os.path.abspath(_expand(program))
    real = os.path.realpath(written)
    if any(_under(real, d) for d in _STANDARD_DIRS):
        return True
    found = shutil.which(os.path.basename(written))
    if found and os.path.realpath(found) == real:
        return True
    if real in _user_classified_paths():
        return True
    return _is_venv_entry(written, real)


def _user_classified_paths() -> frozenset[str]:
    """Programs the user's global ``classify:`` names by path: they asked for that file by name."""
    try:
        from nuh.config import get_config  # lazy import to avoid circular
        classify = get_config().classify_global
    except Exception as exc:
        # Without the config no path is user-trusted; the program is then
        # inspected as a script, which is the stricter outcome.
        import sys
        sys.stderr.write(f"nuh: config: classify paths: {exc}\n")
        return frozenset()
    found = set()
    for prefixes in classify.values():
        if not isinstance(prefixes, list):
            continue
        for prefix in prefixes:
            first = str(prefix).split()[0] if str(prefix).split() else ""
            if "/" in first:
                found.add(os.path.realpath(_expand(first)))
    return frozenset(found)


def _is_venv_entry(written: str, real: str) -> bool:
    """True for an entry point in a virtualenv's bin directory that is not a hand-written script."""
    bin_dir = os.path.dirname(written)
    if os.path.basename(bin_dir) not in ("bin", "Scripts"):
        return False
    if not os.path.isfile(os.path.join(os.path.dirname(bin_dir), "pyvenv.cfg")):
        return False
    try:
        with open(real, "rb") as f:
            head = f.read(_MAX_WRAPPER_BYTES + 1)
    except OSError:
        # Missing or unreadable: nothing shows it is a generated entry point.
        return False
    return head.startswith(_BINARY_MAGIC) or is_console_wrapper(head)


def is_binary(head: bytes) -> bool:
    """True when *head* (a file's first bytes) starts like a compiled executable."""
    return head.startswith(_BINARY_MAGIC)


def is_console_wrapper(head: bytes) -> bool:
    """True for the console-script launcher pip or uv writes for an entry point.

    Imports, then ``if __name__ == "__main__":`` that may rewrite ``sys.argv[0]``
    and ends in ``sys.exit(<imported function>())``. Anything else is a script.
    """
    if len(head) > _MAX_WRAPPER_BYTES or not head.startswith(b"#!"):
        return False
    first_line = head.split(b"\n", 1)[0]
    if b"python" not in first_line:
        return False
    try:
        tree = ast.parse(head.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError, ValueError):
        return False
    imported: set[str] = set()
    main_seen = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            continue
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
            continue
        if isinstance(node, ast.If) and _is_main_test(node.test) and not node.orelse and not main_seen:
            if not _main_body_ok(node.body, imported):
                return False
            main_seen = True
            continue
        return False
    return main_seen


def _is_main_test(test: ast.expr) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__")


def _main_body_ok(body: list[ast.stmt], imported: set[str]) -> bool:
    for stmt in body:
        if isinstance(stmt, ast.Assign) and _argv_target(stmt.targets) and _plain_value(stmt.value):
            continue
        if isinstance(stmt, ast.If) and _plain_value(stmt.test) and _main_body_ok(stmt.body, imported) \
                and _main_body_ok(stmt.orelse, imported):
            continue
        if isinstance(stmt, ast.Expr) and _is_exit_call(stmt.value, imported):
            continue
        return False
    return True


def _is_sys_argv(node: ast.expr) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "argv" and isinstance(node.value, ast.Name)
            and node.value.id == "sys")


def _argv_target(targets: list[ast.expr]) -> bool:
    return len(targets) == 1 and isinstance(targets[0], ast.Subscript) and _is_sys_argv(targets[0].value)


_PLAIN_METHODS = frozenset({"endswith", "startswith", "removesuffix"})


def _plain_value(node: ast.expr) -> bool:
    """True when *node* only reads and reshapes ``sys.argv`` (``re.sub``, endswith, slices)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if not isinstance(func, ast.Attribute):
                return False
            is_re_sub = func.attr == "sub" and isinstance(func.value, ast.Name) and func.value.id == "re"
            if not (is_re_sub or func.attr in _PLAIN_METHODS):
                return False
        elif isinstance(sub, ast.Name) and sub.id not in ("sys", "re"):
            return False
        elif isinstance(sub, ast.Attribute) and sub.attr not in _PLAIN_METHODS | {"argv", "sub"}:
            return False
    return True


def _is_exit_call(node: ast.expr, imported: set[str]) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "exit"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "sys" and len(node.args) == 1
            and not node.keywords):
        return False
    inner = node.args[0]
    return (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id in imported
            and not inner.args and not inner.keywords)
