"""Shell-produced bytes must lose exactly one layer of quoting and expansion."""

from pathlib import Path

import pytest

from nuh import taxonomy
from nuh.bash import classify_command
from tests.test_single_quote_literal import quote_project as quote_project


RUNNERS = [
    "bash -c", "sh -c", "zsh -c", "eval", "bash <<<", "source /dev/stdin <<<",
    ". /dev/stdin <<<", "env X=src sh -c", "xargs sh -c",
    "find . -maxdepth 0 -exec sh -c", "timeout 5 sh -c", "nohup sh -c",
]


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("program,decision", [
    (r"rm -rf \$HOME", taxonomy.BLOCK),
    (r"cat \$HOME/.aws/credentials", taxonomy.ASK),
])
def test_double_quote_removal(quote_project: Path, runner: str, program: str, decision: str) -> None:
    suffix = r" \;" if runner.startswith("find ") else ""
    result = classify_command(f'{runner} "{program}"{suffix}')
    assert result.final_decision == decision, result.reason


def test_nested_quote_removal(quote_project: Path) -> None:
    result = classify_command(r'''bash -c "sh -c \"rm -rf \\\$HOME\""''')
    assert result.final_decision == taxonomy.BLOCK, result.reason


@pytest.mark.parametrize("source", ["source", "."])
def test_other_descriptor_body_is_not_stdin_redirect(quote_project: Path, source: str) -> None:
    command = f"{source} /dev/stdin 3<<'A' <<'B'\n<<<true\nA\nrm -rf /\nB"
    assert classify_command(command).final_decision == taxonomy.BLOCK


def test_literal_escaped_dollar_program(quote_project: Path) -> None:
    assert classify_command(r"bash -c 'rm -rf \\$HOME'").final_decision == taxonomy.ALLOW


def test_heredoc_expansion_values_are_not_expanded_again(quote_project: Path) -> None:
    command = 'X=src; P=\'rm -rf "$X"/*\'; bash <<EOF\n$P\nEOF'
    result = classify_command(command)
    assert result.final_decision == taxonomy.BLOCK, result.reason
