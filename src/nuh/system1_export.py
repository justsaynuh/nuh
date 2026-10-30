"""``nuh system1 export``: the shipped system 1 definition as YAML, for editing by hand.

Block style, keys in the shipped order, ``based_on`` first, and the text a
person edits (the preamble, each question's instructions, a choice option's
text, anything spanning lines) as literal blocks (``|``).
"""

from __future__ import annotations

import json

from nuh import __version__, system1_def

_LITERAL_KEYS = frozenset({"preamble", "instructions", "text"})
_WIDTH = 1 << 16  # never fold a long plain or quoted string onto a second line

HEADER = """\
# nuh's system 1 definition: what system 1 is asked about a tool call, and how its answers decide.
# Written by `nuh system1 export` from the definition nuh {version} ships.
#
# This file replaces the shipped definition entirely: a question or gate left out here is not asked
# or run. Only the blocks under `tools:` merge, each onto the rest of this file for its own tool's calls.
#
# nuh does not update this file when it upgrades. `based_on` says which version it came from;
# `nuh system1 export --stdout` prints the current shipped definition to compare.
#
# After editing, run `nuh system1 check` to validate it (it calls no model).
"""


class _Literal(str):
    """Text written as a literal block (``|``)."""


def shipped() -> dict:
    """The definition nuh ships (``data/system1.json``), as parsed JSON."""
    with open(system1_def.SHIPPED, encoding="utf-8") as f:
        return json.load(f)


def render(data: dict, version: str = __version__) -> str:
    """*data* as a commented YAML definition file, ``based_on`` set to *version*."""
    import yaml  # lazy, like nuh's other yaml imports

    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
            super().increase_indent(flow, False)  # a list sits indented under its key

    Dumper.add_representer(_Literal, lambda dumper, text: dumper.represent_scalar(
        "tag:yaml.org,2002:str", text, style="|"))
    body = {"based_on": version, **{key: value for key, value in data.items() if key != "based_on"}}
    sections = [yaml.dump({key: _mark(value, key)}, Dumper=Dumper, sort_keys=False, allow_unicode=True,
                          width=_WIDTH) for key, value in body.items()]
    return HEADER.format(version=version) + "\n" + "\n".join(sections)


def _mark(value: object, key: object) -> object:
    """*value* (found under *key*) with the strings to write as literal blocks marked."""
    if isinstance(value, str):
        return _Literal(value) if key in _LITERAL_KEYS or "\n" in value else value
    if isinstance(value, dict):
        return {k: _mark(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_mark(item, None) for item in value]
    return value
