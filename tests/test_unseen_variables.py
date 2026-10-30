"""Variables neither set in the command nor in nuh's environment: unknown where a path goes, text elsewhere."""

import os

import pytest

from nuh import taxonomy
from nuh.bash import classify_command

NOT_SET = "not set in this command: a value nuh cannot resolve"


@pytest.fixture(autouse=True)
def env(secrets_home, monkeypatch):
    """No NOPE in the environment; SUB names the project's src, EMPTY is set and empty."""
    monkeypatch.delenv("NOPE", raising=False)
    monkeypatch.setenv("SUB", os.path.join(os.getcwd(), "src"))
    monkeypatch.setenv("EMPTY", "")
    return secrets_home


class TestPathUse:
    @pytest.mark.parametrize("command", [
        'cat "$NOPE"',
        'cat "$NOPE/a.txt"',
        'echo hi > "$NOPE"',
        'cp src/a.py "$NOPE"',
        'cp "$NOPE" out.txt',
        'wc -l src/a.py "$NOPE"',
        'cat "${NOPE%/*}/x"',                # an operator nuh does not compute
        'cat "${SUB%/*}/x"',                 # on a variable it sees too
    ])
    def test_asks_unknown(self, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "uses $" in result.reason and NOT_SET in result.reason
        assert result.floor_rules() == []

    @pytest.mark.parametrize("command,decision", [
        ('cat ~/.ssh/"$NOPE"', taxonomy.BLOCK),
        ('cat "$NOPE/.env"', taxonomy.ASK),
    ])
    def test_literal_part_still_decides(self, command, decision):
        result = classify_command(command)
        assert result.final_decision == decision
        assert result.floor_rules() != []

    def test_delete_keeps_its_floor(self):
        result = classify_command('rm -rf "$NOPE/"')
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["catastrophic"]


class TestSeenOrText:
    @pytest.mark.parametrize("command", [
        'cat "$SUB/a.py"',
        'wc -l "$SUB"/*.py',
        "echo $NOPE",
        'echo "built $NOPE" > out.txt',      # the redirect's target is the path, not echo's text
        'printf "%s\\n" "$NOPE/x"',
        "echo $?",
        "echo ${#NOPE}",
        "cat src/a.$UID",
        'curl -s "http://127.0.0.1:$NOPE/api"',
        'cd "$NOPE" && ls',
    ])
    def test_allowed(self, command):
        result = classify_command(command)
        assert "uses $" not in result.reason
        assert result.final_decision == taxonomy.ALLOW

    def test_program_text_variable_is_the_programs(self):
        result = classify_command("perl -e 'open(F, \"$dir/x\")'")
        assert not any("uses $dir" in sr.reason for sr in result.all_stages())

    def test_seen_variable_still_checked(self):
        assert classify_command('cat "$HOME/.ssh/id_rsa"').final_decision == taxonomy.BLOCK


class TestScriptPath:
    @pytest.mark.parametrize("command", ['bash "$NOPE/run.sh"', 'python3 "$NOPE"', 'source "$NOPE/activate"',
                                         '"$NOPE/run.sh" arg'])
    def test_unknown_script_asks(self, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "uses $NOPE, " + NOT_SET in result.reason

    def test_seen_variable_names_the_script(self, monkeypatch):
        os.makedirs("venv/bin")
        with open("venv/bin/activate", "w") as f:
            f.write("export VIRTUAL_ENV_PROMPT=venv\n")
        monkeypatch.setenv("VENV", os.path.join(os.getcwd(), "venv"))
        literal = classify_command("source venv/bin/activate")
        assert literal.final_decision == taxonomy.ALLOW
        for command in ('source "$VENV/bin/activate"', '. "${VENV}/bin/activate"', 'bash "$VENV/bin/activate"'):
            assert classify_command(command).final_decision == taxonomy.ALLOW, command

    def test_home_script_is_not_under_the_cwd(self, secrets_home):
        with open(os.path.join(secrets_home, "x.py"), "w") as f:
            f.write("print(1)\n")
        reason = classify_command("python3 ~/x.py").reason
        assert "outside project" in reason and "not found" not in reason


class TestDefault:
    @pytest.mark.parametrize("command,decision", [
        ("cat ${NOPE:-src/a.py}", taxonomy.ALLOW),
        ("cat ${NOPE-src/a.py}", taxonomy.ALLOW),
        ("cat ${NOPE:-~/.ssh/id_rsa}", taxonomy.BLOCK),
        ("cat ${EMPTY:-~/.ssh/id_rsa}", taxonomy.BLOCK),   # set but empty: :- takes the default
        ("cat ${SUB:-~/.ssh/id_rsa}/a.py", taxonomy.ALLOW),  # set: its value, not the default
        ("cat ${HOME:-/x}/.ssh/id_rsa", taxonomy.BLOCK),
    ])
    def test_value_set(self, command, decision):
        assert classify_command(command).final_decision == decision

    def test_default_not_literal_asks(self):
        result = classify_command("cat ${NOPE:-$OTHER}")
        assert result.final_decision == taxonomy.ASK
        assert NOT_SET in result.reason
