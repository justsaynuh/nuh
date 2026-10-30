"""The CLI help is complete and its examples are real: a guard against undocumented commands and options."""

import argparse
import shlex

import pytest

from nuh.cli_parser import PROG, build_parser
from nuh.cli_text import COMMANDS, Example
from nuh.cli_tree import options, synopsis, walk

TREE = list(walk(build_parser()))
COMMAND_PATHS = [path for path, _, _ in TREE if path]
EXAMPLES = [(path, example) for path, text in COMMANDS.items() for example in text.examples]
_NESTED = ("config_command", "system1_command")


def _ids(value: object) -> str:
    return value.command if isinstance(value, Example) else str(value or PROG)


def test_every_command_has_a_summary_a_description_and_examples():
    assert len(COMMAND_PATHS) > 20, "the parser tree lost its commands"
    assert sorted(COMMAND_PATHS) == sorted(COMMANDS), "a command without long help in cli_text, or a stale entry"
    for path, parser, summary in TREE:
        assert parser.description, f"`{path or PROG}` has no description"
        if path:
            assert summary.strip(), f"`{path}` has no one-line summary"
            assert 1 <= len(COMMANDS[path].examples) <= 3, f"`{path}` needs 1-3 examples"


@pytest.mark.parametrize(("path", "parser"), [(p, parser) for p, parser, _ in TREE], ids=_ids)
def test_every_option_has_help(path: str, parser: argparse.ArgumentParser):
    # options() leaves out -h (argparse's own) and the subcommand list, whose entries' help is their summary.
    for action in options(parser):
        name = "/".join(action.option_strings) or action.dest
        assert action.help and action.help.strip(), f"`{path or PROG} {name}` has no help text"


@pytest.mark.parametrize("path", ["", *COMMAND_PATHS], ids=_ids)
def test_help_shows_the_description_and_examples(path: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([*path.split(), "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: {PROG} {path}".rstrip())
    if path:
        assert "\nexamples:\n" in out
        assert COMMANDS[path].examples[0].command in out


@pytest.mark.parametrize(("path", "parser"), [(p, parser) for p, parser, _ in TREE], ids=_ids)
def test_synopsis_is_argparses_usage_on_one_line(path: str, parser: argparse.ArgumentParser,
                                                 monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLUMNS", "100000")
    assert parser.format_usage().replace(" [-h]", "") == f"usage: {synopsis(parser)}\n"


@pytest.mark.parametrize(("path", "example"), EXAMPLES, ids=_ids)
def test_every_example_parses_as_the_command_it_documents(path: str, example: Example):
    words = shlex.split(example.command)
    assert words[0] == PROG
    if path == "claude":
        # cli.main hands every word after `claude` to claude before argparse sees it.
        assert words[1] == "claude"
        return
    args = build_parser().parse_args(words[1:])
    invoked = " ".join(filter(None, [args.command, *(getattr(args, name, None) for name in _NESTED)]))
    assert invoked == path or invoked.startswith(f"{path} "), f"{example.command!r} doesn't invoke `{path}`"
