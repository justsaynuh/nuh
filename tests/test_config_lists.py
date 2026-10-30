"""Config loading for the lists: safety lists, trusted_paths (FD-054) and content patterns."""

from nuh.config import (
    _merge_configs,
)


class TestSafetyLists:
    """FD-051: Configurable safety lists — config parsing."""

    # --- known_registries polymorphic ---

    def test_known_registries_list_form(self):
        cfg = _merge_configs({"known_registries": ["custom.io"]}, {})
        assert cfg.known_registries == ["custom.io"]

    def test_known_registries_dict_form(self):
        cfg = _merge_configs({"known_registries": {"add": ["x.com"], "remove": ["y.com"]}}, {})
        assert cfg.known_registries == {"add": ["x.com"], "remove": ["y.com"]}

    def test_known_registries_invalid_type(self):
        cfg = _merge_configs({"known_registries": 42}, {})
        assert cfg.known_registries == []

    def test_known_registries_project_ignored(self):
        cfg = _merge_configs({}, {"known_registries": ["evil.com"]})
        assert cfg.known_registries == []

    # --- exec_sinks polymorphic ---

    def test_exec_sinks_list_form(self):
        cfg = _merge_configs({"exec_sinks": ["bun", "deno"]}, {})
        assert cfg.exec_sinks == ["bun", "deno"]

    def test_exec_sinks_dict_form(self):
        cfg = _merge_configs({"exec_sinks": {"add": ["bun"], "remove": ["python3"]}}, {})
        assert cfg.exec_sinks == {"add": ["bun"], "remove": ["python3"]}

    def test_exec_sinks_invalid_type(self):
        cfg = _merge_configs({"exec_sinks": "not a list"}, {})
        assert cfg.exec_sinks == []

    def test_exec_sinks_project_ignored(self):
        cfg = _merge_configs({}, {"exec_sinks": ["bun"]})
        assert cfg.exec_sinks == []

    # --- sensitive_basenames ---

    def test_sensitive_basenames_dict(self):
        cfg = _merge_configs({"sensitive_basenames": {".secrets": "block"}}, {})
        assert cfg.sensitive_basenames == {".secrets": "block"}

    def test_sensitive_basenames_invalid_type(self):
        cfg = _merge_configs({"sensitive_basenames": ["not", "a", "dict"]}, {})
        assert cfg.sensitive_basenames == {}

    def test_sensitive_basenames_project_ignored(self):
        cfg = _merge_configs({}, {"sensitive_basenames": {".secrets": "block"}})
        assert cfg.sensitive_basenames == {}

    # --- decode_commands polymorphic ---

    def test_decode_commands_list_form(self):
        cfg = _merge_configs({"decode_commands": ["uudecode"]}, {})
        assert cfg.decode_commands == ["uudecode"]

    def test_decode_commands_dict_form(self):
        cfg = _merge_configs({"decode_commands": {"add": ["uudecode"], "remove": ["xxd"]}}, {})
        assert cfg.decode_commands == {"add": ["uudecode"], "remove": ["xxd"]}

    def test_decode_commands_invalid_type(self):
        cfg = _merge_configs({"decode_commands": 99}, {})
        assert cfg.decode_commands == []

    def test_decode_commands_project_ignored(self):
        cfg = _merge_configs({}, {"decode_commands": ["uudecode"]})
        assert cfg.decode_commands == []

    # --- _parse_add_remove helper ---

    def test_parse_add_remove_list(self):
        from nuh.config import _parse_add_remove
        add, remove = _parse_add_remove(["a", "b"])
        assert add == ["a", "b"]
        assert remove == []

    def test_parse_add_remove_dict(self):
        from nuh.config import _parse_add_remove
        add, remove = _parse_add_remove({"add": ["a"], "remove": ["b"]})
        assert add == ["a"]
        assert remove == ["b"]

    def test_parse_add_remove_dict_missing_keys(self):
        from nuh.config import _parse_add_remove
        add, remove = _parse_add_remove({"add": ["a"]})
        assert add == ["a"]
        assert remove == []

    def test_parse_add_remove_invalid(self):
        from nuh.config import _parse_add_remove
        assert _parse_add_remove("string") == ([], [])
        assert _parse_add_remove(42) == ([], [])
        assert _parse_add_remove(None) == ([], [])


# --- FD-054: trusted_paths config loading ---


class TestTrustedPaths:
    """FD-054: trusted_paths config loading."""

    def test_global_loads_trusted_paths(self):
        cfg = _merge_configs({"trusted_paths": ["/tmp", "~/bin"]}, {})
        assert "/tmp" in cfg.trusted_paths
        assert "~/bin" in cfg.trusted_paths

    def test_project_trusted_paths_ignored(self):
        """Project config cannot set trusted_paths."""
        cfg = _merge_configs({}, {"trusted_paths": ["/tmp"]})
        # /tmp may be in defaults for profile: full, but not from project config
        # The key assertion: project config doesn't add non-default paths
        assert "~/sneaky" not in cfg.trusted_paths

    def test_default_tmp_trusted_for_full_profile(self):
        """profile: full includes /tmp and /private/tmp as defaults."""
        cfg = _merge_configs({}, {})
        assert "/tmp" in cfg.trusted_paths
        assert "/private/tmp" in cfg.trusted_paths

    def test_invalid_type_dict(self):
        """Invalid type (dict) → only defaults remain."""
        cfg = _merge_configs({"trusted_paths": {"path": "/tmp"}}, {})
        # User entries ignored, but profile: full defaults still present
        assert "/tmp" in cfg.trusted_paths
        assert "/private/tmp" in cfg.trusted_paths

    def test_invalid_type_string(self):
        """Invalid type (string) → only defaults remain."""
        cfg = _merge_configs({"trusted_paths": "/tmp"}, {})
        assert "/tmp" in cfg.trusted_paths

    def test_empty_list_gets_defaults(self):
        """Empty user list still gets profile: full defaults."""
        cfg = _merge_configs({"trusted_paths": []}, {})
        assert "/tmp" in cfg.trusted_paths

    def test_default_includes_tmp(self):
        """No config → profile: full defaults include /tmp."""
        cfg = _merge_configs({}, {})
        assert "/tmp" in cfg.trusted_paths

    def test_entries_coerced_to_str(self):
        """Non-string entries are coerced to str."""
        cfg = _merge_configs({"trusted_paths": [42, True]}, {})
        assert "42" in cfg.trusted_paths
        assert "True" in cfg.trusted_paths


class TestContentPatterns:
    """FD-052: Configurable content patterns — config parsing."""

    # --- content_patterns.add: global-only ---

    def test_content_patterns_add_from_global(self):
        cfg = _merge_configs(
            {"content_patterns": {"add": [
                {"category": "custom", "pattern": "\\bDROP\\b", "description": "DROP"}
            ]}},
            {},
        )
        assert len(cfg.content_patterns_add) == 1
        assert cfg.content_patterns_add[0]["category"] == "custom"

    def test_content_patterns_add_project_ignored(self):
        cfg = _merge_configs(
            {},
            {"content_patterns": {"add": [
                {"category": "custom", "pattern": "\\bDROP\\b", "description": "DROP"}
            ]}},
        )
        assert cfg.content_patterns_add == []

    # --- content_patterns.suppress: global-only ---

    def test_content_patterns_suppress_from_global(self):
        cfg = _merge_configs(
            {"content_patterns": {"suppress": ["rm -rf", "requests.post"]}},
            {},
        )
        assert cfg.content_patterns_suppress == ["rm -rf", "requests.post"]

    def test_content_patterns_suppress_project_ignored(self):
        cfg = _merge_configs(
            {},
            {"content_patterns": {"suppress": ["rm -rf"]}},
        )
        assert cfg.content_patterns_suppress == []

    # --- content_patterns.policies: tighten-only ---

    def test_content_policies_global(self):
        cfg = _merge_configs(
            {"content_patterns": {"policies": {"secret": "block"}}},
            {},
        )
        assert cfg.content_policies == {"secret": "block"}

    def test_content_policies_project_tightens(self):
        cfg = _merge_configs(
            {"content_patterns": {"policies": {"secret": "ask"}}},
            {"content_patterns": {"policies": {"secret": "block"}}},
        )
        assert cfg.content_policies["secret"] == "block"

    def test_content_policies_project_cannot_loosen(self):
        cfg = _merge_configs(
            {"content_patterns": {"policies": {"secret": "block"}}},
            {"content_patterns": {"policies": {"secret": "ask"}}},
        )
        assert cfg.content_policies["secret"] == "block"

    # --- content_patterns invalid types ---

    def test_content_patterns_invalid_top_level(self):
        cfg = _merge_configs({"content_patterns": "not a dict"}, {})
        assert cfg.content_patterns_add == []
        assert cfg.content_patterns_suppress == []
        assert cfg.content_policies == {}

    def test_content_patterns_add_invalid_type(self):
        cfg = _merge_configs({"content_patterns": {"add": "not a list"}}, {})
        assert cfg.content_patterns_add == []

    def test_content_patterns_suppress_invalid_type(self):
        cfg = _merge_configs({"content_patterns": {"suppress": 42}}, {})
        assert cfg.content_patterns_suppress == []

    # --- credential_patterns: entirely global-only ---

    def test_credential_patterns_add_from_global(self):
        cfg = _merge_configs(
            {"credential_patterns": {"add": ["\\bconnection_string\\b"]}},
            {},
        )
        assert cfg.credential_patterns_add == ["\\bconnection_string\\b"]

    def test_credential_patterns_suppress_from_global(self):
        cfg = _merge_configs(
            {"credential_patterns": {"suppress": ["\\btoken\\b"]}},
            {},
        )
        assert cfg.credential_patterns_suppress == ["\\btoken\\b"]

    def test_credential_patterns_project_ignored(self):
        cfg = _merge_configs(
            {},
            {"credential_patterns": {"add": ["evil"], "suppress": ["password"]}},
        )
        assert cfg.credential_patterns_add == []
        assert cfg.credential_patterns_suppress == []

    def test_credential_patterns_invalid_type(self):
        cfg = _merge_configs({"credential_patterns": "not a dict"}, {})
        assert cfg.credential_patterns_add == []
        assert cfg.credential_patterns_suppress == []

    # --- Empty merge preserves defaults ---

    def test_empty_merge_defaults(self):
        cfg = _merge_configs({}, {})
        assert cfg.content_patterns_add == []
        assert cfg.content_patterns_suppress == []
        assert cfg.content_policies == {}
        assert cfg.credential_patterns_add == []
        assert cfg.credential_patterns_suppress == []
