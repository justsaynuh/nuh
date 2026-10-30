"""Unit tests for nuh.taxonomy — package managers and build tools (FD-019).

Writes, network writes, prefix priority, global installs, round trips; the Supabase MCP tools.
"""

import pytest

from nuh.taxonomy import (
    build_user_table,
    classify_tokens,
    get_builtin_table,
)
from tests.taxonomy_helpers import _ct


class TestFD019FilesystemWrite:
    """FD-019: filesystem_write for make/cmake install."""

    @pytest.mark.parametrize("tokens", [
        ["make", "install"],
        ["gmake", "install"],
        ["cmake", "--install", "build/"],
    ])
    def test_filesystem_write(self, tokens):
        assert _ct(tokens) == "filesystem_write"


class TestFD019NetworkWrite:
    """FD-019: network_write classification for publish/deploy commands."""

    @pytest.mark.parametrize("tokens", [
        ["npm", "publish"],
        ["npm", "unpublish", "my-pkg"],
        ["npm", "deprecate", "my-pkg@1.0", "use v2"],
        ["npm", "dist-tag", "add", "my-pkg@1.0", "latest"],
        ["npm", "access", "public", "my-pkg"],
        ["npm", "owner", "add", "user", "my-pkg"],
        ["yarn", "publish"],
        ["yarn", "npm", "publish"],
        ["yarn", "npm", "tag", "add", "my-pkg@1.0", "latest"],
        ["pnpm", "publish"],
        ["bun", "publish"],
        ["uv", "publish"],
        ["gem", "push", "my.gem"],
        ["gem", "yank", "my-gem", "-v", "1.0"],
        ["gem", "owner", "--add", "user", "my-gem"],
        ["cargo", "publish"],
        ["cargo", "owner", "--add", "user"],
        ["cargo", "yank", "--version", "1.0"],
        ["gradle", "publish"],
        ["gradle", "uploadArchives"],
        ["gradlew", "publish"],
        ["/usr/bin/gradlew", "publish"],
        ["/usr/bin/gradlew", "uploadArchives"],
        ["mvn", "deploy"],
        ["mvn", "site-deploy"],
        ["mvn", "release:perform"],
    ])
    def test_network_write(self, tokens):
        assert _ct(tokens) == "network_write"


class TestFD019PrefixPriority:
    """FD-019: Longest-prefix-first resolves ambiguous commands."""

    def test_npm_audit_vs_audit_fix(self):
        assert _ct(["npm", "audit"]) == "filesystem_read"
        assert _ct(["npm", "audit", "fix"]) == "package_install"

    def test_make_vs_make_install(self):
        assert _ct(["make"]) == "package_run"
        assert _ct(["make", "install"]) == "filesystem_write"

    def test_make_vs_make_dry_run(self):
        assert _ct(["make"]) == "package_run"
        assert _ct(["make", "-n"]) == "filesystem_read"

    def test_cargo_fmt_vs_fmt_check(self):
        assert _ct(["cargo", "fmt"]) == "package_run"
        assert _ct(["cargo", "fmt", "--check"]) == "filesystem_read"

    def test_cmake_build_vs_install(self):
        assert _ct(["cmake", "--build", "build/"]) == "package_install"
        assert _ct(["cmake", "--install", "build/"]) == "filesystem_write"

    def test_gmake_vs_gmake_install(self):
        assert _ct(["gmake"]) == "package_run"
        assert _ct(["gmake", "install"]) == "filesystem_write"

    def test_gmake_vs_gmake_dry_run(self):
        assert _ct(["gmake"]) == "package_run"
        assert _ct(["gmake", "-n"]) == "filesystem_read"

    def test_ctest_vs_ctest_N(self):
        assert _ct(["ctest"]) == "package_run"
        assert _ct(["ctest", "-N"]) == "filesystem_read"


class TestFD019GlobalInstall:
    """FD-019: Global-install flag classifier escalation."""

    @pytest.mark.parametrize("tokens", [
        ["npm", "install", "-g", "typescript"],
        ["npm", "install", "--global", "typescript"],
        ["pnpm", "add", "--global", "turbo"],
        ["bun", "add", "--global", "bun-types"],
        ["pip", "install", "--system", "flask"],
        ["pip", "install", "--target", "/tmp/lib", "flask"],
        ["pip", "install", "--root", "/opt", "flask"],
        ["pip3", "install", "--system", "flask"],
        ["cargo", "install", "--root", "/usr/local", "ripgrep"],
        ["gem", "install", "--system", "bundler"],
    ])
    def test_global_flag_escalates_to_unknown(self, tokens):
        assert _ct(tokens) == "unknown"

    def test_npm_install_without_global_still_install(self):
        assert _ct(["npm", "install", "react"]) == "package_install"

    def test_pip_install_without_system_still_install(self):
        assert _ct(["pip", "install", "flask"]) == "package_install"

    def test_cargo_build_without_root_still_install(self):
        assert _ct(["cargo", "build"]) == "package_install"

    def test_yarn_not_in_global_install_cmds(self):
        """yarn uses 'yarn global add' pattern, not a flag."""
        assert _ct(["yarn", "add", "something"]) == "package_install"

    def test_global_override_via_user_table(self):
        """Global config can override the global-install flag classifier."""
        global_t = build_user_table({"package_install": ["npm install"]})
        builtin_t = get_builtin_table("full")
        result = classify_tokens(
            ["npm", "install", "-g", "x"], global_t, builtin_t
        )
        assert result == "package_install"


class TestFD019Roundtrip:
    """Every JSON prefix entry classifies to its file's action type."""

    def test_all_classify_full_entries_roundtrip(self):
        import json
        from pathlib import Path
        data_dir = Path(__file__).parent.parent / "src" / "nuh" / "data" / "classify_full"
        full_table = get_builtin_table("full")
        failures = []
        for json_file in sorted(data_dir.glob("*.json")):
            expected_type = json_file.stem
            with open(json_file) as f:
                prefixes = json.load(f)
            for prefix_str in prefixes:
                tokens = prefix_str.split()
                result = classify_tokens(tokens, builtin_table=full_table)
                if result != expected_type:
                    failures.append(
                        f"{prefix_str!r} → {result} (expected {expected_type})"
                    )
        assert failures == [], "Misclassified entries:\n" + "\n".join(failures)


class TestFD019NoDuplicates:
    """No prefix string appears in more than one JSON file."""

    def test_no_cross_file_duplicates(self):
        import json
        from pathlib import Path
        data_dir = Path(__file__).parent.parent / "src" / "nuh" / "data" / "classify_full"
        seen: dict[str, str] = {}
        duplicates = []
        for json_file in sorted(data_dir.glob("*.json")):
            with open(json_file) as f:
                prefixes = json.load(f)
            for prefix_str in prefixes:
                if prefix_str in seen:
                    duplicates.append(
                        f"{prefix_str!r} in both {seen[prefix_str]} and {json_file.name}"
                    )
                else:
                    seen[prefix_str] = json_file.name
        assert duplicates == [], "Duplicate entries:\n" + "\n".join(duplicates)


class TestSupabaseMcp:
    """Supabase MCP tool classification (nuh-3f5)."""

    @pytest.mark.parametrize("tool", [
        "mcp__supabase__list_tables",
        "mcp__supabase__list_extensions",
        "mcp__supabase__list_migrations",
        "mcp__supabase__list_edge_functions",
        "mcp__supabase__get_edge_function",
        "mcp__supabase__list_branches",
        "mcp__supabase__list_storage_buckets",
        "mcp__supabase__get_storage_config",
        "mcp__supabase__list_projects",
        "mcp__supabase__get_project",
        "mcp__supabase__list_organizations",
        "mcp__supabase__get_organization",
        "mcp__supabase__get_cost",
        "mcp__supabase__get_project_url",
        "mcp__supabase__get_publishable_keys",
        "mcp__supabase__generate_typescript_types",
        "mcp__supabase__get_logs",
        "mcp__supabase__get_advisors",
        "mcp__supabase__search_docs",
    ])
    def test_supabase_read(self, tool):
        assert _ct([tool]) == "db_read"

    @pytest.mark.parametrize("tool", [
        "mcp__supabase__execute_sql",
        "mcp__supabase__confirm_cost",
        "mcp__supabase__create_branch",
        "mcp__supabase__restore_project",
        "mcp__supabase__update_storage_config",
        "mcp__supabase__rebase_branch",
    ])
    def test_supabase_write(self, tool):
        assert _ct([tool]) == "db_write"

    @pytest.mark.parametrize("tool", [
        "mcp__supabase__create_project",
        "mcp__supabase__pause_project",
        "mcp__supabase__apply_migration",
        "mcp__supabase__deploy_edge_function",
        "mcp__supabase__delete_branch",
        "mcp__supabase__merge_branch",
        "mcp__supabase__reset_branch",
    ])
    def test_supabase_destructive_unclassified(self, tool):
        assert _ct([tool]) == "unknown"
