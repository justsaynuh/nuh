"""Brace expansion before every path check (shell_braces, bash_decompose).

The shell expands ``{a,b}`` before it reads a path, so ``cat {,~/.aws/credentials}``
is ``cat ~/.aws/credentials``. Each brace word must come out exactly as its
pieces written out, and a brace form nuh cannot read asks.
"""

import pytest

from nuh import taxonomy
from nuh.bash import classify_command

pytestmark = pytest.mark.usefixtures("secrets_home")
UNREAD = "brace expansion nuh cannot read"


def _verdict(command: str) -> tuple[str, str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.reason, result.floor_rules()


# (brace form, the same command with every piece written out)
SAME_AS_WRITTEN_OUT = [
    ("cat {,~/.aws/credentials}", "cat ~/.aws/credentials"),
    ("cat {a,~/.ssh/id_rsa}", "cat a ~/.ssh/id_rsa"),
    ("cp {a,~/.ssh/id_rsa} /tmp/x", "cp a ~/.ssh/id_rsa /tmp/x"),
    ("cat {~/.ssh,x}/id_rsa", "cat ~/.ssh/id_rsa x/id_rsa"),
    ("cat ~/.{ssh,aws}/credentials", "cat ~/.ssh/credentials ~/.aws/credentials"),
    ("cat {a,{b,~/.ssh/id_rsa}}", "cat a b ~/.ssh/id_rsa"),
    ("cat $HOME{/.ssh/id_rsa,}", "cat $HOME/.ssh/id_rsa $HOME"),
    ("sudo cat {a,~/.ssh/id_rsa}", "sudo cat a ~/.ssh/id_rsa"),
    ("sh -c 'cat {a,~/.ssh/id_rsa}'", "sh -c 'cat a ~/.ssh/id_rsa'"),
    ('bash -c "cat {,~/.aws/credentials}"', 'bash -c "cat ~/.aws/credentials"'),
    ("{cat,~/.ssh/id_rsa}", "cat ~/.ssh/id_rsa"),
    ("rm -rf {x,~}", "rm -rf x ~"),
    ("rm -rf {,/}", "rm -rf /"),
    ("rm -rf {x,{y,~}}", "rm -rf x y ~"),
    ("rm -rf {x,~/}", "rm -rf x ~/"),
    ("{rm,-rf,~}", "rm -rf ~"),
    ("chmod -R 777 {x,/}", "chmod -R 777 x /"),
    ("tee {x,/etc}/passwd", "tee x/passwd /etc/passwd"),
    ("cp a {x,/etc}/passwd", "cp a x/passwd /etc/passwd"),
    ("cp a {b,~/.claude/settings.json}", "cp a b ~/.claude/settings.json"),
    # zsh writes every file a brace redirect names (multios); bash refuses more than one.
    ("echo x > {a,/etc/passwd}", "echo x > a > /etc/passwd"),
    ("echo x > {/etc,x}/passwd", "echo x > /etc/passwd > x/passwd"),
    ("echo x > {a,~/.claude/settings.json}", "echo x > a > ~/.claude/settings.json"),
    ("echo x >> {a,~/.bashrc}", "echo x >> a >> ~/.bashrc"),
    ("echo x > {,b}", "echo x > b"),
    ("mkdir -p src/{a,b}", "mkdir -p src/a src/b"),
    ("cp src/a.py{,.bak}", "cp src/a.py src/a.py.bak"),
    ("ls src/{a,b}.py", "ls src/a.py src/b.py"),
    ("cat a{1..3}", "cat a1 a2 a3"),
]


@pytest.mark.parametrize("braced,written", SAME_AS_WRITTEN_OUT)
def test_a_brace_word_is_checked_as_its_pieces(braced, written):
    assert _verdict(braced) == _verdict(written)


# Under secrets_home's config (~/.ssh and ~/.aws block, the ~/.aws entry its own); the floors with no
# config at all, agent_hooks included, are in test_floor_config_proof.py.
@pytest.mark.parametrize("command,decision,rule", [
    ("cat {,~/.aws/credentials}", taxonomy.BLOCK, "sensitive_paths"),
    ("cat {a,~/.ssh/id_rsa}", taxonomy.BLOCK, "credential_paths"),
    ("cp {a,~/.ssh/id_rsa} /tmp/x", taxonomy.BLOCK, "credential_paths"),
    ("cat {~/.ssh,x}/id_rsa", taxonomy.BLOCK, "credential_paths"),
    ("rm -rf {x,~}", taxonomy.BLOCK, "catastrophic"),
    ("rm -rf {,/}", taxonomy.BLOCK, "catastrophic"),
    ("{rm,-rf,~}", taxonomy.BLOCK, "catastrophic"),
    ("echo x > {a,/etc/passwd}", taxonomy.ASK, ""),
    ("tee {x,/etc}/passwd", taxonomy.ASK, ""),
])
def test_the_hidden_piece_decides(command, decision, rule):
    found, _reason, rules = _verdict(command)
    assert found == decision
    assert rule in rules if rule else not rules


@pytest.mark.parametrize("command", [
    "mkdir -p src/{a,b}",
    "cp src/a.py{,.bak}",
    "ls src/{a,b}.py",
    "ls {a,b}.txt",
    'echo "{a,~/.ssh/id_rsa}"',                   # quoted: one literal word, printed
    "echo '{a,~/.ssh/id_rsa}'",
    "find . -name x -exec rm {} +",
    "git show HEAD@{1}",
    "echo x > {a,b}",
])
def test_ordinary_braces_still_allow(command):
    assert classify_command(command).final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("command", ['cat "{a,~/.ssh/id_rsa}"', r"cat \{a,~/.ssh/id_rsa\}"])
def test_quoted_braces_are_one_literal_path(command):
    """``{a,~`` is a directory name here, not ``~``: the word names no file in ~/.ssh."""
    result = classify_command(command)
    assert result.final_decision != taxonomy.BLOCK
    assert UNREAD not in result.reason


@pytest.mark.parametrize("command", [
    "cat {a,b",
    "cat a,b}",
    "cat {1..1000}",
    "ls {a..e..2}",
    "echo ${x:-{a,b}}",
    "X=a; cat $X{1,2}",
    "echo x > {a,b",
])
def test_braces_nuh_cannot_read_ask(command):
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert any(UNREAD in sr.reason for sr in result.all_stages())


@pytest.mark.parametrize("command,rule", [
    ("rm -rf ~ {a,b", "catastrophic"),
    ("cat ~/.ssh/id_rsa {a,", "credential_paths"),
])
def test_an_unread_word_keeps_the_floors_on_the_rest(command, rule):
    result = classify_command(command)
    assert result.final_decision == taxonomy.BLOCK
    assert rule in result.floor_rules()


@pytest.mark.parametrize("command", ["X={a,b true", "cat <<< {a,b"])
def test_assignment_and_here_string_stay_as_written(command):
    """Neither shell brace-expands a leading assignment or a here-string, so their braces are just text."""
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW
    assert not any(UNREAD in sr.reason for sr in result.all_stages())
