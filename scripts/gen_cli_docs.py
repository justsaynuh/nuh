"""Regenerate the generated blocks of the docs from the CLI and the action type data (`make docs`).

Usage: PYTHONPATH=src python scripts/gen_cli_docs.py. Each block sits between `<!-- BEGIN GENERATED: <name> -->` and
`<!-- END GENERATED: <name> -->`; everything outside the markers is hand-written and left alone. The blocks:

- site/cli.md: one `nuh <command>` block per command the argparse tree has (`cli_parser`, `cli_text`), its
  summary, synopsis, description, options and examples, exactly as `--help` and `man nuh` have them.
- README.md: `nuh commands`, every command and its one-line summary.
- site/configuration/actions.md: `action types`, each type with its default policy and description (`nuh types`).

`tests/test_gen_cli_docs.py` fails when a committed page differs from `render()` of itself. Unlike the man page,
which every wheel builds for itself (`hatch_build.py`) and which is never committed, these pages are read straight
from the repository (mkdocs builds the site from `site/`, the forge shows the README), so their generated blocks are
committed and kept fresh by that test.
"""

from __future__ import annotations

import argparse
import html
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from nuh import taxonomy
from nuh.cli_parser import PROG, build_parser
from nuh.cli_text import COMMANDS, Example
from nuh.cli_tree import option_groups, synopsis, value_label, walk

ROOT = Path(__file__).resolve().parent.parent
_BLOCK = re.compile(r"<!-- BEGIN GENERATED: (?P<name>[^>\n]+?) -->\n.*?<!-- END GENERATED: (?P=name) -->", re.DOTALL)
_CODE = re.compile(r"`([^`]+)`")
_PROSE = str.maketrans({"\\": "\\\\", "`": "\\`", "*": "\\*", "_": "\\_", "[": "\\[", "]": "\\]", "#": "\\#",
                        "|": "\\|", "&": "&amp;", "<": "&lt;", ">": "&gt;"})
_LIST_MARKER = re.compile(r"^(\d*)([-+.)])(?=\s|$)")  # `- x`, `+ x`, `1. x`, `1) x` start a list


def code(text: str, *, cell: bool = False) -> str:
    """*text* as a code span. In a table *cell* a ``|`` would end the cell however it is escaped in one of the two
    renderers (mkdocs, the forge), so such a span is written as ``<code>`` with the pipe as an entity."""
    if cell and "|" in text:
        return "<code>" + html.escape(text, quote=False).replace("|", "&#124;") + "</code>"
    ticks = "`" * (max(map(len, re.findall("`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def inline(text: str, *, cell: bool = False) -> str:
    """Help markup to Markdown: backticks stay code spans, everything else is escaped to show as written."""
    parts = _CODE.split(text)  # even indexes are prose, odd ones the insides of backticks
    out = "".join(code(part, cell=cell) if index % 2 else part.translate(_PROSE) for index, part in enumerate(parts))
    return _LIST_MARKER.sub(r"\1\\\2", out)


def fence(lang: str, lines: Iterable[str]) -> list[str]:
    """*lines* as a fenced code block, its fence longer than any backtick run inside."""
    body = list(lines)
    ticks = "`" * max([3, *(len(run) + 1 for line in body for run in re.findall("`+", line))])
    return [f"{ticks}{lang}", *body, ticks]


def table(header: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> list[str]:
    """A Markdown table; the cells are Markdown already (``inline(..., cell=True)``)."""
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def _label(action: argparse.Action) -> str:
    """An argument as ``--help`` names it: ``--limit N``-style flags and value, or a positional's name."""
    if not action.option_strings:
        return value_label(action)
    flags = ", ".join(action.option_strings)
    return flags if action.nargs == 0 else f"{flags} {value_label(action)}"


def _examples(examples: Iterable[Example]) -> list[str]:
    lines: list[str] = []
    for index, example in enumerate(examples):
        lines += [*([""] if index else []), f"# {example.comment}", example.command]
    return fence("bash", lines)


def command_block(path: str, parser: argparse.ArgumentParser, summary: str) -> list[str]:
    """One command's reference: heading, summary, synopsis, description, options by group, examples."""
    text = COMMANDS[path]
    lines = [f"### {PROG} {path}", "", inline(summary), "", *fence("text", [synopsis(parser)]), ""]
    for paragraph in text.description:
        lines += [inline(paragraph), ""]
    for title, actions in option_groups(parser):
        if title:
            lines += [f"**{inline(title)}**", ""]
        rows = ((code(_label(action), cell=True), inline(action.help or "", cell=True)) for action in actions)
        lines += [*table(("Option", "Description"), rows), ""]
    return [*lines, "Examples:", "", *_examples(text.examples)]


def cli_blocks() -> dict[str, list[str]]:
    """site/cli.md: a block per command, named ``nuh <command>``."""
    return {f"{PROG} {path}": command_block(path, sub, summary)
            for path, sub, summary in walk(build_parser()) if path}


def readme_blocks() -> dict[str, list[str]]:
    """README.md: the command index."""
    rows = ((code(f"{PROG} {path}", cell=True), inline(summary, cell=True))
            for path, _, summary in walk(build_parser()) if path)
    return {f"{PROG} commands": table(("Command", "What it does"), rows)}


def action_type_blocks() -> dict[str, list[str]]:
    """site/configuration/actions.md: every built-in action type, as ``nuh types`` lists them."""
    rows = ((code(name, cell=True), code(taxonomy.get_policy(name), cell=True), inline(description, cell=True))
            for name, description in taxonomy.load_type_descriptions().items())
    return {"action types": table(("Type", "Default", "Description"), rows)}


PAGES: dict[str, Callable[[], dict[str, list[str]]]] = {
    "site/cli.md": cli_blocks,
    "README.md": readme_blocks,
    "site/configuration/actions.md": action_type_blocks,
}


def render(page: str, blocks: dict[str, list[str]]) -> str:
    """*page* with each generated block's body replaced by *blocks*' lines.

    Raises ``ValueError`` for a block the page lacks, a block it has twice, or a block nothing generates (a
    removed command's), so a stale page fails instead of keeping text no one updates.
    """
    found = [match["name"] for match in _BLOCK.finditer(page)]
    problems = [f"no generated block {name!r}" for name in blocks if name not in found]
    problems += [f"generated block {name!r} appears twice" for name in sorted(set(found)) if found.count(name) > 1]
    problems += [f"generated block {name!r} matches nothing that is generated" for name in found
                 if name not in blocks]
    if problems:
        raise ValueError("; ".join(problems))

    def body(match: re.Match[str]) -> str:
        name = match["name"]
        return "\n".join([f"<!-- BEGIN GENERATED: {name} -->", "", *blocks[name], "",
                          f"<!-- END GENERATED: {name} -->"])

    return _BLOCK.sub(body, page)


def main() -> int:
    """Rewrite every page's generated blocks."""
    for relative, build in PAGES.items():
        path = ROOT / relative
        path.write_text(render(path.read_text(encoding="utf-8"), build()), encoding="utf-8")
        print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
