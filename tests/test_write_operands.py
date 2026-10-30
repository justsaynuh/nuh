"""The project boundary of a write or delete judges every operand the command modifies.

Before, only the last path operand was judged: ``rm ~/notes.txt ./x`` was
allowed as "inside project: ./x". ``file_operands.modified_files`` says which
operands a command modifies (every one for rm, mv, chmod, touch; the
destination for cp, ln, install) and the strictest of them decides.
"""

import pytest

from nuh import file_operands, taxonomy
from nuh.bash import classify_command


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    return project_root


class TestBoundary:
    @pytest.mark.parametrize("command,outside", [
        ("rm ./x ~/notes.txt", "notes.txt"),
        ("rm ~/notes.txt ./x", "notes.txt"),
        ("rm /etc/foo ./x", "/etc/foo"),
        ("rm -- ~/a ./b", "~/a"),
        ("chmod 777 ~/bin/tool ./x", "tool"),
        ("chmod 777 ./a ~/b", "~/b"),
        ("chmod -w ~/b ./a", "~/b"),
        ("touch /opt/x ./y", "/opt/x"),
        ("truncate -s 0 ~/f ./g", "~/f"),
        ("mkdir ~/d ./e", "~/d"),
        ("tee ~/t ./u", "~/t"),
        ("mv ~/notes.txt ./x", "notes.txt"),
        ("mv -t ./out ~/a", "~/a"),
        ("cp -t /opt/out ./a", "/opt/out"),
        ("cp --target-directory=/opt/out ./a", "/opt/out"),
        ("cp -t/opt/out ./a", "/opt/out"),
        ("install -d /opt/n ./x", "/opt/n"),
        ("install -dm 755 /opt/n ./x", "/opt/n"),
        ("install /opt/n ./x -d", "/opt/n"),
        ("install --directory /opt/n ./x", "/opt/n"),
        ("install --dir /opt/n ./x", "/opt/n"),
        ("chgrp staff /opt/x ./y", "/opt/x"),
        ("chgrp staff ./y /opt/x", "/opt/x"),
        ("chattr +i /opt/x ./y", "/opt/x"),
        ("chattr -i /opt/x ./y", "/opt/x"),
        ("chattr -R -v 2 =ai ./y /opt/x", "/opt/x"),
        ("setfacl -m u:bob:rw /opt/x ./y", "/opt/x"),
        ("setfacl -R --modify=u:bob:rw ./y /opt/x", "/opt/x"),
        ("setfacl -b /opt/x", "/opt/x"),
    ])
    def test_an_operand_outside_the_project_asks(self, in_project, command, outside):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "outside project" in result.reason
        assert outside in result.reason

    @pytest.mark.parametrize("command", [
        "chmod 644 ./a ./b",
        "rm -rf ./a ./b",
        "cp ~/notes.txt ./x",
        "cp -t ./out ~/a",
        "ln -s ~/a ./b",
        "install -m 644 ~/a ./b",
        "touch -r ~/ref ./x",
        "chown me:staff ./a ./b",
        "install -d ./a ./b",
        "install -m d ~/a ./b",     # d is -m's mode value, not -d: ~/a is only read
        "install ~/a ./b -- -d",    # after --, -d is a file
        "chgrp staff ./a ./b",
        "chattr +i ./a ./b",
        "chattr -i ./a",
        "setfacl -m u:bob:rw ./a ./b",
        "setfacl -M ~/acl.txt ./a",  # the spec file is only read
    ])
    def test_only_read_operands_outside_the_project_allow(self, in_project, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ALLOW
        assert "inside project" in result.reason


class TestModifiedFiles:
    @pytest.mark.parametrize("tokens,expected", [
        (["rm", "-rf", "a", "b"], ["a", "b"]),
        (["rm", "--", "-f", "b"], ["-f", "b"]),
        (["chmod", "-R", "755", "a", "b"], ["a", "b"]),
        (["chmod", "-w", "a", "b"], ["a", "b"]),
        (["chown", "--reference=r", "a"], ["a"]),
        (["touch", "-r", "ref", "-d", "now", "a"], ["a"]),
        (["mv", "a", "b", "dir"], ["a", "b", "dir"]),
        (["mv", "-t", "dir", "a", "b"], ["dir", "a", "b"]),
        (["cp", "a", "b", "dir"], ["dir"]),
        (["cp", "-t", "dir", "a", "b"], ["dir"]),
        (["cp", "-vtdir", "a"], ["dir"]),
        (["cp", "--target-directory=dir", "a"], ["dir"]),
        (["cp", "-S", "-t", "a", "b"], ["b"]),
        (["install", "-m", "644", "a", "b"], ["b"]),
        (["install", "-d", "a", "b"], ["a", "b"]),
        (["install", "-Dm644", "a", "b"], ["b"]),     # -D makes leading directories of the destination only
        (["install", "-vdm", "755", "a", "b"], ["a", "b"]),
        (["install", "--mode=d", "a", "b"], ["b"]),
        (["chgrp", "-R", "staff", "a", "b"], ["a", "b"]),
        (["chattr", "-R", "-i", "a"], ["a"]),
        (["chattr", "-p", "7", "+P", "a"], ["a"]),
        (["setfacl", "-x", "u:bob", "a", "b"], ["a", "b"]),
        (["setfacl", "--set-file", "spec", "a"], ["a"]),
        (["rm"], []),
    ])
    def test_modified_files(self, tokens, expected):
        assert file_operands.modified_files(tokens) == expected

    def test_command_outside_the_table_is_none(self):
        assert file_operands.modified_files(["cat", "a", "b"]) is None
