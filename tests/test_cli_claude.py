"""nuh claude, the hook command, and the hook script written only when it changed (FD-084)."""

import os
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.usefixtures("cli_reset")


# --- FD-084: Hook write optimization ---


class TestWriteHookScriptOptimization:
    """FD-084: skip hook write when content unchanged."""

    def test_skip_write_when_identical(self, tmp_path, monkeypatch):
        """Hook script not rewritten when content matches."""
        import nuh.cli_install as cli_mod
        hook_path = tmp_path / "nuh_guard.py"
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", hook_path)

        cli_mod._write_hook_script()
        mtime1 = hook_path.stat().st_mtime_ns

        cli_mod._write_hook_script()
        mtime2 = hook_path.stat().st_mtime_ns

        assert mtime1 == mtime2

    def test_write_when_content_differs(self, tmp_path, monkeypatch):
        """Hook script rewritten when content changes."""
        import nuh.cli_install as cli_mod
        hook_path = tmp_path / "nuh_guard.py"
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", hook_path)

        cli_mod._write_hook_script()
        # Corrupt the file
        hook_path.chmod(0o644)
        hook_path.write_text("stale")
        hook_path.chmod(0o444)

        cli_mod._write_hook_script()
        assert "stale" not in hook_path.read_text()


class TestCmdClaude:
    """Tests for nuh claude — per-session launcher."""

    def test_rejects_user_settings(self):
        from nuh.cli_install import cmd_claude
        with pytest.raises(SystemExit):
            cmd_claude(["--settings", "foo.json"])

    def test_rejects_settings_equals_form(self):
        from nuh.cli_install import cmd_claude
        with pytest.raises(SystemExit):
            cmd_claude(["--settings=custom.json"])

    def test_claude_not_found(self):
        from nuh.cli_install import cmd_claude
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                cmd_claude([])

    def test_existing_install_execs_directly(self, tmp_path, monkeypatch):
        import json as json_mod
        import nuh.cli_install as cli_mod
        from nuh import agents
        settings_file = tmp_path / "settings.json"
        settings_data = {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 nuh_guard.py"}]}
        ]}}
        settings_file.write_text(json_mod.dumps(settings_data))
        monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: settings_file})

        exec_calls = []
        def mock_execvp(path, args):
            exec_calls.append((path, args))
            raise SystemExit(0)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch.object(os, "execvp", mock_execvp):
            with pytest.raises(SystemExit):
                cli_mod.cmd_claude(["--resume"])

        assert len(exec_calls) == 1
        path, args = exec_calls[0]
        assert path == "/usr/bin/claude"
        assert args == ["claude", "--resume"]
        assert "--settings" not in args

    def test_no_install_builds_settings_json(self, tmp_path, monkeypatch):
        import json as json_mod
        import nuh.cli_install as cli_mod
        from nuh import agents
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: settings_file})
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", tmp_path / "hooks" / "nuh_guard.py")

        exec_calls = []
        def mock_execvp(path, args):
            exec_calls.append((path, args))
            raise SystemExit(0)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch.object(os, "execvp", mock_execvp):
            with pytest.raises(SystemExit):
                cli_mod.cmd_claude(["-p", "fix bug"])

        assert len(exec_calls) == 1
        path, args = exec_calls[0]
        assert args[0] == "claude"
        assert args[1] == "--settings"
        settings = json_mod.loads(args[2])
        assert "PreToolUse" in settings["hooks"]
        assert "-p" in args
        assert "fix bug" in args

    def test_no_settings_file(self, tmp_path, monkeypatch):
        import nuh.cli_install as cli_mod
        from nuh import agents
        settings_file = tmp_path / "nonexistent" / "settings.json"
        monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: settings_file})
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", tmp_path / "hooks" / "nuh_guard.py")

        exec_calls = []
        def mock_execvp(path, args):
            exec_calls.append((path, args))
            raise SystemExit(0)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch.object(os, "execvp", mock_execvp):
            with pytest.raises(SystemExit):
                cli_mod.cmd_claude([])

        assert exec_calls[0][1][1] == "--settings"
        assert (tmp_path / "hooks" / "nuh_guard.py").exists()

    def test_writes_shim_when_missing(self, tmp_path, monkeypatch):
        import nuh.cli_install as cli_mod
        from nuh import agents
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: settings_file})
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", tmp_path / "hooks" / "nuh_guard.py")

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch.object(os, "execvp", side_effect=SystemExit(0)):
            with pytest.raises(SystemExit):
                cli_mod.cmd_claude([])

        assert (tmp_path / "hooks" / "nuh_guard.py").exists()
        assert "nuh" in (tmp_path / "hooks" / "nuh_guard.py").read_text()

    def test_passthrough_flags(self, tmp_path, monkeypatch):
        import nuh.cli_install as cli_mod
        from nuh import agents
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        monkeypatch.setattr(agents, "AGENT_SETTINGS", {agents.CLAUDE: settings_file})
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT", tmp_path / "hooks" / "nuh_guard.py")

        exec_calls = []
        def mock_execvp(path, args):
            exec_calls.append((path, args))
            raise SystemExit(0)

        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch.object(os, "execvp", mock_execvp):
            with pytest.raises(SystemExit):
                cli_mod.cmd_claude(["--resume", "--verbose"])

        args = exec_calls[0][1]
        assert "--resume" in args
        assert "--verbose" in args


class TestHookCommand:
    """_hook_command() must produce quoted POSIX paths for bash compatibility."""

    @pytest.mark.skipif(os.name != "nt", reason="Path(sys.executable) is a PosixPath off Windows, "
                        "so a faked backslash executable can't be converted there")
    def test_windows_backslashes_converted(self, monkeypatch):
        """Backslash paths from sys.executable/pathlib are converted to forward slashes."""
        import shlex
        import nuh.cli_install as cli_mod
        from pathlib import PureWindowsPath
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT",
                            PureWindowsPath(r"C:\Users\test\.claude\hooks\nuh_guard.py"))
        monkeypatch.setattr("sys.executable",
                            r"C:\Users\test\AppData\Local\Python\python.exe")
        cmd = cli_mod._hook_command()
        assert "\\" not in cmd
        assert "C:/Users/test" in cmd
        assert len(shlex.split(cmd)) == 2

    def test_shlex_parses_to_two_tokens(self, monkeypatch):
        """Output is a valid shell command with exactly two tokens."""
        import shlex
        import nuh.cli_install as cli_mod
        from pathlib import PurePosixPath
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT",
                            PurePosixPath("/home/user/.claude/hooks/nuh_guard.py"))
        monkeypatch.setattr("sys.executable", "/usr/bin/python3")
        parts = shlex.split(cli_mod._hook_command())
        assert len(parts) == 2
        assert "python" in parts[0]
        assert parts[1].endswith("nuh_guard.py")

    def test_spaces_in_paths_preserved(self, monkeypatch):
        """Paths with spaces are quoted so bash treats each as one token."""
        import shlex
        import nuh.cli_install as cli_mod
        from pathlib import PurePosixPath
        monkeypatch.setattr(cli_mod, "_HOOK_SCRIPT",
                            PurePosixPath("/home/my user/.claude/hooks/nuh_guard.py"))
        monkeypatch.setattr("sys.executable", "/opt/my python/bin/python3")
        parts = shlex.split(cli_mod._hook_command())
        assert len(parts) == 2
        assert "my python" in parts[0]
        assert "my user" in parts[1]
