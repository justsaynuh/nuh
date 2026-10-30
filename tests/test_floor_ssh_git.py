"""Static floor rule group 4: ssh options and git configuration that run programs."""

import pytest

from nuh import git_exec, host_extract, sensitive_paths, ssh_args, taxonomy
from nuh.bash import classify_command


def _floor(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


class TestSshLocalhost:
    def test_plain_localhost_still_allowed(self):
        assert _floor("ssh localhost uptime") == (taxonomy.ALLOW, [])

    @pytest.mark.parametrize("command", [
        "ssh -o ProxyCommand=sh localhost uptime",
        "ssh -oProxyCommand=sh localhost uptime",
        "ssh -o 'ProxyCommand sh' localhost uptime",
        "ssh localhost -o ProxyCommand=sh uptime",
        "ssh -o LocalCommand=id -o PermitLocalCommand=yes localhost",
        "ssh -o KnownHostsCommand=/tmp/x localhost",
        "ssh -o Include=/tmp/cfg localhost",
        "ssh -F ./cfg localhost uptime",
        "scp -S ./fake-ssh localhost:a b",
        "rsync -e 'ssh -o ProxyCommand=sh' a localhost:b",
        "rsync -e ./fake-shell a localhost:b",
    ])
    def test_exec_options_floored(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "ssh_exec_options" in rules

    @pytest.mark.parametrize("command", [
        "ssh -J other localhost uptime",
        "ssh -o ProxyJump=other localhost uptime",
        "ssh -o HostName=other localhost uptime",
        "ssh -W other:22 localhost",
        "scp localhost:a other:b",
        "scp a.txt user@other:/tmp/",
    ])
    def test_routing_drops_localhost_shortcut(self, command):
        assert classify_command(command).final_decision == taxonomy.ASK

    @pytest.mark.parametrize("command", [
        "ssh -F /dev/null localhost uptime",
        "ssh -o ProxyCommand=none localhost uptime",
        "ssh -p 2222 -i ~/.ssh/id_ed25519.pub localhost uptime",
    ])
    def test_harmless_options(self, command):
        assert "ssh_exec_options" not in _floor(command)[1]

    def test_every_scp_remote_is_a_destination(self):
        parsed = ssh_args.parse("scp", ["localhost:a", "other:b", "./local"])
        assert ssh_args.destinations(parsed) == ["localhost", "other"]
        assert host_extract.extract_hosts(["scp", "localhost:a", "other:b"]) == ["localhost", "other"]

    def test_jump_hosts_are_destinations(self):
        parsed = ssh_args.parse("ssh", ["-J", "u@a:22,b", "localhost"])
        assert ssh_args.destinations(parsed) == ["localhost", "a", "b"]


class TestGitExec:
    @pytest.mark.parametrize("command", [
        "git -c core.sshCommand=sh fetch",
        "git -c core.fsmonitor=./x status",
        "git -c core.pager=sh log",
        "git -c core.hooksPath=/tmp/h commit -m x",
        "git -c credential.helper='!sh' fetch",
        "git -c credential.https://example.com.helper=x fetch",
        "git -c diff.foo.textconv=./conv diff",
        "git -c filter.lfs.smudge=./x checkout .",
        "git -c pager.log=./x log",
        "git -c alias.x='!sh' x",
        "git -c uploadpack.packObjectsHook=./x fetch",
        "git -c protocol.ext.allow=always fetch",
        "git -c include.path=/tmp/cfg status",
        "git --config-env=core.pager=EVIL log",
        "git --config-env core.sshCommand=EVIL fetch",
        "git --exec-path=/tmp/x status",
        "git config core.hooksPath /tmp/h",
        "git config --global core.pager ./x",
        "git config set core.fsmonitor ./x",
        "git config alias.lg '!sh -c x'",
        "git config --edit",
        "GIT_SSH_COMMAND=sh git fetch",
        "GIT_SSH=./x git fetch",
        "env GIT_EXTERNAL_DIFF=./x git diff",
        "export GIT_PAGER=./x",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0=x git log",
        "GIT_CONFIG_PARAMETERS=\"'core.pager'='x'\" git log",
        "GIT_CONFIG_GLOBAL=/tmp/cfg git log",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "git_exec" in rules

    @pytest.mark.parametrize("command", [
        "git status",
        "git -c core.pager=cat log",
        "git -c pager.log=false log",
        "git -c color.ui=always log",
        "git -c user.name=x commit -m y",
        "git -c alias.lg=log lg",
        "git -c credential.helper= fetch",
        "git config core.pager",
        "git config --get core.sshCommand",
        "git config --unset core.hooksPath",
        "git config user.email me@example.com",
        "GIT_PAGER=cat git log",
        "GIT_EDITOR=true git rebase --continue",
        "GIT_CONFIG_GLOBAL=/dev/null git status",
    ])
    def test_not_floored(self, command):
        assert "git_exec" not in _floor(command)[1]

    def test_key_names_are_case_insensitive(self):
        assert git_exec.key_reason("CORE.SSHCOMMAND", "x")
        assert git_exec.key_reason("Diff.Tool.TextConv", "x")

    def test_global_gitconfig_write(self):
        assert sensitive_paths.check_protected_write("~/.gitconfig").floor == "git_exec"
        assert sensitive_paths.check_protected_write("~/.config/git/config").floor == "git_exec"
