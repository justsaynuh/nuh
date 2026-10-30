"""Make output becomes fresh recipe bytes, rather than shell substitution data."""

from pathlib import Path

import pytest

from nuh import task_runners, taxonomy
from nuh.bash import classify_command


@pytest.fixture
def make_project(project_root: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(project_root)
    return Path(project_root)


@pytest.mark.parametrize("assignment,recipe", [
    ("X := $(shell printf '\\073 printf injected \\043')", "echo $(X)"),
    ("X = $(shell printf '\\073 printf injected \\043')", "echo $(X)"),
    ("X != printf '\\073 printf injected \\043'", "echo $(X)"),
    ("", "echo $(shell printf '\\073 printf injected \\043')"),
    ("X := $(shell printf 'single-quote')", "echo '$(X)'"),
    ("X := $(shell printf 'one two')\nN := $(words $(X))", "echo $(N)"),
    ("X := $(shell printf 'one two')\nN := $(words $(value X))", "echo $(N)"),
])
def test_produced_recipe_bytes_ask(make_project: Path, assignment: str, recipe: str) -> None:
    (make_project / "Makefile").write_text(f"{assignment}\nall:\n\t{recipe}\n")

    result = classify_command("make")

    assert result.final_decision == taxonomy.ASK
    assert "command output" in result.reason


def test_unused_output_keeps_known_commands(make_project: Path) -> None:
    (make_project / "Makefile").write_text("X := $(shell printf harmless)\nall:\n\techo hi\n")

    recipe = task_runners.resolve(["make"])
    result = classify_command("make")

    assert recipe is not None and "printf harmless" in recipe.lines
    assert result.final_decision == taxonomy.ALLOW


def test_parse_time_effect_block_survives_opaque_recipe(make_project: Path) -> None:
    # Classification only: make and this command are never executed.
    (make_project / "Makefile").write_text("X := $(shell rm -rf /)\nall:\n\techo $(X)\n")

    result = classify_command("make")

    assert result.final_decision == taxonomy.BLOCK
    assert "catastrophic" in result.reason


@pytest.mark.parametrize("setting", [
    "SHELL := /bin/sh $(X)",
    ".SHELLFLAGS := $(X)",
])
def test_produced_shell_invocation_asks(make_project: Path, setting: str) -> None:
    (make_project / "Makefile").write_text(f"X := $(shell printf harmless)\n{setting}\nall:\n\techo hi\n")

    result = classify_command("make")

    assert result.final_decision == taxonomy.ASK
    assert "command output" in result.reason


def test_producer_effect_block_survives_unknown_include(make_project: Path) -> None:
    # Classification only; the failing include must not hide a command make already ran.
    (make_project / "Makefile").write_text("X := $(shell rm -rf /)\ninclude $(X)\nall:\n\techo hi\n")

    result = classify_command("make")

    assert result.final_decision == taxonomy.BLOCK
    assert "catastrophic" in result.reason
