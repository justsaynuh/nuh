"""Argument evidence for intentionally weaker literal and one-pass expansion readings than main."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_single_quote_literal import argv_probe as argv_probe


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("command,expected", [
    ("cat '$HOME/.aws/credentials'", ["$HOME/.aws/credentials"]),
    ("cat '~/.aws/credentials'", ["~/.aws/credentials"]),
    ("X=~/.aws/credentials; cat '$X'", ["$X"]),
    ("X=~/.aws/credentials; cat '${X}'", ["${X}"]),
    ("X=~/.aws/credentials; cat '${X:-other}'", ["${X:-other}"]),
    (r"cat \$HOME/.aws/credentials", ["$HOME/.aws/credentials"]),
    ("cat '$(cat /etc/shadow)'", ["$(cat /etc/shadow)"]),
    (r"cat $'\x24HOME/.aws/credentials'", ["$HOME/.aws/credentials"]),
    ("X='$HOME'; cat \"$X/.aws/credentials\"", ["$HOME/.aws/credentials"]),
    ("rm -rf '$HOME'", ["-rf", "$HOME"]),
    ("rm -rf '${HOME}'", ["-rf", "${HOME}"]),
    ("rm -rf '~'", ["-rf", "~"]),
    ("for X in '$HOME'; do cat \"$X/.aws/credentials\"; done", ["$HOME/.aws/credentials"]),
    ("X='$HOME'; bash -c \"cat '$X/.aws/credentials'\"", ["$HOME/.aws/credentials"]),
    ("NUH_OUTER_NUMBER=2; bash -c \"cat '$((NUH_OUTER_NUMBER+2))'\"", ["4"]),
    (r'export X=src; bash -c "rm -rf ${NUH_UNSET:-\$X}/"', ["-rf", "src/"]),
    ("P='cat literal'; bash <<< $P", ["literal"]),
])
def test_main_difference_argument_evidence(
    argv_probe: tuple[Path, dict[str, str]], shell: str, command: str, expected: list[str],
) -> None:
    project, env = argv_probe
    bindir = Path(env["PATH"].split(":")[0])
    (bindir / "bash").symlink_to(shutil.which("bash"))
    env = {**env, "PATH": str(bindir)}
    completed = subprocess.run([shutil.which(shell), "-c", command], cwd=project, env=env,
                               capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(completed.stdout) == expected


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_just_continued_program_argument_evidence(tmp_path: Path, shell: str) -> None:
    """The recipe shell consumes the backslash-newline before parsing bash's program."""
    stub = tmp_path / "argv"
    stub.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    stub.chmod(0o755)
    (tmp_path / "bash").symlink_to(shutil.which("bash"))
    command = 'bash -c "argv ab\\\ncd"'
    env = {"HOME": str(tmp_path), "ZDOTDIR": str(tmp_path), "PATH": str(tmp_path)}
    completed = subprocess.run([shutil.which(shell), "-c", command], cwd=tmp_path, env=env,
                               capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(completed.stdout) == ["abcd"]
