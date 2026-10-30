"""Redirect targets read the variables and loop values set earlier in the command, as the words do."""

import pytest

from nuh import taxonomy
from nuh.bash import classify_command

pytestmark = pytest.mark.usefixtures("secrets_home")


@pytest.mark.parametrize("command", [
    "F=~/.ssh/authorized_keys; echo x > $F",
    'F=~/.ssh/authorized_keys; echo x >> "$F"',
    'D=~/.ssh; echo x > "$D/config"',
    'for f in ~/.ssh/*; do echo x > "$f"; done',
    'for d in src ~/.ssh; do > "$d/x"; done',          # no command: the target alone
])
def test_protected_target_blocks(command):
    assert classify_command(command).final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("command", [
    "F=out.txt; echo x > $F",
    'D="my dir"; echo x > "$D/out.txt"',               # one file, never split
    'for f in a b; do echo x > "out-$f.txt"; done',
])
def test_target_inside_allows(command):
    assert classify_command(command).final_decision == taxonomy.ALLOW


def test_every_loop_value_is_a_target():
    result = classify_command('for f in a b; do echo x > "out-$f.txt"; done')
    assert {sr.redirect_target for sr in result.all_stages() if sr.redirect_target} >= {"out-a.txt", "out-b.txt"}


def test_unresolved_value_asks():
    result = classify_command('F=$(date); echo x > "$F"')
    assert result.final_decision == taxonomy.ASK
    assert "uses $F, set in this command" in result.reason
