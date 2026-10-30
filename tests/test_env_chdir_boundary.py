"""env changes the child cwd after the outer shell expands its arguments."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import shell_env, shell_state, word_quoting
from nuh.bash import classify_command


@pytest.fixture
def env_project(project_root: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project = Path(project_root)
    outside = project.parent / "elsewhere"
    outside.mkdir()
    (project / "inside").mkdir()
    monkeypatch.chdir(project)
    return project, outside


@pytest.mark.parametrize("flag", ["-C {directory}", "--chdir {directory}", "--chdir={directory}"])
@pytest.mark.parametrize("payload", ["rm -rf build", "sh -c 'rm -rf build'"])
def test_wrapped_relative_paths_use_child_directory(
    env_project: tuple[Path, Path], flag: str, payload: str,
) -> None:
    _project, outside = env_project

    result = classify_command(f"env {flag.format(directory=shlex.quote(str(outside)))} {payload}")

    assert result.final_decision == "ask"
    assert "outside project" in result.reason


def test_nested_wrappers_resolve_each_chdir(env_project: tuple[Path, Path]) -> None:
    _project, outside = env_project
    (outside / "nested").mkdir()

    result = classify_command(f"env -C {shlex.quote(str(outside))} env -C nested rm -rf build")

    assert result.final_decision == "ask"
    assert "elsewhere/nested/build" in result.reason


@pytest.mark.parametrize("directory", ["missing-directory", '"$ENV_CHDIR_UNKNOWN"'])
def test_unknown_child_directory_asks(env_project: tuple[Path, Path], directory: str) -> None:
    result = classify_command(f"env -C {directory} rm -rf build")

    assert result.final_decision == "ask"


def test_outer_argument_expansion_precedes_env_settings(env_project: tuple[Path, Path]) -> None:
    project, outside = env_project
    command = f"DIR=inside; env -C \"$DIR\" DIR={shlex.quote(str(outside))} rm -rf build"

    result = classify_command(command)

    assert result.final_decision == "allow"
    assert Path.cwd() == project


@pytest.mark.parametrize("program,decision", [
    ('"rm -rf \'$PWD/build\'"', "allow"),
    ("'rm -rf \"$PWD/build\"'", "ask"),
])
def test_outer_and_inner_pwd_expand_in_their_own_directory(
    env_project: tuple[Path, Path], program: str, decision: str,
) -> None:
    _project, outside = env_project

    result = classify_command(f"env -C {shlex.quote(str(outside))} sh -c {program}")

    assert result.final_decision == decision


def test_inherited_directory_is_expanded_before_env_assignment(
    env_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, outside = env_project
    monkeypatch.setenv("ENV_CHDIR_TEST", str(outside))

    result = classify_command('env -C "$ENV_CHDIR_TEST" ENV_CHDIR_TEST=inside rm -rf build')

    assert result.final_decision == "ask"
    assert "outside project" in result.reason
    assert Path.cwd() == project


def test_quoted_dollar_directory_stays_literal(env_project: tuple[Path, Path]) -> None:
    project, _outside = env_project
    (project / "$ENV_CHDIR_TEST").mkdir()

    result = classify_command("env --chdir='$ENV_CHDIR_TEST' rm -rf build")

    assert result.final_decision == "allow"


def test_unquoted_chdir_cannot_hide_changed_command(
    env_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, outside = env_project
    monkeypatch.setenv("ENV_CHDIR_SPLIT", f"{outside} printf injected")

    result = classify_command("env -C $ENV_CHDIR_SPLIT echo hi")

    assert result.final_decision == "ask"


def test_braced_inherited_directory_is_resolved(
    env_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, _outside = env_project
    monkeypatch.setenv("ENV_CHDIR_TEST", "inside")

    result = classify_command('env -C "${ENV_CHDIR_TEST}" rm -rf build')

    assert result.final_decision == "allow"


def test_entered_and_command_states_share_child_cwd(env_project: tuple[Path, Path]) -> None:
    project, outside = env_project
    state = shell_state.State(cwd=str(project)).with_vars({"KEEP": "outer"})
    with shell_state.entered(state), word_quoting.scope():
        with shell_env.wrapped(["env", "-C", str(outside), "KEEP=child", "echo", "ok"]):
            assert shell_state.current().cwd == str(outside)
            assert shell_state.command_env().cwd == str(outside)
            assert shell_state.current().known_value("KEEP") == "outer"
            assert shell_state.command_env().known_value("KEEP") == "child"
        assert shell_state.current().cwd == str(project)
    assert Path.cwd() == project


def test_argv_oracle_keeps_outer_expansion_before_chdir(env_project: tuple[Path, Path]) -> None:
    project, outside = env_project
    stub = project / "argv.py"
    stub.write_text("import json, os, sys\nprint(json.dumps([os.getcwd(), sys.argv[1:]]))\n")
    command = (f"MARK=outer; env -C {shlex.quote(str(outside))} MARK=child "
               f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))} \"$PWD\" \"$MARK\"")

    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=True, cwd=project)

    assert json.loads(result.stdout) == [str(outside), [str(project), "outer"]]
