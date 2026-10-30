"""``nuh completion SHELL``: print the shell's completion script, rendered from the live parser.

Nothing is written by hand or committed: the script comes from the argparse
tree at run time (``completion``, ``completion_<shell>``), so it always
matches the installed version. It reads no config, log or trace.

``SHELLS`` maps a shell to the module that renders its script (a ``render``
function taking the root parser); another shell is one more module and one
more entry. The modules load only when a script is printed, so building the
parser (which lists the shells as choices) stays cheap.
"""

from __future__ import annotations

import argparse
import importlib
import sys

SHELLS: dict[str, str] = {"zsh": "nuh.completion_zsh"}


def script(shell: str) -> str:
    """*shell*'s completion script for this version (what ``nuh completion SHELL`` prints, ``nuh install`` writes)."""
    from nuh.cli_parser import build_parser  # cli_parser imports this module for SHELLS
    return importlib.import_module(SHELLS[shell]).render(build_parser())


def cmd_completion(args: argparse.Namespace) -> None:
    sys.stdout.write(script(args.shell))
