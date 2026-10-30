"""Keep the test suite out of the developer's home directory, and catch it if it strays.

``isolate_home`` points ``HOME`` at an empty directory before nuh is imported,
so every path nuh resolves from ``~`` (the log, the config, ``~/.claude``
settings and hooks) lands there, in this process and in every subprocess a
test starts. It must run before the first ``import nuh``: nuh resolves some
paths at import time, and a HOME changed later would split them from the
paths it resolves per call.

``install`` adds an audit hook that records every write-like file operation
under the real home that is not inside an allowed root (the repository, the
fake home, the system temp dir). ``take`` hands the records to the conftest
fixture that fails the test which made them.
"""

import os
import sys
import tempfile

_WRITE_EVENTS = frozenset({"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.symlink",
                           "os.link", "os.truncate", "os.chmod", "os.chown", "os.utime", "shutil.rmtree",
                           "shutil.copyfile", "shutil.move"})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

_real_home = ""
_allowed: tuple[str, ...] = ()
_strays: list[str] = []
_busy = False


# Variables that point zsh or nuh's completion step at the real home's shell setup (oh-my-zsh exports ZSH).
_HOME_POINTERS = ("ZDOTDIR", "ZSH", "ZSH_CACHE_DIR", "XDG_DATA_HOME")


def isolate_home(fake_home: str) -> str:
    """Point HOME at *fake_home* for this process and its children; returns the real home it replaced.

    The variables in ``_HOME_POINTERS`` go too: `nuh install` would otherwise
    write the zsh completion into the real oh-my-zsh, and `zsh -ic` would read
    the real startup files.
    """
    global _real_home
    _real_home = os.path.realpath(os.path.expanduser("~"))
    os.environ["HOME"] = fake_home
    for name in _HOME_POINTERS:
        os.environ.pop(name, None)
    return _real_home


def install(allowed_roots: list[str]) -> None:
    """Start recording writes under the real home outside *allowed_roots* (see ``stray``)."""
    global _allowed, _real_home
    _real_home = _real_home or os.path.realpath(os.path.expanduser("~"))
    _allowed = tuple(os.path.realpath(root) for root in [*allowed_roots, tempfile.gettempdir()])
    sys.addaudithook(_audit)


def _paths(event: str, args: tuple) -> list[object]:
    """The paths a write-like audit event touches; [] for a read or a non-write event."""
    if event == "open":
        path, mode, flags = args
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS)
        return [path] if writes else []
    if event in ("os.rename", "os.replace", "os.symlink", "os.link", "shutil.copyfile", "shutil.move"):
        return [args[1]]  # the destination is what gets written
    if event in _WRITE_EVENTS:
        return [args[0]]
    return []


def stray(path: object) -> bool:
    """True when writing *path* would touch the real home outside every allowed root.

    Byte-code caches are not the suite's doing (the interpreter writes them
    next to any module it imports), and a bare file descriptor names no path.
    """
    if not _real_home or isinstance(path, int):
        return False
    try:
        text = os.fsdecode(path)
    except TypeError:
        return False
    absolute = os.path.abspath(text)
    if "__pycache__" in absolute or absolute.endswith(".pyc"):
        return False
    if not (absolute == _real_home or absolute.startswith(_real_home + os.sep)):
        return False
    real = os.path.realpath(absolute)
    return not any(real == root or real.startswith(root + os.sep) for root in _allowed)


def _audit(event: str, args: tuple) -> None:
    global _busy
    if _busy or (event != "open" and event not in _WRITE_EVENTS):
        return
    _busy = True  # realpath inside the hook must not re-enter it
    try:
        for path in _paths(event, args):
            if stray(path):
                _strays.append(f"{event} {os.fsdecode(path)}")
    finally:
        _busy = False


def real_home() -> str:
    """The developer's home, as it was before ``isolate_home``."""
    return _real_home


def take() -> list[str]:
    """The stray writes recorded since the last call, cleared."""
    found = list(_strays)
    _strays.clear()
    return found
