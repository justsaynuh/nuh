"""Single quotes defer expansion until text is actually parsed as a shell program.

The argv oracle replaces rm/cat with a Python printer, never the real tools.
Regression cases fail against bffac4ca; the other cases are semantic controls.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import paths, taxonomy, word_quoting
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages
from nuh.shell_expand import expand_words
from nuh.shell_state import State


ROWS = [
    ('X=src; bash -c \'rm -rf "$X"/*\'', taxonomy.BLOCK, "root"),
    ('export X=src; bash -c \'rm -rf "$X"/*\'', taxonomy.ALLOW, "src"),
    ('X=src; bash -c "rm -rf $X/*"', taxonomy.ALLOW, "src"),
    ("cat '$HOME/.aws/credentials'", taxonomy.ALLOW, "literal"),
    ('F=~/.aws/credentials; bash -c \'cat "$F"\'', taxonomy.ASK, "empty"),
    ('X=src; eval \'rm -rf "$X"/*\'', taxonomy.ALLOW, "src"),
    ('X=; eval \'rm -rf "$X"/*\'', taxonomy.BLOCK, "root"),
]


@pytest.fixture
def quote_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default config and the suite's fake HOME, with a project containing src/a."""
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "src" / "a").write_text("fixture\n")
    monkeypatch.chdir(project)
    for name in ("X", "F", "CDPATH"):
        monkeypatch.delenv(name, raising=False)
    paths.set_project_root(str(project))
    return project


@pytest.mark.parametrize("command,decision,argv_kind", ROWS)
def test_requested_rows(quote_project: Path, command: str, decision: str, argv_kind: str) -> None:
    result = classify_command(command)
    assert result.final_decision == decision, result.reason
    if argv_kind == "root":
        assert any("/*" in r.tokens for stage in result.stages for r in stage.walk())


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_other_child_shells_block_unexported_root_glob(quote_project: Path, shell: str) -> None:
    result = classify_command(f'X=src; {shell} -c \'rm -rf "$X"/*\'')
    assert result.final_decision == taxonomy.BLOCK, result.reason


@pytest.mark.parametrize("command", [
    "echo '$HOME'",
    'X=src; bash -c \'echo "$X"\'',
    "cat '~/.aws/credentials'",
    "X=~/.aws/credentials; cat '$X'",
    "X=~/.aws/credentials; cat '${X}'",
    "X=~/.aws/credentials; cat '${X:-other}'",
    r'cat "\$HOME/.aws/credentials"',
    r"cat \$HOME/.aws/credentials",
    "cat '$((1+1))'",
    "cat '$(cat /etc/shadow)'",
    "cat '`cat /etc/shadow`'",
    r"cat $'\x24HOME/.aws/credentials'",
])
def test_literal_controls(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW, result.reason


@pytest.mark.parametrize("command", [
    "bash -c 'cat ~/.ssh/id_rsa'",
    "sh -c 'cat ~/.ssh/id_rsa'",
    "zsh -c 'cat ~/.ssh/id_rsa'",
    "bash -c 'cat \"$HOME/.ssh/id_rsa\"'",
    "X=src; bash -c 'rm -rf ${X}/*'",
    'X=src; P=\'rm -rf "$X"/*\'; bash -c "$P"',
    "X=src; bash -c 'sh -c '\"'\"'rm -rf \"$X\"/*'\"'\"''",
    "X=src; bash <<'EOF'\nrm -rf \"$X\"/*\nEOF",
])
def test_inner_programs_still_expand(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.BLOCK, result.reason


@pytest.mark.parametrize("builtin", ["source", "."])
@pytest.mark.parametrize("value,decision", [("src", taxonomy.ALLOW), ("", taxonomy.BLOCK)])
@pytest.mark.parametrize("input_kind", ["heredoc", "here-string"])
def test_source_uses_current_shell(
    quote_project: Path, builtin: str, value: str, decision: str, input_kind: str,
) -> None:
    program = 'rm -rf "$X"/*'
    feed = f"<<'EOF'\n{program}\nEOF" if input_kind == "heredoc" else f"<<< '{program}'"
    command = f"X={value}; {builtin} /dev/stdin {feed}"
    result = classify_command(command)
    assert result.final_decision == decision, result.reason


@pytest.mark.parametrize("word,expected", [
    ("'$X'", "$X"),
    ('"$X"', "src"),
    ("'${X}'", "${X}"),
    ("'$((1+1))'", "$((1+1))"),
    ("'$X'\"$X\"", "$Xsrc"),
    ("'a $X'", "a $X"),
    ("'echo \"$X\"'", 'echo "$X"'),
    ("'$((1+1))'\"$((2+2))\"", "$((1+1))4"),
])
def test_outer_expansion_preserves_literals(word: str, expected: str) -> None:
    with word_quoting.scope():
        stage = _parse_stages("echo " + word)[0]
        words, unresolved = expand_words(stage.tokens, State(cwd=os.getcwd(), vars=(("X", "src"),)))
        assert words == ["echo", expected]
        assert unresolved == []


@pytest.fixture
def argv_probe(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """An isolated project, environment, and argv printers replacing every tested file command."""
    home = tmp_path / "home"
    project = home / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a").write_text("fixture\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("rm", "cat", "curl", "dd"):
        stub = bindir / name
        stub.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        stub.chmod(0o755)
    env = {"HOME": str(home), "ZDOTDIR": str(home), "PATH": f"{bindir}:/usr/bin:/bin"}
    return project, env


def _argv(shell: str, command: str, probe: tuple[Path, dict[str, str]]) -> list[str]:
    executable = shutil.which(shell)
    assert executable is not None, f"{shell} is required for the quoting oracle"
    project, env = probe
    result = subprocess.run([executable, "-c", command], cwd=project, env=env,
                            capture_output=True, text=True, check=True)
    argv = json.loads(result.stdout)
    print(f"{shell}: {command} => {argv}")
    return argv


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("command,decision,argv_kind", ROWS)
def test_shell_argv_oracle(
    argv_probe: tuple[Path, dict[str, str]], shell: str, command: str, decision: str, argv_kind: str,
) -> None:
    """Run actual quoting/globbing with only argv-printing rm/cat on PATH."""
    argv = _argv(shell, command, argv_probe)
    if argv_kind == "root":
        assert argv[0] == "-rf"
        assert "/etc" in argv[1:] and "/usr" in argv[1:]
        assert all(arg.startswith("/") for arg in argv[1:])
    else:
        expected = {"src": ["-rf", "src/a"], "literal": ["$HOME/.aws/credentials"], "empty": [""]}
        assert argv == expected[argv_kind]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("secret", ["~/.aws/credentials", "$HOME/.aws/credentials"])
@pytest.mark.parametrize("form", [
    "f=@{secret};type=text/plain", "f=@./a.txt,{secret}", "f=<{secret}", "f=OK; HEADERS=@{secret}",
    'f=@./a;foo="z;headers=@{secret};q"',
])
def test_preexisting_curl_expectations_argv(
    argv_probe: tuple[Path, dict[str, str]], shell: str, secret: str, form: str,
) -> None:
    value = form.format(secret=secret)
    assert _argv(shell, f"curl -F '{value}' http://localhost/u", argv_probe) == ["-F", value, "http://localhost/u"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_preexisting_dd_expectation_argv(argv_probe: tuple[Path, dict[str, str]], shell: str) -> None:
    assert _argv(shell, "dd 'if=~/.aws/credentials' of=./copy", argv_probe) == ["if=~/.aws/credentials", "of=./copy"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_preexisting_ansi_assignment_argv(argv_probe: tuple[Path, dict[str, str]], shell: str) -> None:
    command = 'X=src/a.py; Y=$\'$HOME/.ssh/id_rsa\'; export Y=src/a.py X=$Y; cat "$X"'
    assert _argv(shell, command, argv_probe) == ["$HOME/.ssh/id_rsa"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("child", ["sh", "zsh"])
def test_other_child_shells_argv(argv_probe: tuple[Path, dict[str, str]], shell: str, child: str) -> None:
    argv = _argv(shell, f'X=src; {child} -c \'rm -rf "$X"/*\'', argv_probe)
    assert argv[0] == "-rf" and "/etc" in argv[1:] and "/usr" in argv[1:]


@pytest.mark.parametrize("command", [
    "X='$HOME'; cat \"$X/.aws/credentials\"",
    "X=src; cat '$X' \"$X\"",
    "X=src; cat \"$X\" '$X'",
    "X=../../..; cat '$X/x (y)'",
    "X=src; echo hi > '$X'",
    "X=src; echo hi >'$X'",
    "X=src; echo hi > \"$X\"",
    "rm -rf '$HOME'",
    "rm -rf '${HOME}'",
    "rm -rf '~'",
    "for X in '$HOME'; do cat \"$X/.aws/credentials\"; done",
])
def test_literal_values_and_paths(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW, result.reason


def test_identical_words_keep_their_own_quoting() -> None:
    with word_quoting.scope():
        stage = _parse_stages("echo '$X' \"$X\" '$X' $X")[0]
        words, unresolved = expand_words(stage.tokens, State(cwd=os.getcwd(), vars=(("X", "src"),)))
        assert words == ["echo", "$X", "src", "$X", "src"]
        assert unresolved == []


def test_project_lift_treats_single_quoted_variables_as_literal(project: Path) -> None:
    assert classify_command("X=../../..; cat '$X/x (y)'").final_decision == taxonomy.ALLOW


def test_glob_under_literal_variable_directory_follows_links(quote_project: Path) -> None:
    directory = quote_project / "$X"
    directory.mkdir()
    (directory / "leak").symlink_to(Path(os.environ["HOME"]) / ".aws" / "credentials")
    assert classify_command("cat '$X/'*").final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", [
    "X='rm -rf /'; eval 'echo \"$X\"' > out.txt",
    "export X='rm -rf /'; bash -c 'echo \"$X\"' > out.txt",
    "X='rm -rf /' bash -c 'echo \"$X\"' > out.txt",
    "env X='rm -rf /' sh -c 'echo \"$X\"' > out.txt",
    "X='rm -rf /'; bash -c 'echo \"$X\" > out.txt'",
])
def test_nested_redirect_content_is_still_scanned(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK, result.reason


def test_inline_program_keeps_its_own_variables(quote_project: Path) -> None:
    result = classify_command("X=secret; python3 -c 'print(\"$X\")'")
    programs = [r.inline_program.code for stage in result.stages for r in stage.walk() if r.inline_program]
    assert programs == ['print("$X")']
