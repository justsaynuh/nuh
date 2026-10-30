"""``nuh completion zsh``: valid zsh covering the whole argparse tree, help text escaped, completing in a real
zsh (through a pty), and rendered without reading config or loading nuh's dependencies."""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import cli, completion
from nuh.cli_parser import build_parser
from nuh.cli_tree import options, walk
from nuh.completion_zsh import render
from tests.zshdriver import Offered, complete

SCRIPT = render(build_parser())
needs_zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed")

TRICKY = 'a: b [c] d\'s `e` and \\ $(touch pwned) "g" `lone ]'
"""Help text with every character that breaks an unescaped spec: colon, brackets, quotes, backticks, ``$(...)``."""
TRICKY_SHOWN = 'a: b [c] d\'s e and \\ $(touch pwned) "g" `lone ]'
"""How zsh should show it: the markup's backticks dropped, everything else verbatim."""


def _functions(script: str) -> dict[str, str]:
    """Function name -> body, for every ``name() {`` ... ``}`` block of the script."""
    return dict(re.findall(r"^(\S+)\(\) \{\n(.*?)^\}$", script, re.MULTILINE | re.DOTALL))


def _function_name(path: str, prog: str = "nuh") -> str:
    return "_" + "_".join([prog, *path.split()])


def _zsh_n(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    path = tmp_path / "_script"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(["zsh", "-n", str(path)], capture_output=True, text=True, check=False)


def _tricky_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tricky")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    go = sub.add_parser("go", help=TRICKY)
    go.add_argument("--note", metavar="TEXT", help=TRICKY)
    go.add_argument("--pick", choices=["a b", "it's", "$(touch pwned)"], help="one of three awkward words")
    go.add_argument("--dir", metavar="DIR", help="a directory")
    return parser


@needs_zsh
def test_the_script_is_valid_zsh(tmp_path: Path):
    proc = _zsh_n(SCRIPT, tmp_path)
    assert (proc.returncode, proc.stderr) == (0, "")


@needs_zsh
def test_sourcing_the_script_registers_it(tmp_path: Path):
    script = tmp_path / "script.zsh"
    script.write_text(SCRIPT, encoding="utf-8")
    shell = f'autoload -Uz compinit && compinit -u -D && source {script} && print -r -- "$_comps[nuh]"'
    proc = subprocess.run(["zsh", "-f", "-c", shell], capture_output=True, text=True, check=False,
                          env={"HOME": str(tmp_path)})
    assert (proc.stdout, proc.stderr) == ("_nuh\n", "")


def test_every_command_and_option_is_in_its_function():
    bodies = _functions(SCRIPT)
    for path, parser, _ in walk(build_parser()):
        body = bodies[_function_name(path)]
        if path:
            parent, _, child = path.rpartition(" ")
            assert f"\n    '{child}:" in bodies[f"{_function_name(parent)}_commands"], f"`{path}` isn't offered"
        for action in options(parser):
            for flag in action.option_strings:
                spec = rf"\n    '(\([^)]*\))?\*?{re.escape(flag)}[=+]?\["
                assert re.search(spec, body), f"`{path or 'nuh'} {flag}` has no spec"
            if not action.option_strings:
                label = re.escape(str(action.metavar or action.dest))
                assert re.search(rf"\n    '[*:]?:{label}:", body), f"`{path} {action.dest}` has no spec"


def test_values_complete_from_choices_or_metavar():
    bodies = _functions(SCRIPT)
    assert ":DECISION:(allow ask block)'" in bodies["_nuh_log"]
    assert r":TYPE:((filesystem_read\:Read\ files\ or\ list\ directories " in bodies["_nuh_allow"]
    assert ":TOOL:(Bash Read Write Edit " in bodies["_nuh_test"]
    assert ":PROVIDER:(" in bodies["_nuh_trace"] and "system1" in bodies["_nuh_trace"]
    assert ":AGENT:(claude codex all)'" in bodies["_nuh_install"]
    assert ":FILE:_files'" in bodies["_nuh_system1_check"]
    assert ":DIR:_directories'" in bodies["_nuh_trace"]
    assert ":RULE:'" in bodies["_nuh_forget"], "rules live in the config, which completion does not read"


def test_mutually_exclusive_options_exclude_each_other():
    body = _functions(SCRIPT)["_nuh_log"]
    assert "'(-d --decision --asks)--blocks[" in body
    assert "'(--llm)--no-llm[" in _functions(SCRIPT)["_nuh_test"]


@needs_zsh
def test_awkward_help_and_choices_survive_escaping(tmp_path: Path):
    script = render(_tricky_parser())
    proc = _zsh_n(script, tmp_path)
    assert (proc.returncode, proc.stderr) == (0, "")
    assert complete(script, "tricky", "tricky ", cwd=tmp_path).description("go") == TRICKY_SHOWN
    assert complete(script, "tricky", "tricky go --n", cwd=tmp_path).description("--note") == TRICKY_SHOWN
    picks = complete(script, "tricky", "tricky go --pick ", cwd=tmp_path)
    assert picks.matches == {"a b", "it's", "$(touch pwned)"}
    assert not (tmp_path / "pwned").exists(), "a choice was evaluated as a command"


@needs_zsh
def test_awkward_described_words_survive_escaping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    words = ("a:b", "c d", "$(touch pwned)")
    monkeypatch.setitem(completion._BY_METAVAR, "TYPE", lambda: completion.Values(
        completion.ValueKind.WORDS, words, (TRICKY, "plain", "`$(touch pwned)`")))
    parser = argparse.ArgumentParser(prog="tricky")
    parser.add_argument("kind", metavar="TYPE", help="a described word")
    script = render(parser)
    assert (_zsh_n(script, tmp_path).returncode) == 0
    offered = complete(script, "tricky", "tricky ", cwd=tmp_path)
    assert offered.matches == set(words)
    assert offered.description("a:b") == TRICKY_SHOWN
    assert any(line.rstrip().endswith("-- $(touch pwned)") for line in offered.displays)
    assert not (tmp_path / "pwned").exists(), "a described word was evaluated as a command"


def _offered(line: str, tmp_path: Path) -> Offered:
    return complete(SCRIPT, "nuh", line, cwd=tmp_path)


@needs_zsh
@pytest.mark.parametrize(("line", "expected"), [
    ("nuh config ", {"show", "path"}),
    ("nuh system1 ", {"export", "check"}),
    ("nuh tr", {"trace", "trust"}),
    ("nuh completion ", {"zsh"}),
    ("nuh trace --view ", {"answers", "prompt", "path", "raw", "all"}),
    ("nuh install --agent ", {"claude", "codex", "all"}),
    ("nuh log --b", {"--blocks"}),
])
def test_tab_offers_what_the_cli_accepts(line: str, expected: set, tmp_path: Path):
    assert _offered(line, tmp_path).matches == expected


@needs_zsh
def test_tab_offers_every_command(tmp_path: Path):
    commands = {path for path, _, _ in walk(build_parser()) if path and " " not in path}
    assert _offered("nuh ", tmp_path).matches == commands


@needs_zsh
def test_tab_offers_action_types_with_their_descriptions(tmp_path: Path):
    offered = _offered("nuh allow git_", tmp_path)
    assert {"git_safe", "git_write", "git_history_rewrite"} <= offered.matches
    assert offered.description("git_safe") == "Read-only git operations (status, log, diff)"


def test_rendering_reads_no_config_and_loads_no_dependency(tmp_path: Path):
    broken = tmp_path / ".config" / "nuh" / "config.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("this: [is not yaml\n", encoding="utf-8")
    code = ("import sys; from nuh.cli import main; main(['completion', 'zsh']); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('yaml', 'celpy', 'lark')), file=sys.stderr)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=tmp_path,
                          env={"HOME": str(tmp_path), "PYTHONPATH": ":".join(sys.path)})
    assert (proc.stderr, proc.stdout) == ("[]\n", SCRIPT)


def test_completion_command_prints_the_script(capsys: pytest.CaptureFixture):
    cli.main(["completion", "zsh"])
    assert capsys.readouterr().out == SCRIPT
