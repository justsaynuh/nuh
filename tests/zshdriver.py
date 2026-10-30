"""Drive a real interactive zsh in a pseudo-terminal and record what one TAB offers, for the completion tests.

The shell starts with ``zsh -f`` (no user rc files) and loads compsys from a
temporary ``fpath`` holding only the script under test. ``compadd`` is
wrapped to append every match it is asked to add (``M <match>``) and every
display string it is given (``D <text>``) to a file, and TAB is bound to a
completion widget that writes ``END`` when completion is done, so the test
reads a file instead of parsing terminal output, and knows when to stop.

A call with ``-D``, ``-O`` or ``-A`` only filters or collects, adding
nothing, so it is passed straight through: recording it would run it twice,
and ``-D`` filters its array in place (``_describe`` keeps descriptions in a
parallel array, which a second pass misaligns, dropping matches).
"""

import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import time
from dataclasses import dataclass
from pathlib import Path

_SETUP = r"""
fpath=({fdir} $fpath)
autoload -Uz compinit && compinit -u -D
compadd() {{
  local -a __hits __descr
  local __i
  if (( ! ${{@[(I)-[DOA]]}} )); then
    builtin compadd -O __hits "$@"
    for (( __i = 1; __i < $#; __i++ )); do
      [[ ${{@[__i]}} == -d ]] && __descr=("${{(@P)${{@[__i+1]}}}}")
    done
    print -rl -- "${{(@)^__hits/#/M }}" "${{(@)^__descr/#/D }}" >> {out}
  fi
  builtin compadd "$@"
}}
_capture() {{ _main_complete; print -r -- END >> {out} }}
zle -C capture complete-word _capture
bindkey '^I' capture
"""

_WIDE = struct.pack("HHHH", 50, 1000, 0, 0)
"""A 1000-column terminal, so ``_describe`` doesn't truncate the descriptions it lays out."""

TIMEOUT = 15.0
"""Seconds one completion may take; a run under load takes well under one."""


@dataclass(frozen=True)
class Offered:
    """What one TAB offered: the matches, and the display lines (``name -- description``) of described ones."""

    matches: frozenset
    displays: tuple

    def description(self, match: str) -> str:
        """The description shown beside *match*. Raises ``KeyError`` if it has none."""
        for line in self.displays:
            name, sep, text = line.partition(" -- ")
            if sep and match in name.split():
                return text.strip()
        raise KeyError(match)


def complete(script: str, name: str, line: str, *, cwd: Path) -> Offered:
    """Install *script* as the completion function ``_<name>``, type *line*, press TAB, return what was offered.

    Raises:
        TimeoutError: The completion didn't finish within ``TIMEOUT`` seconds.
    """
    fdir = cwd / ".zfunc"
    fdir.mkdir(exist_ok=True)
    (fdir / f"_{name}").write_text(script, encoding="utf-8")
    out = cwd / ".offered"
    out.write_text("", encoding="utf-8")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, _WIDE)
    env = {"HOME": str(cwd), "TERM": "xterm", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    proc = subprocess.Popen(["zsh", "-f", "-i"], stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                            start_new_session=True)
    os.close(slave)
    try:
        os.write(master, _SETUP.format(fdir=fdir, out=out).encode())
        os.write(master, f"{line}\t".encode())
        return _wait(master, out)
    finally:
        proc.kill()
        proc.wait()
        os.close(master)


def _wait(master: int, out: Path) -> Offered:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            os.read(master, 65536)  # drain the terminal so the shell never blocks writing to it
        lines = out.read_text(encoding="utf-8").splitlines()
        if lines and lines[-1] == "END":
            matches = frozenset(line[2:] for line in lines if line.startswith("M "))
            return Offered(matches, tuple(line[2:] for line in lines if line.startswith("D ")))
    raise TimeoutError(f"no completion within {TIMEOUT}s; recorded so far: {out.read_text(encoding='utf-8')!r}")
