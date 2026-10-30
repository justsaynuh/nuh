"""Unit tests for nuh.taxonomy — global overrides of flag classifiers (FD-050).

Also sed, tar, builtins, system info and coreutils (FD-018).
"""

import pytest

from nuh.taxonomy import (
    build_user_table,
    classify_tokens,
    get_builtin_table,
)
from tests.taxonomy_helpers import _ct


# --- FD-050: Global config overrides flag classifiers ---


class TestGlobalOverridesFlagClassifiers:
    """FD-050: Global table checked before flag classifiers; profile:none skips them."""

    # --- V1: Default behavior (no regressions) ---

    def test_default_sed_i_unchanged(self):
        """No global table → sed -i still classified by flag classifier."""
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], builtin_table=builtin_t) == "filesystem_write"

    def test_default_curl_d_unchanged(self):
        """No global table → curl -d still classified by flag classifier."""
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["curl", "-d", "data", "url"], builtin_table=builtin_t) == "network_write"

    def test_default_git_push_force_unchanged(self):
        """No global table → git push --force still classified by flag classifier."""
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "push", "--force"], builtin_table=builtin_t) == "git_history_rewrite"

    # --- V4–V10: Per-classifier override via global table ---

    def test_global_overrides_find(self):
        """Global classify entry overrides _classify_find."""
        global_t = build_user_table({"filesystem_read": ["find"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["find", ".", "-delete"], global_t, builtin_t) == "filesystem_read"

    def test_global_overrides_sed(self):
        """Global classify entry overrides _classify_sed."""
        global_t = build_user_table({"filesystem_read": ["sed"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], global_t, builtin_t) == "filesystem_read"

    def test_global_overrides_tar(self):
        """Global classify entry overrides _classify_tar."""
        global_t = build_user_table({"filesystem_read": ["tar"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["tar", "czf", "a.tar.gz", "."], global_t, builtin_t) == "filesystem_read"

    def test_global_overrides_git(self):
        """Global classify entry overrides _classify_git."""
        global_t = build_user_table({"git_safe": ["git tag"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "tag", "-d", "v1"], global_t, builtin_t) == "git_safe"

    def test_global_overrides_curl(self):
        """Global classify entry overrides _classify_curl."""
        global_t = build_user_table({"network_outbound": ["curl"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["curl", "-d", "data", "url"], global_t, builtin_t) == "network_outbound"

    def test_global_overrides_wget(self):
        """Global classify entry overrides _classify_wget."""
        global_t = build_user_table({"network_outbound": ["wget"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["wget", "--post-data=x", "url"], global_t, builtin_t) == "network_outbound"

    def test_global_overrides_httpie(self):
        """Global classify entry overrides _classify_httpie."""
        global_t = build_user_table({"network_outbound": ["http"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["http", "POST", "example.com"], global_t, builtin_t) == "network_outbound"

    # --- V11–V13: Granularity (longest-prefix-first) ---

    def test_longest_prefix_wins(self):
        """More specific 'sed -i' wins over broader 'sed'."""
        global_t = build_user_table({
            "filesystem_write": ["sed -i"],
            "filesystem_read": ["sed"],
        })
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], global_t, builtin_t) == "filesystem_write"
        assert classify_tokens(["sed", "s/a/b/", "file"], global_t, builtin_t) == "filesystem_read"

    def test_partial_override_leaves_other_commands(self):
        """Overriding git tag doesn't affect git push (flag classifier still runs)."""
        global_t = build_user_table({"git_safe": ["git tag"]})
        builtin_t = get_builtin_table("full")
        # git tag overridden
        assert classify_tokens(["git", "tag", "-d", "v1"], global_t, builtin_t) == "git_safe"
        # git push still uses flag classifier
        assert classify_tokens(["git", "push", "--force"], global_t, builtin_t) == "git_history_rewrite"

    def test_git_push_granularity(self):
        """More specific 'git push --force' wins over broader 'git push'."""
        global_t = build_user_table({
            "git_history_rewrite": ["git push --force"],
            "git_safe": ["git push"],
        })
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "push", "--force"], global_t, builtin_t) == "git_history_rewrite"
        assert classify_tokens(["git", "push", "origin", "main"], global_t, builtin_t) == "git_safe"

    # --- V14–V15: Git global flag stripping ---

    def test_git_C_stripped_for_global_lookup(self):
        """git -C /path push --force matches global 'git push --force'."""
        global_t = build_user_table({"git_safe": ["git push --force"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "-C", "/path", "push", "--force"], global_t, builtin_t) == "git_safe"

    def test_git_no_pager_stripped_for_global_lookup(self):
        """git --no-pager push matches global 'git push'."""
        global_t = build_user_table({"git_safe": ["git push"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "--no-pager", "push"], global_t, builtin_t) == "git_safe"

    def test_git_equals_joined_flag_stripped_for_global_lookup(self):
        """git --git-dir=/path push matches global 'git push'."""
        global_t = build_user_table({"git_safe": ["git push"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "--git-dir=/path", "push"], global_t, builtin_t) == "git_safe"

    def test_git_paginate_flag_stripped_for_global_lookup(self):
        """git -P push --force matches global 'git push --force'."""
        global_t = build_user_table({"git_safe": ["git push --force"]})
        builtin_t = get_builtin_table("full")
        assert classify_tokens(["git", "-P", "push", "--force"], global_t, builtin_t) == "git_safe"

    # --- V16–V21: Profile:none ---

    def test_profile_none_sed_unknown(self):
        """profile:none → sed -i → unknown (flag classifier skipped)."""
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], profile="none") == "unknown"

    def test_profile_none_curl_unknown(self):
        """profile:none → curl -d → unknown."""
        assert classify_tokens(["curl", "-d", "data", "url"], profile="none") == "unknown"

    def test_profile_none_find_unknown(self):
        """profile:none → find -delete → unknown."""
        assert classify_tokens(["find", ".", "-delete"], profile="none") == "unknown"

    def test_profile_none_git_unknown(self):
        """profile:none → git push --force → unknown."""
        assert classify_tokens(["git", "push", "--force"], profile="none") == "unknown"

    def test_profile_none_global_still_works(self):
        """profile:none + global table → global table still classifies."""
        global_t = build_user_table({"filesystem_read": ["sed"]})
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], global_t, profile="none") == "filesystem_read"

    def test_profile_none_ls_unknown(self):
        """profile:none → ls → unknown (builtin table empty, flag classifiers skipped)."""
        table = get_builtin_table("none")
        assert classify_tokens(["ls", "-la"], builtin_table=table, profile="none") == "unknown"

    # --- V25: Supply-chain safety ---

    def test_project_cannot_override_flag_classifiers(self):
        """Project table is Phase 3 — cannot override flag classifiers."""
        builtin_t = get_builtin_table("full")
        project_t = build_user_table({"filesystem_read": ["sed"]})
        # Project says sed → filesystem_read, but flag classifier runs first
        assert classify_tokens(["sed", "-i", "s/a/b/", "file"], None, builtin_t, project_t) == "filesystem_write"


# --- FD-018: sed classifier ---


class TestClassifySed:
    """Flag-dependent sed classification: -i/-I → write, else → read."""

    def test_bare_sed_is_read(self):
        assert _ct(["sed", "s/a/b/", "file"]) == "filesystem_read"

    def test_sed_e_is_read(self):
        assert _ct(["sed", "-e", "s/a/b/", "file"]) == "filesystem_read"

    def test_sed_n_is_read(self):
        assert _ct(["sed", "-n", "s/a/b/p", "file"]) == "filesystem_read"

    def test_sed_i_is_write(self):
        assert _ct(["sed", "-i", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_i_bak_is_write(self):
        assert _ct(["sed", "-i.bak", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_I_bsd_is_write(self):
        assert _ct(["sed", "-I", ".bak", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_ni_combined_is_write(self):
        assert _ct(["sed", "-ni", "s/a/b/p", "file"]) == "filesystem_write"

    def test_sed_nI_bsd_combined_is_write(self):
        assert _ct(["sed", "-nI", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_in_place_long_is_write(self):
        assert _ct(["sed", "--in-place", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_in_place_eq_bak_is_write(self):
        assert _ct(["sed", "--in-place=.bak", "s/a/b/", "file"]) == "filesystem_write"

    def test_sed_i_after_expr_is_write(self):
        assert _ct(["sed", "-e", "s/a/b/", "-i", "file"]) == "filesystem_write"

    def test_sed_ein_combined_is_write(self):
        assert _ct(["sed", "-ein", "s/a/b/", "file"]) == "filesystem_write"


# --- FD-018: tar classifier ---


class TestClassifyTar:
    """Flag-dependent tar classification: mode detection with write precedence."""

    def test_tar_tf_is_read(self):
        assert _ct(["tar", "tf", "a.tar"]) == "filesystem_read"

    def test_tar_list_long_is_read(self):
        assert _ct(["tar", "--list", "-f", "a.tar"]) == "filesystem_read"

    def test_tar_dash_tf_is_read(self):
        assert _ct(["tar", "-tf", "a.tar"]) == "filesystem_read"

    def test_tar_cf_is_write(self):
        assert _ct(["tar", "cf", "a.tar", "dir/"]) == "filesystem_write"

    def test_tar_xf_is_write(self):
        assert _ct(["tar", "xf", "a.tar"]) == "filesystem_write"

    def test_tar_czf_is_write(self):
        assert _ct(["tar", "czf", "a.tar.gz", "dir/"]) == "filesystem_write"

    def test_tar_dash_xzf_is_write(self):
        assert _ct(["tar", "-xzf", "a.tar.gz"]) == "filesystem_write"

    def test_tar_txf_write_wins(self):
        """Both t and x present: write takes precedence."""
        assert _ct(["tar", "-txf", "a.tar"]) == "filesystem_write"

    def test_tar_rf_append_is_write(self):
        assert _ct(["tar", "rf", "a.tar", "newfile"]) == "filesystem_write"

    def test_tar_delete_is_write(self):
        assert _ct(["tar", "--delete", "-f", "a.tar", "member"]) == "filesystem_write"

    def test_tar_extract_long_is_write(self):
        assert _ct(["tar", "--extract", "-f", "a.tar"]) == "filesystem_write"

    def test_tar_bare_conservative_default(self):
        """No mode recognized → conservative default (write)."""
        assert _ct(["tar"]) == "filesystem_write"

    def test_tar_uf_update_is_write(self):
        assert _ct(["tar", "uf", "a.tar", "dir/"]) == "filesystem_write"

    def test_tar_filename_not_misread(self):
        """Filename 'archive.tar' should not be parsed as mode string after a flag."""
        assert _ct(["tar", "-f", "archive.tar"]) == "filesystem_write"


# --- FD-018: bash builtins ---


class TestBashBuiltins:
    """Bash builtins classified as filesystem_read (allow)."""

    @pytest.mark.parametrize("cmd", [
        ["cd", "/tmp"],
        ["pwd"],
        ["type", "ls"],
        ["which", "python"],
        ["command", "-v", "git"],
        ["command", "-V", "git"],
        ["help", "cd"],
        ["alias"],
        ["test", "-f", "file"],
        ["true"],
        ["false"],
        ["dirs"],
        ["pushd", "/tmp"],
        ["popd"],
        ["compgen", "-c", "git"],
        ["[", "-f", "file", "]"],
    ])
    def test_builtin_is_read(self, cmd):
        assert _ct(cmd) == "filesystem_read"

    def test_bare_command_is_unknown(self):
        """Bare 'command' (no args) → unknown (not filesystem_read)."""
        assert _ct(["command"]) == "unknown"

    def test_command_psql_is_unknown(self):
        """'command psql' → unknown at taxonomy level (unwrap is bash.py's job)."""
        assert _ct(["command", "psql"]) == "unknown"


# --- FD-018: system info + compute ---


class TestSystemInfo:
    """System info, process info, and compute-only → filesystem_read."""

    @pytest.mark.parametrize("cmd", [
        ["uname", "-a"],
        ["hostname"],
        ["whoami"],
        ["uptime"],
        ["date"],
        ["cal"],
        ["nproc"],
        ["arch"],
        ["ps", "aux"],
        ["pgrep", "python"],
        ["free", "-h"],
        ["top", "-l", "1"],
        ["htop"],
        ["sleep", "5"],
        ["seq", "1", "10"],
        ["factor", "42"],
        ["expr", "1", "+", "1"],
        ["time", "ls"],
    ])
    def test_system_info_is_read(self, cmd):
        assert _ct(cmd) == "filesystem_read"


# --- FD-018: expanded coreutils ---


class TestCoreutilsExpanded:
    """Text processing, file info, binary inspection, compressed reading, wrappers."""

    @pytest.mark.parametrize("cmd", [
        # Text processing
        ["sort", "file.txt"],
        ["cut", "-d:", "-f1", "/etc/passwd"],
        ["uniq", "-c"],
        ["paste", "a.txt", "b.txt"],
        ["join", "a.txt", "b.txt"],
        ["comm", "a.txt", "b.txt"],
        ["tr", "a-z", "A-Z"],
        ["nl", "file.txt"],
        ["tac", "file.txt"],
        ["rev", "file.txt"],
        ["fold", "-w", "80"],
        ["fmt", "-w", "72", "file.txt"],
        ["column", "-t"],
        ["expand", "file.txt"],
        ["unexpand", "file.txt"],
        # File info & checksums
        ["basename", "/foo/bar.txt"],
        ["dirname", "/foo/bar.txt"],
        ["realpath", "file.txt"],
        ["readlink", "link"],
        ["md5sum", "file"],
        ["sha256sum", "file"],
        ["sha1sum", "file"],
        ["sha512sum", "file"],
        ["cksum", "file"],
        ["b2sum", "file"],
        ["cmp", "a", "b"],
        ["whereis", "python"],
        # Binary inspection
        ["od", "-x", "file"],
        ["hexdump", "-C", "file"],
        ["strings", "binary"],
        # Compressed reading
        ["zcat", "file.gz"],
        ["zgrep", "pattern", "file.gz"],
        ["zless", "file.gz"],
        ["zmore", "file.gz"],
        # Misc
        ["getconf", "PAGE_SIZE"],
        ["locale"],
        ["tty"],
        # nice/nohup/timeout/stdbuf removed — were classification bypasses (FD-105)
    ])
    def test_coreutils_is_read(self, cmd):
        assert _ct(cmd) == "filesystem_read"
