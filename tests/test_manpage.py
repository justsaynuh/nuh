"""The man page documents every command and option, and renders without roff warnings."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nuh import manual
from nuh.cli_parser import PROG, build_parser
from nuh.cli_tree import options, value_label, walk
from nuh.manpage import inline, main, render

PAGE = render()


def _roff(text: str) -> str:
    return text.replace("-", "\\-")


def _sections() -> dict[str, str]:
    """The page's sections and subsections, keyed by heading line: ``.SH OPTIONS``, ``.SS "nuh config show"``."""
    sections: dict[str, str] = {}
    heading = ""
    for line in PAGE.splitlines():
        if line.startswith((".SH ", ".SS ")):
            heading = line
            sections[heading] = ""
        elif heading:
            sections[heading] += line + "\n"
    return sections


def test_every_command_and_option_is_in_its_section():
    sections = _sections()
    tree = list(walk(build_parser()))
    commands = sorted(h for h in sections if h.startswith(".SS "))
    assert commands == sorted(f'.SS "{_roff(f"{PROG} {path}")}"' for path, _, _ in tree if path)
    for path, parser, _ in tree:
        body = sections[f'.SS "{_roff(f"{PROG} {path}")}"' if path else ".SH OPTIONS"]
        for action in options(parser):
            for flag in action.option_strings:
                assert f"\\fB{_roff(flag)}\\fR" in body, f"`{path or PROG} {flag}` is missing from the page"
            if not action.option_strings:
                assert f"\\fI{_roff(value_label(action))}\\fR" in body, f"`{path} {action.dest}` is missing"
            assert inline(action.help or "") in body, f"the help of `{path or PROG} {action.dest}` is missing"


def test_hand_written_sections_are_all_there():
    sections = _sections()
    for heading, entries in ((".SH FILES", manual.FILES), (".SH ENVIRONMENT", manual.ENVIRONMENT),
                             (".SH SEE ALSO", manual.SEE_ALSO)):
        for entry in entries:
            assert _roff(entry.term) in sections[heading], f"{entry.term} is missing from {heading}"
    assert ".TH NUH 1 \"\" " in PAGE, "no date in .TH: the page depends only on the source and version"


def test_markup_becomes_roff():
    assert inline("run `nuh log -v` now") == "run \\fBnuh log \\-v\\fR now"
    assert inline(".nuh.yaml, § \\d") == "\\&.nuh.yaml, \\[u00A7] \\ed"


@pytest.mark.parametrize("lint", [["mandoc", "-T", "lint", "-W", "warning"], ["groff", "-man", "-ww", "-z"]],
                         ids=["mandoc", "groff"])
def test_page_renders_without_warnings(lint: list, tmp_path: Path):
    if shutil.which(lint[0]) is None:
        pytest.skip(f"{lint[0]} is not installed")
    page = tmp_path / "nuh.1"
    page.write_text(PAGE, encoding="utf-8")
    proc = subprocess.run([*lint, str(page)], capture_output=True, text=True, check=False)
    assert (proc.returncode, proc.stdout + proc.stderr) == (0, "")


def test_preview_command_writes_the_page(tmp_path: Path, capsys: pytest.CaptureFixture):
    out = tmp_path / "man" / "nuh.1"
    assert main([str(out)]) == 0
    assert out.read_text(encoding="utf-8") == PAGE
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err
