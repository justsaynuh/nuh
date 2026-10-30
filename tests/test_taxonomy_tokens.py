"""Unit tests for nuh.taxonomy — classify_tokens (flag stripping, edge cases, basenames, awk).

Also shell wrappers, exec sinks and decode stages.
"""

import pytest

from nuh import config
from nuh.config import NuhConfig
from nuh.shell_wrapper import is_shell_wrapper
from nuh.taxonomy import (
    build_user_table,
    classify_tokens,
    get_builtin_table,
    is_decode_stage,
    is_exec_sink,
)
from tests.taxonomy_helpers import _FULL, _ct


class TestClassifyTokens:
    """Action type classification via prefix matching (continued from test_taxonomy.py)."""

    # git flag stripping
    def test_git_C_flag_stripped(self):
        assert _ct(["git", "-C", "/dir", "rm", "file"]) == "git_discard"

    def test_git_no_pager_stripped(self):
        assert _ct(["git", "--no-pager", "log"]) == "git_safe"

    def test_git_git_dir_stripped(self):
        assert _ct(["git", "--git-dir", "/x", "push", "--force"]) == "git_history_rewrite"

    def test_git_multiple_flags_stripped(self):
        assert _ct(["git", "-C", "/dir", "--no-pager", "status"]) == "git_safe"

    def test_git_equals_joined_global_value_flags_stripped(self):
        assert _ct(["git", "--git-dir=/x", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--work-tree=/x", "rm", "file"]) == "git_discard"
        assert _ct(["git", "--namespace=ns", "status"]) == "git_safe"

    def test_git_more_boolean_global_flags_stripped(self):
        assert _ct(["git", "-P", "status"]) == "git_safe"
        assert _ct(["git", "-p", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--paginate", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--no-advice", "status"]) == "git_safe"
        assert _ct(["git", "--no-lazy-fetch", "status"]) == "git_safe"
        assert _ct(["git", "--no-lazy-fetch", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--no-optional-locks", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--bare", "status"]) == "git_safe"
        assert _ct(["git", "--bare", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--icase-pathspecs", "status"]) == "git_safe"
        assert _ct(["git", "--icase-pathspecs", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--literal-pathspecs", "status"]) == "git_safe"
        assert _ct(["git", "--glob-pathspecs", "branch", "-D", "old"]) == "git_history_rewrite"
        assert _ct(["git", "--noglob-pathspecs", "rm", "file"]) == "git_discard"

    def test_git_config_env_variants_stripped(self):
        assert _ct(["git", "--config-env", "http.extraHeader=ENV", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "--config-env=http.extraHeader=ENV", "rm", "file"]) == "git_discard"

    def test_git_c_valid_values_stripped(self):
        assert _ct(["git", "-c", "core.editor=true", "status"]) == "git_safe"
        assert _ct(["git", "-c", "core.editor=true", "push", "--force"]) == "git_history_rewrite"

    def test_git_c_valid_values_work_with_global_overrides(self):
        tbl = build_user_table({"testing": ["git status"]})
        assert classify_tokens(
            ["git", "-c", "core.editor=true", "status"],
            global_table=tbl,
            builtin_table=_FULL,
        ) == "testing"

    def test_git_exec_path_equals_joined_flag_stripped(self):
        assert _ct(["git", "--exec-path=/tmp/git-core", "push", "--force"]) == "git_history_rewrite"

    def test_git_new_global_flags_work_with_global_overrides(self):
        tbl = build_user_table({"testing": ["git status"]})
        assert classify_tokens(
            ["git", "--config-env=http.extraHeader=ENV", "status"],
            global_table=tbl,
            builtin_table=_FULL,
        ) == "testing"
        assert classify_tokens(
            ["git", "--exec-path=/tmp/git-core", "status"],
            global_table=tbl,
            builtin_table=_FULL,
        ) == "testing"

    def test_git_config_env_invalid_values_fail_closed(self):
        assert _ct(["git", "--config-env", "push", "--force"]) == "unknown"
        assert _ct(["git", "--config-env", "push=ENV", "status"]) == "unknown"
        assert _ct(["git", "--config-env=http.extraHeader", "push", "--force"]) == "unknown"
        assert _ct(["git", "--config-env=push=ENV", "push", "--force"]) == "unknown"

    def test_git_c_invalid_values_fail_closed(self):
        assert _ct(["git", "-c", "push", "status"]) == "unknown"
        assert _ct(["git", "-c", "push", "push", "--force"]) == "unknown"

    def test_git_exec_path_bare_form_not_stripped(self):
        assert _ct(["git", "--exec-path", "push", "--force"]) == "unknown"

    # git reset --hard → git_discard (DD#3)
    def test_git_reset_hard_is_discard(self):
        assert _ct(["git", "reset", "--hard"]) == "git_discard"

    # Edge cases
    def test_empty_tokens(self):
        assert _ct([]) == "unknown"

    def test_unknown_command(self):
        assert _ct(["foobar", "--flag"]) == "unknown"

    # FD-065: basename normalization
    def test_basename_normalization(self):
        assert _ct(["/usr/bin/rm", "-rf", "/"]) == "filesystem_delete"

    def test_basename_curl(self):
        assert _ct(["/usr/local/bin/curl", "-X", "POST", "url"]) == "network_write"

    def test_basename_no_change(self):
        assert _ct(["rm", "-rf", "/"]) == "filesystem_delete"

    # FD-091: path-style classify entries with basename normalization
    def test_user_classify_path_prefix(self):
        """User entry 'vendor/bin/codecept run' names that file, so it keeps its identity."""
        config._cached_config = NuhConfig(classify_global={"testing": ["vendor/bin/codecept run"]})
        tbl = build_user_table(config._cached_config.classify_global)
        assert classify_tokens(["vendor/bin/codecept", "run"], global_table=tbl) == "testing"

    def test_user_classify_dotslash(self):
        """User entry './my-tool test' names that file, so it keeps its identity."""
        config._cached_config = NuhConfig(classify_global={"testing": ["./my-tool test"]})
        tbl = build_user_table(config._cached_config.classify_global)
        assert classify_tokens(["./my-tool", "test"], global_table=tbl) == "testing"

    def test_path_spelled_program_without_user_entry_is_a_script(self):
        """Program identity: a basename table entry does not vouch for a path-spelled file."""
        tbl = build_user_table({"testing": ["my-tool test"]})
        assert classify_tokens(["./my-tool", "test"], global_table=tbl) == "lang_exec"

    def test_user_classify_absolute_path(self):
        """User entry '/usr/local/bin/foo' matches after basename normalization."""
        tbl = build_user_table({"testing": ["/usr/local/bin/foo"]})
        assert classify_tokens(["/usr/local/bin/foo"], global_table=tbl) == "testing"

    def test_builtin_gradlew_dotslash(self):
        """./gradlew is the project's wrapper script: run as a script (program identity)."""
        assert _ct(["./gradlew", "tasks"]) == "lang_exec"

    def test_user_classify_bare_still_works(self):
        """Bare command entries still match (no regression)."""
        tbl = build_user_table({"testing": ["codecept run"]})
        assert classify_tokens(["codecept", "run"], global_table=tbl) == "testing"

    def test_user_classify_path_via_project_table(self):
        """Path entry works via project_table (Phase 3), not just global_table."""
        tbl = build_user_table({"testing": ["vendor/bin/codecept run"]})
        assert classify_tokens(
            ["vendor/bin/codecept", "run"], project_table=tbl, profile="none",
        ) == "testing"

    def test_project_table_overrides_builtin_prefix(self):
        """Project classify entries beat builtins for the same command prefix."""
        tbl = build_user_table({"container_destructive": ["make docker-clean"]})
        builtin = get_builtin_table("full")
        assert classify_tokens(
            ["make", "docker-clean"],
            builtin_table=builtin,
            project_table=tbl,
            profile="none",
        ) == "container_destructive"

    def test_project_table_overrides_builtin_prefix_with_full_profile(self):
        """Override also works with profile='full' (flag classifiers enabled)."""
        tbl = build_user_table({"container_destructive": ["make docker-clean"]})
        builtin = get_builtin_table("full")
        assert classify_tokens(
            ["make", "docker-clean"],
            builtin_table=builtin,
            project_table=tbl,
            profile="full",
        ) == "container_destructive"

    def test_project_cannot_loosen_builtin(self):
        """A project classify entry cannot weaken a builtin."""
        # docker rm is container_destructive (ask) in builtins;
        # project tries to reclassify as filesystem_read (allow) — should be denied.
        tbl = build_user_table({"filesystem_read": ["docker rm"]})
        builtin = get_builtin_table("full")
        assert classify_tokens(
            ["docker", "rm", "abc"],
            builtin_table=builtin,
            project_table=tbl,
            profile="none",
        ) == "container_destructive"

    def test_user_classify_multi_token_path(self):
        """Path in non-first position: only first token gets basename'd."""
        tbl = build_user_table({"testing": ["php vendor/bin/codecept run"]})
        # 'php' has no path — stays 'php'. 'vendor/bin/codecept' is NOT first token
        # so it stays as-is in both the table and the input.
        assert classify_tokens(
            ["php", "vendor/bin/codecept", "run"], global_table=tbl,
        ) == "testing"

    def test_build_user_table_normalizes_path(self):
        """build_user_table normalizes path in first token."""
        tbl = build_user_table({"testing": ["vendor/bin/codecept run"]})
        assert tbl[0][0] == ("codecept", "run")

    def test_load_classify_table_no_dotslash_duplicates(self):
        """Built-in table has no duplicate prefixes after normalization."""
        table = get_builtin_table("full")
        seen: set[tuple[str, ...]] = set()
        dupes = []
        for prefix, action_type in table:
            if prefix in seen:
                dupes.append((prefix, action_type))
            seen.add(prefix)
        assert dupes == [], f"Duplicate prefixes in builtin table: {dupes}"

    def test_builtin_gradlew_build_standard_dir(self):
        """/usr/bin/gradlew build classifies via builtin gradlew entry (package_install)."""
        assert _ct(["/usr/bin/gradlew", "build"]) == "package_install"

    def test_basename_empty_guard(self):
        """Entry with just '/' doesn't crash — basename returns empty string."""
        tbl = build_user_table({"testing": ["/"]})
        # '/' → basename '' → fallback keeps '/' → no crash
        assert len(tbl) == 1

    # FD-065: awk meta-execution
    def test_awk_safe(self):
        assert _ct(["awk", "{print $1}", "file"]) == "filesystem_read"

    def test_awk_system(self):
        assert _ct(["awk", 'BEGIN{system("whoami")}']) == "lang_exec"

    def test_awk_getline(self):
        assert _ct(["gawk", "{x | getline y}", "file"]) == "lang_exec"

    def test_awk_flag_skip(self):
        assert _ct(["awk", "-F:", "{print $1}", "/etc/passwd"]) == "filesystem_read"

    def test_mawk_nawk(self):
        assert _ct(["mawk", 'BEGIN{system("x")}']) == "lang_exec"


# --- is_shell_wrapper ---


class TestIsShellWrapper:
    """Shell wrapper detection for unwrapping."""

    def test_bash_c(self):
        is_w, inner = is_shell_wrapper(["bash", "-c", "rm -rf /"])
        assert is_w is True
        assert inner == "rm -rf /"

    def test_sh_c(self):
        is_w, inner = is_shell_wrapper(["sh", "-c", "ls"])
        assert is_w is True
        assert inner == "ls"

    def test_dash_c(self):
        is_w, inner = is_shell_wrapper(["dash", "-c", "echo hi"])
        assert is_w is True

    def test_zsh_c(self):
        is_w, inner = is_shell_wrapper(["zsh", "-c", "echo hi"])
        assert is_w is True

    def test_eval(self):
        is_w, inner = is_shell_wrapper(["eval", "echo", "hello"])
        assert is_w is True
        assert inner == "echo hello"

    def test_source_not_wrapper(self):
        is_w, _ = is_shell_wrapper(["source", "script.sh"])
        assert is_w is False

    def test_dot_not_wrapper(self):
        is_w, _ = is_shell_wrapper([".", "script.sh"])
        assert is_w is False

    def test_bash_without_c_not_wrapper(self):
        is_w, _ = is_shell_wrapper(["bash", "script.sh"])
        assert is_w is False

    @pytest.mark.parametrize("tokens", [
        ["bash", "./x.sh", "-c", "ls"],
        ["sh", "-e", "/tmp/evil.sh", "-c", "echo hi"],
        ["bash", "--", "x", "-c", "ls"],
        ["bash", "./x.sh", "<<<", "ls"],
        ["bash", "--weird", "-c", "ls"],
        ["bash", "<<<", "ls", "<<<", "rm -rf /"],
    ])
    def test_c_after_operand_or_unreadable_argv_not_wrapper(self, tokens):
        assert is_shell_wrapper(tokens) == (False, None)

    @pytest.mark.parametrize("tokens", [
        ["bash", "-xec", "ls"], ["bash", "-cl", "ls"], ["bash", "-o", "pipefail", "-c", "ls"],
        ["bash", "+x", "-c", "ls"], ["bash", "-s", "arg", "<<<", "ls"],
    ])
    def test_c_among_options_is_wrapper(self, tokens):
        assert is_shell_wrapper(tokens) == (True, "ls")

    def test_empty(self):
        is_w, _ = is_shell_wrapper([])
        assert is_w is False

    def test_bash_c_missing_arg(self):
        is_w, _ = is_shell_wrapper(["bash", "-c"])
        assert is_w is False

    # FD-066: here-string detection
    def test_bash_here_string(self):
        is_w, inner = is_shell_wrapper(["bash", "<<<", "rm -rf /"])
        assert is_w is True
        assert inner == "rm -rf /"

    def test_sh_here_string(self):
        is_w, inner = is_shell_wrapper(["sh", "<<<", "ls"])
        assert is_w is True
        assert inner == "ls"

    def test_zsh_here_string(self):
        is_w, inner = is_shell_wrapper(["zsh", "<<<", "echo hi"])
        assert is_w is True
        assert inner == "echo hi"

    def test_non_wrapper_here_string(self):
        is_w, inner = is_shell_wrapper(["cat", "<<<", "text"])
        assert is_w is False
        assert inner is None

    def test_bash_here_string_missing_arg(self):
        is_w, _ = is_shell_wrapper(["bash", "<<<"])
        assert is_w is False


# --- is_exec_sink ---


class TestIsExecSink:
    """Exec sink detection for pipe composition."""

    @pytest.mark.parametrize("token", ["bash", "sh", "dash", "zsh", "eval",
                                        "python", "python3", "node", "ruby", "perl", "php"])
    def test_sinks(self, token):
        assert is_exec_sink(token) is True

    @pytest.mark.parametrize("token", ["cat", "grep", "ls", "curl", "rm", ""])
    def test_non_sinks(self, token):
        assert is_exec_sink(token) is False


# --- is_decode_stage ---


class TestIsDecodeStage:
    """Decode command detection for pipe composition."""

    def test_base64_d(self):
        assert is_decode_stage(["base64", "-d"]) is True

    def test_base64_decode(self):
        assert is_decode_stage(["base64", "--decode"]) is True

    def test_xxd_r(self):
        assert is_decode_stage(["xxd", "-r"]) is True

    def test_base64_encode_not_decode(self):
        assert is_decode_stage(["base64"]) is False

    def test_non_decode(self):
        assert is_decode_stage(["cat", "file"]) is False

    def test_empty(self):
        assert is_decode_stage([]) is False
