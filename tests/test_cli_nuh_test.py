"""nuh test: every tool (FD-069), and shell quotes kept as typed (FD-085)."""

import argparse

import pytest

from nuh.content import reset_content_patterns


pytestmark = pytest.mark.usefixtures("cli_reset")


# --- nuh test full tool support (FD-069) ---


class TestCmdTest:
    """Tests for nuh test with Write/Edit content, Grep patterns, and MCP tools."""

    @pytest.fixture(autouse=True)
    def _reset_content(self):
        reset_content_patterns()
        yield
        reset_content_patterns()

    def test_write_secret_content(self, tmp_path, capsys):
        from nuh.cli_dryrun import cmd_test
        target = str(tmp_path / "project" / "config.py")
        args = argparse.Namespace(
            tool="Write", path=target,
            content="AKIA1234567890ABCDEF", pattern=None, args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out
        assert "AWS access key" in out

    @pytest.mark.parametrize(("key", "desc"), [
        ("sk-ant-api03-" + "Ab3_dE-fG7" * 9 + "abcAA", "Anthropic key"),
        ("sk-proj-" + "a1B2-c3D4_" * 7 + "abcdT3BlbkFJ" + "x9Y8_w7V6-" * 7 + "wxyz", "OpenAI key"),
    ], ids=["anthropic", "openai-proj"])
    def test_write_vendor_key_asks(self, tmp_path, capsys, key, desc):
        from nuh.cli_dryrun import cmd_test
        target = str(tmp_path / "project" / "settings.py")
        args = argparse.Namespace(
            tool="Write", path=target,
            content=f'CLIENT_KEY = "{key}"\n', pattern=None, args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out
        assert desc in out

    @pytest.mark.parametrize(("content", "verdict", "desc"), [
        # Built at runtime: nuh scans Write content for key-shaped text.
        ("-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n", "ASK", "private key"),
        ("DATABASE_URL=postgres://app:" + "Pw7d2e" * 3 + "@db:5432/app\n", "ALLOW", ""),
    ], ids=["openssh-key-asks", "postgres-url-password-is-redact-only"])
    def test_write_secret_ask_or_redact_only(self, tmp_path, capsys, content, verdict, desc):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(tool="Write", path=str(tmp_path / "project" / "settings.conf"),
                                  content=content, pattern=None, args=[])
        cmd_test(args)
        out = capsys.readouterr().out
        assert verdict in out
        assert desc in out

    def test_write_safe_content(self, tmp_path, capsys):
        from nuh.cli_dryrun import cmd_test
        target = str(tmp_path / "project" / "test.txt")
        args = argparse.Namespace(
            tool="Write", path=target,
            content="hello world", pattern=None, args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ALLOW" in out

    def test_edit_secret_content(self, tmp_path, capsys):
        from nuh.cli_dryrun import cmd_test
        target = str(tmp_path / "project" / "app.py")
        args = argparse.Namespace(
            tool="Edit", path=target,
            content="api_secret = 'hunter2hunter2'", pattern=None, args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out
        assert "hardcoded API key" in out

    def test_grep_credential_pattern_outside_project(self, capsys):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool="Grep", path="/tmp",
            content=None, pattern=r"password\s*=", args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out
        assert "credential" in out.lower()

    def test_grep_safe_pattern(self, capsys):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool="Grep", path=".",
            content=None, pattern="TODO", args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ALLOW" in out

    def test_mcp_unknown_tool(self, capsys):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool="mcp__example__tool", path=None,
            content=None, pattern=None, args=[],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out
        assert "unrecognized tool" in out.lower() or "mcp__example__tool" in out

    def test_backward_compat_positional_path(self, capsys):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool="Read", path=None,
            content=None, pattern=None, args=["./README.md"],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ALLOW" in out

    def test_bash_no_args_exits(self):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool=None, path=None,
            content=None, pattern=None, config=None, args=[],
        )
        with pytest.raises(SystemExit):
            cmd_test(args)

    def test_config_classify_override(self, capsys):
        """FD-076: --config classify override reclassifies command."""
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool=None, path=None, content=None, pattern=None,
            config='{"classify": {"git_safe": ["git push --force"]}}',
            args=["git", "push", "--force"],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ALLOW" in out

    def test_config_action_override(self, capsys):
        """FD-076: --config actions override changes policy."""
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool=None, path=None, content=None, pattern=None,
            config='{"actions": {"filesystem_delete": "block"}}',
            args=["rm", "foo.txt"],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "BLOCK" in out

    def test_config_profile_none(self, capsys):
        """FD-076: --config profile:none makes everything unknown → ask."""
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool=None, path=None, content=None, pattern=None,
            config='{"profile": "none"}',
            args=["git", "status"],
        )
        cmd_test(args)
        out = capsys.readouterr().out
        assert "ASK" in out


# --- Shell quote preservation (FD-085) ---


class TestCmdTestQuotePreservation:
    """Ensure nuh test handles both single-string and multi-arg invocations."""

    def _run(self, args_list, capsys):
        from nuh.cli_dryrun import cmd_test
        args = argparse.Namespace(
            tool=None, path=None, content=None, pattern=None,
            config=None, args=args_list,
        )
        cmd_test(args)
        return capsys.readouterr().out

    def test_single_string_simple(self, capsys):
        """nuh test "rm -rf /" — common pattern, must not regress."""
        out = self._run(["rm -rf /"], capsys)
        assert "filesystem_delete" in out
        assert "BLOCK" in out or "ASK" in out

    def test_single_string_pipe(self, capsys):
        """nuh test "cat foo | grep bar" — pipe preserved in single string."""
        out = self._run(["cat foo | grep bar"], capsys)
        # Should decompose into two stages (cat + grep)
        assert "[1]" in out
        assert "[2]" in out

    def test_single_arg_no_spaces(self, capsys):
        """nuh test "ls" — trivial single arg."""
        out = self._run(["ls"], capsys)
        assert "filesystem_read" in out

    def test_multi_arg_embedded_and(self, capsys):
        """nuh test -- ssh user@host "cd /app && python deploy.py" — the reported bug."""
        out = self._run(["ssh", "user@host", "cd /app && python deploy.py"], capsys)
        assert "network_outbound" in out
        # Must be a single stage — the && is inside the quoted remote payload
        assert "[2]" not in out

    def test_multi_arg_embedded_pipe(self, capsys):
        """Multi-arg where one token contains a pipe character."""
        out = self._run(["echo", "hello | world"], capsys)
        # "hello | world" should stay as one token, not split on |
        assert "[2]" not in out

    def test_multi_arg_no_metacharacters(self, capsys):
        """nuh test -- git push --force — no metacharacters, same as join."""
        out = self._run(["git", "push", "--force"], capsys)
        assert "git_history_rewrite" in out

    def test_multi_arg_apostrophe(self, capsys):
        """Multi-arg with apostrophe — must not cause shlex error."""
        out = self._run(["echo", "it's a test"], capsys)
        # Should classify without error
        assert "Decision:" in out or "decision" in out.lower()
