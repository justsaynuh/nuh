"""Tests for config loading and merging (FD-006)."""

from unittest.mock import patch

import pytest

from nuh.config import (
    NuhConfig,
    get_config,
    reset_config,
    is_path_allowed,
    _merge_configs,
    _load_yaml_file,
)
from nuh import paths


class TestDefaults:
    """Config defaults when no YAML files exist."""

    def test_default_config(self, tmp_path):
        """Without any config files, get_config returns sensible defaults."""
        paths.set_project_root(str(tmp_path))
        reset_config()
        with patch("nuh.config._GLOBAL_CONFIG", str(tmp_path / "nonexistent.yaml")):
            cfg = get_config()
        assert isinstance(cfg, NuhConfig)
        assert cfg.profile == "full"
        assert cfg.classify_global == {}
        assert cfg.classify_project == {}
        assert cfg.actions == {}
        assert cfg.sensitive_paths_default == "ask"
        assert cfg.sensitive_paths == {}
        assert cfg.allow_paths == {}
        assert cfg.known_registries == []

    def test_config_cached(self, tmp_path):
        """get_config returns same instance on second call."""
        paths.set_project_root(str(tmp_path))
        reset_config()
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_reset_clears_cache(self, tmp_path):
        paths.set_project_root(str(tmp_path))
        reset_config()
        cfg1 = get_config()
        reset_config()
        cfg2 = get_config()
        assert cfg1 is not cfg2


class TestLoadYaml:
    def test_missing_file(self):
        assert _load_yaml_file("/nonexistent/path.yaml") == {}

    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")
        assert _load_yaml_file(str(f)) == {"key": "value"}

    def test_non_dict_yaml(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("- item1\n- item2\n")
        assert _load_yaml_file(str(f)) == {}


class TestMergeConfigs:
    """Test config merging rules."""

    def test_empty_merge(self):
        cfg = _merge_configs({}, {})
        assert cfg.classify_global == {}
        assert cfg.classify_project == {}
        assert cfg.actions == {}

    def test_classify_kept_separate(self):
        """Global and project classify are stored separately, not unioned."""
        global_cfg = {"classify": {"package_run": ["just build"]}}
        project_cfg = {"classify": {"package_run": ["task dev"]}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert cfg.classify_global == {"package_run": ["just build"]}
        assert cfg.classify_project == {"package_run": ["task dev"]}

    def test_actions_tighten_only(self):
        """Project can tighten actions but not loosen."""
        global_cfg = {"actions": {"filesystem_read": "allow", "network_outbound": "ask"}}
        project_cfg = {"actions": {"filesystem_read": "ask", "network_outbound": "allow"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        # filesystem_read: allow → ask (tightened) ✓
        assert cfg.actions["filesystem_read"] == "ask"
        # network_outbound: ask → allow (loosened) — stays at ask
        assert cfg.actions["network_outbound"] == "ask"

    def test_actions_project_adds_new(self):
        """Project can add new action types."""
        global_cfg = {"actions": {}}
        project_cfg = {"actions": {"custom_type": "block"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert cfg.actions["custom_type"] == "block"

    def test_sensitive_paths_tighten_only(self):
        """Sensitive paths tighten only per path."""
        global_cfg = {"sensitive_paths": {"~/.custom": "ask"}}
        project_cfg = {"sensitive_paths": {"~/.custom": "block"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert cfg.sensitive_paths["~/.custom"] == "block"

    def test_sensitive_paths_no_loosen(self):
        global_cfg = {"sensitive_paths": {"~/.custom": "block"}}
        project_cfg = {"sensitive_paths": {"~/.custom": "ask"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert cfg.sensitive_paths["~/.custom"] == "block"

    # --- trust_project_config is no setting: a project only tightens, whatever the global config says ---

    @pytest.mark.parametrize("global_extra,project_cfg,read", [
        ({"actions": {"network_outbound": "ask"}}, {"actions": {"network_outbound": "allow"}},
         lambda cfg: cfg.actions["network_outbound"] == "ask"),
        ({"sensitive_paths": {"~/.custom": "block"}}, {"sensitive_paths": {"~/.custom": "ask"}},
         lambda cfg: cfg.sensitive_paths["~/.custom"] == "block"),
        ({"sensitive_paths_default": "block"}, {"sensitive_paths_default": "ask"},
         lambda cfg: cfg.sensitive_paths_default == "block"),
        ({"content_patterns": {"policies": {"secret": "block"}}},
         {"content_patterns": {"policies": {"secret": "ask"}}},
         lambda cfg: cfg.content_policies["secret"] == "block"),
    ])
    def test_trust_project_config_does_not_loosen(self, capsys, global_extra, project_cfg, read):
        cfg = _merge_configs({"trust_project_config": True, **global_extra}, project_cfg)
        assert read(cfg)
        assert capsys.readouterr().err == "nuh: config: unknown key trust_project_config; ignored\n"
        assert cfg.unknown == ["trust_project_config"]

    def test_a_project_s_llm_section_is_ignored_with_a_warning(self, capsys):
        cfg = _merge_configs({}, {"llm": {"backend": "decide"}})  # a project's llm section is never read
        assert capsys.readouterr().err == (
            "nuh: config: llm is global-only; ignored in .nuh.yaml (a project may only tighten)\n")
        assert cfg.unknown == []

    def test_project_cannot_loosen(self):
        global_cfg = {"actions": {"network_outbound": "ask"}}
        project_cfg = {"actions": {"network_outbound": "allow"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert cfg.actions["network_outbound"] == "ask"

    def test_sensitive_paths_union(self):
        global_cfg = {"sensitive_paths": {"~/.a": "ask"}}
        project_cfg = {"sensitive_paths": {"~/.b": "block"}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert "~/.a" in cfg.sensitive_paths
        assert "~/.b" in cfg.sensitive_paths

    def test_allow_paths_global_only(self):
        """allow_paths from project config are silently ignored."""
        global_cfg = {"allow_paths": {"~/.aws": ["/home/user/project"]}}
        project_cfg = {"allow_paths": {"~/.ssh": ["/home/user/project"]}}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert "~/.aws" in cfg.allow_paths
        assert "~/.ssh" not in cfg.allow_paths

    def test_known_registries_global_only_list(self):
        """known_registries: global only, list form."""
        global_cfg = {"known_registries": ["custom.registry.io"]}
        project_cfg = {"known_registries": ["another.registry.io"]}
        cfg = _merge_configs(global_cfg, project_cfg)
        # Project config is silently ignored — only global values
        assert cfg.known_registries == ["custom.registry.io"]

    def test_known_registries_global_only_dict(self):
        """known_registries: global only, dict form."""
        global_cfg = {"known_registries": {"add": ["custom.io"], "remove": ["github.com"]}}
        cfg = _merge_configs(global_cfg, {})
        assert cfg.known_registries == {"add": ["custom.io"], "remove": ["github.com"]}

    def test_invalid_types_handled(self):
        """Non-dict/non-list values don't crash merge."""
        global_cfg = {"classify": "not a dict", "actions": 42, "known_registries": "string"}
        project_cfg = {"sensitive_paths": None}
        cfg = _merge_configs(global_cfg, project_cfg)
        assert isinstance(cfg, NuhConfig)


class TestIsPathAllowed:
    def test_allowed(self, tmp_path):
        """Path in allow_paths for current project root is exempted."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        paths.set_project_root(str(project_dir))
        reset_config()

        # Manually set config with allow_paths
        from nuh import config
        config._cached_config = NuhConfig(
            allow_paths={"~/.aws": [str(project_dir)]},
        )

        assert is_path_allowed("~/.aws", str(project_dir)) is True
        assert is_path_allowed("~/.aws/credentials", str(project_dir)) is True

    def test_not_allowed_wrong_root(self, tmp_path):
        """Path in allow_paths but for different project root."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        paths.set_project_root(str(project_dir))
        reset_config()

        from nuh import config
        config._cached_config = NuhConfig(
            allow_paths={"~/.aws": [str(other_dir)]},
        )

        assert is_path_allowed("~/.aws", str(project_dir)) is False

    def test_no_project_root(self):
        reset_config()
        assert is_path_allowed("~/.aws", None) is False

    def test_empty_allow_paths(self, tmp_path):
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        paths.set_project_root(str(project_dir))
        reset_config()

        from nuh import config
        config._cached_config = NuhConfig()
        assert is_path_allowed("~/.aws", str(project_dir)) is False


class TestSensitivePathsDefault:
    def test_global_default(self):
        cfg = _merge_configs({"sensitive_paths_default": "block"}, {})
        assert cfg.sensitive_paths_default == "block"

    def test_project_tightens(self):
        cfg = _merge_configs(
            {"sensitive_paths_default": "ask"},
            {"sensitive_paths_default": "block"},
        )
        assert cfg.sensitive_paths_default == "block"

    def test_project_cannot_loosen(self):
        cfg = _merge_configs(
            {"sensitive_paths_default": "block"},
            {"sensitive_paths_default": "ask"},
        )
        assert cfg.sensitive_paths_default == "block"


class TestProfile:
    """Profile loading and validation."""

    def test_profile_from_global(self):
        cfg = _merge_configs({"profile": "minimal"}, {})
        assert cfg.profile == "minimal"

    def test_profile_none(self):
        cfg = _merge_configs({"profile": "none"}, {})
        assert cfg.profile == "none"

    def test_profile_default_full(self):
        cfg = _merge_configs({}, {})
        assert cfg.profile == "full"

    def test_profile_ignored_in_project(self):
        """Project config cannot set the profile."""
        cfg = _merge_configs({}, {"profile": "none"})
        assert cfg.profile == "full"

    def test_profile_invalid_fallback(self):
        """Invalid profile falls back to 'full'."""
        cfg = _merge_configs({"profile": "turbo"}, {})
        assert cfg.profile == "full"

    def test_profile_global_overrides_project(self):
        """Global profile wins; project profile ignored."""
        cfg = _merge_configs({"profile": "minimal"}, {"profile": "none"})
        assert cfg.profile == "minimal"


class TestLlmCanBlock:
    """llm.can_block config loading; llm.max_decision, which it replaced, is a removed key."""

    def test_default_false(self, capsys):
        assert _merge_configs({}, {}).llm_can_block is False
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("value", [True, False])
    def test_from_global(self, value, capsys):
        assert _merge_configs({"llm": {"can_block": value}}, {}).llm_can_block is value
        assert capsys.readouterr().err == ""

    def test_project_cannot_set_it(self):
        assert _merge_configs({}, {"llm": {"can_block": True}}).llm_can_block is False

    def test_not_a_bool_warns_and_reads_false(self, capsys):
        assert _merge_configs({"llm": {"can_block": "yes"}}, {}).llm_can_block is False
        assert "llm.can_block must be true or false" in capsys.readouterr().err

    def test_max_decision_is_no_setting(self, capsys):
        cfg = _merge_configs({"llm": {"max_decision": "block"}}, {})
        assert (cfg.llm_can_block, cfg.unknown) == (False, ["llm.max_decision"])
        assert capsys.readouterr().err == "nuh: config: unknown key llm.max_decision; ignored\n"


class TestGatingConfig:
    """tools and human_only: global only, validated entry by entry (config_gating)."""

    def test_defaults_are_empty_and_quiet(self, capsys):
        cfg = _merge_configs({}, {})
        assert (cfg.tools, cfg.human_only, capsys.readouterr().err) == ({}, {}, "")

    def test_entries_are_read(self):
        cfg = _merge_configs({"tools": {"WebFetch": "check", "mcp__forge__*": "approve"},
                              "human_only": {"git_remote_write": True, "sensitive_paths": False}}, {})
        assert cfg.tools == {"WebFetch": "check", "mcp__forge__*": "approve"}
        assert cfg.human_only == {"git_remote_write": True, "sensitive_paths": False}

    @pytest.mark.parametrize("raw,expected", [
        ({"Write": "review"}, "tools.Write must be one of defer, approve, check, decide, got 'review'"),
        (["Bash"], "tools must map tool names to"),
    ])
    def test_bad_tools_entries_warn_and_are_dropped(self, raw, expected, capsys):
        assert _merge_configs({"tools": raw}, {}).tools == {}
        assert expected in capsys.readouterr().err

    @pytest.mark.parametrize("raw,expected", [
        ({"no_such_key": True}, "human_only: unknown key 'no_such_key' ignored"),
        ({"lang_exec": "yes"}, "human_only.lang_exec must be true or false, got 'yes'; keeping false"),
        ({"credential_paths": "no"}, "human_only.credential_paths must be true or false, got 'no'; keeping true"),
        ({"unattributed": False}, "human_only: unknown key 'unattributed' ignored"),
    ])
    def test_bad_human_only_entries_warn_and_are_dropped(self, raw, expected, capsys):
        assert _merge_configs({"human_only": raw}, {}).human_only == {}
        assert expected in capsys.readouterr().err

    @pytest.mark.parametrize("key", ["tools", "human_only"])
    def test_a_project_cannot_set_them(self, key, capsys):
        value = {"Bash": "approve"} if key == "tools" else {"credential_paths": False}
        cfg = _merge_configs({}, {key: value})
        assert (cfg.tools, cfg.human_only) == ({}, {})
        assert f"{key} is global-only; ignored in .nuh.yaml" in capsys.readouterr().err

    def test_override_merges_onto_the_config(self, monkeypatch):
        from nuh import config
        config._cached_config = _merge_configs({"tools": {"WebFetch": "check"}}, {})
        config.apply_override({"tools": {"Bash": "approve"}, "human_only": {"lang_exec": True}})
        assert config.get_config().tools == {"WebFetch": "check", "Bash": "approve"}
        assert config.get_config().human_only == {"lang_exec": True}
