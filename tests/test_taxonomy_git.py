"""Unit tests for nuh.taxonomy — the git classifier (FD-017) and git subcommands."""

import pytest

from tests.taxonomy_helpers import _ct


# --- _classify_git (FD-017) ---


class TestClassifyGit:
    """Flag-dependent git classification via _classify_git()."""

    # --- tag ---
    def test_tag_bare_safe(self):
        assert _ct(["git", "tag"]) == "git_safe"

    def test_tag_with_args_write(self):
        assert _ct(["git", "tag", "v1.0"]) == "git_write"

    def test_tag_annotated_write(self):
        assert _ct(["git", "tag", "-a", "v1.0", "-m", "release"]) == "git_write"

    # --- branch ---
    def test_branch_bare_safe(self):
        assert _ct(["git", "branch"]) == "git_safe"

    def test_branch_list_a_safe(self):
        assert _ct(["git", "branch", "-a"]) == "git_safe"

    def test_branch_list_r_safe(self):
        assert _ct(["git", "branch", "-r"]) == "git_safe"

    def test_branch_list_flag_safe(self):
        assert _ct(["git", "branch", "--list"]) == "git_safe"

    def test_branch_v_safe(self):
        assert _ct(["git", "branch", "-v"]) == "git_safe"

    def test_branch_vv_safe(self):
        assert _ct(["git", "branch", "-vv"]) == "git_safe"

    def test_branch_create_write(self):
        assert _ct(["git", "branch", "newfeature"]) == "git_write"

    def test_branch_d_discard(self):
        assert _ct(["git", "branch", "-d", "old"]) == "git_discard"

    def test_branch_delete_discard(self):
        assert _ct(["git", "branch", "--delete", "old"]) == "git_discard"

    def test_branch_D_history_rewrite(self):
        assert _ct(["git", "branch", "-D", "old"]) == "git_history_rewrite"

    def test_branch_delete_force_history_rewrite(self):
        assert _ct(["git", "branch", "--delete", "--force", "old"]) == "git_history_rewrite"

    @pytest.mark.parametrize("tokens", [
        ["git", "branch", "-d", "-f", "old"],
        ["git", "branch", "-f", "-d", "old"],
        ["git", "branch", "-df", "old"],
        ["git", "branch", "-fd", "old"],
        ["git", "branch", "--force", "-d", "old"],
    ])
    def test_branch_force_delete_variants_history_rewrite(self, tokens):
        assert _ct(tokens) == "git_history_rewrite"

    def test_branch_delete_beats_safe_flag(self):
        assert _ct(["git", "branch", "-d", "-v", "old"]) == "git_discard"

    def test_branch_force_delete_beats_safe_flag(self):
        assert _ct(["git", "branch", "-D", "-v", "old"]) == "git_history_rewrite"

    # --- config ---
    def test_config_get_safe(self):
        assert _ct(["git", "config", "--get", "user.name"]) == "git_safe"

    def test_config_list_safe(self):
        assert _ct(["git", "config", "--list"]) == "git_safe"

    def test_config_get_all_safe(self):
        assert _ct(["git", "config", "--get-all", "remote.origin.url"]) == "git_safe"

    def test_config_get_regexp_safe(self):
        assert _ct(["git", "config", "--get-regexp", "user"]) == "git_safe"

    def test_config_read_key_safe(self):
        assert _ct(["git", "config", "user.name"]) == "git_safe"

    def test_config_set_write(self):
        assert _ct(["git", "config", "user.name", "Alice"]) == "git_write"

    def test_config_unset_write(self):
        assert _ct(["git", "config", "--unset", "user.name"]) == "git_write"

    def test_config_unset_all_write(self):
        assert _ct(["git", "config", "--unset-all", "user.name"]) == "git_write"

    def test_config_replace_all_write(self):
        assert _ct(["git", "config", "--replace-all", "k", "v"]) == "git_write"

    # --- reset ---
    def test_reset_hard_discard(self):
        assert _ct(["git", "reset", "--hard"]) == "git_discard"

    def test_reset_hard_head_discard(self):
        assert _ct(["git", "reset", "--hard", "HEAD~1"]) == "git_discard"

    def test_reset_soft_write(self):
        assert _ct(["git", "reset", "--soft", "HEAD~1"]) == "git_write"

    def test_reset_mixed_write(self):
        assert _ct(["git", "reset", "HEAD~1"]) == "git_write"

    def test_reset_bare_write(self):
        assert _ct(["git", "reset"]) == "git_write"

    # --- push ---
    def test_push_bare_remote_write(self):
        assert _ct(["git", "push"]) == "git_remote_write"

    def test_push_origin_main_remote_write(self):
        assert _ct(["git", "push", "origin", "main"]) == "git_remote_write"

    def test_push_force_history(self):
        assert _ct(["git", "push", "--force"]) == "git_history_rewrite"

    def test_push_f_history(self):
        assert _ct(["git", "push", "-f"]) == "git_history_rewrite"

    def test_push_force_with_lease_history(self):
        assert _ct(["git", "push", "--force-with-lease"]) == "git_history_rewrite"

    def test_push_force_with_lease_equals_history(self):
        assert _ct(["git", "push", "--force-with-lease=main"]) == "git_history_rewrite"
        assert _ct(["git", "push", "origin", "--force-with-lease=refs/heads/main"]) == "git_history_rewrite"

    def test_push_force_if_includes_history(self):
        assert _ct(["git", "push", "--force-if-includes"]) == "git_history_rewrite"

    def test_push_plus_refspec_history(self):
        assert _ct(["git", "push", "origin", "+main"]) == "git_history_rewrite"

    def test_push_origin_force_history(self):
        assert _ct(["git", "push", "origin", "--force"]) == "git_history_rewrite"

    def test_push_origin_main_force_history(self):
        assert _ct(["git", "push", "origin", "main", "--force"]) == "git_history_rewrite"

    @pytest.mark.parametrize("tokens", [
        ["git", "push", "origin", "--delete", "old"],
        ["git", "push", "--delete", "origin", "old"],
        ["git", "push", "-d", "origin", "old"],
        ["git", "push", "origin", ":old"],
    ])
    def test_push_delete_variants_history(self, tokens):
        assert _ct(tokens) == "git_history_rewrite"

    # --- add ---
    def test_add_write(self):
        assert _ct(["git", "add", "."]) == "git_write"

    def test_add_dry_run_safe(self):
        assert _ct(["git", "add", "--dry-run", "."]) == "git_safe"

    def test_add_n_safe(self):
        assert _ct(["git", "add", "-n", "."]) == "git_safe"

    # --- rm ---
    def test_rm_discard(self):
        assert _ct(["git", "rm", "file.txt"]) == "git_discard"

    def test_rm_cached_write(self):
        assert _ct(["git", "rm", "--cached", "file.txt"]) == "git_write"

    # --- clean ---
    def test_clean_fd_history(self):
        assert _ct(["git", "clean", "-fd"]) == "git_history_rewrite"

    def test_clean_dry_run_safe(self):
        assert _ct(["git", "clean", "--dry-run"]) == "git_safe"

    def test_clean_n_safe(self):
        assert _ct(["git", "clean", "-n"]) == "git_safe"

    @pytest.mark.parametrize("tokens", [
        ["git", "clean", "-nfd"],
        ["git", "clean", "-fdn"],
        ["git", "clean", "-nd"],
    ])
    def test_clean_combined_dry_run_safe(self, tokens):
        assert _ct(tokens) == "git_safe"

    # --- reflog ---
    def test_reflog_bare_safe(self):
        assert _ct(["git", "reflog"]) == "git_safe"

    def test_reflog_show_safe(self):
        assert _ct(["git", "reflog", "show"]) == "git_safe"

    def test_reflog_delete_discard(self):
        assert _ct(["git", "reflog", "delete"]) == "git_discard"

    def test_reflog_expire_discard(self):
        assert _ct(["git", "reflog", "expire"]) == "git_discard"

    # --- checkout ---
    def test_checkout_branch_write(self):
        assert _ct(["git", "checkout", "main"]) == "git_write"

    def test_checkout_dot_discard(self):
        assert _ct(["git", "checkout", "."]) == "git_discard"

    def test_checkout_dashdash_discard(self):
        assert _ct(["git", "checkout", "--", "file.txt"]) == "git_discard"

    def test_checkout_head_discard(self):
        assert _ct(["git", "checkout", "HEAD", "file.txt"]) == "git_discard"

    def test_checkout_force_discard(self):
        assert _ct(["git", "checkout", "--force"]) == "git_discard"

    def test_checkout_f_discard(self):
        assert _ct(["git", "checkout", "-f"]) == "git_discard"

    def test_checkout_ours_discard(self):
        assert _ct(["git", "checkout", "--ours", "file.txt"]) == "git_discard"

    def test_checkout_theirs_discard(self):
        assert _ct(["git", "checkout", "--theirs", "file.txt"]) == "git_discard"

    def test_checkout_B_discard(self):
        assert _ct(["git", "checkout", "-B", "branch"]) == "git_discard"

    # --- switch ---
    def test_switch_branch_write(self):
        assert _ct(["git", "switch", "main"]) == "git_write"

    def test_switch_force_discard(self):
        assert _ct(["git", "switch", "--force", "main"]) == "git_discard"

    def test_switch_f_discard(self):
        assert _ct(["git", "switch", "-f", "main"]) == "git_discard"

    def test_switch_discard_changes_discard(self):
        assert _ct(["git", "switch", "--discard-changes", "main"]) == "git_discard"

    # --- restore ---
    def test_restore_discard(self):
        assert _ct(["git", "restore", "file.txt"]) == "git_discard"

    def test_restore_staged_write(self):
        assert _ct(["git", "restore", "--staged", "file.txt"]) == "git_write"

    # --- fallthrough ---
    def test_unknown_subcommand_falls_through(self):
        """Subcommands not handled by _classify_git() fall to prefix matching."""
        assert _ct(["git", "commit", "-m", "msg"]) == "git_write"

    def test_git_alone_falls_through(self):
        assert _ct(["git"]) == "unknown"


class TestGitSubcommands:
    """FD-017 Commit 2: Expanded git subcommand coverage."""

    # git_safe — new entries
    @pytest.mark.parametrize("sub", [
        "archive", "blame", "format-patch", "gitk", "grep",
        "annotate", "bisect", "bugreport", "count-objects", "diagnose",
        "difftool", "fast-export", "fsck", "help", "merge-tree",
        "range-diff", "rerere", "show-branch", "verify-commit", "verify-tag",
        "version", "whatchanged",
        "cherry", "diff-files", "diff-index", "diff-tree", "for-each-repo",
        "get-tar-commit-id", "ls-remote", "merge-base", "pack-redundant",
        "show-index", "show-ref", "unpack-file", "var", "verify-pack",
        "check-attr", "check-ignore", "check-mailmap", "check-ref-format",
        "column", "fmt-merge-msg", "interpret-trailers", "mailinfo",
        "mailsplit", "patch-id", "sh-i18n", "sh-setup", "stripspace",
        "remote",
    ])
    def test_git_safe_subcommands(self, sub):
        assert _ct(["git", sub]) == "git_safe"

    # git_write — new entries
    @pytest.mark.parametrize("sub", [
        "am", "bundle", "cherry-pick", "citool", "clone", "gc", "gui",
        "init", "maintenance", "mv", "revert", "scalar", "sparse-checkout",
        "worktree", "fast-import", "mergetool", "notes", "pack-refs",
        "repack", "submodule", "apply", "checkout-index", "commit-graph",
        "commit-tree", "hash-object", "index-pack", "merge-file",
        "merge-index", "mktag", "mktree", "multi-pack-index",
        "pack-objects", "prune-packed", "read-tree", "symbolic-ref",
        "unpack-objects", "update-index", "update-ref", "write-tree",
        "fetch-pack", "send-pack", "update-server-info",
        "credential", "credential-cache", "credential-store", "hook",
        "merge-one-file",
    ])
    def test_git_write_subcommands(self, sub):
        assert _ct(["git", sub]) == "git_write"

    # git_discard — new entry
    def test_git_prune_discard(self):
        assert _ct(["git", "prune"]) == "git_discard"

    # git_history_rewrite — new entries
    @pytest.mark.parametrize("sub", ["filter-branch", "replace"])
    def test_git_history_rewrite_subcommands(self, sub):
        assert _ct(["git", sub]) == "git_history_rewrite"

    # network_outbound — new entries
    @pytest.mark.parametrize("sub", ["daemon", "http-backend"])
    def test_git_network_outbound(self, sub):
        assert _ct(["git", sub]) == "network_outbound"
