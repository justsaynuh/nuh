"""MULTIOS counts fd duplications; env chdir expands tilde before entering the child."""

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
from nuh.shell_env import _wrapper_cwd
from nuh.shell_state import State
from tests.test_single_quote_literal import quote_project as quote_project


@pytest.mark.parametrize("redirect", ["<f", "0<f", "<&3", "0<&3", "<>f", "0<>f"])
def test_every_fd_zero_redirect_counts(quote_project: Path, redirect: str) -> None:
    (quote_project / "f").write_text("echo first\n")
    command = f"bash 3<f {redirect} <<< 'echo hi'"
    with word_quoting.scope():
        stages = _parse_stages(command)
        assert stages is not None and stages[0].stdin_ambiguous
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("redirect", ["<f", "0<f", "<&3", "0<&3", "<>f", "0<>f"])
def test_multios_program_evidence(quote_project: Path, shell: str, redirect: str) -> None:
    stub = quote_project / "argv"
    stub.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    stub.chmod(0o755)
    (quote_project / "f").write_text("argv file\n")
    env = {"PATH": f"{quote_project}:/usr/bin:/bin", "HOME": str(quote_project), "ZDOTDIR": str(quote_project)}
    completed = subprocess.run([shutil.which(shell), "-c", f"bash 3<f {redirect} <<< 'argv here'"],
                               cwd=quote_project, env=env, capture_output=True, text=True, check=True, timeout=10)
    programs = [json.loads(line) for line in completed.stdout.splitlines()]
    assert programs == ([["here"]] if shell == "bash" else [["file"], ["here"]])


def test_env_chdir_expands_outer_tilde(quote_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(quote_project.parent))
    paths.set_project_root(str(quote_project))
    result = classify_command("env -C ~/proj sh -c 'rm ok'")
    assert result.final_decision == taxonomy.ALLOW, result.reason


def test_env_chdir_tilde_uses_shell_local_home(quote_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/unrelated")
    outer = State(cwd=str(quote_project), vars=(("HOME", str(quote_project.parent)),))
    assert _wrapper_cwd("~/proj", outer) == (str(quote_project), "")


@pytest.mark.parametrize("tilde", ["~+", "~-", "~nuh_missing_user_7283"])
def test_unresolved_env_chdir_tilde_stays_opaque(quote_project: Path, tilde: str) -> None:
    (quote_project / tilde / "proj").mkdir(parents=True)
    result = classify_command(f"env -C {tilde}/proj sh -c 'rm ok'")
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_env_chdir_outer_tilde_evidence(quote_project: Path, shell: str) -> None:
    home = quote_project.parent
    env = {"HOME": str(home), "ZDOTDIR": str(home), "PATH": os.environ["PATH"]}
    completed = subprocess.run([shutil.which(shell), "-c", "env -C ~/proj HOME=/ignored sh -c 'pwd'"],
                               cwd=home, env=env, capture_output=True, text=True, check=True, timeout=10)
    assert completed.stdout.strip() == str(quote_project)
