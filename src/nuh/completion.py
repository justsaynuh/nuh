"""What every shell's completion script shares: how an argument's values complete, and help as plain text.

An argument's values come from its ``choices``, else from its metavar, which
names the value's type (``cli_parser``): ``FILE``/``PATH`` complete paths,
``DIR`` directories, ``TYPE`` the built-in action types with their
descriptions, ``TOOL`` the tool names nuh installs a hook for, ``PROVIDER``
the LLM providers and ``AGENT`` the agents ``--agent`` takes. Any other
metavar completes nothing and is only shown as a hint.

Every value is static, so it is rendered into the script: nothing calls back
into nuh at TAB time, and nothing here reads the user's config. (Custom
action types live in the config; reading it costs ~45 ms per TAB, so they are
not offered.)
"""

from __future__ import annotations

import argparse
import enum
import functools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class ValueKind(enum.Enum):
    """Where an argument's values come from."""

    NONE = "none"
    """Nothing to offer; the shell shows the metavar as a hint."""
    WORDS = "words"
    """A fixed list (``Values.words``), each word optionally described (``Values.descriptions``)."""
    FILES = "files"
    DIRECTORIES = "directories"


@dataclass(frozen=True)
class Values:
    """How one argument's values complete."""

    kind: ValueKind
    words: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()
    """One per word when given; empty when the words are offered bare."""


@functools.cache
def _action_types() -> Values:
    with open(Path(__file__).with_name("data") / "types.json", encoding="utf-8") as f:
        described: dict[str, str] = json.load(f)
    return Values(ValueKind.WORDS, tuple(described), tuple(described.values()))


@functools.cache
def _tools() -> Values:
    from nuh import agents
    names = dict.fromkeys(name for matchers in agents.AGENT_TOOL_MATCHERS.values() for name in matchers
                          if name.isidentifier())  # leaves out the mcp__.* pattern
    return Values(ValueKind.WORDS, tuple(names))


@functools.cache
def _providers() -> Values:
    from nuh import llm_providers
    return Values(ValueKind.WORDS, tuple(llm_providers.NAMES))


@functools.cache
def _agents() -> Values:
    from nuh import agents
    return Values(ValueKind.WORDS, (*sorted(agents.INSTALLABLE_AGENTS), "all"))


_BY_METAVAR: dict[str, Callable[[], Values]] = {
    "FILE": lambda: Values(ValueKind.FILES),
    "PATH": lambda: Values(ValueKind.FILES),
    "DIR": lambda: Values(ValueKind.DIRECTORIES),
    "TYPE": _action_types,
    "TOOL": _tools,
    "PROVIDER": _providers,
    "AGENT": _agents,
}
"""Metavar -> its values, computed when a script is rendered (the imports stay out of the parser)."""

_CODE = re.compile(r"`([^`]+)`")


def values(action: argparse.Action) -> Values:
    """How *action*'s values complete: its ``choices``, else what its metavar names, else nothing."""
    if action.choices is not None:
        return Values(ValueKind.WORDS, tuple(str(choice) for choice in action.choices))
    source = _BY_METAVAR.get(str(action.metavar))
    return source() if source is not None else Values(ValueKind.NONE)


def plain(text: str) -> str:
    """Help text as one line of plain text: backtick markup dropped, whitespace collapsed."""
    return " ".join(_CODE.sub(r"\1", text).split())
