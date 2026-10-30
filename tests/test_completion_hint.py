"""The CLI's missing-zsh-completion hint (completion_hint, cli.main): stderr only, zsh users at a terminal."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nuh import agents, cli, cli_install, config, manpage
from nuh.cli_completion import script
from nuh.completion_hint import TEXT
from nuh.config import _merge_configs

HINT = TEXT + "\n"


class Tty(io.StringIO):
    """A stderr that says it is a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A zsh user, with an empty HOME and no config file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: home / ".claude/settings.json",
                                                   agents.CODEX: home / ".codex/hooks.json"})
    monkeypatch.setattr(cli_install, "_HOOK_SCRIPT", home / ".claude/hooks/nuh_guard.py")
    return home


def run(words: list[str], capsys: pytest.CaptureFixture, *, tty: bool = True) -> tuple[str, str]:
    """(stdout, stderr) of ``nuh WORDS`` with stderr a terminal (or, ``tty=False``, a pipe).

    stderr is swapped here, not in a fixture: pytest puts capsys's own back when the test starts.
    A SystemExit 0 (help) counts as a run.
    """
    err = Tty() if tty else io.StringIO()
    saved, sys.stderr = sys.stderr, err
    try:
        cli.main(words)
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        sys.stderr = saved
    return capsys.readouterr().out, err.getvalue()


def install_completion(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script("zsh"))


def test_shown_on_every_run(home, capsys):
    for _ in range(2):
        out, err = run(["types"], capsys)
        assert err == HINT
        assert "filesystem_read" in out


@pytest.mark.parametrize("words", [["--help"], ["trust", "--help"], ["system1", "export", "-h"], ["install", "-h"],
                                   ["completion", "--help"], []])
def test_help_shows_it_on_stderr_and_stdout_is_unchanged(home, capsys, monkeypatch, words):
    out, err = run(words, capsys)
    assert err == HINT
    monkeypatch.setenv("SHELL", "/bin/bash")
    plain_out, plain_err = run(words, capsys)
    assert (out, plain_err) == (plain_out, "")
    assert out.startswith("usage: nuh")


def test_not_shown_when_stderr_is_not_a_terminal(home, capsys, monkeypatch):
    assert run(["types"], capsys, tty=False)[1] == ""
    assert run(["--help"], capsys, tty=False)[1] == ""


def test_not_shown_when_the_shell_is_not_zsh(home, capsys, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert run(["types"], capsys)[1] == ""


@pytest.mark.parametrize("where", ["site-functions", "oh-my-zsh", "zsh-cache-dir", "xdg"])
def test_not_shown_once_nuh_wrote_the_completion(home, capsys, monkeypatch, where):
    if where == "site-functions":
        install_completion(home / ".local/share/zsh/site-functions/_nuh")
    elif where == "oh-my-zsh":
        install_completion(home / ".oh-my-zsh/cache/completions/_nuh")
    elif where == "zsh-cache-dir":
        monkeypatch.setenv("ZSH_CACHE_DIR", str(home / "omz-cache"))
        install_completion(home / "omz-cache/completions/_nuh")
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
        install_completion(home / "data/zsh/site-functions/_nuh")
    assert run(["types"], capsys)[1] == ""
    assert run(["--help"], capsys)[1] == ""


def test_a_foreign_nuh_completion_does_not_count(home, capsys):
    foreign = home / ".local/share/zsh/site-functions/_nuh"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("#compdef nuh\n")
    assert run(["types"], capsys)[1] == HINT


@pytest.mark.parametrize("words", [["completion", "zsh"], ["install", "--completion-only"], ["update"],
                                   ["uninstall"], ["--version"]])
def test_not_shown_for_commands_that_handle_the_completion_or_print_for_parsing(home, capsys, words):
    assert HINT not in run(words, capsys)[1]


def test_completion_output_is_only_the_script(home, capsys):
    assert run(["completion", "zsh"], capsys) == (script("zsh"), "")


def test_the_global_config_silences_it_everywhere(home, capsys):
    Path(config._GLOBAL_CONFIG).write_text("completion_hint: false\n")
    assert run(["types"], capsys)[1] == ""
    assert run(["trust", "--help"], capsys)[1] == ""


def test_a_project_setting_warns_and_is_ignored(capsys):
    assert _merge_configs({}, {"completion_hint": False}).completion_hint is True
    assert "completion_hint is global-only; ignored in .nuh.yaml" in capsys.readouterr().err
    assert _merge_configs({"completion_hint": False}, {}).completion_hint is False


def test_a_config_that_does_not_parse_keeps_the_default(home, capsys):
    Path(config._GLOBAL_CONFIG).write_text("completion_hint: [false\n")
    err = run(["--help"], capsys)[1]
    assert "completion_hint uses its default (true)" in err
    assert err.endswith(HINT)


def test_it_writes_no_files(home, capsys, tmp_path):
    before = sorted(tmp_path.rglob("*"))
    assert run(["types"], capsys)[1] == HINT
    assert run(["--help"], capsys)[1] == HINT
    assert sorted(tmp_path.rglob("*")) == before


def test_config_show_lists_the_setting(home, capsys):
    out, _ = run(["config", "show"], capsys)
    assert "completion_hint:" in out


def test_the_man_page_never_carries_it(home):
    assert "completion isn't installed" not in manpage.render()


_PROBE = """
import io, json, sys
class Tty(io.StringIO):
    def isatty(self): return True
sys.stderr = Tty()
from nuh.cli import main
try:
    main(sys.argv[1:])
except SystemExit:
    pass
loaded = [m for m in sys.modules if m.startswith("nuh.") or m == "yaml"]
print("\\n" + json.dumps({"err": sys.stderr.getvalue(), "loaded": loaded}))
"""


def _probe(home: Path, words: list[str]) -> dict:
    """``nuh WORDS`` in a fresh interpreter whose stderr is a terminal: what it printed there and what it loaded."""
    env = {"HOME": str(home), "SHELL": "/bin/zsh", "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.run([sys.executable, "-c", _PROBE, *words], capture_output=True, text=True, check=True,
                          env=env, cwd=home)
    return json.loads(proc.stdout.splitlines()[-1])


def test_the_check_loads_no_completion_renderer(home):
    result = _probe(home, ["config", "path"])
    assert result["err"] == HINT
    # nuh.cli_completion is the parser's (it lists the shells); the renderer and the writer stay unloaded.
    assert not {"nuh.completion_zsh", "nuh.completion", "nuh.completion_install"} & set(result["loaded"])


def test_help_with_the_completion_installed_reads_no_config(home):
    install_completion(home / ".local/share/zsh/site-functions/_nuh")
    result = _probe(home, ["--help"])
    assert result["err"] == ""
    assert not {"nuh.config", "yaml"} & set(result["loaded"])
