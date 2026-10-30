"""Non-shell interpreters are reviewed against their produced program bytes."""

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import config, taxonomy, word_quoting
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages
from nuh.bash_policy import _lang_exec_program
from nuh.llm_prompt import _build_prompt
from tests.test_single_quote_literal import quote_project as quote_project


def _feed(kind: str, body: str) -> str:
    if kind == "heredoc":
        return f"python3 <<EOF\n{body}\nEOF"
    return f"python3 {'-c' if kind == 'argument' else '<<<'} \"{body}\""


@pytest.mark.parametrize("kind", ["argument", "heredoc", "here-string"])
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("program", ["print('expanded')", "print('$NUH_INNER_LITERAL')"])
def test_inline_program_receives_outer_expansion_once(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, kind: str, inherited: bool, program: str,
) -> None:
    name = "NUH_INLINE_PROGRAM"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NUH_INNER_LITERAL", "must-not-be-substituted")
    prefix = f"{name}={shlex.quote(program)}; "
    if inherited:
        monkeypatch.setenv(name, program)
        prefix = ""
    result = classify_command(prefix + _feed(kind, "$" + name))
    programs = [stage.inline_program.code for outer in result.stages for stage in outer.walk()
                if stage.inline_program is not None]
    assert programs == [program]
    assert f"```\n{program}\n```" in _build_prompt(result).user


@pytest.mark.parametrize("command,program", [
    ("python3 -c 'print(\"$NUH_LITERAL_CODE\")'", 'print("$NUH_LITERAL_CODE")'),
    ("python3 <<'EOF'\nprint('$NUH_LITERAL_CODE')\nEOF", "print('$NUH_LITERAL_CODE')"),
    ("python3 <<< 'print(\"$NUH_LITERAL_CODE\")'", 'print("$NUH_LITERAL_CODE")'),
    (r'''python3 -c "print('\$NUH_LITERAL_CODE')"''', "print('$NUH_LITERAL_CODE')"),
])
def test_quoted_inline_dollars_remain_program_text(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, command: str, program: str,
) -> None:
    monkeypatch.setenv("NUH_LITERAL_CODE", "must-not-be-substituted")
    result = classify_command(command)
    programs = [stage.inline_program.code for outer in result.stages for stage in outer.walk()
                if stage.inline_program is not None]
    assert programs == [program]


def test_awk_script_operands_retain_literal_dollars() -> None:
    # Pure awk reads classify as filesystem_read; test the shared program handoff directly.
    with word_quoting.scope():
        stages = _parse_stages("awk '{ print $1 }'")
        assert stages is not None
        target, program, unreadable = _lang_exec_program(stages[0].tokens, None)
    assert target is None
    assert unreadable == ""
    assert program is not None and program.code == "{ print $1 }"




@pytest.mark.parametrize("kind", ["argument", "heredoc", "here-string"])
@pytest.mark.parametrize("actions,decision", [
    ({}, taxonomy.ASK),
    ({taxonomy.LANG_EXEC: taxonomy.ALLOW}, taxonomy.ASK),
    ({taxonomy.LANG_EXEC: taxonomy.BLOCK}, taxonomy.BLOCK),
    ({taxonomy.UNKNOWN: taxonomy.BLOCK}, taxonomy.BLOCK),
])
def test_unknown_inline_bytes_cannot_be_reviewed_as_clean_code(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
    actions: dict[str, str], decision: str,
) -> None:
    monkeypatch.delenv("NUH_UNKNOWN_INLINE", raising=False)
    monkeypatch.setattr(config, "_cached_config", config.NuhConfig(actions=actions))
    result = classify_command(_feed(kind, "print('$NUH_UNKNOWN_INLINE')"))
    assert result.final_decision == decision, result.reason
    stages = [stage for outer in result.stages for stage in outer.walk()]
    assert any(stage.unreadable_program for stage in stages)
    assert all(stage.inline_program is None for stage in stages)
    assert "content scan clean" not in result.reason


@pytest.mark.parametrize("kind", ["argument", "heredoc", "here-string"])
def test_produced_inline_code_receives_content_inspection(
    quote_project: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    program = "import os; os.system('rm -rf /')"
    monkeypatch.setenv("NUH_INLINE_PROGRAM", program)
    result = classify_command(_feed(kind, "$NUH_INLINE_PROGRAM"))
    assert result.final_decision != taxonomy.ALLOW
    assert "destructive" in result.reason


@pytest.fixture
def inline_probe(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """The python3 executable prints incoming program text without executing it."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python3"
    stub.write_text(f"#!{sys.executable}\nimport json, sys\n"
                    "print(json.dumps(sys.argv[2] if sys.argv[1:2] == ['-c'] else sys.stdin.read()))\n")
    stub.chmod(0o755)
    return tmp_path, {"HOME": str(tmp_path), "ZDOTDIR": str(tmp_path), "PATH": str(bindir)}


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("kind", ["argument", "heredoc", "here-string"])
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("program", ["print('expanded')", "print('$NUH_INNER_LITERAL')"])
def test_interpreter_program_bytes_oracle(
    inline_probe: tuple[Path, dict[str, str]], shell: str, kind: str, inherited: bool, program: str,
) -> None:
    project, env = inline_probe
    env["NUH_INNER_LITERAL"] = "must-not-be-substituted"
    name = "NUH_INLINE_PROGRAM"
    prefix = f"{name}={shlex.quote(program)}; "
    if inherited:
        env[name] = program
        prefix = ""
    executable = shutil.which(shell)
    assert executable is not None
    result = subprocess.run([executable, "-c", prefix + _feed(kind, "$" + name)], cwd=project, env=env,
                            capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == program + ("" if kind == "argument" else "\n")


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("kind", ["argument", "heredoc", "here-string"])
@pytest.mark.parametrize("probe,expected", [
    ("$HOME", "HOME"), (r"\$HOME", "$HOME"), ("$(printf produced)", "produced"),
    ("`printf produced`", "produced"), ("$((2+2))", "4"), ("ab\\\ncd", "abcd"),
])
@pytest.mark.parametrize("inner_quote", [False, True])
def test_inline_boundary_probe_matrix(
    quote_project: Path, inline_probe: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
    shell: str, kind: str, probe: str, expected: str, inner_quote: bool,
) -> None:
    project, env = inline_probe
    monkeypatch.setenv("HOME", env["HOME"])
    quote = "'" if inner_quote else ""
    expected = env["HOME"] if expected == "HOME" else expected
    body = f"print({quote}{probe}{quote})"
    produced = f"print({quote}{expected}{quote})"
    command = _feed(kind, body)
    result = classify_command(command)
    programs = [stage.inline_program.code for stage in result.all_stages() if stage.inline_program]
    if probe in ("$(printf produced)", "`printf produced`"):
        assert not programs
        assert result.final_decision == taxonomy.ASK
    else:
        assert programs == [produced]
    completed = subprocess.run([shutil.which(shell), "-c", command], cwd=project, env=env,
                               capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(completed.stdout) == produced + ("" if kind == "argument" else "\n")
