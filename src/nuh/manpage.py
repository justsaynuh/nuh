"""Render the man page from the argparse tree (``cli_parser``), its long help (``cli_text``) and ``manual``.

The page is a build artifact, never committed: ``hatch_build.py`` renders it
into every wheel, editable ones included, as shared data at
``share/man/man1/nuh.1``, and ``make man`` renders the same page for a local
preview. Plain man(7) macros only, so groff and mandoc both render it; no
date in ``.TH``, so the output depends on nothing but the source and version.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from nuh import __version__, manual
from nuh.cli_parser import PROG, build_parser
from nuh.cli_text import COMMANDS, Example
from nuh.cli_tree import options, synopsis, value_label, walk

INSTALL_PATH = "share/man/man1/nuh.1"
"""Where the page installs, relative to the environment's prefix (the wheel's shared data)."""

_CODE = re.compile(r"`([^`]+)`")


def _chars(text: str, *, code: bool = True) -> str:
    """Escape roff's backslash and spell non-ASCII characters as ``\\[uXXXX]``. In *code* (commands, options,
    paths) a hyphen is a minus sign (``\\-``), so the rendered text copies as typed; in prose it stays a hyphen."""
    text = text.replace("\\", "\\e")
    if code:
        text = text.replace("-", "\\-")
    return "".join(c if ord(c) < 128 else f"\\[u{ord(c):04X}]" for c in text)


def inline(text: str) -> str:
    """Markup to roff: ``code`` becomes bold. A leading ``.`` or ``'`` is guarded so it is not a request."""
    parts = _CODE.split(text)  # even indexes are prose, odd ones the insides of backticks
    text = "".join(f"\\fB{_chars(part)}\\fR" if index % 2 else _chars(part, code=False)
                   for index, part in enumerate(parts))
    return f"\\&{text}" if text.startswith((".", "'")) else text


def _paragraphs(paragraphs: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for paragraph in paragraphs:
        lines += [".PP", inline(paragraph)]
    return lines


def _entries(entries: Iterable[manual.Entry], term_font: str = "B") -> list[str]:
    lines: list[str] = []
    for entry in entries:
        lines += [".TP", f"\\f{term_font}{_chars(entry.term)}\\fR", inline(entry.text)]
    return lines


def _examples(examples: Iterable[Example]) -> list[str]:
    lines = [".PP", "Examples:", ".PP", ".RS 4", ".nf"]
    for index, example in enumerate(examples):
        if index:
            lines.append("")
        lines += [_chars(f"# {example.comment}", code=False), _chars(example.command)]
    return [*lines, ".fi", ".RE"]


def _options(parser: argparse.ArgumentParser) -> list[str]:
    lines: list[str] = []
    for action in options(parser):
        if action.option_strings:
            value = "" if action.nargs == 0 else f" \\fI{_chars(value_label(action))}\\fR"
            term = ", ".join(f"\\fB{_chars(flag)}\\fR" for flag in action.option_strings) + value
        else:
            term = f"\\fI{_chars(value_label(action))}\\fR"
        lines += [".TP", term, inline(action.help or "")]
    return lines


def _command(path: str, parser: argparse.ArgumentParser) -> list[str]:
    text = COMMANDS[path]
    lines = [f'.SS "{_chars(f"{PROG} {path}")}"', f"\\fB{_chars(synopsis(parser))}\\fR"]
    return [*lines, *_paragraphs(text.description), *_options(parser), *_examples(text.examples)]


def render() -> str:
    """The man page, as roff text."""
    parser = build_parser()
    commands = [(path, sub) for path, sub, _ in walk(parser) if path]
    lines = [
        f'.TH NUH 1 "" "{_chars(f"{PROG} {__version__}")}" "User Commands"',
        ".nh",  # identifiers and options must not be hyphenated
        ".SH NAME",
        f"{_chars(PROG)} \\- {inline(manual.SUMMARY)}",
        ".SH SYNOPSIS",
        f"\\fB{_chars(PROG)}\\fR \\fICOMMAND\\fR [\\fIOPTION\\fR ...]",
        ".br",
        f"\\fB{_chars(PROG)}\\fR [\\fB\\-h\\fR] [\\fB\\-\\-version\\fR]",
        ".SH DESCRIPTION",
        *_paragraphs(manual.DESCRIPTION),
        ".SH OPTIONS",
        "Every command also takes \\fB\\-h\\fR, \\fB\\-\\-help\\fR.",
        *_options(parser),
        ".SH COMMANDS",
    ]
    for path, sub in commands:
        lines += _command(path, sub)
    lines += [".SH FILES", *_entries(manual.FILES, "I"), ".SH ENVIRONMENT", *_entries(manual.ENVIRONMENT)]
    lines += [".SH SEE ALSO", *_entries(manual.SEE_ALSO, "I")]
    return "\n".join(lines) + "\n"


def write(path: Path) -> None:
    """Render the page into *path*, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8")


def main(argv: list[str]) -> int:
    """``python -m nuh.manpage OUT`` (``make man``): write the page to OUT."""
    if len(argv) != 1:
        print("usage: python -m nuh.manpage OUT", file=sys.stderr)
        return 2
    write(Path(argv[0]))
    print(f"wrote {argv[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
