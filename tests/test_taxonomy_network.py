"""Unit tests for nuh.taxonomy — network classifiers (FD-022).

Also configurable exec sinks and decode commands (FD-051) and shadow detection (FD-062).
"""

from nuh import taxonomy
from nuh.classify_shadows import find_flag_classifier_shadows, find_table_shadows
from nuh.taxonomy import (
    get_builtin_table,
    get_policy,
    is_decode_stage,
    is_exec_sink,
)
from tests.taxonomy_helpers import _ct


# --- FD-022: Network classifiers ---


class TestFD022Classifiers:
    """FD-022: curl, wget, httpie flag-dependent classification."""

    # --- _classify_curl ---
    def test_curl_bare_outbound(self):
        assert _ct(["curl", "https://example.com"]) == "network_outbound"

    def test_curl_X_POST(self):
        assert _ct(["curl", "-X", "POST", "https://example.com"]) == "network_write"

    def test_curl_XPOST_no_space(self):
        assert _ct(["curl", "-XPOST", "https://example.com"]) == "network_write"

    def test_curl_request_eq_POST(self):
        assert _ct(["curl", "--request=POST", "https://example.com"]) == "network_write"

    def test_curl_request_eq_post_lowercase(self):
        assert _ct(["curl", "--request=post", "https://example.com"]) == "network_write"

    def test_curl_d_flag(self):
        assert _ct(["curl", "-d", "data", "https://example.com"]) == "network_write"

    def test_curl_data_eq(self):
        assert _ct(["curl", "--data=x", "https://example.com"]) == "network_write"

    def test_curl_F_form(self):
        assert _ct(["curl", "-F", "file=@f", "https://example.com"]) == "network_write"

    def test_curl_form_long(self):
        assert _ct(["curl", "--form", "file=@f", "https://example.com"]) == "network_write"

    def test_curl_T_upload(self):
        assert _ct(["curl", "-T", "file.txt", "https://example.com"]) == "network_write"

    def test_curl_upload_file(self):
        assert _ct(["curl", "--upload-file", "file.txt", "https://example.com"]) == "network_write"

    def test_curl_json_flag(self):
        assert _ct(["curl", "--json", '{"key":"val"}', "https://example.com"]) == "network_write"

    def test_curl_combined_sXPOST(self):
        assert _ct(["curl", "-sXPOST", "https://example.com"]) == "network_write"

    def test_curl_contradictory_X_GET_d(self):
        """Data flags take priority over method flags: -X GET -d data → write."""
        assert _ct(["curl", "-X", "GET", "-d", "data", "https://example.com"]) == "network_write"

    def test_curl_sXGET_outbound(self):
        assert _ct(["curl", "-sXGET", "https://example.com"]) == "network_outbound"

    # --- _classify_wget ---
    def test_wget_bare_outbound(self):
        assert _ct(["wget", "https://example.com"]) == "network_outbound"

    def test_wget_post_data(self):
        assert _ct(["wget", "--post-data", "x", "https://example.com"]) == "network_write"

    def test_wget_post_data_eq(self):
        assert _ct(["wget", "--post-data=x", "https://example.com"]) == "network_write"

    def test_wget_post_file(self):
        assert _ct(["wget", "--post-file", "data.txt", "https://example.com"]) == "network_write"

    def test_wget_method_POST(self):
        assert _ct(["wget", "--method", "POST", "https://example.com"]) == "network_write"

    def test_wget_method_eq_post(self):
        assert _ct(["wget", "--method=post", "https://example.com"]) == "network_write"

    def test_wget_method_GET_post_data_contradictory(self):
        """Data flags take priority: --method=GET --post-data=x → write."""
        assert _ct(["wget", "--method=GET", "--post-data=x", "https://example.com"]) == "network_write"

    # --- _classify_httpie ---
    def test_httpie_bare_outbound(self):
        assert _ct(["http", "example.com"]) == "network_outbound"

    def test_httpie_explicit_GET(self):
        assert _ct(["http", "GET", "example.com"]) == "network_outbound"

    def test_httpie_POST(self):
        assert _ct(["http", "POST", "example.com"]) == "network_write"

    def test_httpie_PUT(self):
        assert _ct(["http", "PUT", "example.com"]) == "network_write"

    def test_httpie_DELETE(self):
        assert _ct(["http", "DELETE", "example.com"]) == "network_write"

    def test_httpie_PATCH(self):
        assert _ct(["http", "PATCH", "example.com"]) == "network_write"

    def test_httpie_form_flag(self):
        assert _ct(["http", "--form", "example.com"]) == "network_write"

    def test_httpie_f_flag(self):
        assert _ct(["http", "-f", "example.com"]) == "network_write"

    def test_httpie_data_items(self):
        assert _ct(["http", "example.com", "key=value"]) == "network_write"

    def test_httpie_not_httpie_returns_none(self):
        """Non-httpie commands should not be caught."""
        assert _ct(["curl", "example.com"]) != "network_write"  # curl has its own classifier

    # --- Policy defaults ---
    def test_network_write_policy(self):
        assert get_policy("network_write") == "context"

    def test_network_diagnostic_policy(self):
        assert get_policy("network_diagnostic") == "allow"

    # --- Table entries ---
    def test_ping_diagnostic(self):
        assert _ct(["ping", "8.8.8.8"]) == "network_diagnostic"

    def test_netstat_filesystem_read(self):
        assert _ct(["netstat", "-an"]) == "filesystem_read"

    def test_netcat_outbound(self):
        assert _ct(["netcat", "example.com", "80"]) == "network_outbound"

    def test_ss_filesystem_read(self):
        assert _ct(["ss", "-tulpn"]) == "filesystem_read"

    def test_lsof_filesystem_read(self):
        assert _ct(["lsof", "-i"]) == "filesystem_read"

    def test_openssl_s_client_outbound(self):
        assert _ct(["openssl", "s_client"]) == "network_outbound"


# --- FD-051: Configurable exec sinks ---


class TestExecSinksConfigurable:
    """FD-051: exec_sinks add/remove/profile-none and new defaults."""

    def _setup_merge(self, cfg):
        from nuh import config
        taxonomy.reset_exec_sinks()
        taxonomy._exec_sinks_merged = False
        config._cached_config = cfg

    def teardown_method(self):
        from nuh import config
        config._cached_config = None
        taxonomy.reset_exec_sinks()
        taxonomy._exec_sinks_merged = True

    def test_new_defaults_bun(self):
        """bun is now a default exec sink."""
        assert "bun" in taxonomy._EXEC_SINKS_DEFAULTS
        assert is_exec_sink("bun")

    def test_new_defaults_deno(self):
        assert "deno" in taxonomy._EXEC_SINKS_DEFAULTS

    def test_new_defaults_fish(self):
        assert "fish" in taxonomy._EXEC_SINKS_DEFAULTS

    def test_new_defaults_pwsh(self):
        assert "pwsh" in taxonomy._EXEC_SINKS_DEFAULTS

    def test_add_via_list(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(exec_sinks=["custom_shell"]))
        assert is_exec_sink("custom_shell")
        # Default still present
        assert is_exec_sink("bash")

    def test_add_via_dict(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(exec_sinks={"add": ["custom_shell"]}))
        assert is_exec_sink("custom_shell")

    def test_remove_via_dict(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(exec_sinks={"remove": ["python3"]}))
        assert not is_exec_sink("python3")
        # Others still present
        assert is_exec_sink("bash")

    def test_profile_none_clears(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(profile="none"))
        assert not is_exec_sink("bash")
        assert not is_exec_sink("python3")

    def test_profile_none_with_add(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(profile="none", exec_sinks=["bash"]))
        assert is_exec_sink("bash")
        assert not is_exec_sink("python3")


# --- FD-051: Configurable decode commands ---


class TestDecodeCommandsConfigurable:
    """FD-051: decode_commands add/remove/profile-none and new defaults."""

    def _setup_merge(self, cfg):
        from nuh import config
        taxonomy.reset_decode_commands()
        taxonomy._decode_commands_merged = False
        config._cached_config = cfg

    def teardown_method(self):
        from nuh import config
        config._cached_config = None
        taxonomy.reset_decode_commands()
        taxonomy._decode_commands_merged = True

    def test_new_default_uudecode(self):
        """uudecode is now a default decode command."""
        assert ("uudecode", None) in taxonomy._DECODE_COMMANDS_DEFAULTS
        assert is_decode_stage(["uudecode", "file.uu"])

    def test_add_via_list(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(decode_commands=["openssl base64"]))
        assert is_decode_stage(["openssl", "base64", "-d", "payload"])
        # Default still present
        assert is_decode_stage(["base64", "-d"])

    def test_add_command_only(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(decode_commands=["mydecode"]))
        assert is_decode_stage(["mydecode", "whatever"])

    def test_remove_by_command_name(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(decode_commands={"remove": ["xxd"]}))
        assert not is_decode_stage(["xxd", "-r"])
        # Others still present
        assert is_decode_stage(["base64", "-d"])

    def test_profile_none_clears(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(profile="none"))
        assert not is_decode_stage(["base64", "-d"])
        assert not is_decode_stage(["xxd", "-r"])

    def test_profile_none_with_add(self):
        from nuh.config import NuhConfig
        self._setup_merge(NuhConfig(profile="none", decode_commands=["uudecode"]))
        assert is_decode_stage(["uudecode", "file"])
        assert not is_decode_stage(["base64", "-d"])


# --- Shadow detection (FD-062) ---


class TestFindTableShadows:
    """Detect user classify entries that shadow finer-grained builtin entries."""

    def test_short_prefix_shadows_longer(self):
        user = [(("docker",), "container_destructive")]
        builtin = [
            (("docker", "rm"), "container_destructive"),
            (("docker", "rmi"), "container_destructive"),
            (("docker", "system", "prune"), "container_destructive"),
        ]
        result = find_table_shadows(user, builtin)
        assert ("docker",) in result
        assert len(result[("docker",)]) == 3

    def test_no_overlap_empty(self):
        user = [(("mycmd",), "lang_exec")]
        builtin = [(("docker", "rm"), "container_destructive")]
        assert find_table_shadows(user, builtin) == {}

    def test_exact_match_included(self):
        user = [(("docker", "rm"), "container_destructive")]
        builtin = [(("docker", "rm"), "container_destructive")]
        result = find_table_shadows(user, builtin)
        assert result[("docker", "rm")] == [("docker", "rm")]

    def test_user_longer_than_builtin_no_shadow(self):
        user = [(("docker", "rm", "-f"), "container_destructive")]
        builtin = [(("docker",), "container_destructive")]
        assert find_table_shadows(user, builtin) == {}

    def test_empty_tables(self):
        assert find_table_shadows([], []) == {}
        assert find_table_shadows([(("x",), "t")], []) == {}
        assert find_table_shadows([], [(("x",), "t")]) == {}

    def test_against_real_builtin_table(self):
        """User 'docker' should shadow multiple real builtin docker entries."""
        user = [(("docker",), "container_destructive")]
        builtin = get_builtin_table("full")
        result = find_table_shadows(user, builtin)
        assert ("docker",) in result
        assert len(result[("docker",)]) >= 2


class TestFindFlagClassifierShadows:
    """Detect user classify entries that shadow Phase 2 flag classifiers."""

    def test_curl_detected(self):
        user = [(("curl",), "network_outbound")]
        assert ("curl",) in find_flag_classifier_shadows(user)

    def test_git_detected(self):
        user = [(("git",), "git_safe")]
        assert ("git",) in find_flag_classifier_shadows(user)

    def test_find_detected(self):
        user = [(("find",), "filesystem_read")]
        assert ("find",) in find_flag_classifier_shadows(user)

    def test_non_flag_cmd_not_detected(self):
        user = [(("docker",), "container_destructive")]
        assert find_flag_classifier_shadows(user) == []

    def test_multi_token_not_detected(self):
        user = [(("curl", "-d"), "network_write")]
        assert find_flag_classifier_shadows(user) == []

    def test_all_flag_cmds(self):
        from nuh.classify_shadows import _FLAG_CLASSIFIER_CMDS
        user = [((cmd,), "unknown") for cmd in _FLAG_CLASSIFIER_CMDS]
        result = find_flag_classifier_shadows(user)
        assert len(result) == len(_FLAG_CLASSIFIER_CMDS)
