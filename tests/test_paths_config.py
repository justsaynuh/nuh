"""Unit tests for nuh.paths — sensitive paths and basenames from config (FD-051), config self-protection (FD-075)."""

import os
from unittest.mock import patch

import pytest

from nuh import path_names, paths, sensitive_paths
from nuh.config import NuhConfig


# --- sensitive path config override ---


class TestSensitivePathConfigOverride:
    """FD-025: Verify config can override hardcoded sensitive path policies."""

    def _mock_config(self, sensitive_paths):
        return NuhConfig(sensitive_paths=sensitive_paths)

    def test_override_ssh_block_to_ask(self):
        """Global config can relax ~/.ssh from block to ask."""
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.ssh": "ask"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.ssh/id_rsa")
        assert result is not None
        assert result["decision"] == "ask"

    def test_unoverridden_path_keeps_default(self):
        """Paths not in config keep their hardcoded policy."""
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.ssh": "ask"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.gnupg/key")
        assert result is not None
        assert result["decision"] == "block"

    def test_add_new_sensitive_path(self):
        """Config can add entirely new sensitive paths."""
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.kube": "ask"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.kube/config")
        assert result is not None
        assert result["decision"] == "ask"

    def test_hook_path_immutable(self):
        """~/.claude/hooks stays blocked regardless of config."""
        with patch("nuh.config.get_config", return_value=self._mock_config({})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Edit", "~/.claude/hooks/nuh_guard.py")
        assert result is not None
        assert result["decision"] == "block"
        assert "self-modification" in result["reason"]

    def test_merge_happens_once(self):
        """Merge is lazy and only runs once per lifecycle."""
        cfg = self._mock_config({"~/.ssh": "ask"})
        with patch("nuh.config.get_config", return_value=cfg):
            sensitive_paths.reset_sensitive_paths()
            sensitive_paths._ensure_sensitive_paths_merged()
            assert sensitive_paths._sensitive_paths_merged is True
            # Calling again should not re-merge (flag stays True)
            sensitive_paths._ensure_sensitive_paths_merged()
            assert sensitive_paths._sensitive_paths_merged is True

    @pytest.mark.parametrize("entry,target,decision", [
        ("~/.ssh", "~/.ssh/id_rsa", "block"),
        ("~/.aws", "~/.aws/credentials", "ask"),
        ("~/.gnupg", "~/.gnupg/key", "block"),
    ])
    def test_allow_cannot_remove_a_builtin_credential_entry(self, capsys, entry, target, decision):
        """A built-in credential entry is a floor: sensitive_paths allow warns and is ignored (C2)."""
        with patch("nuh.config.get_config", return_value=self._mock_config({entry: "allow"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", target)
        assert (result["decision"], result["_floor"]) == (decision, "credential_paths")
        assert f"sensitive_paths: {entry}: allow cannot remove a built-in entry" in capsys.readouterr().err

    def test_allow_cannot_remove_a_builtin_sensitive_entry(self):
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.bashrc": "allow"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.bashrc")
        assert (result["decision"], result["_floor"]) == ("ask", "sensitive_paths")

    def test_moving_a_credential_entry_between_ask_and_block_still_works(self):
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.gnupg": "ask"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.gnupg/key")
        assert (result["decision"], result["_floor"]) == ("ask", "credential_paths")

    def test_allow_only_removes_targeted_path(self):
        """sensitive_paths: allow on ~/.ssh should not affect ~/.gnupg."""
        with patch("nuh.config.get_config", return_value=self._mock_config({"~/.ssh": "allow"})):
            sensitive_paths.reset_sensitive_paths()
            result = paths.check_path("Read", "~/.gnupg/key")
        assert result is not None
        assert result["decision"] == "block"


# --- FD-051: Configurable sensitive basenames ---


class TestSensitiveBasenamesConfigurable:
    """FD-051: sensitive_basenames add/override/allow-remove, profile none."""

    def _mock_config(self, sensitive_basenames, profile="full"):
        return NuhConfig(sensitive_basenames=sensitive_basenames, profile=profile)

    def test_new_defaults(self):
        """FD-051: new default sensitive basenames are present."""
        sensitive_paths.reset_sensitive_paths()
        names = {e[0] for e in sensitive_paths._SENSITIVE_BASENAMES}
        assert ".env" in names
        assert ".env.local" in names
        assert ".env.production" in names
        assert ".npmrc" in names
        assert ".pypirc" in names

    def test_add_new_basename(self):
        """Config adds a new sensitive basename."""
        with patch("nuh.config.get_config", return_value=self._mock_config({".secrets": "block"})):
            sensitive_paths.reset_sensitive_paths()
            matched, pattern, policy = sensitive_paths.is_sensitive("/project/.secrets")
        assert matched is True
        assert policy == "block"

    def test_override_existing_basename(self):
        """Config overrides .env from ask to block."""
        with patch("nuh.config.get_config", return_value=self._mock_config({".env": "block"})):
            sensitive_paths.reset_sensitive_paths()
            matched, _, policy = sensitive_paths.is_sensitive("/project/.env")
        assert matched is True
        assert policy == "block"

    def test_allow_cannot_remove_a_builtin_basename(self, capsys):
        """A built-in basename is a credential floor: allow warns and is ignored (C2)."""
        with patch("nuh.config.get_config", return_value=self._mock_config({".env": "allow"})):
            sensitive_paths.reset_sensitive_paths()
            matched, _, _ = sensitive_paths.is_sensitive("/project/.env")
        assert matched is True
        assert "sensitive_basenames: .env: allow cannot remove a built-in entry" in capsys.readouterr().err

    def test_allow_removes_only_targeted(self):
        """allow on .env doesn't affect .env.local."""
        with patch("nuh.config.get_config", return_value=self._mock_config({".env": "allow"})):
            sensitive_paths.reset_sensitive_paths()
            matched, _, _ = sensitive_paths.is_sensitive("/project/.env.local")
        assert matched is True

    def test_profile_none_keeps_builtin_basenames(self):
        """profile: none keeps the built-in basenames: each is a credential floor (C2)."""
        with patch("nuh.config.get_config", return_value=self._mock_config({}, profile="none")):
            sensitive_paths.reset_sensitive_paths()
            matched, _, _ = sensitive_paths.is_sensitive("/project/.env")
        assert matched is True

    def test_profile_none_with_config(self):
        """profile: none keeps the defaults, and config entries apply too."""
        with patch("nuh.config.get_config", return_value=self._mock_config({".secrets": "block"}, profile="none")):
            sensitive_paths.reset_sensitive_paths()
            matched_env, _, _ = sensitive_paths.is_sensitive("/project/.env")
            matched_secrets, _, policy = sensitive_paths.is_sensitive("/project/.secrets")
        assert matched_env is True
        assert matched_secrets is True
        assert policy == "block"

    def test_reset_restores_basenames(self):
        """reset_sensitive_paths restores basenames to defaults."""
        sensitive_paths._SENSITIVE_BASENAMES.clear()
        sensitive_paths.reset_sensitive_paths()
        names = {e[0] for e in sensitive_paths._SENSITIVE_BASENAMES}
        assert ".env" in names
        assert ".env.local" in names


# --- FD-075: Config self-protection ---


class TestIsNahConfigPath:
    """FD-075: is_nuh_config_path() detects ~/.config/nuh/ paths."""

    def test_exact_config_dir(self):
        resolved = os.path.realpath(os.path.join(os.path.expanduser("~"), ".config", "nuh"))
        assert path_names.is_nuh_config_path(resolved) is True

    def test_child_of_config_dir(self):
        resolved = os.path.realpath(os.path.join(os.path.expanduser("~"), ".config", "nuh", "config.yaml"))
        assert path_names.is_nuh_config_path(resolved) is True

    def test_not_config_dir(self):
        assert path_names.is_nuh_config_path("/tmp/something") is False

    def test_config_sibling_not_matched(self):
        """~/.config/other is not nuh config."""
        resolved = os.path.realpath(os.path.join(os.path.expanduser("~"), ".config", "other"))
        assert path_names.is_nuh_config_path(resolved) is False

    def test_prefix_collision(self):
        """~/.config/nuh-evil should not match (prefix without separator)."""
        resolved = os.path.realpath(os.path.join(os.path.expanduser("~"), ".config", "nuh-evil"))
        assert path_names.is_nuh_config_path(resolved) is False

    def test_empty(self):
        assert path_names.is_nuh_config_path("") is False


class TestConfigSelfProtection:
    """FD-075: check_path and check_path_basic protect ~/.config/nuh/."""

    def setup_method(self):
        sensitive_paths._sensitive_paths_merged = True

    def test_check_path_basic_returns_ask(self):
        resolved = paths.resolve_path("~/.config/nuh/config.yaml")
        result = sensitive_paths.check_path_basic(resolved)
        assert result is not None
        decision, reason = result
        assert decision == "ask"
        assert "nuh config" in reason

    def test_check_path_write_ask(self):
        result = paths.check_path("Write", "~/.config/nuh/config.yaml")
        assert result is not None
        assert result["decision"] == "ask"
        assert "nuh config" in result["reason"]
        assert "guard self-protection" in result["reason"]

    def test_check_path_edit_ask(self):
        result = paths.check_path("Edit", "~/.config/nuh/config.yaml")
        assert result is not None
        assert result["decision"] == "ask"
        assert "nuh config" in result["reason"]

    def test_check_path_read_ask(self):
        result = paths.check_path("Read", "~/.config/nuh/config.yaml")
        assert result is not None
        assert result["decision"] == "ask"
        assert "nuh config" in result["reason"]

    def test_not_block_like_hook(self):
        """Config path gets ASK for Write/Edit, NOT BLOCK (unlike hook path)."""
        write_result = paths.check_path("Write", "~/.config/nuh/config.yaml")
        hook_result = paths.check_path("Write", "~/.claude/hooks/nuh_guard.py")
        assert write_result["decision"] == "ask"
        assert hook_result["decision"] == "block"

    def test_survives_profile_none(self):
        """Config path protection is hardcoded — not cleared by profile: none."""
        # Simulate profile: none clearing _SENSITIVE_DIRS (and the built-in ~/.ssh rule)
        sensitive_paths._SENSITIVE_DIRS.clear()
        sensitive_paths._SENSITIVE_BASENAMES.clear()
        sensitive_paths._ssh_narrow = False

        # Config path should STILL be caught (hardcoded, not in _SENSITIVE_DIRS)
        result = paths.check_path("Write", "~/.config/nuh/config.yaml")
        assert result is not None
        assert result["decision"] == "ask"
        assert "nuh config" in result["reason"]

        # Contrast: a regular sensitive path is gone
        result_ssh = paths.check_path("Read", "~/.ssh/id_rsa")
        # Only check_path_basic would catch it, but _SENSITIVE_DIRS is cleared
        # Hook check doesn't match, nuh config check doesn't match, is_sensitive returns False
        assert result_ssh is None

    def test_subdirectory_protected(self):
        """Subdirectories of ~/.config/nuh/ are also protected."""
        result = paths.check_path("Write", "~/.config/nuh/subdir/file.txt")
        assert result is not None
        assert result["decision"] == "ask"

    def test_nuh_log_protected(self):
        """Log file in config dir is protected."""
        result = paths.check_path("Write", "~/.config/nuh/nuh.log")
        assert result is not None
        assert result["decision"] == "ask"


class TestSettingsJsonProtection:
    """FD-075: ~/.claude/settings.json in _SENSITIVE_DIRS."""

    def test_settings_json_in_defaults(self):
        """settings.json is in the default sensitive dirs."""
        resolved = paths.resolve_path("~/.claude/settings.json")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "ask"
        assert "settings.json" in pattern

    def test_settings_local_json_in_defaults(self):
        """settings.local.json is in the default sensitive dirs."""
        resolved = paths.resolve_path("~/.claude/settings.local.json")
        matched, pattern, policy = sensitive_paths.is_sensitive(resolved)
        assert matched is True
        assert policy == "ask"
        assert "settings.local.json" in pattern

    def test_check_path_catches_settings(self):
        result = paths.check_path("Write", "~/.claude/settings.json")
        assert result is not None
        assert result["decision"] == "ask"
        assert "agent settings" in result["reason"]
        assert result["_floor"] == "agent_hooks"

    def test_settings_cleared_by_profile_none(self):
        """settings.json protection IS cleared by profile: none (by design)."""
        sensitive_paths._SENSITIVE_DIRS.clear()
        resolved = paths.resolve_path("~/.claude/settings.json")
        matched, _, _ = sensitive_paths.is_sensitive(resolved)
        assert matched is False


def test_sensitive_system_shadow_is_blocked() -> None:
    sensitive_paths.reset_sensitive_paths()
    decision = sensitive_paths.check_path_basic_raw("/etc/shadow")
    assert decision == ("block", "targets sensitive path: /etc/shadow")
