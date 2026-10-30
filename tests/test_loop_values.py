"""for-loop lists nuh expands (globs, ~, braces, known variables) and the ones it cannot (shell_loops.values)."""

import os

import pytest

from nuh import shell_loops, shell_state, shell_walk, taxonomy
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages

UNRESOLVED = "set in this command to a value nuh cannot resolve"
pytestmark = pytest.mark.usefixtures("secrets_home")


def _decision(command: str) -> str:
    return classify_command(command).final_decision


class TestExpandedLists:
    @pytest.mark.parametrize("command", [
        'for f in ~/.ssh/*; do cat "$f"; done',
        'for f in ~/.aws/*; do cat "$f"; done',
        'for f in $HOME/.ssh/*; do cat "$f"; done',
        'for f in ~/.ssh/{config,id_rsa}; do cat "$f"; done',
        'for f in ~/.ssh/*.nomatch; do cat "$f"; done',   # no match: the word as written, still in ~/.ssh
        'for f in src/a.py ~/.ssh/i*; do cat "$f"; done',
        'for d in src ~/.ssh; do for f in $d/*; do cat "$f"; done; done',
        'cd ~/.ssh && for f in *; do cat "$f"; done',
    ])
    def test_a_protected_value_blocks_like_the_literal(self, command):
        assert _decision("cat ~/.ssh/id_rsa") == taxonomy.BLOCK
        assert _decision(command) == taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        'for f in src/*.py; do wc -l "$f"; done',
        'for f in src/{a,b}.py; do wc -l "$f"; done',
        'for f in src/*.rs; do cat "$f"; done',          # no match: the word as written
        'for d in src; do for f in $d/*.py; do cat "$f"; done; done',
        'for f in ~/.ssh/*; do echo "$f"; done',          # text, not a path
    ])
    def test_values_inside_allow(self, command):
        assert _decision(command) == taxonomy.ALLOW

    def test_every_match_is_a_value(self):
        result = classify_command('for f in src/*.py; do wc -l "$f"; done')
        checked = {tuple(sr.tokens) for sr in result.all_stages()}
        assert {("wc", "-l", "src/a.py"), ("wc", "-l", "src/b.py")} <= checked


class TestUnknownLists:
    @pytest.mark.parametrize("command", [
        'for f in $(cat list); do cat "$f"; done',
        'for f in `cat list`; do cat "$f"; done',
        'for f in $NUH_TEST_UNSET; do cat "$f"; done',
        'for f in "$@"; do cat "$f"; done',
        'for f in src/**/*.py; do cat "$f"; done',
        'for f in ~+/x; do cat "$f"; done',
        'for f in many/*; do cat "$f"; done',           # over the cap
        'f=src/a.py; for f in $(cat list); do cat "$f"; done',
    ])
    def test_path_use_asks(self, monkeypatch, command):
        monkeypatch.delenv("NUH_TEST_UNSET", raising=False)
        os.makedirs("many")
        for i in range(300):
            open(os.path.join("many", f"f{i}"), "w").close()
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "uses $f, " + UNRESOLVED in result.reason
        assert result.floor_rules() == []

    def test_text_use_allows(self):
        assert _decision('for f in $(cat list); do echo "$f"; done') == taxonomy.ALLOW

    def test_literal_part_still_decides(self):
        assert _decision('for f in $(ls ~/.ssh); do cat ~/.ssh/$f; done') == taxonomy.BLOCK


class TestRead:
    def test_read_names_are_opaque(self):
        stages = _parse_stages("while read -r f; do cat \"$f\"; done < list")
        steps = shell_walk.walk(stages, shell_state.State(cwd=os.getcwd()))
        assert "f" in steps[1].state.opaque

    def test_path_use_of_a_read_name_asks(self):
        result = classify_command('while read f; do cat "$f"; done < list.txt')
        assert result.final_decision == taxonomy.ASK
        assert any("uses $f, " + UNRESOLVED in sr.reason for sr in result.all_stages())


class TestValues:
    def _values(self, words, known=None, cwd=None):
        return shell_loops.values(tuple(words), (known or {}).get, cwd)

    def test_braces_nested_and_ranges(self):
        assert self._values(["x{a,{b,c}}", "{1..3}", "{3..1}"]) == ("xa", "xb", "xc", "1", "2", "3")

    def test_variable_with_whitespace_gives_whole_and_pieces(self):
        assert self._values(["$L"], {"L": "a b"}) == ("a b", "a", "b")

    def test_braces_as_both_shells_expand_them(self):
        # Letter and zero-padded sequences expand; {a} has no comma or range, so it is literal.
        assert self._values(["{a..c}", "{01..3}", "{a}"]) == ("a", "b", "c", "01", "02", "03", "{a}")

    @pytest.mark.parametrize("word", ["{a..e..2}", "{a,b", "${L%x}", "$1", "a~b", "~-", "@(a|b)", "=ls"])
    def test_unreadable_forms(self, word):
        assert self._values([word], {"L": "a"}) is None

    def test_relative_glob_needs_a_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        assert self._values(["*.txt"], cwd=str(tmp_path)) == ("a.txt",)
        assert self._values(["*.txt"]) is None
