"""``nuh install``/``update``/``uninstall`` write, refresh and remove the zsh completion (completion_install).

Each test gets its own HOME. A fake ``zsh`` on PATH answers the
``ZSH_CACHE_DIR``/``fpath`` query with controlled output; one test asks the
real zsh, when there is one.
"""

import argparse
import json
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from nuh import agents, cli_install, completion_files, completion_install
from nuh.cli_completion import script

REAL_ZSH = shutil.which("zsh")
SCRIPT = script("zsh")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty HOME with Claude Code's settings directory, and an empty PATH (no zsh) until a test adds one."""
    home = tmp_path / "home"
    paths = {agents.CLAUDE: home / ".claude/settings.json", agents.CODEX: home / ".codex/hooks.json"}
    paths[agents.CLAUDE].parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(agents, "AGENT_SETTINGS", paths)
    monkeypatch.setattr(cli_install, "_HOOK_SCRIPT", home / ".claude/hooks/nuh_guard.py")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return home


def fake_zsh(home: Path, cache_dir: str = "", fpath: tuple[str, ...] = ()) -> None:
    """A ``zsh`` on PATH that prints a banner (as a noisy ~/.zshrc would), then answers the query."""
    answer = " ".join(shlex.quote(line) for line in ["banner from ~/.zshrc", completion_install.SENTINEL, cache_dir,
                                                       *fpath])
    zsh = home.parent / "bin" / "zsh"
    zsh.write_text(f"#!/bin/sh\nprintf '%s\\n' {answer}\n")  # a builtin: PATH holds only this script
    zsh.chmod(0o755)


def site_functions(home: Path) -> Path:
    return home / ".local/share/zsh/site-functions"


def omz_completions(home: Path) -> Path:
    directory = home / ".oh-my-zsh/cache/completions"
    directory.mkdir(parents=True)
    return directory


def install(capsys: pytest.CaptureFixture, **flags) -> str:
    cli_install.cmd_install(argparse.Namespace(agent=None, **flags))
    return capsys.readouterr().out


def test_oh_my_zsh_completions_directory_wins(home, capsys):
    completions = omz_completions(home)
    fake_zsh(home, cache_dir=str(home / ".oh-my-zsh/cache"))
    out = install(capsys)
    target = completions / "_nuh"
    assert target.read_text() == SCRIPT
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert "  Completion:  ~/.oh-my-zsh/cache/completions/_nuh (zsh; start a new shell to load it)" in out
    assert "Not on zsh's fpath" not in out
    assert not site_functions(home).exists()
    assert cli_install._HOOK_SCRIPT.exists()


def test_a_custom_zsh_cache_dir_from_zshrc_is_used(home, capsys):
    custom = home / "cache-elsewhere"
    (custom / "completions").mkdir(parents=True)
    omz_completions(home)
    fake_zsh(home, cache_dir=str(custom))
    install(capsys)
    assert (custom / "completions/_nuh").read_text() == SCRIPT
    assert not (home / ".oh-my-zsh/cache/completions/_nuh").exists()


def test_without_oh_my_zsh_site_functions_with_the_fpath_hint(home, capsys):
    fake_zsh(home, fpath=("/usr/share/zsh/functions/Completion",))
    out = install(capsys)
    assert (site_functions(home) / "_nuh").read_text() == SCRIPT
    assert "  Completion:  ~/.local/share/zsh/site-functions/_nuh" in out
    assert "add `fpath=(~/.local/share/zsh/site-functions $fpath)` to ~/.zshrc before compinit" in out


def test_no_fpath_hint_when_site_functions_is_on_fpath(home, capsys):
    fake_zsh(home, fpath=(str(site_functions(home)), "/usr/share/zsh/functions/Completion"))
    out = install(capsys)
    assert (site_functions(home) / "_nuh").exists()
    assert "Not on zsh's fpath" not in out


def test_xdg_data_home_moves_site_functions(home, capsys, monkeypatch):
    data = home / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    fake_zsh(home)
    install(capsys)
    assert (data / "zsh/site-functions/_nuh").read_text() == SCRIPT


@pytest.mark.skipif(REAL_ZSH is None, reason="zsh is not installed")
def test_real_zsh_without_oh_my_zsh(home, capsys, monkeypatch):
    bin_dir = home.parent / "bin"
    (bin_dir / "zsh").symlink_to(REAL_ZSH)
    out = install(capsys)
    target = site_functions(home) / "_nuh"
    assert target.read_text() == SCRIPT
    assert "Not on zsh's fpath" in out  # an empty ~/.zshrc puts nothing of ours on fpath
    subprocess.run([REAL_ZSH, "-n", str(target)], check=True)


def test_no_completion_writes_nothing(home, capsys):
    omz_completions(home)
    fake_zsh(home, cache_dir=str(home / ".oh-my-zsh/cache"))
    out = install(capsys, no_completion=True)
    assert "Completion" not in out
    assert completion_files.written(str(home / ".oh-my-zsh/cache")) == []
    assert not site_functions(home).exists()
    assert cli_install._HOOK_SCRIPT.exists()


def test_completion_only_installs_no_hook(home, capsys):
    fake_zsh(home)
    out = install(capsys, completion_only=True)
    assert (site_functions(home) / "_nuh").read_text() == SCRIPT
    assert not cli_install._HOOK_SCRIPT.exists()
    assert not agents.AGENT_SETTINGS[agents.CLAUDE].exists()
    assert out.startswith("  Completion:  ")


def test_without_zsh_a_note_and_the_hook_still_installs(home, capsys):
    out = install(capsys)
    assert "  Completion:  skipped (zsh is not installed)" in out
    assert not site_functions(home).exists()
    assert cli_install._HOOK_SCRIPT.exists()
    assert json.loads(agents.AGENT_SETTINGS[agents.CLAUDE].read_text())["hooks"]["PreToolUse"]


def test_codex_and_claude_write_it_once(home, capsys):
    agents.AGENT_SETTINGS[agents.CODEX].parent.mkdir(parents=True)
    fake_zsh(home)
    out = install_all(capsys)
    assert out.count("Completion:") == 1


def install_all(capsys: pytest.CaptureFixture) -> str:
    cli_install.cmd_install(argparse.Namespace(agent="all"))
    return capsys.readouterr().out


@pytest.mark.skipif(REAL_ZSH is None, reason="zsh is not installed")
def test_the_written_file_is_valid_zsh_and_is_the_printed_script(home, capsys):
    fake_zsh(home)
    install(capsys)
    target = site_functions(home) / "_nuh"
    subprocess.run([REAL_ZSH, "-n", str(target)], check=True)
    assert target.read_text() == SCRIPT
    assert completion_files.nuh_written(target)


def test_no_temp_file_is_left_behind(home, capsys):
    fake_zsh(home)
    install(capsys)
    cli_install.cmd_update(argparse.Namespace(agent=None))
    assert [p.name for p in site_functions(home).iterdir()] == ["_nuh"]


def test_a_failed_write_leaves_no_temp_file_and_the_old_file(home, monkeypatch):
    target = site_functions(home) / "_nuh"
    target.parent.mkdir(parents=True)
    target.write_text(SCRIPT.replace("_nuh_", "_old_"))
    monkeypatch.setattr(completion_install.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        completion_install._write(target, SCRIPT)
    assert [p.name for p in target.parent.iterdir()] == ["_nuh"]
    assert "_old_" in target.read_text()


def _fail_replace(src, dst):
    raise OSError("disk full")


def test_a_foreign_nuh_completion_is_never_overwritten(home, capsys):
    target = site_functions(home) / "_nuh"
    target.parent.mkdir(parents=True)
    target.write_text("#compdef nuh\n# my own\n")
    fake_zsh(home)
    out = install(capsys)
    assert target.read_text() == "#compdef nuh\n# my own\n"
    assert "left alone: nuh did not write it" in out


def test_update_refreshes_an_existing_file_where_it_is(home, capsys):
    fake_zsh(home)
    install(capsys)
    stale = site_functions(home) / "_nuh"
    stale.write_text(SCRIPT.replace("_nuh_", "_stale_"))
    omz_completions(home)  # oh-my-zsh appeared since: update keeps the file where nuh wrote it
    cli_install.cmd_update(argparse.Namespace(agent=None))
    assert stale.read_text() == SCRIPT
    assert not (home / ".oh-my-zsh/cache/completions/_nuh").exists()


def test_update_writes_one_when_there_is_none(home, capsys):
    fake_zsh(home)
    install(capsys, no_completion=True)
    cli_install.cmd_update(argparse.Namespace(agent=None))
    assert (site_functions(home) / "_nuh").read_text() == SCRIPT


def test_update_without_an_installed_hook_writes_no_completion(home, capsys):
    fake_zsh(home)
    cli_install.cmd_update(argparse.Namespace(agent=None))
    assert not site_functions(home).exists()


def test_uninstall_removes_nuhs_file_and_keeps_a_foreign_one(home, capsys):
    fake_zsh(home, cache_dir=str(home / ".oh-my-zsh/cache"))
    install(capsys)
    foreign = omz_completions(home) / "_nuh"
    foreign.write_text("#compdef nuh\n# hand-written\n")
    cli_install.cmd_uninstall(argparse.Namespace(agent=None))
    out = capsys.readouterr().out
    assert not (site_functions(home) / "_nuh").exists()
    assert foreign.read_text() == "#compdef nuh\n# hand-written\n"
    assert "~/.local/share/zsh/site-functions/_nuh (deleted)" in out
    assert "~/.oh-my-zsh/cache/completions/_nuh (kept: nuh did not write it)" in out


def test_uninstall_finds_the_file_without_zsh(home, capsys):
    fake_zsh(home)
    install(capsys)
    (home.parent / "bin" / "zsh").unlink()
    cli_install.cmd_uninstall(argparse.Namespace(agent=None))
    assert not (site_functions(home) / "_nuh").exists()


def test_uninstalling_one_agent_keeps_the_completion_while_another_has_nuh(home, capsys):
    agents.AGENT_SETTINGS[agents.CODEX].parent.mkdir(parents=True)
    fake_zsh(home)
    install_all(capsys)
    cli_install.cmd_uninstall(argparse.Namespace(agent=agents.CODEX))
    assert "Completion:  kept (nuh is still installed for another agent)" in capsys.readouterr().out
    assert (site_functions(home) / "_nuh").exists()
    cli_install.cmd_uninstall(argparse.Namespace(agent=agents.CLAUDE))
    assert not (site_functions(home) / "_nuh").exists()


def test_zsh_that_cannot_answer_counts_as_unknown(home, capsys):
    zsh = home.parent / "bin" / "zsh"
    zsh.write_text("#!/bin/sh\nexit 3\n")
    zsh.chmod(0o755)
    out = install(capsys)
    assert "zsh did not answer (exit 3)" in out
    assert (site_functions(home) / "_nuh").exists()
    assert "Not on zsh's fpath" in out
