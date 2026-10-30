"""One-pass expansion retains literal dollars, reference boundaries, and loop quoting.

Real-shell evidence uses a hermetic PATH whose rm/cat only print argv.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import shell_expand, shell_loops, taxonomy, word_paths, word_quoting
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages
from nuh.shell_state import State, Variant
from tests.test_single_quote_literal import quote_project as quote_project


@pytest.fixture
def clean_expansion_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("X", "d", "detc", "_etc", "NUH_UNSET"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_expansion_env")
@pytest.mark.parametrize("prefix,expected", [("X=src", taxonomy.BLOCK), ("export X=src", taxonomy.ALLOW)])
def test_escaped_default_reaches_child_once(quote_project: Path, prefix: str, expected: str) -> None:
    command = prefix + r'; bash -c "rm -rf ${NUH_UNSET:-\$X}/"'
    result = classify_command(command)
    assert result.final_decision == expected, result.reason


@pytest.mark.usefixtures("clean_expansion_env")
@pytest.mark.parametrize("command,expected", [
    ('X=etc; for d in /; do cat "$d$X/shadow"; done', taxonomy.BLOCK),
    ('X=etc; : /; cat "$_$X/shadow"', taxonomy.ASK),
])
def test_substitution_does_not_merge_unknown_names(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, command: str, expected: str,
) -> None:
    # These unrelated inherited variables must never replace $d or $_.
    monkeypatch.setenv("detc", "src")
    monkeypatch.setenv("_etc", "src")
    result = classify_command(command)
    assert result.final_decision == expected, result.reason


def test_loop_reference_keeps_its_original_name(quote_project: Path) -> None:
    result = classify_command('X=etc; detc=ok; for d in /; do rm -rf "$d$X"; done')
    assert any(stage.tokens == ["rm", "-rf", "/etc"] for stage in result.all_stages()), result


def test_last_argument_reference_keeps_its_original_name(quote_project: Path) -> None:
    result = classify_command('X=etc; _etc=ok; ls -d /; rm -rf "$_$X"')
    assert result.final_decision == taxonomy.ASK, result.reason
    assert "$_" in result.reason, result.reason


def test_loop_list_keeps_quoted_brackets(quote_project: Path) -> None:
    directory = quote_project / "$X"
    directory.mkdir()
    (directory / "a").write_text("glob decoy\n")
    with word_quoting.scope():
        words = tuple(_parse_stages("echo '$X/[ab]'")[0].tokens[1:])
        assert shell_loops.values(words, {}.get, str(quote_project)) == ("$X/[ab]",)


@pytest.mark.parametrize("value", ["$X", "glob*$X", "part $HOME*"])
def test_identical_or_unquoted_value_dollars_stay_literal(quote_project: Path, value: str) -> None:
    with word_quoting.scope():
        words = _parse_stages("echo $X")[0].tokens
        expanded, _ = shell_expand.expand_words(words, State(cwd=str(quote_project), vars=(("X", value),)))
        assert expanded[1:] == value.split()
        assert all("$" not in word_quoting.expansion(word) for word in expanded[1:])


def test_split_variant_value_dollars_stay_literal(quote_project: Path) -> None:
    with word_quoting.scope():
        word = _parse_stages("echo $X")[0].tokens[1]
        expanded = shell_expand.apply_variant(word, Variant({"X": "part $HOME*"}, frozenset({"X"})))
        assert expanded == ["part", "$HOME*"]
        assert all("$" not in word_quoting.expansion(piece) for piece in expanded)


@pytest.mark.parametrize("value", ["a b", "*", "A x", "*$HOME"])
def test_unquoted_values_keep_path_checks_conservative(quote_project: Path, value: str) -> None:
    with word_quoting.scope():
        word = _parse_stages('echo $S"/x (y)"')[0].tokens[1]
        expanded = shell_expand.apply_variant(word, Variant({"S": value}))
        assert all(word_paths.named_paths(piece) is None for piece in expanded)
        assert all("$" not in word_quoting.expansion(piece) for piece in expanded)


def test_unquoted_glob_keeps_external_matches(quote_project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_text("outside project\n")
    (quote_project / "link").symlink_to(outside, target_is_directory=True)
    with word_quoting.scope():
        word = _parse_stages("echo $S/file")[0].tokens[1]
        expanded, _ = shell_expand.expand_words([word], State(cwd=str(quote_project), vars=(("S", "*"),)))
        # Default read/delete policy can allow outside paths; the expansion's
        # path set must nevertheless retain the external symlink target.
        named = word_paths.named_paths(expanded[0])
        assert named is not None and str(outside / "file") in named


@pytest.mark.usefixtures("clean_expansion_env")
@pytest.mark.parametrize("export,expected", [("", taxonomy.BLOCK), ("export X; ", taxonomy.ALLOW)])
def test_quoted_bracket_loop_retains_child_environment(
    quote_project: Path, export: str, expected: str,
) -> None:
    (quote_project / "src" / "[a]").mkdir()
    command = 'for X in "src/[a]"; do ' + export + 'bash -c \'rm -rf "$X"/\'; done'
    result = classify_command(command)
    assert result.final_decision == expected, result.reason


@pytest.fixture
def expansion_probe(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "probe"
    (project / "src" / "[a]").mkdir(parents=True)
    (project / "src" / "a").mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("rm", "cat"):
        stub = bindir / name
        stub.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        stub.chmod(0o755)
    for name in ("bash", "zsh"):
        executable = shutil.which(name)
        assert executable is not None
        (bindir / name).symlink_to(executable)
    return project, {"HOME": str(tmp_path), "ZDOTDIR": str(tmp_path), "PATH": str(bindir)}


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("command,expected", [
    (r'X=src; bash -c "rm -rf ${NUH_UNSET:-\$X}/"', ["-rf", "/"]),
    (r'export X=src; bash -c "rm -rf ${NUH_UNSET:-\$X}/"', ["-rf", "src/"]),
    ('X=etc; for d in /; do cat "$d$X/shadow"; done', ["/etc/shadow"]),
    ('X=etc; : /; cat "$_$X/shadow"', ["/etc/shadow"]),
    ("for p in '$X/[ab]'; do cat \"$p\"; done", ["$X/[ab]"]),
    ('for X in "src/[a]"; do bash -c \'rm -rf "$X"/\'; done', ["-rf", "/"]),
    ('for X in "src/[a]"; do export X; bash -c \'rm -rf "$X"/\'; done', ["-rf", "src/[a]/"]),
])
def test_expansion_argv_evidence(
    expansion_probe: tuple[Path, dict[str, str]], shell: str, command: str, expected: list[str],
) -> None:
    project, env = expansion_probe
    executable = shutil.which(shell)
    assert executable is not None
    result = subprocess.run([executable, "-c", command], cwd=project, env=env,
                            capture_output=True, text=True, check=True, timeout=10)
    argv = json.loads(result.stdout)
    assert argv == expected
