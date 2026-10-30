"""Outer expansion precedes nested parsing; all execution probes use argv stubs."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import config, taxonomy
from nuh.bash import classify_command
from tests.test_single_quote_literal import argv_probe as argv_probe, quote_project as quote_project


PROGRAMS = [
    'bash -c "rm -rf \'$HOME\'"',
    'sh -c "rm -rf \'$HOME\'"',
    'zsh -c "rm -rf \'$HOME\'"',
    'eval "rm -rf \'$HOME\'"',
    'su -c "rm -rf \'$HOME\'"',
    'sudo sh -c "rm -rf \'$HOME\'"',
    'env X=src sh -c "rm -rf \'$HOME\'"',
    'xargs sh -c "rm -rf \'$HOME\'"',
    'find . -maxdepth 0 -exec sh -c "rm -rf \'$HOME\'" \\;',
    'bash <<< "rm -rf \'$HOME\'"',
    'bash <<< \'rm -rf "$HOME"\'',
]

HEREDOCS = [
    "X=/; bash <<EOF\nrm -rf '$X'\nEOF",
    "X=/; source /dev/stdin <<EOF\nrm -rf '$X'\nEOF",
    "X=/; . /dev/stdin <<EOF\nrm -rf '$X'\nEOF",
    "X=/; sudo sh <<EOF\nrm -rf '$X'\nEOF",
    "X=/; env X=src sh <<EOF\nrm -rf '$X'\nEOF",
    "X=/; command bash <<EOF\nrm -rf '$X'\nEOF",
]


@pytest.mark.parametrize("command", PROGRAMS + HEREDOCS)
def test_outer_expansion_blocks(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    print(f"{command!r} => {result.final_decision}: {result.reason}")
    assert result.final_decision == taxonomy.BLOCK, result.reason


@pytest.mark.parametrize("runner", ["bash -c", "eval", "bash <<<", "env HOME=src sh -c"])
def test_outer_expansion_sensitive_read(quote_project: Path, runner: str) -> None:
    result = classify_command(f'{runner} "cat \'$HOME/.aws/credentials\'"')
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("runner", ["bash", "source /dev/stdin"])
@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"', r"\EOF"])
def test_quoted_delimiters_defer(quote_project: Path, runner: str, delimiter: str) -> None:
    result = classify_command(f"X=/; {runner} <<{delimiter}\nrm -rf '$X'\nEOF")
    assert result.final_decision == taxonomy.ALLOW, result.reason


@pytest.mark.parametrize("command,expected", [
    ("X=/; bash <<EOF\nrm -rf '\\$X'\nEOF", taxonomy.ALLOW),
    ("X='$HOME'; bash -c \"cat '$X/.aws/credentials'\"", taxonomy.ALLOW),
    ("X=/; env X=src sh -c 'rm -rf \"$X\"/*'", taxonomy.ALLOW),
    ("X=/; env X=src sh -c \"rm -rf '$X'\"", taxonomy.BLOCK),
    ("X=/; X=src bash -c \"rm -rf '$X'\"", taxonomy.BLOCK),
])
def test_outer_and_child_environments(quote_project: Path, command: str, expected: str) -> None:
    result = classify_command(command)
    assert result.final_decision == expected, result.reason


@pytest.mark.parametrize("command", [
    'bash -c "rm -rf \'$NUH_UNKNOWN_OUTER\'"',
    'env NUH_UNKNOWN_OUTER=src sh -c "rm -rf \'$NUH_UNKNOWN_OUTER\'"',
    'eval "rm -rf \'$NUH_UNKNOWN_OUTER\'"',
    "bash <<EOF\nrm -rf '$NUH_UNKNOWN_OUTER'\nEOF",
    'X=$(printf /); bash -c "rm -rf \'$X\'"',
    'bash -c "rm -rf \'$(printf /)\'"',
    "bash <<EOF\nrm -rf '$(printf /)'\nEOF",
    'bash -c "rm -rf \'${HOME%/*}\'"',
])
def test_unknown_outer_expansion_asks(quote_project: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.delenv("NUH_UNKNOWN_OUTER", raising=False)
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("command", [
    'bash -c "cat \'$((2+2))\'"',
    "bash <<EOF\ncat '$((2+2))'\nEOF",
    'N=2; bash -c "cat \'$((N+2))\'"',
])
def test_outer_arithmetic_text(quote_project: Path, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW, result.reason
    assert any(r.tokens == ["cat", "4"] for stage in result.stages for r in stage.walk())


@pytest.fixture
def safe_probe(argv_probe: tuple[Path, dict[str, str]]) -> tuple[Path, dict[str, str]]:
    """Hermetic PATH: privileged/remote wrappers only print their incoming arguments."""
    project, env = argv_probe
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    for name in ("bash", "sh", "zsh", "env", "xargs", "find"):
        executable = shutil.which(name)
        assert executable is not None
        (bindir / name).symlink_to(executable)
    for name in ("su", "sudo", "ssh"):
        stub = bindir / name
        stub.write_text(f"#!{sys.executable}\nimport json, sys\n"
                        "args = sys.argv[1:]\n"
                        "print(json.dumps(args + [sys.stdin.read()] if args == ['sh'] else args))\n")
        stub.chmod(0o755)
    return project, {**env, "PATH": str(bindir)}


def probe_argv(shell: str, command: str, probe: tuple[Path, dict[str, str]]) -> list[str]:
    """Run a real outer shell with no destructive, privileged or remote tool available."""
    project, env = probe
    executable = shutil.which(shell)
    assert executable is not None
    result = subprocess.run([executable, "-c", command], cwd=project, env=env, input="\n",
                            capture_output=True, text=True, check=True, timeout=10)
    argv = json.loads(result.stdout)
    print(f"{shell}: {command!r} => {argv}")
    return argv


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("command", PROGRAMS + HEREDOCS)
def test_shell_argv(safe_probe: tuple[Path, dict[str, str]], shell: str, command: str) -> None:
    argv = probe_argv(shell, command, safe_probe)
    home = safe_probe[1]["HOME"]
    target = "/" if command in HEREDOCS else home
    if command.startswith("su "):
        assert argv == ["-c", f"rm -rf '{home}'"]
        assert probe_argv(shell, argv[1], safe_probe) == ["-rf", home]
    elif command.startswith("sudo ") and command not in HEREDOCS:
        assert argv == ["sh", "-c", f"rm -rf '{home}'"]
        assert probe_argv("sh", argv[2], safe_probe) == ["-rf", home]
    elif command == HEREDOCS[3]:
        assert argv == ["sh", "rm -rf '/'\n"]
        assert probe_argv("sh", "sh <<'EOF'\n" + argv[1] + "EOF", safe_probe) == ["-rf", "/"]
    else:
        assert argv == ["-rf", target]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_ssh_outer_argv(safe_probe: tuple[Path, dict[str, str]], shell: str, quote_project: Path) -> None:
    command = 'ssh host "rm -rf \'$HOME\'"'
    argv = probe_argv(shell, command, safe_probe)
    assert argv == ["host", f"rm -rf '{safe_probe[1]['HOME']}'"]
    # SSH is classified as a remote operation; nuh does not walk the remote program.
    assert classify_command(command).final_decision == taxonomy.ASK


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"', r"\EOF"])
def test_literal_heredoc_argv(safe_probe: tuple[Path, dict[str, str]], shell: str, delimiter: str) -> None:
    assert probe_argv(shell, f"bash <<{delimiter}\nrm -rf '$X'\nEOF", safe_probe) == ["-rf", "$X"]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("runner", ["bash -c", "eval", "bash <<<"])
def test_sensitive_read_argv(safe_probe: tuple[Path, dict[str, str]], shell: str, runner: str) -> None:
    command = f'{runner} "cat \'$HOME/.aws/credentials\'"'
    assert probe_argv(shell, command, safe_probe) == [safe_probe[1]["HOME"] + "/.aws/credentials"]


@pytest.mark.parametrize("command,expected", [
    ("X=/; bash <<EOF\nrm -rf '\\$X'\nEOF", ["-rf", "$X"]),
    ("X='$HOME'; bash -c \"cat '$X/.aws/credentials'\"", ["$HOME/.aws/credentials"]),
    ("X=/; env X=src sh -c 'rm -rf \"$X\"/*'", ["-rf", "src/a"]),
    ("X=/; env X=src sh -c \"rm -rf '$X'\"", ["-rf", "/"]),
    ("X=/; X=src bash -c \"rm -rf '$X'\"", ["-rf", "/"]),
    ("bash <<'EOF'\nrm -rf '$X'\nEOF", ["-rf", "$X"]),
    ('bash -c "cat \'$((2+2))\'"', ["4"]),
    ("bash <<EOF\ncat '$((2+2))'\nEOF", ["4"]),
])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_boundary_argv(
    safe_probe: tuple[Path, dict[str, str]], shell: str, command: str, expected: list[str],
) -> None:
    assert probe_argv(shell, command, safe_probe) == expected


@pytest.mark.parametrize("runner", [
    "bash -c", "sh -c", "zsh -c", "eval", "su -c", "sudo sh -c", "env X=src sh -c",
    "xargs sh -c", "find . -maxdepth 0 -exec sh -c", "bash <<<",
])
def test_unknown_outer_expansion_in_every_consumer(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, runner: str,
) -> None:
    monkeypatch.delenv("NUH_UNKNOWN_OUTER", raising=False)
    suffix = r" \;" if runner.startswith("find ") else ""
    result = classify_command(f'{runner} "rm -rf \'$NUH_UNKNOWN_OUTER\'"{suffix}')
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("runner", ["bash -c", "eval", "bash <<<"])
def test_program_parts_retain_independent_masks(quote_project: Path, runner: str) -> None:
    # Concatenated word pieces and separate eval operands both retain their own quoting.
    command = f"{runner} 'cat' \"'$HOME/.aws/credentials'\"" if runner == "eval" else (
        f"{runner} 'cat '\"'$HOME/.aws/credentials'\"")
    assert classify_command(command).final_decision == taxonomy.ASK


def test_outer_arithmetic_uses_inherited_environment(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NUH_OUTER_NUMBER", "2")
    result = classify_command('bash -c "cat \'$((NUH_OUTER_NUMBER+2))\'"')
    assert result.final_decision == taxonomy.ALLOW, result.reason
    assert any(r.tokens == ["cat", "4"] for stage in result.stages for r in stage.walk())


@pytest.mark.parametrize("quote", ["'", '"'])
def test_find_placeholder_preserves_program_suffix(quote_project: Path, quote: str) -> None:
    command = f"find ./src -maxdepth 0 -exec sh -c {quote}echo {{}}; rm -rf /{quote} \\;"
    result = classify_command(command)
    assert result.final_decision == taxonomy.BLOCK, result.reason
    assert any(r.tokens == ["rm", "-rf", "/"] for stage in result.stages for r in stage.walk())


@pytest.mark.parametrize("runner,expected", [
    ("bash -c", taxonomy.ALLOW), ("sh -c", taxonomy.ALLOW), ("zsh -c", taxonomy.ALLOW),
    ("eval", taxonomy.ALLOW), ("su -c", taxonomy.ASK), ("sudo sh -c", taxonomy.ASK),
    ("env X=src sh -c", taxonomy.ALLOW), ("xargs sh -c", taxonomy.ASK),
    ("find . -maxdepth 0 -exec sh -c", taxonomy.ALLOW), ("bash <<<", taxonomy.ALLOW),
])
def test_literal_program_in_every_consumer(quote_project: Path, runner: str, expected: str) -> None:
    program = shlex.quote("cat '$HOME/.aws/credentials'")
    suffix = r" \;" if runner.startswith("find ") else ""
    result = classify_command(f"{runner} {program}{suffix}")
    assert result.final_decision == expected, result.reason


@pytest.mark.parametrize("runner", [
    "bash -c", "sh -c", "zsh -c", "eval", "su -c", "sudo sh -c", "env X=src sh -c",
    "xargs sh -c", "find . -maxdepth 0 -exec sh -c", "bash <<<",
])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_literal_program_argv(safe_probe: tuple[Path, dict[str, str]], shell: str, runner: str) -> None:
    program = "cat '$HOME/.aws/credentials'"
    suffix = r" \;" if runner.startswith("find ") else ""
    argv = probe_argv(shell, f"{runner} {shlex.quote(program)}{suffix}", safe_probe)
    if runner == "su -c":
        assert argv == ["-c", program]
        argv = probe_argv("sh", argv[1], safe_probe)
    elif runner == "sudo sh -c":
        assert argv == ["sh", "-c", program]
        argv = probe_argv("sh", argv[2], safe_probe)
    assert argv == ["$HOME/.aws/credentials"]


@pytest.mark.parametrize("parameter", ["?", "#", "$"])
def test_unknown_numeric_outer_parameters_ask(quote_project: Path, parameter: str) -> None:
    result = classify_command(f'bash -c "cat \'${parameter}\'"')
    assert result.final_decision == taxonomy.ASK, result.reason


@pytest.mark.parametrize("command,actions", [
    ("xargs sh -c 'cat /etc/passwd'", {"lang_exec": "block"}),
    ('bash -c "cat \'$NUH_UNKNOWN_OUTER\'"', {"unknown": "block"}),
])
def test_stricter_configured_policy_survives(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, command: str, actions: dict[str, str],
) -> None:
    monkeypatch.delenv("NUH_UNKNOWN_OUTER", raising=False)
    monkeypatch.setattr(config, "_cached_config", config.NuhConfig(actions=actions))
    result = classify_command(command)
    assert result.final_decision == taxonomy.BLOCK, result.reason
