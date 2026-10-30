"""Unit tests for nuh.paths — path resolution, sensitive checks, check_path."""

import os

from nuh import path_names, paths, sensitive_paths


# --- resolve_path ---


class TestResolvePath:
    def test_tilde_expansion(self):
        result = paths.resolve_path("~/file.txt")
        assert result.startswith("/")
        assert "~" not in result

    def test_env_var_expansion(self):
        result = paths.resolve_path("$HOME/file.txt")
        assert result == os.path.realpath(os.path.join(os.path.expanduser("~"), "file.txt"))

    def test_relative_path(self):
        result = paths.resolve_path("./file.txt")
        assert os.path.isabs(result)

    def test_absolute_path(self):
        assert paths.resolve_path("/usr/bin/env") == os.path.realpath("/usr/bin/env")

    def test_empty(self):
        assert paths.resolve_path("") == ""


# --- friendly_path ---


class TestFriendlyPath:
    def test_home_prefix(self):
        home = os.path.expanduser("~")
        assert path_names.friendly_path(home + "/file.txt") == "~/file.txt"

    def test_home_exact(self):
        home = os.path.expanduser("~")
        assert path_names.friendly_path(home) == "~"

    def test_non_home(self):
        assert path_names.friendly_path("/usr/bin/env") == "/usr/bin/env"


# --- is_hook_path ---


class TestIsHookPath:
    def test_exact_hooks_dir(self):
        hooks = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "hooks"))
        assert path_names.is_hook_path(hooks) is True

    def test_child_of_hooks(self):
        hooks = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "hooks"))
        assert path_names.is_hook_path(hooks + "/nuh_guard.py") is True

    def test_not_hooks(self):
        assert path_names.is_hook_path("/tmp/something") is False

    def test_claude_but_not_hooks(self):
        claude = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude"))
        assert path_names.is_hook_path(claude + "/settings.json") is False

    def test_empty(self):
        assert path_names.is_hook_path("") is False


# --- is_sensitive ---


class TestIsSensitive:
    def test_ssh_block(self):
        resolved = paths.resolve_path("~/.ssh/id_rsa")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.ssh"
        assert policy == "block"

    def test_gnupg_block(self):
        resolved = paths.resolve_path("~/.gnupg/key")
        matched, _, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "block"

    def test_git_credentials_block(self):
        resolved = paths.resolve_path("~/.git-credentials")
        matched, _, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "block"

    def test_netrc_block(self):
        resolved = paths.resolve_path("~/.netrc")
        matched, _, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "block"

    def test_aws_ask(self):
        resolved = paths.resolve_path("~/.aws/credentials")
        matched, _, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "ask"

    def test_gcloud_ask(self):
        resolved = paths.resolve_path("~/.config/gcloud/credentials.json")
        matched, _, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "ask"

    def test_azure_ask(self):
        resolved = paths.resolve_path("~/.azure/accessTokens.json")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.azure"
        assert policy == "ask"

    def test_github_cli_hosts_ask(self):
        resolved = paths.resolve_path("~/.config/gh/hosts.yml")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.config/gh"
        assert policy == "ask"

    def test_gitea_cli_config_ask(self):
        for path in ("~/.config/tea/config.yml", "~/.config/tea"):
            matched, pattern, policy = sensitive_paths.is_sensitive(paths.resolve_path(path))
            assert (matched, pattern, policy) == (True, "~/.config/tea", "ask")

    def test_docker_config_ask(self):
        resolved = paths.resolve_path("~/.docker/config.json")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.docker/config.json"
        assert policy == "ask"

    def test_terraform_credentials_ask(self):
        resolved = paths.resolve_path("~/.terraform.d/credentials.tfrc.json")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.terraform.d/credentials.tfrc.json"
        assert policy == "ask"

    def test_terraformrc_ask(self):
        resolved = paths.resolve_path("~/.terraformrc")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert pattern == "~/.terraformrc"
        assert policy == "ask"

    def test_env_basename(self):
        matched, pattern, policy = sensitive_paths.is_sensitive("/project/.env")
        assert matched is True
        assert pattern == ".env"
        assert policy == "ask"

    def test_env_local_now_matched(self):
        """FD-051: .env.local is now a default sensitive basename."""
        matched, pattern, policy = sensitive_paths.is_sensitive("/project/.env.local")
        assert matched is True
        assert pattern == ".env.local"
        assert policy == "ask"

    def test_normal_path(self):
        matched, _, _ = sensitive_paths.is_sensitive("/tmp/normal.txt")
        assert matched is False

    def test_empty(self):
        matched, _, _ = sensitive_paths.is_sensitive("")
        assert matched is False


# --- check_path ---


class TestCheckPath:
    def setup_method(self):
        sensitive_paths._sensitive_paths_merged = True  # isolate from live config

    def test_hook_block_for_write(self):
        result = paths.check_path("Write", "~/.claude/hooks/evil.py")
        assert result is not None
        assert result["decision"] == "block"
        assert "self-modification" in result["reason"]

    def test_hook_block_for_edit(self):
        result = paths.check_path("Edit", "~/.claude/hooks/nuh_guard.py")
        assert result is not None
        assert result["decision"] == "block"

    def test_hook_read_allowed(self):
        """Reading hooks is allowed — only modification is blocked."""
        result = paths.check_path("Read", "~/.claude/hooks/nuh_guard.py")
        assert result is None

    def test_hook_glob_allowed(self):
        """Glob on hooks directory is allowed."""
        result = paths.check_path("Glob", "~/.claude/hooks/")
        assert result is None

    def test_hook_bash_allowed(self):
        """Bash reading hooks is allowed."""
        result = paths.check_path("Bash", "~/.claude/hooks/")
        assert result is None

    def test_sensitive_block(self):
        result = paths.check_path("Read", "~/.ssh/id_rsa")
        assert result is not None
        assert result["decision"] == "block"
        assert "~/.ssh" in result["reason"]

    def test_sensitive_ask(self):
        result = paths.check_path("Read", "~/.aws/credentials")
        assert result is not None
        assert result["decision"] == "ask"

    def test_sensitive_ask_azure(self):
        result = paths.check_path("Read", "~/.azure/accessTokens.json")
        assert result is not None
        assert result["decision"] == "ask"
        assert "~/.azure" in result["reason"]

    def test_github_cli_hosts_ask(self):
        result = paths.check_path("Read", "~/.config/gh/hosts.yml")
        assert result is not None
        assert result["decision"] == "ask"
        assert "~/.config/gh" in result["reason"]

    def test_sensitive_ask_docker_config(self):
        result = paths.check_path("Read", "~/.docker/config.json")
        assert result is not None
        assert result["decision"] == "ask"
        assert "~/.docker/config.json" in result["reason"]

    def test_sensitive_ask_terraform_credentials(self):
        result = paths.check_path("Read", "~/.terraform.d/credentials.tfrc.json")
        assert result is not None
        assert result["decision"] == "ask"
        assert "~/.terraform.d/credentials.tfrc.json" in result["reason"]

    def test_sensitive_ask_terraformrc(self):
        result = paths.check_path("Read", "~/.terraformrc")
        assert result is not None
        assert result["decision"] == "ask"
        assert "~/.terraformrc" in result["reason"]

    def test_sensitive_block_home_env_var(self):
        result = paths.check_path("Read", "$HOME/.ssh/id_rsa")
        assert result is not None
        assert result["decision"] == "block"

    def test_sensitive_block_dynamic_user_substitution(self):
        result = paths.check_path("Read", "/Users/$(whoami)/.ssh/id_rsa")
        assert result is not None
        assert result["decision"] == "block"

    def test_sensitive_ask_home_glob(self):
        result = paths.check_path("Read", "/home/*/.aws/credentials")
        assert result is not None
        assert result["decision"] == "ask"

    def test_clean_path(self):
        result = paths.check_path("Read", "/tmp/safe.txt")
        assert result is None

    def test_empty_path(self):
        assert paths.check_path("Read", "") is None
