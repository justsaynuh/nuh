"""Put the long help (``cli_text``) on the parsers, so ``--help`` shows each command's description and examples."""

from __future__ import annotations

import argparse
import textwrap

from nuh.cli_text import COMMANDS, OVERVIEW, Example
from nuh.cli_tree import walk

WIDTH = 80
"""``--help`` wraps descriptions at this width (argparse wraps option help to the terminal)."""


def wrap(paragraphs: tuple[str, ...], width: int = WIDTH) -> str:
    """*paragraphs* wrapped at *width*, separated by blank lines."""
    return "\n\n".join(textwrap.fill(p, width, break_on_hyphens=False, break_long_words=False) for p in paragraphs)


def examples_text(examples: tuple[Example, ...]) -> str:
    """The ``examples:`` epilog: each example's comment as a ``#`` line above its command."""
    lines = ["examples:"]
    for index, example in enumerate(examples):
        if index:
            lines.append("")
        lines += [f"  # {example.comment}", f"  {example.command}"]
    return "\n".join(lines)


def describe_tree(parser: argparse.ArgumentParser) -> None:
    """Give every parser under *parser* its long help and a formatter that keeps the layout.

    A command missing from ``cli_text.COMMANDS`` keeps argparse's plain help:
    the CLI must run regardless, and ``tests/test_cli_help.py`` fails on it.
    """
    for path, sub, _ in walk(parser):
        sub.formatter_class = argparse.RawDescriptionHelpFormatter
        if not path:
            sub.description = wrap(OVERVIEW)
            continue
        text = COMMANDS.get(path)
        if text is not None:
            sub.description = wrap(text.description)
            sub.epilog = examples_text(text.examples)
