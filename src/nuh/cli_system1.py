"""``nuh system1``: write out the shipped system 1 definition to edit (export), validate one (check).

Neither calls a model. ``export`` writes the user file nuh reads
(``system1.yaml`` beside the global config, or ``llm.system1.definition``
when set) and never overwrites one without ``--force``. Writing there is
``nuh_admin`` (self_protect); ``export --stdout`` and ``check`` only read.
"""

from __future__ import annotations

import argparse
import os
import sys

from nuh import __version__, system1_check, system1_def, system1_export
from nuh.system1_rules import DefinitionError

def cmd_system1(args: argparse.Namespace) -> None:
    if args.system1_command == "export":
        raise SystemExit(_export(args))
    if args.system1_command == "check":
        raise SystemExit(_check(args))
    print("Usage: nuh system1 {export [PATH] [--force] [--stdout] | check [PATH]}")


def _fail(message: str, code: int = 1) -> int:
    print(f"nuh system1: {message}", file=sys.stderr)
    return code


def _section() -> dict:
    """The configured ``llm.system1`` section ({} when there is none)."""
    from nuh.config import get_config  # lazy import: config loading is the slow part of a CLI start
    section = get_config().llm.get("system1")
    return section if isinstance(section, dict) else {}


def _export(args: argparse.Namespace) -> int:
    if args.stdout and args.path:
        return _fail("--stdout prints the definition and takes no PATH", 2)
    text = system1_export.render(system1_export.shipped())
    if args.stdout:
        sys.stdout.write(text)
        return 0
    try:
        section = _section()
        target = os.path.abspath(os.path.expanduser(args.path)) if args.path else _default_target(section)
    except DefinitionError as exc:
        return _fail(str(exc))
    if target.endswith(".json"):
        return _fail(f"{target}: export writes YAML; name a .yaml file", 2)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w" if args.force else "x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        return _fail(f"{target} exists; pass --force to overwrite it")
    except OSError as exc:
        return _fail(f"cannot write {target}: {exc.strerror or exc}")
    print(f"Wrote {target} (based on nuh {__version__}). Run `nuh system1 check` after editing it.")
    _say_if_unused(target, section)
    return 0


def _check(args: argparse.Namespace) -> int:
    try:
        where = os.path.abspath(os.path.expanduser(args.path)) if args.path else system1_def.path(_section())
        report = system1_check.check(where)
    except DefinitionError as exc:
        return _fail(f"check failed: {exc}")
    print("\n".join(report))
    return 0


def _default_target(section: dict) -> str:
    """The file nuh reads a user definition from: ``llm.system1.definition`` when set, else the user file."""
    return system1_def.path(section) if section.get("definition") is not None else system1_def.user_path()


def _say_if_unused(target: str, section: dict) -> None:
    try:
        used = system1_def.path(section)
    except DefinitionError as exc:
        print(f"Note: nuh cannot use any definition until this is fixed: {exc}")
        return
    if os.path.realpath(used) != os.path.realpath(target):
        print(f"Note: nuh reads {used}; set llm.system1.definition to {target} to use this file.")
