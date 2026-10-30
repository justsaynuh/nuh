"""Hint battery, sections 28-45: reads, lang exec, shell forms and special paths (see test_hint_battery.py)."""

import pytest

from tests.hint_helpers import _hint


pytestmark = pytest.mark.usefixtures("hint_isolated")


# ===================================================================
# 28. FILESYSTEM READS — all allowed, no hint
# ===================================================================
class TestFilesystemReadsAllowed:
    """Various read-only commands outside project — all allowed by default."""

    @pytest.mark.parametrize("cmd", [
        "wc -l /etc/hosts",
        "head -n 5 /var/log/syslog",
        "tail -f /var/log/syslog",
        "md5sum /etc/passwd",
        "file /usr/bin/python3",
        "stat /tmp/test.txt",
        "du -sh /tmp/",
        "df -h /",
        "diff /etc/hosts /tmp/hosts.bak",
        "awk '{print}' /etc/passwd",
        "sed -n '1,10p' /etc/hosts",
    ])
    def test_reads_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 29. LANG EXEC VARIANTS
# ===================================================================
class TestLangExecHints:
    """Language runtime execution — lang_exec hint."""

    @pytest.mark.parametrize("cmd", [
        # Safe inline code is now allowed (nah-koi.1), use script files for ask hints
        "python3 nonexistent_script.py",
        "node nonexistent_script.js",
        "ruby nonexistent_script.rb",
        "perl nonexistent_script.pl",
    ])
    def test_lang_exec_hint(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh allow lang_exec" in hint

    @pytest.mark.parametrize("cmd", [
        "python3 -c 'print(1)'",
        "node -e 'console.log(1)'",
        "ruby -e 'puts 1'",
        "perl -e 'print 1'",
    ])
    def test_inline_clean_asks_without_llm(self, cmd):
        """Clean inline code asks without an LLM verdict (static floor section 8)."""
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh allow lang_exec" in hint

    def test_python_module_lang_exec(self):
        """python3 -m is lang_exec (module execution, FD-079)."""
        decision, hint = _hint("python3 -m http.server 8000")
        assert decision == "ask"
        assert "nuh allow lang_exec" in hint


# ===================================================================
# 30. BEADS PIPED TO TOOLS
# ===================================================================
class TestBeadsPipedHints:
    """Beads output piped to tools — safe if pipe target is safe."""

    def test_bd_list_pipe_head_allowed(self):
        """bd list | head — both safe, allowed."""
        decision, hint = _hint("bd list --json | head -1")
        assert decision == "allow"
        assert hint is None

    def test_bd_ready_pipe_head_allowed(self):
        decision, hint = _hint("bd ready --json | head -1")
        assert decision == "allow"
        assert hint is None

    def test_bd_show_pipe_jq(self):
        """bd show | jq — jq is unknown, gets classify hint."""
        decision, hint = _hint("bd show nah-123 --json | jq .title")
        assert decision == "ask"
        assert "nuh classify" in hint
        assert "jq" in hint


# ===================================================================
# 31. SIGNAL VARIANTS
# ===================================================================
class TestSignalHints:
    """Various kill signal forms."""

    @pytest.mark.parametrize("cmd", [
        "kill -0 1234",
        "kill -TERM 1",
        "kill -SIGSTOP 1234",
    ])
    def test_kill_variants_classify(self, cmd):
        """kill with non-standard signals — may be unknown, classify hint."""
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None


# ===================================================================
# 32. CRON
# ===================================================================
class TestCronHints:
    """Cron commands — unknown, classify."""

    @pytest.mark.parametrize("cmd", [
        "crontab -l",
        "crontab -e",
    ])
    def test_cron_classify(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh classify" in hint


# ===================================================================
# 33. GLOB PATTERNS IN PATHS
# ===================================================================
class TestGlobPatternHints:
    """Globs in outside paths — trust the parent dir."""

    @pytest.mark.parametrize("cmd", [
        "rm /tmp/*.log",
        "rm -rf /tmp/nuh-*",
    ])
    def test_glob_outside_trust(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_tilde_file_trust(self):
        """rm ~/file.txt — trust hint for home subdir."""
        decision, hint = _hint("rm ~/file.txt")
        assert decision == "ask"
        assert "nuh trust" in hint


# ===================================================================
# 34. /dev/* SPECIAL FILES — should not ask (nah-gwm)
# ===================================================================
class TestDevSpecialFiles:
    """/dev/stderr, /dev/stdout, /dev/tty, /dev/fd/* are safe sinks."""

    @pytest.mark.parametrize("cmd", [
        "git status > /dev/stderr",
        "echo msg > /dev/stdout",
        "echo test > /dev/fd/2",
        "echo test > /dev/tty",
    ])
    def test_dev_special_should_not_ask(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow", (
            f"should allow but got {decision} with hint: {hint}"
        )

    @pytest.mark.parametrize("cmd", [
        "echo test | tee /dev/null",
        "echo test | tee /dev/stderr",
    ])
    @pytest.mark.xfail(reason="nah-gwm: tee /dev/* goes through context resolver, not redirect handler")
    def test_tee_dev_special_should_not_ask(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow", (
            f"should allow but got {decision} with hint: {hint}"
        )


# ===================================================================
# 35. CURL FLAG PARSING — wrong host extraction (nah-4tk)
# ===================================================================
class TestCurlFlagHints:
    """Curl with auth flags — host extraction picks up wrong token."""

    def test_curl_basic_auth_wrong_host(self):
        """curl -u user:pass should hint the URL host, not 'user'."""
        decision, hint = _hint("curl -u user:pass https://api.example.com")
        assert decision == "ask"
        assert hint is not None
        # BUG: extracts "user" as the host from -u flag
        if "nuh trust user" in hint:
            pytest.xfail("nah-4tk: curl -u flag value parsed as host")
        assert "example.com" in hint

    def test_curl_header_auth_wrong_host(self):
        """curl -H 'Auth: token' should hint the URL host, not 'Authorization'."""
        decision, hint = _hint(
            "curl -H 'Authorization: Bearer TOKEN' https://api.example.com"
        )
        assert decision == "ask"
        assert hint is not None
        if "nuh trust Authorization" in hint:
            pytest.xfail("nah-4tk: curl -H value parsed as host")
        assert "example.com" in hint

    def test_curl_cert_flag(self):
        """curl --cert /path — should hint the URL host."""
        decision, hint = _hint(
            "curl --cert /etc/ssl/client.crt https://example.com"
        )
        assert decision == "ask"
        assert "nuh trust" in hint
        assert "example.com" in hint


# ===================================================================
# 36. REDIRECT INSIDE PROJECT — should allow (nah-4tk)
# ===================================================================
class TestRedirectInsideProject:
    """Redirects to project-local files should allow."""

    @pytest.mark.parametrize("cmd", [
        "echo test > ./output.txt",
        "cat input > ./result",
        "echo 'key=val' >> .env.local",
    ])
    @pytest.mark.xfail(reason="nah-4tk: redirect inside project still triggers ask")
    def test_redirect_local_should_allow(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow", (
            f"should allow local redirect but got {decision} with hint: {hint}"
        )


# ===================================================================
# 37. MKTEMP — broad hint for temp file creation
# ===================================================================
class TestMktempHints:
    """mktemp creates temp files — broad filesystem_write hint is wrong."""

    @pytest.mark.parametrize("cmd", [
        "mktemp",
        "mktemp -d",
    ])
    @pytest.mark.xfail(reason="nah-4tk: mktemp hints broad filesystem_write")
    def test_mktemp_not_broad(self, cmd):
        decision, hint = _hint(cmd)
        assert "nuh allow filesystem_write" not in (hint or "")

    def test_mktemp_with_path_hints_trust(self):
        """mktemp /tmp/nuh-XXXXXX — has a path, should trust /tmp."""
        decision, hint = _hint("mktemp /tmp/nuh-XXXXXX")
        assert decision == "ask"
        assert "nuh trust" in hint


# ===================================================================
# 38. XARGS PIPED — broad hints for composed commands
# ===================================================================
class TestXargsPipedHints:
    """xargs in pipes — broad hints because composition context is lost."""

    def test_xargs_rm_broad(self):
        """find | xargs rm — hints filesystem_delete broadly."""
        decision, hint = _hint("find . -name '*.pyc' | xargs rm")
        assert decision == "ask"
        assert hint is not None
        # This is actually a composition but doesn't trigger composition_rule
        # because xargs is the exec sink, not bash/sh

    def test_xargs_curl_broad(self):
        """cat urls | xargs curl — hints network_outbound broadly."""
        decision, hint = _hint("cat urls.txt | xargs curl")
        assert decision == "ask"
        assert hint is not None

    def test_xargs_wc_allowed(self):
        """ls | xargs wc — both read-only, should allow."""
        decision, hint = _hint("ls /tmp/*.log | xargs wc -l")
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 39. TEE TO LOCAL FILE
# ===================================================================
class TestTeeLocalFile:
    """tee to project-local file should allow."""

    @pytest.mark.xfail(reason="nah-4tk: tee to local path still triggers ask")
    def test_tee_local_should_allow(self):
        """echo | tee ./local.txt — inside project, should allow."""
        decision, hint = _hint("echo test | tee ./local.txt")
        assert decision == "allow", (
            f"should allow tee to local file but got {decision} with hint: {hint}"
        )


# ===================================================================
# 40. DOTFILE SENSITIVITY
# ===================================================================
class TestDotfileHints:
    """.env files trigger content sensitivity in some contexts."""

    def test_cat_env_sensitive(self):
        """cat .env — built-in credential basename, a floor: the hint names it (C2)."""
        decision, hint = _hint("cat .env")
        assert decision == "ask"
        assert hint.startswith("Static floor rule credential_paths")

    def test_write_env_in_project(self):
        """echo > ./.env — write to sensitive dotfile in project."""
        decision, hint = _hint("echo 'test' > ./.env")
        assert decision == "ask"
        # This is a redirect issue — currently gets broad filesystem_write


# ===================================================================
# 41. /proc AND /sys — reads allowed
# ===================================================================
class TestProcSysReads:
    """/proc and /sys reads are allowed."""

    @pytest.mark.parametrize("cmd", [
        "cat /proc/self/status",
        "cat /proc/1/cmdline",
        "ls /proc/",
        "cat /sys/class/net/eth0/address",
    ])
    def test_proc_sys_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 42. HERE-STRING — should allow
# ===================================================================
class TestHereString:
    """Here-strings (<<<) should be safe."""

    @pytest.mark.parametrize("cmd", [
        "cat <<< 'hello world'",
        "wc -c <<< 'test'",
    ])
    def test_here_string_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 43. GIT ENV VARS
# ===================================================================
class TestGitEnvVars:
    """Git with environment variables should still classify correctly."""

    def test_git_dir_env(self):
        decision, hint = _hint("GIT_DIR=/other/repo git status")
        assert decision == "allow"

    def test_git_work_tree_env(self):
        decision, hint = _hint("GIT_WORK_TREE=/tmp/tree git diff")
        assert decision == "allow"

    def test_editor_env_rebase(self):
        """EDITOR=vim git rebase -i — still git_history_rewrite."""
        decision, hint = _hint("EDITOR=vim git rebase -i HEAD~3")
        assert decision == "ask"
        assert "git_history_rewrite" in hint


# ===================================================================
# 44. FILES WITH SPACES
# ===================================================================
class TestFilesWithSpaces:
    """Quoted paths with spaces."""

    def test_rm_quoted_path_trust(self):
        decision, hint = _hint('rm "/tmp/file with spaces"')
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_cat_quoted_outside(self):
        """cat '/etc/hosts' — read, should allow."""
        decision, hint = _hint("cat '/etc/hosts'")
        assert decision == "allow"


# ===================================================================
# 45. SIMPLE COMMANDS — all allowed
# ===================================================================
class TestSimpleCommandsAllowed:
    """Degenerate / simple commands — all should allow."""

    @pytest.mark.parametrize("cmd", [
        "echo",
        "cat",
        "ls",
        "pwd",
        "whoami",
        "uname -a",
        "hostname",
        "date",
        "uptime",
        "true",
        "false",
        "sleep 10",
    ])
    def test_simple_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None
