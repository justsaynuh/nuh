"""Unit tests for nuh.paths — symlinks (GitHub #57), the project root, trusted paths without a git root."""

import os
from unittest.mock import patch

from nuh import config, paths, sensitive_paths
from nuh.config import NuhConfig


# --- Symlink regression tests (GitHub #57) ---


class TestSymlinkResolution:
    """Symlinks to sensitive targets must be caught by path classification."""

    def test_symlink_to_ssh_blocked(self, tmp_path):
        """Symlink to ~/.ssh → sensitive path detected."""
        target = tmp_path / "fake_ssh"
        target.mkdir()
        link = tmp_path / "innocent.txt"
        link.symlink_to(target)
        # Pretend target is ~/.ssh by patching sensitive dirs
        resolved_target = str(target.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved_target, "~/.ssh", "block"))
        try:
            result = paths.check_path("Read", str(link))
            assert result is not None
            assert result["decision"] == "block"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original

    def test_symlink_to_sensitive_dir_file(self, tmp_path):
        """Symlink to a file inside a sensitive directory."""
        sensitive_dir = tmp_path / "sensitive"
        sensitive_dir.mkdir()
        secret = sensitive_dir / "key.pem"
        secret.write_text("secret")
        link = tmp_path / "harmless.pem"
        link.symlink_to(secret)
        resolved_dir = str(sensitive_dir.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved_dir, "~/sensitive", "ask"))
        try:
            result = paths.check_path("Read", str(link))
            assert result is not None
            assert result["decision"] == "ask"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original

    def test_chained_symlinks(self, tmp_path):
        """Chain: link1 → link2 → sensitive. realpath resolves the full chain."""
        sensitive = tmp_path / "secrets"
        sensitive.mkdir()
        link2 = tmp_path / "stage2"
        link2.symlink_to(sensitive)
        link1 = tmp_path / "stage1"
        link1.symlink_to(link2)
        resolved = str(sensitive.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved, "~/secrets", "block"))
        try:
            result = paths.check_path("Read", str(link1))
            assert result is not None
            assert result["decision"] == "block"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original

    def test_relative_symlink(self, tmp_path):
        """Relative symlink (../../sensitive) resolved correctly."""
        sensitive = tmp_path / "sensitive"
        sensitive.mkdir()
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        link = subdir / "link"
        link.symlink_to(os.path.relpath(sensitive, subdir))
        resolved = str(sensitive.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved, "~/sensitive", "block"))
        try:
            result = paths.check_path("Read", str(link))
            assert result is not None
            assert result["decision"] == "block"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original

    def test_broken_symlink_not_sensitive(self, tmp_path):
        """Broken symlink (target doesn't exist) — not sensitive, should allow."""
        link = tmp_path / "broken"
        link.symlink_to("/nonexistent/path/that/does/not/exist")
        result = paths.check_path("Read", str(link))
        assert result is None  # not sensitive

    def test_symlink_clean_path_still_allowed(self, tmp_path):
        """Symlink to a non-sensitive target — should allow."""
        target = tmp_path / "safe_dir"
        target.mkdir()
        safe_file = target / "data.txt"
        safe_file.write_text("hello")
        link = tmp_path / "link.txt"
        link.symlink_to(safe_file)
        result = paths.check_path("Read", str(link))
        assert result is None

    def test_symlink_write_tool(self, tmp_path):
        """Write through symlink to sensitive target → caught."""
        sensitive = tmp_path / "protected"
        sensitive.mkdir()
        link = tmp_path / "writable.txt"
        link.symlink_to(sensitive / "config")
        resolved = str(sensitive.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved, "~/protected", "ask"))
        try:
            result = paths.check_path("Write", str(link))
            assert result is not None
            assert result["decision"] == "ask"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original

    def test_symlink_with_allow_paths_no_bypass(self, tmp_path):
        """allow_paths on the symlink dir must NOT exempt a sensitive target."""
        sensitive = tmp_path / "gnupg"
        sensitive.mkdir()
        secret = sensitive / "key"
        secret.write_text("private")
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        link = allowed_dir / "harmless"
        link.symlink_to(secret)

        resolved_sensitive = str(sensitive.resolve())
        original = list(sensitive_paths._SENSITIVE_DIRS)
        sensitive_paths._SENSITIVE_DIRS.append((resolved_sensitive, "~/.gnupg", "block"))
        paths.set_project_root(str(tmp_path))
        fake_config = config.NuhConfig()
        fake_config.allow_paths = {str(allowed_dir): [str(tmp_path)]}
        try:
            with patch("nuh.config.get_config", return_value=fake_config):
                result = paths.check_path("Read", str(link))
                assert result is not None
                assert result["decision"] == "block", \
                    "allow_paths on symlink dir must not bypass sensitive target"
        finally:
            sensitive_paths._SENSITIVE_DIRS[:] = original


# --- set/reset/get project root ---


class TestProjectRoot:
    def test_set_and_get(self):
        paths.set_project_root("/fake/project")
        assert paths.get_project_root() == "/fake/project"

    def test_reset_clears(self):
        paths.set_project_root("/fake/project")
        paths.reset_project_root()
        # After reset, get_project_root will auto-detect (via git).
        # We can't assert a specific value, but we can verify it doesn't
        # return the old value if we reset and set again.
        paths.set_project_root("/other/project")
        assert paths.get_project_root() == "/other/project"

    def test_autouse_fixture_resets(self):
        # This test runs after the autouse _reset_paths fixture,
        # so any previous set_project_root should be cleared.
        # We just verify set/get works from a clean state.
        paths.set_project_root("/test/root")
        assert paths.get_project_root() == "/test/root"


class TestTrustedPathNoGitRoot:
    """FD-107: trusted_paths should work even with no git root."""

    def teardown_method(self):
        config._cached_config = None

    def test_trusted_path_no_git_root(self):
        """Trusted path should allow even with no git root."""
        paths.set_project_root(None)
        config._cached_config = NuhConfig(trusted_paths=["/tmp"])
        result = paths.check_project_boundary("Write", "/tmp/test.txt")
        assert result is None  # allowed

    def test_untrusted_path_no_git_root(self):
        """Untrusted path with no git root should still ask."""
        paths.set_project_root(None)
        config._cached_config = NuhConfig()
        result = paths.check_project_boundary("Write", "/var/data/file.txt")
        assert result is not None
        assert result["decision"] == "ask"
        assert "no git root" in result["reason"]
