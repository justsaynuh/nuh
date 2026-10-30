"""Runner interpolation cannot erase command output before shell parsing."""

from pathlib import Path

import pytest

from nuh import taxonomy
from nuh.bash import classify_command
from tests.test_single_quote_literal import quote_project as quote_project


@pytest.mark.parametrize("runner", ["just", "task"])
def test_unresolved_runner_output_asks(quote_project: Path, runner: str) -> None:
    if runner == "just":
        (quote_project / "justfile").write_text("X := `printf '\\073 rm -rf / \\043'`\nall:\n    echo {{X}}\n")
    else:
        (quote_project / "Taskfile.yml").write_text(
            "version: '3'\nvars:\n  X:\n    sh: printf '\\073 rm -rf / \\043'\n"
            "tasks:\n  all:\n    cmds:\n      - echo {{.X}}\n")
    result = classify_command(f"{runner} all")
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("runner", ["just", "task"])
def test_runner_output_does_not_hide_known_block(quote_project: Path, runner: str) -> None:
    if runner == "just":
        (quote_project / "justfile").write_text("X := `printf hi`\nall:\n    echo {{X}}\n    rm -rf /\n")
    else:
        (quote_project / "Taskfile.yml").write_text(
            "version: '3'\nvars:\n  X:\n    sh: printf hi\ntasks:\n  all:\n"
            "    cmds:\n      - echo {{.X}}\n      - rm -rf /\n")
    assert classify_command(f"{runner} all").final_decision == taxonomy.BLOCK
