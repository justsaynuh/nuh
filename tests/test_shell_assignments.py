"""Variable assignments as bash and zsh make them: builtins, prefixes, precommands, and what a new shell inherits.

Every expectation here was checked against bash 5.2 and zsh 5.9 with an
argv-printing stub (the value each shell hands ``cat``).
"""

import os

import pytest

from nuh import shell_state, shell_walk, taxonomy
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages

KEY = "$HOME/.ssh/id_rsa"
SAFE = "src/a.py"


@pytest.fixture(autouse=True)
def home(secrets_home, monkeypatch):
    """A project with src/a.py; a temporary HOME whose ~/.ssh blocks. INHERITED is in the environment."""
    monkeypatch.setenv("INHERITED", "src")
    monkeypatch.delenv("X", raising=False)
    monkeypatch.delenv("Y", raising=False)
    return secrets_home


def decision(command: str) -> str:
    return classify_command(command).final_decision


class TestSetterExpandsFirst:
    """``export X=new Y=$X``: every argument is expanded before the builtin assigns any (Y gets X's old value)."""

    @pytest.mark.parametrize("command", [
        f'X={KEY}; export X={SAFE} Y=$X; cat "$Y"',
        f'X={KEY}; declare X={SAFE} Y=$X; cat "$Y"',
        f'X={KEY}; typeset X={SAFE} Y=$X; cat "$Y"',
        f'X={KEY}; readonly X={SAFE} Y=$X; cat "$Y"',
        f'X={KEY}; export X={SAFE} Y="$X"; cat "$Y"',
        f'typeset X={KEY}; typeset X={SAFE} Y=$X; cat "$Y"',
        f'X={KEY}; export X={{a,{SAFE}}} Y=$X; cat "$Y"',
    ])
    def test_old_value_is_read(self, command):
        assert decision(command) == taxonomy.BLOCK

    def test_ansi_c_value_is_literal(self):
        # ANSI-C quotes decode escapes, but $HOME stays literal even through the setter.
        # bash/zsh argv evidence: cat receives "$HOME/.ssh/id_rsa", so only its basename asks.
        assert decision(f"X={SAFE}; Y=$'{KEY}'; export Y={SAFE} X=$Y; cat \"$X\"") == taxonomy.ASK

    @pytest.mark.parametrize("command", [
        f'X={KEY}; X={SAFE} Y=$X; cat "$Y"',   # plain assignments: left to right in both shells
        f'A={SAFE} B=$A; cat "$B"',
        'A=src B=$A; ls "$B"',
    ])
    def test_plain_assignments_run_left_to_right(self, command):
        assert decision(command) == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        f'X={SAFE}; declare -x X={KEY}; cat "$X"',
        f'X={SAFE}; typeset -r X={KEY}; cat "$X"',
        f'X={SAFE}; declare -g X={KEY}; cat "$X"',
        f'S=export; $S X={KEY}; cat "$X"',     # the builtin's name from a variable is still the builtin
    ])
    def test_setter_forms_assign(self, command):
        assert decision(command) == taxonomy.BLOCK

    def test_option_nuh_does_not_model_leaves_the_value_unknown(self):
        result = classify_command(f'X={SAFE}; declare -i X=1; cat "$X"')
        assert result.final_decision == taxonomy.ASK


class TestPrecommands:
    """``builtin export`` assigns in both shells; ``command export`` only in bash; ``noglob export`` only in zsh."""

    @pytest.mark.parametrize("command", [
        f'X={SAFE}; builtin export X={KEY}; cat "$X"',
        f'X={SAFE}; builtin readonly X={KEY}; cat "$X"',
        f'X={SAFE}; builtin declare X={KEY}; cat "$X"',
        f'X={SAFE}; time export X={KEY}; cat "$X"',
        f'X={SAFE}; command export X={KEY}; cat "$X"',
        f'X={SAFE}; command declare X={KEY}; cat "$X"',
        f'X={SAFE}; command typeset X={KEY}; cat "$X"',
        f'X={SAFE}; command -p export X={KEY}; cat "$X"',
        f'X={SAFE}; noglob export X={KEY}; cat "$X"',
    ])
    def test_assignment_through_precommand_is_seen(self, command):
        assert decision(command) == taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        f'X={SAFE}; {{ command export X={KEY}; }}; cat "$X"',
        f'X={SAFE}; {{ builtin export X={KEY}; }}; cat "$X"',
    ])
    def test_in_a_group_it_may_not_have_run(self, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "$X" in result.reason

    def test_both_readings_are_kept(self):
        stages = _parse_stages("X=a; command export X=b; cat $X")
        state = shell_walk.walk(stages, shell_state.State(cwd=os.getcwd()))[2].state
        assert {dict(row)["X"] for row in state.rows} == {"a", "b"}

    def test_same_value_in_both_shells_stays_plain(self):
        assert decision(f'X={SAFE}; command export X=src/b.py; cat "$X"') == taxonomy.ALLOW


class TestPrefixBeforeBuiltin:
    """``X=v export X`` keeps v in bash, not in zsh; POSIX sh keeps any prefix before a special builtin."""

    @pytest.mark.parametrize("command", [
        f'X={SAFE}; X={KEY} export X; cat "$X"',
        f'X={SAFE}; X={KEY} readonly X; cat "$X"',
        f'X={SAFE}; X={KEY} builtin export X; cat "$X"',
        f'X={SAFE}; X={KEY} command export X; cat "$X"',
        f'X={SAFE}; X={KEY} export Y=$X; cat "$Y"',    # zsh expands Y=$X after the prefix
        f'X={SAFE}; X={KEY} :; cat "$X"',
    ])
    def test_prefix_value_may_stay(self, command):
        assert decision(command) == taxonomy.BLOCK

    def test_prefix_before_a_program_does_not_stay(self):
        assert decision(f'X={SAFE}; X={KEY} true; cat "$X"') == taxonomy.ALLOW


class TestNestedShell:
    """A prefix or ``env`` assignment reaches the shell or eval'd text the command runs."""

    @pytest.mark.parametrize("command", [
        f"X={SAFE}; X={KEY} bash -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} zsh -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} sh -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} eval 'cat \"$X\"'",
        f"X={SAFE}; env X={KEY} sh -c 'cat \"$X\"'",
        f"env X={KEY} sh -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} nohup bash -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} time bash -c 'cat \"$X\"'",
        f"X={SAFE}; nohup env X={KEY} bash -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} command bash -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} sudo -E bash -c 'cat \"$X\"'",
        f"X={SAFE}; sudo X={KEY} bash -c 'cat \"$X\"'",
        f"X={SAFE}; X={KEY} bash <<'EOF'\ncat \"$X\"\nEOF",
        f"X={SAFE}; env X={KEY} bash <<'EOF'\ncat \"$X\"\nEOF",
    ])
    def test_inner_script_sees_the_assignment(self, command):
        assert decision(command) == taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        f"export X={KEY}; bash -c 'cat \"$X\"'",
        f"X={KEY}; export X; bash <<'EOF'\ncat \"$X\"\nEOF",
    ])
    def test_exported_value_reaches_the_new_shell(self, command):
        assert decision(command) == taxonomy.BLOCK

    def test_unexported_value_may_not_reach_it(self):
        # bash and zsh both run `rm -rf /`: X is not exported, so the new shell has none.
        assert decision("X=src; bash <<'EOF'\nrm -rf \"$X\"/\nEOF") == taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        "export X=src; bash <<'EOF'\nrm -rf \"$X\"/\nEOF",
        "INHERITED=src; bash <<'EOF'\nrm -rf \"$INHERITED\"/\nEOF",   # in the environment: assigning changes it
        f"X={SAFE}; bash -c 'cat \"$X\"'",
    ])
    def test_value_that_reaches_it_is_judged(self, command):
        assert decision(command) == taxonomy.ALLOW

    def test_env_unset_empties_it(self):
        assert decision("export X=src; env -u X bash <<'EOF'\nrm -rf \"$X\"/\nEOF") == taxonomy.BLOCK


class TestScopes:
    @pytest.mark.parametrize("command", [
        f'X={SAFE}; (X={KEY}); cat "$X"',
        f'X={SAFE}; (export X={KEY}); cat "$X"',
        'X=src; ls "$X"',
        'export X=src; ls "$X"',
    ])
    def test_allowed(self, command):
        assert decision(command) == taxonomy.ALLOW

    def test_unset_empties_the_variable(self):
        assert decision('X=src; unset X; rm -rf "$X"/') == taxonomy.BLOCK
