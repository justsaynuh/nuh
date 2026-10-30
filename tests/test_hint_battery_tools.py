"""Hint battery, sections 11-27: chains, wrappers, proportionality, and tool by tool (see test_hint_battery.py)."""

import os

import pytest

from nuh import paths
from tests.hint_helpers import _hint


pytestmark = pytest.mark.usefixtures("hint_isolated")


@pytest.fixture
def build_makefile(monkeypatch):
    """Run in the project, whose Makefile's default goal only echoes: ``make`` itself allows.

    Without it, ``make`` reads whatever Makefile the caller's directory has (or asks for the missing one).
    """
    root = paths.get_project_root()
    with open(os.path.join(root, "Makefile"), "w") as f:
        f.write("all:\n\techo built\n")
    monkeypatch.chdir(root)


# ===================================================================
# 11. CHAINED COMMANDS → first ask stage wins
# ===================================================================
class TestChainedCommandHints:
    """Multi-stage commands — hint comes from the first ask stage."""

    def test_force_push_then_rm(self):
        """git push --force && rm — git_history_rewrite hint wins."""
        decision, hint = _hint("git push --force && rm -rf /tmp/cache")
        assert decision == "ask"
        assert hint is not None
        assert "nuh allow git_history_rewrite" in hint

    def test_db_write_then_network(self):
        """psql && curl — db_write hint wins."""
        decision, hint = _hint("psql -c SELECT && curl example.com")
        assert decision == "ask"
        assert "nuh allow db_write" in hint

    def test_destructive_beads_then_safe(self):
        """bd delete; bd list — beads_destructive hint wins."""
        decision, hint = _hint("bd delete x; bd list")
        assert decision == "ask"
        assert "nuh allow beads_destructive" in hint

    def test_chained_deletes_same_dir(self):
        """rm /tmp/a && rm /tmp/b — trust hint for the directory."""
        decision, hint = _hint("rm /tmp/a && rm /tmp/b")
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint


# ===================================================================
# 12. SHELL WRAPPERS
# ===================================================================
class TestShellWrapperHints:
    """Shell wrappers should unwrap and hint the inner command."""

    def test_bash_c_rm(self):
        """bash -c 'rm ...' — unwrapped, hint for the inner command."""
        decision, hint = _hint("bash -c 'rm -rf /tmp/test'")
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint

    def test_eval_curl(self):
        """eval 'curl ...' — unwrapped, hint the host."""
        decision, hint = _hint("eval 'curl https://evil.com'")
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint

    def test_command_unwrap_psql(self):
        """command psql — unwrapped to psql, db_write hint."""
        decision, hint = _hint("command psql -c DROP")
        assert decision == "ask"
        assert hint is not None
        assert "nuh allow" in hint

    def test_xargs_rm(self):
        """xargs rm — the rm is the classified command."""
        decision, hint = _hint("xargs rm < list.txt")
        assert decision == "ask"
        assert hint is not None


# ===================================================================
# 13. PROPORTIONALITY — hint should be narrow, not broad
# ===================================================================
class TestProportionality:
    """Hints should suggest the narrowest possible fix."""

    def test_no_allow_filesystem_read_for_sensitive(self):
        """Sensitive path should never hint allow filesystem_read."""
        decision, hint = _hint("cat ~/.aws/config")
        assert "nuh allow filesystem_read" not in (hint or "")

    def test_no_allow_filesystem_write_for_outside(self):
        """Outside project write should never hint allow filesystem_write."""
        decision, hint = _hint("touch /opt/homebrew/file")
        assert "nuh allow filesystem_write" not in (hint or "")

    def test_no_allow_filesystem_delete_for_outside(self):
        """Outside project delete should never hint allow filesystem_delete."""
        decision, hint = _hint("rm /tmp/test.txt")
        assert "nuh allow filesystem_delete" not in (hint or "")

    def test_no_allow_network_outbound_for_unknown_host(self):
        """Unknown host should never hint allow network_outbound."""
        decision, hint = _hint("curl https://api.example.com")
        assert "nuh allow network_outbound" not in (hint or "")

    def test_trust_root_not_suggested(self):
        """Hint should never suggest 'nuh trust /' (too broad)."""
        decision, hint = _hint("rm /tmp/test.txt")
        if hint:
            assert hint.rstrip() != "To always allow: nuh trust /"

    def test_redirect_not_broad_filesystem_write(self):
        """Redirect to /tmp should NOT suggest nuh allow filesystem_write."""
        decision, hint = _hint("echo hello > /tmp/output.txt")
        assert "nuh allow filesystem_write" not in (hint or "")

    def test_tee_outside_hints_path_not_type(self, build_makefile):
        """make | tee /tmp/build.log — hint the path, not the type."""
        decision, hint = _hint("make 2>&1 | tee /tmp/build.log")
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint


# ===================================================================
# 14. DATABASE EDGE CASES
# ===================================================================
class TestDatabaseHints:
    """Database commands — db_write hint, not network or filesystem."""

    @pytest.mark.parametrize("cmd", [
        "psql -c SELECT",
        "psql",
        "mysql -e SHOW",
        "mysql",
        "sqlite3 /tmp/test.db",
        "dolt sql SELECT",
    ])
    def test_db_write_hint(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None
        assert "nuh allow db_write" in hint

    def test_psql_with_host_still_db_write(self):
        """psql -h host should still hint db_write, not network trust."""
        decision, hint = _hint("psql -h unknown.db.com mydb")
        assert decision == "ask"
        assert "nuh allow db_write" in hint


# ===================================================================
# 15. NETWORK DIAGNOSTICS — allowed, no hint
# ===================================================================
class TestNetworkDiagnosticAllowed:
    """Network diagnostics are allowed regardless of host."""

    @pytest.mark.parametrize("cmd", [
        "ping unknown.host",
        "dig unknown.host",
        "nslookup unknown.host",
        "traceroute unknown.host",
    ])
    def test_network_diagnostic_allowed(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 16. BLOCK — no hint (not overridable)
# ===================================================================
class TestBlockNoHint:
    """Blocked commands should not have hints."""

    @pytest.mark.parametrize("cmd", [
        "cat ~/.ssh/my_deploy_key",
        "cat ~/.netrc",
        "ls ~/.gnupg/",
        "echo key > ~/.ssh/authorized_keys",
        "ssh-add ~/.ssh/id_ed25519",
    ])
    def test_block_no_hint(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "block"
        assert hint is None


# ===================================================================
# 17. PIPE TO TEE — hint should be trust <dir>, not type
# ===================================================================
class TestTeeHints:
    """tee to outside paths should hint trust, not broad type."""

    @pytest.mark.parametrize("cmd", [
        "echo hello | tee /tmp/out.txt",
        "cat file.txt | tee ~/backup.txt",
        "make | tee /tmp/build.log",
        "git log | tee /tmp/gitlog.txt",
        "echo test | tee -a /tmp/append.log",
    ])
    def test_tee_outside_hints_trust(self, build_makefile, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint
        assert "nuh allow filesystem" not in hint


# ===================================================================
# 18. CONDITIONAL CHAINS — hint from first ask stage
# ===================================================================
class TestConditionalChainHints:
    """Chains with || and && — first ask stage determines hint."""

    def test_and_chain_second_asks(self):
        """true && rm /tmp/x — rm is the ask stage."""
        decision, hint = _hint("true && rm /tmp/x")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_or_chain_second_asks(self):
        """test -f /tmp/x || touch /tmp/x — touch is the ask stage."""
        decision, hint = _hint("test -f /tmp/x || touch /tmp/x")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_semicolon_safe_commands(self):
        """echo a; echo b — both allow, no hint."""
        decision, hint = _hint("echo a; echo b")
        assert decision == "allow"
        assert hint is None


# ===================================================================
# 19. DOCKER — unknown, classify hints
# ===================================================================
class TestDockerHints:
    """Docker commands not in classify table → unknown, classify hint."""

    @pytest.mark.parametrize("cmd", [
        "docker exec -it container bash",
        "docker run -v /tmp:/data alpine",
        "docker run --rm -it ubuntu bash",
        "docker cp container:/app/file.txt /tmp/",
        "docker logs container",
        "docker inspect container",
    ])
    def test_docker_unknown_classify(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None
        assert "nuh classify" in hint


# ===================================================================
# 20. SUDO / PRIVILEGE ESCALATION
# ===================================================================
class TestSudoHints:
    """sudo commands — a floored ask (privilege): the hint names the floor, not a classify."""

    @pytest.mark.parametrize("cmd", [
        "sudo rm -rf /tmp/cache",
        "sudo apt update",
    ])
    def test_sudo_floor(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "floor rule privilege" in hint.lower()


# ===================================================================
# 21. SYSTEM SERVICES
# ===================================================================
class TestSystemServiceHints:
    """systemctl/service — unknown, classify hint."""

    @pytest.mark.parametrize("cmd", [
        "systemctl restart nginx",
        "systemctl status sshd",
        "service apache2 start",
    ])
    def test_system_service_classify(self, cmd):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert "nuh classify" in hint


# ===================================================================
# 22. TAR/ZIP OUTSIDE PROJECT
# ===================================================================
class TestArchiveHints:
    """Archive commands writing outside project → trust hint."""

    @pytest.mark.parametrize("cmd, expected_in_hint", [
        ("tar -czf /tmp/backup.tar.gz .", "nuh trust"),
        ("tar -xzf archive.tar.gz -C /tmp/extract", "nuh trust"),
        ("tar -xzf archive.tar -C /opt/dest", "nuh trust"),
    ])
    def test_tar_outside_trust_hint(self, cmd, expected_in_hint):
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None
        assert expected_in_hint in hint

    @pytest.mark.parametrize("cmd,expected_in_hint", [
        ("zip /tmp/archive.zip *.py", "nuh trust /tmp"),
        ("unzip file.zip -d /tmp/extracted", "nuh classify unzip"),
    ])
    def test_archive_output_trust_hint(self, cmd: str, expected_in_hint: str) -> None:
        """Known zip destinations offer path trust; unknown unzip members retain uncertainty."""
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert expected_in_hint in hint


# ===================================================================
# 23. SED IN-PLACE
# ===================================================================
class TestSedHints:
    """sed -i triggers filesystem_write."""

    def test_sed_read_only_allowed(self):
        """sed without -i is read-only."""
        decision, hint = _hint("sed -n '1,10p' /etc/hosts")
        assert decision == "allow"

    def test_sed_inplace_outside_project(self):
        """sed -i outside project → trust hint."""
        decision, hint = _hint("sed -i 's/old/new/' /tmp/config")
        assert decision == "ask"
        assert hint is not None
        # Should hint trust for the path, not broad filesystem_write
        assert "nuh trust" in hint


# ===================================================================
# 24. RSYNC — network hint quirks
# ===================================================================
class TestRsyncHints:
    """Rsync classified as network — various hint issues."""

    def test_rsync_local_to_outside(self):
        """rsync to /tmp — classified as network, should still ask."""
        decision, hint = _hint("rsync -av . /tmp/backup/")
        assert decision == "ask"
        assert hint is not None

    def test_rsync_to_remote(self):
        """rsync to remote host — should hint trust."""
        decision, hint = _hint("rsync -e ssh file.txt user@host:/tmp/")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_rsync_bad_trust_target(self):
        """rsync -av . /tmp/ should NOT suggest 'nuh trust .' (the source)."""
        decision, hint = _hint("rsync -av . /tmp/backup/")
        if hint and "nuh trust" in hint:
            assert "nuh trust ." != hint.split("nuh trust ")[1].strip()


# ===================================================================
# 25. MAKE INSTALL — broad hint
# ===================================================================
class TestMakeInstallHints:
    """make install runs its recipe: the recipe's targets decide."""

    @pytest.fixture
    def makefile(self, monkeypatch):
        root = paths.get_project_root()
        with open(os.path.join(root, "Makefile"), "w") as f:
            f.write("install:\n\tcp app $(DESTDIR)/usr/local/bin/app\n")
        monkeypatch.chdir(root)

    def test_make_install_not_broad(self, makefile):
        """make install should not suggest nuh allow filesystem_write."""
        decision, hint = _hint("make install")
        assert decision == "ask"
        assert "nuh allow filesystem_write" not in (hint or "")

    def test_make_install_destdir_hints_trust(self, makefile):
        """make install DESTDIR=/tmp/staging — the recipe writes outside the project: trust hint."""
        decision, hint = _hint("make install DESTDIR=/tmp/staging")
        assert decision == "ask"
        assert hint is not None
        assert "nuh trust" in hint


# ===================================================================
# 26. CP / MV — which path gets hinted
# ===================================================================
class TestCopyMoveHints:
    """cp/mv should hint based on the outside path, not the source."""

    def test_cp_outside_to_outside(self):
        """cp /etc/resolv.conf /tmp/bak — trust hint for one of the outside dirs."""
        decision, hint = _hint("cp /etc/resolv.conf /tmp/resolv.bak")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_mv_both_tmp(self):
        """mv /tmp/old /tmp/new — trust /tmp."""
        decision, hint = _hint("mv /tmp/old.txt /tmp/new.txt")
        assert decision == "ask"
        assert "nuh trust" in hint

    def test_cp_target_dir_flag_names_the_destination(self):
        """cp --target-directory=/opt/dest file.txt: the hint trusts where it writes, not a broad allow."""
        assert _hint("cp --target-directory=/opt/dest file.txt") == ("ask", "To always allow: nuh trust /opt")

    @pytest.mark.parametrize("cmd,hint", [
        ("rm ./x /opt/n", "To always allow: nuh trust /opt"),
        ("rm /opt/n ./x", "To always allow: nuh trust /opt"),
        ("touch ./a /srv/data/b ./c", "To always allow: nuh trust /srv/data"),
        ("chmod 600 ./x /srv/keys/k", "To always allow: nuh trust /srv/keys"),
    ])
    def test_hint_names_the_operand_that_asked(self, cmd, hint, monkeypatch):
        """The first path-like word is in the project; the hint names the operand the boundary check judged."""
        monkeypatch.chdir(paths.get_project_root())  # ./x is in the project
        assert _hint(cmd) == ("ask", hint)


# ===================================================================
# 27. ABSOLUTE PATH COMMANDS
# ===================================================================
class TestAbsolutePathCommands:
    """Commands invoked with absolute paths."""

    @pytest.mark.parametrize("cmd", [
        "/usr/bin/env python3 script.py",
        "/usr/local/bin/node script.js",
    ])
    def test_absolute_path_unknown(self, cmd):
        """Absolute path commands — classified after basename normalization."""
        decision, hint = _hint(cmd)
        assert decision == "ask"
        assert hint is not None

    def test_absolute_rm(self):
        """/usr/bin/rm — should normalize to rm."""
        decision, hint = _hint("/usr/bin/rm -rf /tmp/cache")
        assert decision == "ask"
        assert "nuh trust" in hint
