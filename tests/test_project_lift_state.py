"""In-project Bash under project_lifts_sensitive: variables nuh cannot resolve, ls output, loop bodies, arithmetic."""

import pytest

from nuh import shell_arith, taxonomy
from nuh.bash import classify_command

UNRESOLVED = "set in this command to a value nuh cannot resolve"


def _run(command: str, project):
    other = project.parent.parent / "Other"
    return classify_command(command.replace("{P}", str(project)).replace("{O}", str(other)))


class TestUnresolvedVariable:
    @pytest.mark.parametrize("command", [
        'W=$(echo a); sed -n 1p "$W"',
        'W=$(echo a); for r in 1 2; do sed -n "${r}p" "$W" | cut -c1-9; done',
        'W=$(echo a); grep -n x "$W/a.txt"',
        'W=$(echo a); echo x > "$W"',
        'W=$(echo a); cp notes.txt "$W"',
        'W=$(echo a); rm -rf "$W"',
    ])
    def test_unknown_not_a_sensitive_file_in_the_cwd(self, project, command):
        result = _run(command, project)
        assert result.final_decision == taxonomy.ASK
        assert "uses $W, " + UNRESOLVED in result.reason
        assert result.floor_rules() == []

    def test_same_in_a_sensitive_cwd_outside_the_project(self, project, monkeypatch):
        monkeypatch.chdir(project.parent.parent / "Other")
        result = classify_command('W=$(echo a); cat "$W"')
        assert "uses $W, " + UNRESOLVED in result.reason
        assert result.floor_rules() == []

    @pytest.mark.parametrize("command,rule", [
        ('W=$(echo a); cat "{O}/$W"', "sensitive_paths"),
        ('W=$(echo a); cat "$W/.env"', "credential_paths"),
        ('W=$(echo a); echo x > "$W/.git/hooks/pre-commit"', "git_exec"),
    ])
    def test_what_the_rest_of_the_word_names_still_counts(self, project, command, rule):
        result = _run(command, project)
        assert result.final_decision == taxonomy.ASK
        assert rule in result.floor_rules()


class TestLsSubstitution:
    @pytest.fixture
    def text(self, project):
        """guidelines/_text with one PRINT file per book, two in a third, and a directory."""
        root = project / "guidelines" / "_text"
        for book, names in (("wb", ["wb_PRINT.txt"]), ("hb", ["hb_PRINT.txt", ".hidden"]),
                            ("two", ["a_PRINT.txt", "b_PRINT.txt"])):
            (root / book).mkdir(parents=True)
            for name in names:
                (root / book / name).write_text("x\n")
        (root / "dir_PRINT.txt").mkdir()
        return root

    @pytest.mark.parametrize("command", [
        'W=$(ls wb/*PRINT.txt); H=$(ls hb/*PRINT.txt); for r in 1 2; do sed -n "${r}p" "$W"; done; grep -n x "$H"',
        'W=`ls wb/wb_PRINT.txt`; cat $W',
        'W=$(ls -1 wb/w?_PRINT.txt); cat "$W"',
        'D=$(ls -d dir_*); ls "$D"',
    ])
    def test_one_match_is_the_value(self, text, command):
        result = classify_command(f'cd "{text}" && {command}')
        assert result.final_decision == taxonomy.ALLOW

    def test_one_match_outside_is_checked(self, text):
        result = classify_command(f'cd "{text}" && W=$(ls ../../../Other/*.txt); cat "$W"')
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]

    @pytest.mark.parametrize("command", [
        'W=$(ls two/*PRINT.txt); cat "$W"',      # two matches: one word with a newline
        'W=$(ls none/*.txt); cat "$W"',           # no match: empty
        'W=$(ls wb/*print.txt); cat "$W"',        # matches only under nocaseglob
        'W=$(ls hb/*); cat "$W"',                 # hb_PRINT.txt, or .hidden as well under dotglob
        'W=$(ls dir_*); cat "$W"',                # a directory: ls lists what is in it
        'W=$(ls -l wb/*PRINT.txt); cat "$W"',
        'W=$(ls "wb/*PRINT.txt"); cat "$W"',
        'W=$(ls wb/[w]b_PRINT.txt); cat "$W"',
        'W=$(ls wb/*PRINT.txt hb/*PRINT.txt); cat "$W"',
        'D=$(echo wb); W=$(ls $D/*.txt); cat "$W"',
    ])
    def test_anything_else_stays_unresolved(self, text, command):
        result = classify_command(f'cd "{text}" && {command}')
        assert result.final_decision == taxonomy.ASK
        assert UNRESOLVED in result.reason
        assert result.floor_rules() == []

    def test_empty_output_still_reaches_root(self, text):
        result = classify_command(f'cd "{text}" && W=$(ls none/*.txt); rm -rf $W/')
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["catastrophic"]


class TestLoopBody:
    @pytest.fixture
    def shared_docs(self, project):
        for name in ("A", "B"):
            (project / "Shared Docs" / name).mkdir(parents=True)
            (project / "Shared Docs" / name / f"{name}_x.pdf").write_text("%PDF\n")
        return project

    @pytest.mark.parametrize("command", [
        "H=notes.txt; for n in 1018 1034; do s=$((n-40)); awk -v s=$s -v e=$n 'NR>=s && NR<=e' \"$H\"; done",
        'D="Shared Docs"; for p in A B; do f="$D/$p/${p}_x.pdf"; echo "$p | $(wc -c "$f") | $(head -c4 "$f")"; done',
        'for p in A B; do f="Shared Docs/$p"; g="$f/${p}_x.pdf"; cat "$g"; done',
        'for p in A B\ndo\n  f="Shared Docs/$p/${p}_x.pdf"\n  cat "$f"\ndone',
        'for i in 1 2; do sed -n "$((i*10)),$((i*10+5))p" notes.txt; done',
    ])
    def test_assignment_seen_by_later_commands_in_the_body(self, shared_docs, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_values_stay_paired_with_their_loop_value(self, shared_docs):
        # Both pairings stay inside, so the decision cannot show a lost pairing: check the readings.
        result = classify_command('for p in A B; do f="Shared Docs/$p/${p}_x.pdf"; q=$p; cat "$f" "$q"; done')
        readings = {tuple(sr.tokens) for sr in result.stages[-1].walk() if sr.tokens and sr.tokens[0] == "cat"}
        assert ("cat", "Shared Docs/A/A_x.pdf", "A") in readings
        assert ("cat", "Shared Docs/B/B_x.pdf", "B") in readings
        assert not any("A" in tokens[-1] and "B_x" in tokens[-2] for tokens in readings)
        # The value holds a space, but "$f" is quoted: never split.
        assert not any("Shared" in tokens for tokens in readings)
        # Unquoted, it is read split as well as joined, each piece still paired with its loop value.
        result = classify_command('for p in A B; do f="Shared Docs/$p/${p}_x.pdf"; cat $f; done')
        readings = {tuple(sr.tokens) for sr in result.stages[-1].walk() if sr.tokens and sr.tokens[0] == "cat"}
        assert {("cat", "Shared", "Docs/A/A_x.pdf"), ("cat", "Shared", "Docs/B/B_x.pdf")} <= readings
        assert ("cat", "Shared", "Docs/A/B_x.pdf") not in readings

    def test_a_value_outside_is_checked(self, shared_docs):
        result = classify_command('for p in A ../../Other; do f="Shared Docs/$p/notes.txt"; cat "$f"; done')
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]

    @pytest.mark.parametrize("command,name", [
        ('for p in A B; do f="Shared Docs/$p/${p}_x.pdf"; done; cat "$f"', "f"),          # after: may not have run
        ('for p in A B; do cat "$f"; f="Shared Docs/$p/${p}_x.pdf"; done', "f"),          # the last iteration's
        ('for p in A B; do if true; then f="Shared Docs/$p"; fi; cat "$f/x"; done', "f"),  # nested: may not run
        ('for n in 1 2; do s=$((n+x)); cat "$s"; done', "s"),                          # x unknown
        ('for n in 1 2; do s=$((n/0)); cat "$s"; done', "s"),
        ('s=$((08+1)); cat "$s"', "s"),                                                # octal in bash
        ('for p in A; do f="Shared Docs/A"; done; for q in B; do cat "$f/x"; done', "f"),  # a sibling loop is after
        ('for p in A; do f="Shared Docs/A"; done; if true; then cat "$f/x"; fi', "f"),
    ])
    def test_anything_uncertain_stays_unresolved(self, shared_docs, command, name):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert f"uses ${name}, " + UNRESOLVED in result.reason
        assert result.floor_rules() == []

    def test_definite_assignment_before_the_loop_still_known(self, shared_docs):
        command = 'f="Shared Docs/A/A_x.pdf"; for p in A B; do echo $p; done; cat "$f"'
        assert classify_command(command).final_decision == taxonomy.ALLOW


class TestArithmetic:
    @pytest.mark.parametrize("expr,values,expected", [
        ("n-40", {"n": "1018"}, 978), ("$n - 40", {"n": "1018"}, 978), ("(1+2)*3", {}, 9), ("-7/2", {}, -3),
        ("-7%2", {}, -1), ("7%-2", {}, 1), ("+5", {}, 5), ("0", {}, 0),
        ("08", {}, None), ("x+1", {}, None), ("1/0", {}, None), ("a=1", {"a": "2"}, None), ("1<2", {}, None),
        ("n+1", {"n": "a/b"}, None), ("(1+2", {}, None), ("1 2", {}, None),
    ])
    def test_evaluate(self, expr, values, expected):
        assert shell_arith.evaluate(expr, values.get) == expected

    def test_expand(self):
        assert shell_arith.expand("p$((i*2)).txt", {"i": "3"}.get) == "p6.txt"
        assert shell_arith.expand("$((i))-$((j))", {"i": "1"}.get) is None
        assert shell_arith.names("x$((a+$b*c))y") == {"a", "b", "c"}
