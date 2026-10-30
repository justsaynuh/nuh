"""``make``'s command line: directory, makefiles, goals, variable overrides and read-only forms."""

import os
import re
from dataclasses import dataclass, field

from nuh.task_recipe import Unreadable

_READ_ONLY_LONG = frozenset({"--just-print", "--dry-run", "--recon", "--print-data-base", "--question",
                             "--help", "--version", "--touch"})
_READ_ONLY_SHORT = frozenset("npqvht")
_VALUE_SHORT = frozenset("CfIoWE")
_OPTIONAL_NUMBER_SHORT = frozenset("jl")
_VALUE_LONG = {"--directory": "C", "--file": "f", "--makefile": "f", "--include-dir": "I", "--old-file": "o",
               "--assume-old": "o", "--what-if": "W", "--new-file": "W", "--assume-new": "W", "--eval": "E",
               "--jobs": "j", "--load-average": "l", "--max-load": "l", "--output-sync": "O"}


@dataclass
class Invocation:
    directory: str = "."
    files: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    read_only: bool = False
    always_make: bool = False


def parse_args(args: list[str]) -> Invocation:
    """Read make's command line; Unreadable for options nuh cannot follow (``--eval``, ``-f -``)."""
    inv = Invocation()
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            inv.goals += args[i:]
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if name in _READ_ONLY_LONG:
                inv.read_only = True
            elif name == "--always-make":
                inv.always_make = True
            elif name in _VALUE_LONG:
                if not eq and _VALUE_LONG[name] not in "jlO":
                    value = args[i] if i < len(args) else ""
                    i += 1
                _apply(inv, _VALUE_LONG[name], value)
            continue
        if tok.startswith("-") and len(tok) > 1:
            i = _short_cluster(inv, tok[1:], args, i)
            continue
        if "=" in tok and re.match(r"^[^=\s]+:?=", tok):
            name, _sep, value = tok.partition("=")
            inv.overrides[name.rstrip(":")] = value
            continue
        inv.goals.append(tok)
    return inv


def _short_cluster(inv: Invocation, letters: str, args: list[str], i: int) -> int:
    for n, letter in enumerate(letters):
        rest = letters[n + 1:]
        if letter in _VALUE_SHORT:
            value = rest
            if not value:
                value = args[i] if i < len(args) else ""
                i += 1
            _apply(inv, letter, value)
            return i
        if letter in _OPTIONAL_NUMBER_SHORT:
            if not rest and i < len(args) and args[i].replace(".", "", 1).isdigit():
                i += 1
            return i
        if letter == "O":
            return i  # -O[TYPE]: output sync, its type attached
        if letter == "B":
            inv.always_make = True
        elif letter in _READ_ONLY_SHORT:
            inv.read_only = True
    return i


def _apply(inv: Invocation, letter: str, value: str) -> None:
    if letter == "C":
        inv.directory = os.path.join(inv.directory, os.path.expanduser(value))
    elif letter == "f":
        if value == "-":
            raise Unreadable("the makefile comes from stdin")
        inv.files.append(value)
    elif letter == "E":
        raise Unreadable("make --eval adds makefile text from the command line")
