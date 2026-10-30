"""Static floor rule group 3: credential paths, filename patterns, .git exec files, the ~/.ssh split."""

import os

import pytest

from nuh import config, hook, paths, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig

SSH = "~/" + ".ssh"


def _floor(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    return project_root


def _remerge(**cfg) -> None:
    config._cached_config = NuhConfig(**cfg)
    sensitive_paths.reset_sensitive_paths()


class TestCredentialPaths:
    @pytest.mark.parametrize("command", [
        "cat ~/.pgpass",
        "cat ~/.cargo/credentials.toml",
        "cat ~/.cargo/credentials",
        "cat ~/.config/gh/hosts.yml",
        "cat ~/.config/tea/config.yml",
        "ls ~/.config/tea",
        "cat ~/.docker/config.json",
        "cat ~/.kube/config",
        "cat ~/.aws/config",
        "cat ~/.npmrc",
        "cat ~/.pypirc",
        "cat .env",
        "cat .env.development",
        "cat .env.staging",
        "cat server.pem",
        "cat certs/tls.key",
        "cat backup.p12",
        "cat store.pfx",
        "cat id_rsa",
        "cat deploy/id_ed25519_ci",
        "cat terraform.tfstate",
    ])
    def test_floored_ask(self, in_project, command):
        assert _floor(command) == (taxonomy.ASK, ["credential_paths"])

    @pytest.mark.parametrize("command", [
        "cat id_rsa.pub",
        "cat .env.example",
        "cat .env.sample",
        "cat .env.template",
        "cat .env.dist",
        "cat .env.local.example",
        "cat README.md",
    ])
    def test_not_sensitive(self, in_project, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_shell_init_file_asks_under_sensitive_paths(self):
        assert _floor("cat ~/.zshrc") == (taxonomy.ASK, ["sensitive_paths"])

    def test_agent_settings_read_asks_under_sensitive_paths(self):
        assert _floor("cat ~/.claude/settings.json") == (taxonomy.ASK, ["sensitive_paths"])

    def test_read_tool(self, in_project):
        result = hook.handle_read({"file_path": ".env.production"})
        assert result["decision"] == taxonomy.ASK
        assert result["_floor"] == "credential_paths"

    def test_user_entry_does_not_unfloor_a_key(self, in_project, tmp_path):
        docs = tmp_path / "docs"
        _remerge(sensitive_paths={str(docs): "ask"})
        assert _floor(f"cat {docs}/server.pem") == (taxonomy.ASK, ["credential_paths"])
        assert _floor(f"cat {docs}/notes.txt") == (taxonomy.ASK, ["sensitive_paths"])
        assert _floor(f"cat {docs}/notes.txt {docs}/server.pem") == (taxonomy.ASK, ["credential_paths"])

    def test_sensitive_paths_default_block_raises_builtins(self, in_project):
        """0.x c6a39e58: the block default applies with no sensitive_paths entries."""
        _remerge(sensitive_paths_default="block")
        assert classify_command("cat ~/.aws/config").final_decision == taxonomy.BLOCK
        assert classify_command("cat .env").final_decision == taxonomy.BLOCK


class TestSymlinks:
    def test_symlink_named_env(self, tmp_path):
        target = tmp_path / "plain.txt"
        target.write_text("x")
        (tmp_path / ".env").symlink_to(target)
        verdict = sensitive_paths.check_path_basic_raw(str(tmp_path / ".env"))
        assert verdict == (taxonomy.ASK, "targets sensitive path: .env")

    def test_innocuous_name_for_env(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("x")
        (tmp_path / "notes.txt").symlink_to(target)
        assert sensitive_paths.check_path_basic_raw(str(tmp_path / "notes.txt"))[0] == taxonomy.ASK


class TestGitExecFiles:
    @pytest.mark.parametrize("path", [
        ".git/hooks/pre-commit",
        ".git/config",
        ".git/modules/sub/hooks/post-checkout",
        ".git/worktrees/wt/config.worktree",
    ])
    def test_write_tool(self, in_project, path):
        decision = paths.check_path("Write", os.path.join(in_project, path))
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "git_exec"

    @pytest.mark.parametrize("command", [
        "echo x >> .git/config",
        "cp hook.sh .git/hooks/pre-push",
        "chmod +x .git/hooks/pre-push",
        "sed -i s/a/b/ .git/config",
    ])
    def test_bash_writes(self, in_project, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "git_exec" in rules

    def test_reads_are_fine(self, in_project):
        assert classify_command("cat .git/config").final_decision == taxonomy.ALLOW
        assert paths.check_path("Read", os.path.join(in_project, ".git/hooks/pre-commit")) is None

    def test_branch_named_hooks_is_not_a_hook(self, in_project):
        assert sensitive_paths.check_protected_write(os.path.join(in_project, ".git/refs/heads/hooks")) is None


class TestSshSplit:
    @pytest.mark.parametrize("command", [
        f"cat {SSH}/known_hosts",
        f"cat {SSH}/id_ed25519.pub",
        f"cat {SSH}/config",
    ])
    def test_host_files_readable(self, command):
        assert classify_command(command).final_decision != taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        f"ls {SSH}",
        f"cat {SSH}/id_ed25519",
        f"cat {SSH}/my_deploy_key",
        f"echo x >> {SSH}/authorized_keys",
        f"cp evil {SSH}/config",
        "cat /home/*/.ssh/id_rsa",
    ])
    def test_blocks(self, command):
        assert classify_command(command).final_decision == taxonomy.BLOCK

    def test_write_tool_config_blocks(self):
        assert paths.check_path("Write", f"{SSH}/config")["decision"] == taxonomy.BLOCK

    def test_user_entry_restores_whole_directory(self):
        _remerge(sensitive_paths={SSH: "block"})
        assert classify_command(f"cat {SSH}/known_hosts").final_decision == taxonomy.BLOCK

    def test_user_allow_cannot_turn_it_off(self, capsys):
        """The ~/.ssh rule is a credential floor: an allow warns and the narrow rule stays (C2)."""
        _remerge(sensitive_paths={SSH: "allow"})
        assert paths.check_path("Read", f"{SSH}/my_deploy_key")["decision"] == taxonomy.BLOCK
        assert paths.check_path("Read", f"{SSH}/id_ed25519")["decision"] == taxonomy.BLOCK
        assert paths.check_path("Read", f"{SSH}/known_hosts") is None
        assert "allow cannot remove a built-in entry (floor credential_paths)" in capsys.readouterr().err

    @pytest.mark.parametrize("entry", [None, "ask", "allow", "block"])
    @pytest.mark.parametrize("command", [
        f"echo x >> {SSH}/config",
        f"echo x > {SSH}/authorized_keys",
        f"cp x {SSH}/rc",
        f"tee -a {SSH}/environment",
        f"sed -i s/a/b/ {SSH}/config",
        f"mv x {SSH}/config.d/extra",
    ])
    def test_exec_file_writes_block_whatever_the_user_entry(self, entry, command):
        _remerge(sensitive_paths={SSH: entry} if entry else {})
        decision, rules = _floor(command)
        assert decision == taxonomy.BLOCK
        assert sensitive_paths.CREDENTIAL_RULE in rules

    @pytest.mark.parametrize("entry", ["ask", "allow"])
    def test_write_tool_exec_file_blocks_whatever_the_user_entry(self, entry):
        _remerge(sensitive_paths={SSH: entry})
        assert paths.check_path("Edit", f"{SSH}/authorized_keys")["decision"] == taxonomy.BLOCK

    def test_no_entry_reads_stay_open(self):
        _remerge()
        for name in ("config", "known_hosts", "id_ed25519.pub"):
            assert classify_command(f"cat {SSH}/{name}").final_decision == taxonomy.ALLOW
        assert classify_command(f"cat {SSH}/id_ed25519").final_decision == taxonomy.BLOCK
