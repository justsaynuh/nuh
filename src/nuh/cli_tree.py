"""nuh's argparse tree as data: every command parser, its documented arguments and a one-line usage.

The help completeness tests, the man page (``manpage``), the shell
completion (``completion_zsh``) and the docs site's CLI reference
(``scripts/gen_cli_docs.py``) read the parser through these, so all of them
see exactly what ``--help`` shows. argparse has no public API for
walking subparsers, so this module is the one place that reads its internals.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator


def walk(parser: argparse.ArgumentParser, path: str = "") -> Iterator[tuple[str, argparse.ArgumentParser, str]]:
    """Every parser under *parser*, depth first in help order, as (command path, parser, one-line summary).

    The top level has path ``""`` and no summary; a nested command's path is ``"config show"``.
    """
    if not path:
        yield path, parser, ""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            summaries = {choice.dest: choice.help or "" for choice in action._choices_actions}
            for name, sub in action.choices.items():
                sub_path = f"{path} {name}".strip()
                yield sub_path, sub, summaries.get(name, "")
                yield from walk(sub, sub_path)


def options(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """*parser*'s documented arguments, in order: every option and positional but ``-h`` and a subcommand list."""
    return [action for action in parser._actions
            if not isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))]


def option_groups(parser: argparse.ArgumentParser) -> list[tuple[str, list[argparse.Action]]]:
    """*parser*'s documented arguments (``options``) by argument group, as ``--help`` lists them: ``("", ...)`` for
    the positionals and plain options, then ``(title, ...)`` for each ``add_argument_group``; empty groups left out.
    """
    documented = options(parser)
    plain = (parser._positionals, parser._optionals)
    groups = [("", [action for group in plain for action in group._group_actions if action in documented])]
    groups += [(group.title or "", [action for action in group._group_actions if action in documented])
               for group in parser._action_groups if group not in plain]
    return [(title, actions) for title, actions in groups if actions]


def help_option(parser: argparse.ArgumentParser) -> argparse.Action | None:
    """*parser*'s ``-h, --help``, which argparse adds and ``options`` leaves out; None if it has none."""
    return next((action for action in parser._actions if isinstance(action, argparse._HelpAction)), None)


def subcommand_label(parser: argparse.ArgumentParser) -> str | None:
    """The metavar of *parser*'s subcommand list (``COMMAND``), or None if it has no subcommands."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return str(action.metavar or action.dest)
    return None


def exits(action: argparse.Action) -> bool:
    """Whether the option prints something and exits (``-h``, ``--version``), so nothing completes after it."""
    return isinstance(action, (argparse._HelpAction, argparse._VersionAction))


def repeatable(action: argparse.Action) -> bool:
    """Whether the option means something given more than once (``action="append"``, ``"count"``)."""
    return isinstance(action, (argparse._AppendAction, argparse._AppendConstAction, argparse._CountAction))


def remainder(action: argparse.Action) -> bool:
    """Whether the positional takes every remaining word (``nargs=argparse.REMAINDER``)."""
    return action.nargs == argparse.REMAINDER


def value_label(action: argparse.Action) -> str:
    """The name of the value *action* takes, as the help shows it: ``TYPE``, ``{allow,ask,block}``, ``SINCE``."""
    if action.metavar is not None:
        return str(action.metavar)
    if action.choices is not None:
        return "{" + ",".join(str(choice) for choice in action.choices) + "}"
    return action.dest if not action.option_strings else action.dest.upper()


def synopsis(parser: argparse.ArgumentParser) -> str:
    """argparse's own usage line for *parser*, on one line whatever the terminal width, without ``usage:`` and
    ``[-h]`` (every command takes ``-h``)."""
    formatter = parser.formatter_class(prog=parser.prog, width=100_000)
    formatter.add_usage(parser.usage, parser._actions, parser._mutually_exclusive_groups, prefix="")
    return " ".join(formatter.format_help().split()).replace(" [-h]", "")
