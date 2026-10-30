"""Unit tests for nuh.taxonomy — gh commands, profiles (FD-032), the three-table lookup (FD-032)."""

import pytest

from nuh.taxonomy import (
    build_user_table,
    classify_tokens,
    get_builtin_table,
)
from tests.taxonomy_helpers import _ct


class TestGhCommands:
    """FD-017 Commit 3: gh (GitHub CLI) classification."""

    # git_safe — read-only queries
    @pytest.mark.parametrize("tokens", [
        ["gh", "issue", "list"],
        ["gh", "issue", "view", "123"],
        ["gh", "issue", "status"],
        ["gh", "pr", "list"],
        ["gh", "pr", "view", "456"],
        ["gh", "pr", "status"],
        ["gh", "pr", "checks", "456"],
        ["gh", "pr", "diff", "456"],
        ["gh", "repo", "list"],
        ["gh", "repo", "view"],
        ["gh", "release", "list"],
        ["gh", "release", "view", "v1.0"],
        ["gh", "run", "list"],
        ["gh", "run", "view", "123"],
        ["gh", "workflow", "list"],
        ["gh", "workflow", "view", "ci.yml"],
        ["gh", "codespace", "list"],
        ["gh", "gist", "list"],
        ["gh", "gist", "view", "abc"],
        ["gh", "project", "list"],
        ["gh", "project", "view", "1"],
        ["gh", "search", "code", "foo"],
        ["gh", "search", "issues", "bar"],
        ["gh", "search", "prs", "baz"],
        ["gh", "search", "repos", "qux"],
        ["gh", "search", "commits", "fix"],
        ["gh", "gpg-key", "list"],
        ["gh", "ssh-key", "list"],
        ["gh", "secret", "list"],
        ["gh", "variable", "list"],
        ["gh", "variable", "get", "VAR"],
        ["gh", "label", "list"],
        ["gh", "ruleset", "list"],
        ["gh", "ruleset", "view", "1"],
        ["gh", "ruleset", "check"],
        ["gh", "extension", "browse"],
        ["gh", "extension", "search", "foo"],
        ["gh", "browse"],
        ["gh", "org", "list"],
        ["gh", "status"],
        ["gh", "cache", "list"],
        ["gh", "auth", "status"],
        ["gh", "auth", "token"],
    ])
    def test_gh_safe(self, tokens):
        assert _ct(tokens) == "git_safe"

    # git_write — local gh operations
    @pytest.mark.parametrize("tokens", [
        ["gh", "pr", "checkout", "456"],
        ["gh", "repo", "clone", "owner/repo"],
        ["gh", "gist", "clone", "abc"],
        ["gh", "codespace", "ssh"],
    ])
    def test_gh_write(self, tokens):
        assert _ct(tokens) == "git_write"

    # git_remote_write — remote state mutations
    @pytest.mark.parametrize("tokens", [
        ["gh", "issue", "create"],
        ["gh", "issue", "close", "123"],
        ["gh", "issue", "comment", "123"],
        ["gh", "issue", "edit", "123"],
        ["gh", "issue", "reopen", "123"],
        ["gh", "issue", "lock", "123"],
        ["gh", "issue", "unlock", "123"],
        ["gh", "issue", "pin", "123"],
        ["gh", "issue", "unpin", "123"],
        ["gh", "issue", "transfer", "123", "owner/repo"],
        ["gh", "issue", "develop", "123"],
        ["gh", "pr", "create"],
        ["gh", "pr", "close", "456"],
        ["gh", "pr", "comment", "456"],
        ["gh", "pr", "edit", "456"],
        ["gh", "pr", "merge", "456"],
        ["gh", "pr", "ready", "456"],
        ["gh", "pr", "reopen", "456"],
        ["gh", "pr", "review", "456"],
        ["gh", "pr", "lock", "456"],
        ["gh", "pr", "unlock", "456"],
        ["gh", "pr", "update-branch"],
        ["gh", "repo", "create", "my-repo"],
        ["gh", "repo", "edit"],
        ["gh", "repo", "fork"],
        ["gh", "repo", "sync"],
        ["gh", "repo", "autolink", "create"],
        ["gh", "repo", "deploy-key", "add"],
        ["gh", "release", "create", "v1.0"],
        ["gh", "release", "edit", "v1.0"],
        ["gh", "release", "upload", "v1.0", "file.tar.gz"],
        ["gh", "run", "rerun", "123"],
        ["gh", "workflow", "run", "ci.yml"],
        ["gh", "codespace", "create"],
        ["gh", "codespace", "stop"],
        ["gh", "codespace", "edit"],
        ["gh", "gist", "create", "file.py"],
        ["gh", "gist", "edit", "abc"],
        ["gh", "gist", "rename", "abc", "newname"],
        ["gh", "project", "create"],
        ["gh", "project", "edit", "1"],
        ["gh", "project", "close", "1"],
        ["gh", "project", "copy", "1"],
        ["gh", "project", "field-create"],
        ["gh", "project", "item-add"],
        ["gh", "project", "item-archive"],
        ["gh", "project", "item-create"],
        ["gh", "project", "item-edit"],
        ["gh", "project", "link"],
        ["gh", "project", "mark-template"],
        ["gh", "project", "unlink"],
        ["gh", "gpg-key", "add", "key.pub"],
        ["gh", "ssh-key", "add", "key.pub"],
        ["gh", "secret", "set", "TOKEN"],
        ["gh", "variable", "set", "VAR"],
        ["gh", "label", "create", "bug"],
        ["gh", "label", "edit", "bug"],
        ["gh", "label", "clone", "owner/repo"],
    ])
    def test_gh_remote_write(self, tokens):
        assert _ct(tokens) == "git_remote_write"

    # git_history_rewrite — destructive / hard to reverse
    @pytest.mark.parametrize("tokens", [
        ["gh", "repo", "delete", "owner/repo"],
        ["gh", "repo", "archive", "owner/repo"],
        ["gh", "repo", "unarchive", "owner/repo"],
        ["gh", "repo", "rename", "new-name"],
        ["gh", "issue", "delete", "123"],
        ["gh", "release", "delete", "v1.0"],
        ["gh", "release", "delete-asset", "v1.0"],
        ["gh", "run", "cancel", "123"],
        ["gh", "run", "delete", "123"],
        ["gh", "workflow", "disable", "ci.yml"],
        ["gh", "workflow", "enable", "ci.yml"],
        ["gh", "codespace", "delete"],
        ["gh", "codespace", "rebuild"],
        ["gh", "gist", "delete", "abc"],
        ["gh", "project", "delete", "1"],
        ["gh", "project", "field-delete", "1"],
        ["gh", "project", "item-delete", "1"],
        ["gh", "gpg-key", "delete", "123"],
        ["gh", "ssh-key", "delete", "123"],
        ["gh", "secret", "delete", "TOKEN"],
        ["gh", "variable", "delete", "VAR"],
        ["gh", "label", "delete", "bug"],
        ["gh", "cache", "delete", "abc"],
    ])
    def test_gh_history_rewrite(self, tokens):
        assert _ct(tokens) == "git_history_rewrite"

    # filesystem_read — local config reads
    @pytest.mark.parametrize("tokens", [
        ["gh", "config", "get", "editor"],
        ["gh", "config", "list"],
        ["gh", "alias", "list"],
        ["gh", "extension", "list"],
        ["gh", "completion"],
        ["gh", "attestation", "trusted-root"],
    ])
    def test_gh_filesystem_read(self, tokens):
        assert _ct(tokens) == "filesystem_read"

    # filesystem_write — local config/file writes
    @pytest.mark.parametrize("tokens", [
        ["gh", "config", "set", "editor", "vim"],
        ["gh", "config", "clear-cache"],
        ["gh", "alias", "set", "co", "pr checkout"],
        ["gh", "alias", "delete", "co"],
        ["gh", "alias", "import", "aliases.yml"],
        ["gh", "extension", "create", "my-ext"],
        ["gh", "extension", "install", "owner/ext"],
        ["gh", "extension", "remove", "owner/ext"],
        ["gh", "extension", "upgrade", "owner/ext"],
        ["gh", "release", "download", "v1.0"],
        ["gh", "run", "download", "123"],
        ["gh", "attestation", "download"],
        ["gh", "attestation", "verify"],
        ["gh", "repo", "set-default"],
        ["gh", "auth", "login"],
        ["gh", "auth", "logout"],
        ["gh", "auth", "refresh"],
        ["gh", "auth", "setup-git"],
        ["gh", "auth", "switch"],
    ])
    def test_gh_filesystem_write(self, tokens):
        assert _ct(tokens) == "filesystem_write"

    # lang_exec — runs arbitrary code
    @pytest.mark.parametrize("tokens", [
        ["gh", "api", "/repos/owner/repo"],
        ["gh", "extension", "exec", "my-ext"],
    ])
    def test_gh_lang_exec(self, tokens):
        assert _ct(tokens) == "lang_exec"


# --- Profiles (FD-032) ---


class TestProfiles:
    """Built-in classify table loading by profile."""

    def test_profile_full_loads_all(self):
        table = get_builtin_table("full")
        action_types = {at for _, at in table}
        # Full profile has tool-specific types absent from minimal
        assert "container_destructive" in action_types
        assert "lang_exec" in action_types
        assert "package_install" in action_types
        assert "db_write" in action_types
        assert "beads_safe" in action_types
        assert "beads_write" in action_types
        assert "beads_destructive" in action_types

    def test_profile_minimal_subset(self):
        table = get_builtin_table("minimal")
        action_types = {at for _, at in table}
        # Minimal has core types
        assert "filesystem_delete" in action_types
        assert "filesystem_read" in action_types
        assert "filesystem_write" in action_types
        assert "git_safe" in action_types
        assert "network_diagnostic" in action_types
        assert "process_signal" in action_types
        # Minimal does NOT have tool-specific types
        assert "container_destructive" not in action_types
        assert "lang_exec" not in action_types
        assert "package_install" not in action_types
        assert "package_run" not in action_types
        assert "db_write" not in action_types
        assert "beads_safe" not in action_types
        assert "beads_write" not in action_types
        assert "beads_destructive" not in action_types

    def test_profile_none_empty(self):
        table = get_builtin_table("none")
        assert table == []

    def test_profile_minimal_smaller_than_full(self):
        full = get_builtin_table("full")
        minimal = get_builtin_table("minimal")
        assert len(minimal) < len(full)

    def test_profile_minimal_docker_unknown(self):
        """Docker commands are not classified in minimal profile."""
        table = get_builtin_table("minimal")
        assert classify_tokens(["docker", "rm", "x"], builtin_table=table) == "unknown"

    def test_profile_minimal_rm_still_classified(self):
        """Core commands are classified in minimal profile."""
        table = get_builtin_table("minimal")
        assert classify_tokens(["rm", "file"], builtin_table=table) == "filesystem_delete"

    def test_profile_minimal_curl_still_classified(self):
        table = get_builtin_table("minimal")
        assert classify_tokens(["curl", "example.com"], builtin_table=table) == "network_outbound"

    def test_profile_none_everything_unknown(self):
        """With none profile, table-only commands are unknown."""
        table = get_builtin_table("none")
        assert classify_tokens(["rm", "-rf", "/"], builtin_table=table) == "unknown"
        assert classify_tokens(["git", "status"], builtin_table=table) == "unknown"

    def test_profile_none_curl_skipped(self):
        """Flag classifiers skipped under profile=none (FD-050)."""
        table = get_builtin_table("none")
        assert classify_tokens(["curl", "x"], builtin_table=table, profile="none") == "unknown"

    def test_profile_none_find_skipped(self):
        """Flag classifiers skipped under profile=none (FD-050)."""
        table = get_builtin_table("none")
        assert classify_tokens(["find", ".", "-name", "*.py"], builtin_table=table, profile="none") == "unknown"
        assert classify_tokens(["find", ".", "-delete"], builtin_table=table, profile="none") == "unknown"

    def test_profile_none_git_skipped(self):
        """Flag classifiers skipped under profile=none (FD-050)."""
        table = get_builtin_table("none")
        assert classify_tokens(["git", "push", "--force"], builtin_table=table, profile="none") == "unknown"
        assert classify_tokens(["git", "reset", "--hard"], builtin_table=table, profile="none") == "unknown"


# --- Three-table lookup (FD-032) ---


class TestThreeTableLookup:
    """Three-table priority: global → built-in → project."""

    def test_global_wins_over_builtin(self):
        """Global user entry overrides built-in classification."""
        global_t = build_user_table({"package_run": ["docker rm"]})
        builtin_t = get_builtin_table("full")
        result = classify_tokens(["docker", "rm", "x"], global_t, builtin_t, None)
        assert result == "package_run"  # global wins over container_destructive

    def test_builtin_wins_over_project(self):
        """Built-in beats project even with longer prefix (supply-chain safety)."""
        builtin_t = get_builtin_table("full")
        project_t = build_user_table({"filesystem_read": ["rm -rf"]})
        result = classify_tokens(["rm", "-rf", "foo"], None, builtin_t, project_t)
        assert result == "filesystem_delete"  # built-in "rm" wins, project "rm -rf" ignored

    def test_project_fills_gaps(self):
        """Project entries work for commands with no built-in match."""
        builtin_t = get_builtin_table("full")
        project_t = build_user_table({"my_deploys": ["my-tool deploy"]})
        result = classify_tokens(["my-tool", "deploy", "prod"], None, builtin_t, project_t)
        assert result == "my_deploys"

    def test_global_fills_gaps(self):
        """Global entries also work for unclassified commands."""
        global_t = build_user_table({"my_safe": ["terraform plan"]})
        builtin_t = get_builtin_table("full")
        result = classify_tokens(["terraform", "plan"], global_t, builtin_t, None)
        assert result == "my_safe"

    def test_no_tables_returns_unknown(self):
        """No tables provided → returns unknown (no implicit fallback)."""
        assert classify_tokens(["rm", "file"]) == "unknown"
        assert classify_tokens(["docker", "rm", "x"]) == "unknown"

    def test_supply_chain_reclassify_blocked(self):
        """Project can't reclassify rm to bypass filesystem_delete policy."""
        builtin_t = get_builtin_table("full")
        # Malicious project tries to reclassify rm variants as safe
        project_t = build_user_table({
            "filesystem_read": ["rm -rf", "rm -f", "rmdir --ignore"],
        })
        # All still match built-in "rm" / "rmdir" first
        assert classify_tokens(["rm", "-rf", "/"], None, builtin_t, project_t) == "filesystem_delete"
        assert classify_tokens(["rm", "-f", "file"], None, builtin_t, project_t) == "filesystem_delete"
        assert classify_tokens(["rmdir", "--ignore", "dir"], None, builtin_t, project_t) == "filesystem_delete"

    def test_project_with_minimal_profile(self):
        """Project fills gaps in minimal profile."""
        builtin_t = get_builtin_table("minimal")
        project_t = build_user_table({"my_docker": ["docker rm"]})
        # docker rm is unknown in minimal, project fills the gap
        assert classify_tokens(["docker", "rm", "x"], None, builtin_t, project_t) == "my_docker"

    def test_global_override_with_minimal_profile(self):
        """Global can reclassify even in minimal profile."""
        global_t = build_user_table({"my_safe_rm": ["rm"]})
        builtin_t = get_builtin_table("minimal")
        # Global "rm" wins over minimal built-in "rm → filesystem_delete"
        assert classify_tokens(["rm", "file"], global_t, builtin_t, None) == "my_safe_rm"
