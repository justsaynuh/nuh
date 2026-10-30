"""The CLI's one-line hint that nuh's zsh completion is not installed (``cli.main``; never the hook).

``cli.main`` calls ``show`` only for a zsh user at a terminal (stderr is a
TTY, ``$SHELL`` is zsh): after any ``--help`` output, and before every other
command except ``completion``, ``install``, ``update`` and ``uninstall``. It
repeats on every such run until the completion exists or the global config
sets ``completion_hint: false``.

It goes to stderr, never stdout, so help text and command output stay clean
for pipes; the man page and docs generators render from the parser and never
run ``main``. Finding the file only stats and reads the candidate paths
(``completion_files``, with ``ZSH_CACHE_DIR`` taken from the environment: no
zsh is started), and the config is loaded only when the hint would show. It
writes nothing.
"""

from __future__ import annotations

import os
import sys

from nuh import completion_files

TEXT = ("nuh: zsh completion isn't installed; run 'nuh install' (or 'nuh completion zsh > DIR_ON_FPATH/_nuh';"
        " completion_hint: false in the global config hides this)")


def show() -> None:
    """Print the hint to stderr unless a ``_nuh`` nuh wrote exists or the global config turns it off."""
    if completion_files.written(os.environ.get("ZSH_CACHE_DIR")):
        return
    from nuh.config import ConfigError, completion_hint  # only when the hint would show: --help stays cheap
    try:
        enabled = completion_hint()
    except ConfigError as exc:
        sys.stderr.write(f"nuh: {exc}; completion_hint uses its default (true)\n")
        enabled = True
    if enabled:
        sys.stderr.write(TEXT + "\n")
