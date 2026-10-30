"""Boundary matrix: real shells execute only an argv printer as the payload."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import taxonomy, word_quoting
from nuh.bash import classify_command
from tests.test_single_quote_literal import quote_project as quote_project


PROBES = ["$HOME", r"\$HOME", "$(printf produced)", "`printf produced`", "$((2+2))", "ab\\\ncd"]
PATHS = [
    "bash -c", "sh -c", "zsh -c", "eval", "env X=src sh -c", "xargs sh -c",
    "find . -maxdepth 0 -exec sh -c", "timeout 5 sh -c", "nohup sh -c",
    "bash <<<", "source /dev/stdin <<<", ". /dev/stdin <<<",
    "heredoc", "source-heredoc", "make", "npm", "pnpm", "yarn", "just", "task",
    "watch", "script -c", "ssh",
    "su -c", "sudo sh -c", "uv run sh -c", "npx --no-install sh -c", "bundle exec sh -c",
    "poetry run sh -c", "nested", "env -C . sh -c",
]


@pytest.fixture
def boundary_probe(quote_project: Path, tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A hermetic executable set; argv is the only leaf command in any program."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "argv"
    stub.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    stub.chmod(0o755)
    for name in ("bash", "sh", "zsh", "env", "xargs", "find", "timeout", "nohup", "cat", "tee", "make"):
        executable = shutil.which(name)
        assert executable, name
        (bindir / name).symlink_to(executable)
    # These boundaries stay opaque. Their stubs expose the exact remote or
    # wrapper argument instead of starting a terminal, timer, or connection.
    for name in ("watch", "script", "ssh", "su", "sudo", "uv", "npx", "bundle", "poetry"):
        (bindir / name).symlink_to(stub)
    env = {"HOME": os.environ["HOME"], "PATH": str(bindir), "ZDOTDIR": str(tmp_path)}
    return quote_project, env


def _command(path: str, program: str, project: Path) -> str:
    if path == "nested":
        return "bash -c " + shlex.quote(f'sh -c "{program}"')
    if path == "heredoc":
        return f"bash <<EOF\n{program}\nEOF"
    if path == "source-heredoc":
        return f"source /dev/stdin <<EOF\n{program}\nEOF"
    if path == "make":
        # Make consumes one dollar layer before the recipe shell sees it.
        (project / "Makefile").write_text("all:\n\t@bash -c \"" + program.replace("$", "$$").replace(
            "\\\n", "\\\n\t") + '"\n')
        return "make -s all"
    if path in ("npm", "pnpm", "yarn"):
        (project / "package.json").write_text(json.dumps({"scripts": {"probe": f'bash -c "{program}"'}}))
        return f"{path} run probe"
    if path == "just":
        (project / "justfile").write_text('all:\n    bash -c "' + program.replace("\\\n", "\\\n    ") + '"\n')
        return "just all"
    if path == "task":
        import yaml
        (project / "Taskfile.yml").write_text(yaml.safe_dump({
            "version": "3", "tasks": {"all": {"cmds": [f'bash -c "{program}"']}}}))
        return "task all"
    if path == "ssh":
        return f'ssh host "{program}"'
    if path == "watch":
        return f'watch -n 1 "{program}"'
    suffix = r" \;" if path.startswith("find ") else ""
    return f'{path} "{program}"{suffix}'


def _run(command: str, probe: tuple[Path, dict[str, str]], shell: str = "bash") -> list[str]:
    project, env = probe
    executable = shutil.which(shell)
    assert executable
    result = subprocess.run([executable, "-c", command], cwd=project, env=env, input="\n",
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("probe", PROBES)
@pytest.mark.parametrize("inner_quote", [False, True])
@pytest.mark.parametrize("outer_shell", ["bash", "zsh"])
def test_produced_boundary_matrix(
    boundary_probe: tuple[Path, dict[str, str]], path: str, probe: str, inner_quote: bool, outer_shell: str,
) -> None:
    project, env = boundary_probe
    operand = f"'{probe}'" if inner_quote else probe
    command = _command(path, "cat " + operand, project)
    result = classify_command(command)
    oracle = _command(path, "argv " + operand, project)
    if path in ("npm", "pnpm", "yarn", "just", "task"):
        # Runner binaries need not be installed. Execute their resolved shell
        # recipe with the same literal bytes the manifest hands its shell.
        argv = _run(f'bash -c "argv {operand}"', boundary_probe, outer_shell)
    else:
        argv = _run(oracle, boundary_probe, outer_shell)
    if path in ("watch", "script -c", "ssh"):
        assert result.final_decision == taxonomy.ASK, result.reason
        argv = _run(argv[-1], boundary_probe, outer_shell)
    elif path == "su -c":
        argv = _run(argv[-1], boundary_probe, outer_shell)
    elif path == "sudo sh -c":
        argv = _run(shlex.join(argv), boundary_probe, outer_shell)
    elif path in ("uv run sh -c", "npx --no-install sh -c", "bundle exec sh -c", "poetry run sh -c"):
        argv = _run(shlex.join(argv[1:]), boundary_probe, outer_shell)
    if probe == "$HOME" or (probe == r"\$HOME" and not inner_quote):
        expected = env["HOME"]
    elif probe == r"\$HOME":
        expected = "$HOME"
    else:
        expected = {PROBES[2]: "produced", PROBES[3]: "produced", PROBES[4]: "4", PROBES[5]: "abcd"}[probe]
    assert argv == [expected], (path, probe, inner_quote, argv)
    if path in ("watch", "script -c", "ssh"):
        return
    if probe in (PROBES[2], PROBES[3]):
        assert result.final_decision in (taxonomy.ASK, taxonomy.BLOCK), result.reason
    else:
        assert any([word_quoting.expand_path(word) for word in stage.tokens] == ["cat", expected]
                   for stage in result.all_stages()), (
            path, probe, inner_quote, result)


@pytest.mark.parametrize("fd", ["", "3"])
def test_multiple_heredocs_do_not_create_quotes(
    quote_project: Path, boundary_probe: tuple[Path, dict[str, str]], fd: str,
) -> None:
    command = f"bash {fd}<<'A' <<'B'\necho '\nA\nrm -rf / #'\nB"
    assert classify_command(command).final_decision == taxonomy.BLOCK
    oracle = f"bash {fd}<<'A' <<'B'\necho '\nA\nargv / #'\nB"
    assert _run(oracle, boundary_probe) == ["/"]


@pytest.mark.parametrize("delimiter", ["EOF", "'EOF'"])
@pytest.mark.parametrize("body", [r'argv \"x\"', r"argv '\$HOME'", r"argv \$HOME", r"argv \\$HOME"])
def test_heredoc_escape_bytes(
    boundary_probe: tuple[Path, dict[str, str]], delimiter: str, body: str,
) -> None:
    from nuh import shell_lex, shell_program_text
    from nuh.shell_state import State

    text = f"bash <<{delimiter}\n{body}\nEOF"
    doc = shell_lex.scan(text).heredocs[0]
    with word_quoting.scope():
        produced, unreadable = shell_program_text.expand(shell_program_text.heredoc(text, doc), State(cwd=os.getcwd()))
    assert not unreadable
    # Replace bash with cat to observe the writer's exact bytes, without
    # parsing the quotes or executing the program in those bytes.
    project, env = boundary_probe
    result = subprocess.run([shutil.which("bash"), "-c", text.replace("bash <<", "cat <<", 1)],
                            cwd=project, env=env, capture_output=True, text=True, check=True)
    assert result.stdout == produced + "\n"


def test_changed_stdin_expectation_argv(boundary_probe: tuple[Path, dict[str, str]]) -> None:
    project, _ = boundary_probe
    (project / "input.sh").write_text("argv last\n")
    assert _run("bash <<< 'argv first' < input.sh", boundary_probe) == ["last"]
    assert _run("bash <<< 'argv first' <<< 'argv last'", boundary_probe) == ["last"]


def test_zsh_multios_asks(boundary_probe: tuple[Path, dict[str, str]]) -> None:
    command = "bash <<'A' <<'B'\nrm -rf /\nA\necho safe\nB"
    assert classify_command(command).final_decision in (taxonomy.ASK, taxonomy.BLOCK)
    oracle = "bash <<'A' <<'B'\nargv /\nA\n:\nB"
    assert _run(oracle, boundary_probe, "zsh") == ["/"]


@pytest.mark.parametrize("word", [
    r'"\$HOME"', r'"\`printf value\`"', r'"\""', r'"\\"', r'"\q"',
    "\"ab\\\ncd\"", r"'\$HOME'", r"'\`printf value\`'", r"'\q'", r"'\\'",
    r'''"\$HOME"'$HOME'"$HOME"''',
])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_outer_quote_removal_bytes(
    boundary_probe: tuple[Path, dict[str, str]], word: str, shell: str,
) -> None:
    from nuh import shell_program_text
    from nuh.bash_parse import _parse_stages
    from nuh.shell_state import State

    project, env = boundary_probe
    with word_quoting.scope():
        stages = _parse_stages("argv " + word)
        assert stages is not None
        produced, unreadable = shell_program_text.expand(stages[0].tokens[1], State(cwd=str(project)))
    assert not unreadable
    completed = subprocess.run([shutil.which(shell), "-c", "printf '%s' " + word], cwd=project, env=env,
                               capture_output=True, text=True, check=True)
    assert completed.stdout == produced


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_heredoc_substituted_dollars_reach_child(
    boundary_probe: tuple[Path, dict[str, str]], shell: str,
) -> None:
    command = "X=outer; P='argv \"$X\"'; bash <<EOF\n$P\nEOF"
    assert _run(command, boundary_probe, shell) == [""]


@pytest.mark.parametrize("source", ["source", "."])
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_non_stdin_body_redirect_argv(
    boundary_probe: tuple[Path, dict[str, str]], source: str, shell: str,
) -> None:
    command = f"{source} /dev/stdin 3<<'A' <<'B'\n<<<true\nA\nargv actual\nB"
    assert _run(command, boundary_probe, shell) == ["actual"]
