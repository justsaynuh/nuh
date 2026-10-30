"""The zsh completion step of ``nuh install``, ``update`` and ``uninstall``: write, refresh or remove ``_nuh``.

pipx links a package's executables and man pages, never its shell
completions, so ``nuh install`` writes the completion itself
(``completion_files`` says where). An interactive zsh is asked for
oh-my-zsh's ``ZSH_CACHE_DIR`` and for ``fpath``, since both are set by the
user's ``~/.zshrc``. The file is written atomically (a temp file in the same
directory, renamed over the old one) with mode 644, and only a file nuh
wrote (its header line) is ever replaced or deleted. Shell startup files are
never edited: when site-functions is not on ``fpath``, nuh says what to add.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nuh import completion_files
from nuh.cli_completion import script

LABEL = "  Completion:  "
_PAD = " " * len(LABEL)
SENTINEL = "__nuh_completion_query__"
"""Printed before the answers, so whatever ~/.zshrc prints (banners, prompts) is skipped."""
_QUERY = f'print -r -- {SENTINEL}; print -r -- "$ZSH_CACHE_DIR"; print -rl -- $fpath'
# An interactive zsh runs the user's ~/.zshrc (oh-my-zsh, plugins), which can take a few seconds.
_TIMEOUT_S = 10


@dataclass(frozen=True)
class Shell:
    """What the user's interactive zsh reports; None where it did not answer."""

    cache_dir: str | None
    fpath: list[str] | None


def ask_zsh(zsh: str) -> Shell:
    """``ZSH_CACHE_DIR`` and ``fpath`` as an interactive *zsh* sets them (``zsh -ic``)."""
    try:
        proc = subprocess.run([zsh, "-ic", _QUERY], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{LABEL}zsh did not answer ({exc}); oh-my-zsh and fpath unknown")
        return Shell(None, None)
    lines = proc.stdout.splitlines()
    if SENTINEL not in lines:
        print(f"{LABEL}zsh did not answer (exit {proc.returncode}); oh-my-zsh and fpath unknown")
        return Shell(None, None)
    cache_dir, *fpath = lines[len(lines) - lines[::-1].index(SENTINEL):] or [""]
    return Shell(cache_dir or None, [entry for entry in fpath if entry])


def install(*, update: bool) -> bool:
    """Write ``_nuh`` (``update``: refresh the files nuh wrote, or write as install does); False when a write failed.

    Install writes the target (``completion_files.target``) and refreshes any
    other copy nuh wrote, so no stale one stays on ``fpath``.
    """
    zsh = shutil.which("zsh")
    if zsh is None:
        print(f"{LABEL}skipped (zsh is not installed)")
        return True
    shell = ask_zsh(zsh)
    existing = completion_files.written(shell.cache_dir)
    first = existing if update and existing else [completion_files.target(shell.cache_dir)]
    paths = first + [path for path in existing if path not in first]
    text = script("zsh")
    ok = True
    for path in paths:
        ok = _install_one(path, text, shell.fpath) and ok
    return ok


def remove() -> bool:
    """Delete every ``_nuh`` nuh wrote, leaving any other alone; False when a delete failed."""
    zsh = shutil.which("zsh")
    cache_dir = ask_zsh(zsh).cache_dir if zsh else None
    ok, seen = True, False
    for path in completion_files.candidates(cache_dir):
        if completion_files.nuh_written(path):
            seen = True
            try:
                path.unlink()
            except OSError as exc:
                print(f"{LABEL}could not delete {_show(path)}: {exc}", file=sys.stderr)
                ok = False
                continue
            print(f"{LABEL}{_show(path)} (deleted)")
        elif path.exists():
            seen = True
            print(f"{LABEL}{_show(path)} (kept: nuh did not write it)")
    if not seen:
        print(f"{LABEL}not found")
    return ok


def _install_one(path: Path, text: str, fpath: list[str] | None) -> bool:
    if path.exists() and not completion_files.nuh_written(path):
        print(f"{LABEL}{_show(path)} left alone: nuh did not write it")
        return True
    try:
        _write(path, text)
    except OSError as exc:
        print(f"{LABEL}could not write {_show(path)}: {exc}", file=sys.stderr)
        return False
    print(f"{LABEL}{_show(path)} (zsh; start a new shell to load it)")
    if path.parent == completion_files.site_functions() and not _on_fpath(path.parent, fpath):
        print(f"{_PAD}Not on zsh's fpath: add `fpath=({_show(path.parent)} $fpath)` to ~/.zshrc before compinit")
    return True


def _write(path: Path, text: str) -> None:
    """Replace *path* with *text* in one rename, mode 644; no temp file is left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            os.fchmod(f.fileno(), 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _on_fpath(directory: Path, fpath: list[str] | None) -> bool:
    """False when *fpath* is unknown: the hint to add the directory is then the safe answer."""
    real = os.path.realpath(directory)
    return fpath is not None and any(os.path.realpath(os.path.expanduser(entry)) == real for entry in fpath)


def _show(path: Path) -> str:
    """*path* with the home directory as ``~``."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text
