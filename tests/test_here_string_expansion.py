"""Here-string operands expand as one word; execution probes only run argv printers."""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from nuh import taxonomy
from nuh.bash import classify_command
from tests.test_single_quote_literal import argv_probe as argv_probe, quote_project as quote_project


@pytest.mark.parametrize("runner", ["bash", "source /dev/stdin", ". /dev/stdin"])
@pytest.mark.parametrize("operator", ["<<< ", "<<<"])
@pytest.mark.parametrize("program,decision", [
    ("echo hi; rm -rf /", taxonomy.BLOCK),
    ("cat '$HOME/.aws/credentials'", taxonomy.ALLOW),
])
@pytest.mark.parametrize("inherited", [False, True])
def test_here_string_preserves_complete_program(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, runner: str, operator: str,
    program: str, decision: str, inherited: bool,
) -> None:
    name = "NUH_HERE_PROGRAM"
    monkeypatch.delenv(name, raising=False)
    prefix = f"{name}={shlex.quote(program)}; "
    if inherited:
        monkeypatch.setenv(name, program)
        prefix = ""
    result = classify_command(f"{prefix}{runner} {operator}${name}")
    assert result.final_decision == decision, result.reason
    expected = ["rm", "-rf", "/"] if decision == taxonomy.BLOCK else ["cat", "$HOME/.aws/credentials"]
    assert any(stage.tokens == expected for outer in result.stages for stage in outer.walk())


@pytest.mark.parametrize("runner", ["bash", "source /dev/stdin", ". /dev/stdin"])
@pytest.mark.parametrize("operator", ["<<< ", "<<<"])
def test_partial_here_string_preserves_spaced_value(quote_project: Path, runner: str, operator: str) -> None:
    result = classify_command(f"P='hi; rm -rf /'; {runner} {operator}echo\\ $P")
    assert result.final_decision == taxonomy.BLOCK, result.reason
    assert any(stage.tokens == ["rm", "-rf", "/"] for outer in result.stages for stage in outer.walk())


@pytest.mark.parametrize("quoting", ["single", "escaped"])
def test_literal_here_string_variable_stays_literal(quote_project: Path, quoting: str) -> None:
    program = "cat '$NUH_HERE_PROGRAM'"
    operand = shlex.quote(program) if quoting == "single" else '"' + program.replace("$", r"\$") + '"'
    command = f"NUH_HERE_PROGRAM='rm -rf /'; bash <<<{operand}"
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW, result.reason
    assert any(stage.tokens == ["cat", "$NUH_HERE_PROGRAM"]
               for outer in result.stages for stage in outer.walk())


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("runner", ["bash", "source /dev/stdin", ". /dev/stdin"])
@pytest.mark.parametrize("operator", ["<<< ", "<<<"])
@pytest.mark.parametrize("program,expected", [
    ("echo hi; rm -rf /", ["-rf", "/"]),
    ("cat '$HOME/.aws/credentials'", ["$HOME/.aws/credentials"]),
])
def test_here_string_argv_oracle(
    argv_probe: tuple[Path, dict[str, str]], shell: str, runner: str, operator: str,
    program: str, expected: list[str],
) -> None:
    project, env = argv_probe
    executable = shutil.which(shell)
    assert executable is not None
    command = f"P={shlex.quote(program)}; {runner} {operator}$P"
    result = subprocess.run([executable, "-c", command], cwd=project, env=env,
                            capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout.splitlines()[-1]) == expected
