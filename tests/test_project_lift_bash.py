"""Ordinary in-project Bash under project_lifts_sensitive: words that are not paths, and paths that stay inside."""

import os

import pytest

from nuh import file_operands, operands, shell_loops, taxonomy
from nuh.bash import classify_command


def _decide(command: str, project) -> str:
    other = project.parent.parent / "Other"
    return classify_command(command.replace("{P}", str(project)).replace("{O}", str(other))).final_decision


class TestScriptsAreNotPaths:
    @pytest.mark.parametrize("command", [
        r"sed 's/^ *\([0-9]*\)/x/' notes.txt",
        "sed -n -e 's|a/b|c|' notes.txt",
        "sed -i.orig-file 's/a/b/' notes.txt",
        r"""awk -F'\t' '{split($1,a,"+"); printf "%d\t%s\n", a[1]+a[2], $2}' notes.txt""",
        "grep -e '../../x' notes.txt",
        "grep -A 3 'a/b' notes.txt",
        "rg -g '*.txt' 'x/y' .",
    ])
    def test_script_or_pattern_in_project_allowed(self, project, command):
        assert _decide(command, project) == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        "sed -f {O}/x.sed notes.txt",
        "sed 's/a/b/' {O}/notes.txt",
        "awk -f {O}/p.awk notes.txt",
        "grep -rn 'a/b' {O}",
        "rg -e x {O}",
    ])
    def test_script_file_and_operands_still_checked(self, project, command):
        assert _decide(command, project) == taxonomy.ASK

    def test_sed_write_command_target_checked(self, project):
        assert _decide("sed 's/a/b/w {O}/out' notes.txt", project) == taxonomy.ASK
        home_ssh = os.path.expanduser("~/.ssh/authorized_keys")
        assert _decide(f"sed -n 'w {home_ssh}' notes.txt", project) == taxonomy.BLOCK

    def test_sed_write_inside_project_allowed(self, project):
        assert _decide("sed -n '/x/w out.txt' notes.txt", project) == taxonomy.ALLOW


class TestQuotedGreaterThan:
    @pytest.mark.parametrize("command", [
        "awk 'NR>=5 && NR<=9' notes.txt",
        "awk '$1 > 5' notes.txt",
        r"""awk 'NR>=455 && NR<=490 && NF {printf "%d\t%s\n", NR, $0}' notes.txt""",
        "awk '{print (a > b)}' notes.txt",
        "grep 'x>y' notes.txt",
    ])
    def test_comparison_is_no_redirect(self, project, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ALLOW
        assert not any(sr.redirect_target for sr in result.all_stages())

    @pytest.mark.parametrize("command,target", [
        ("echo '>' x", ""), ('echo "a>b"', ""), (r"echo a\>b", ""),
        ("echo a>out.txt", "out.txt"), ("echo 'a>b'>out.txt", "out.txt"), ("echo a 2>err.txt", "err.txt"),
    ])
    def test_only_unquoted_gt_redirects(self, project, command, target):
        stages = classify_command(command).all_stages()
        assert [sr.redirect_target for sr in stages if sr.redirect_target] == ([target] if target else [])

    def test_unquoted_redirect_still_checked(self, project):
        assert _decide("echo a>{O}/x", project) == taxonomy.ASK


class TestAwkOutputFiles:
    @pytest.mark.parametrize("command,reason", [
        ("""awk '{print > "/etc/x"}' notes.txt""", "outside project"),
        ("""awk '{printf("%s", $0) >> "/etc/y"}' notes.txt""", "outside project"),
        ("""awk '{print > "{O}/x"}' notes.txt""", "sensitive path"),
        ("awk '{print > f}' notes.txt", "file named by an expression"),
        ("""awk '{print > "out" n}' notes.txt""", "file named by an expression"),
    ])
    def test_write_target_checked(self, project, command, reason):
        other = str(project.parent.parent / "Other")
        result = classify_command(command.replace("{O}", other))
        assert result.final_decision == taxonomy.ASK
        assert reason in result.reason

    def test_write_inside_project_allowed(self, project):
        assert _decide("""awk '{print > "out.txt"}' notes.txt""", project) == taxonomy.ALLOW

    def test_protected_write_floored(self, project):
        result = classify_command("""awk '{print > ".git/hooks/pre-commit"}' notes.txt""")
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["git_exec"]

    def test_getline_literal_read_checked(self, project):
        assert _decide("""awk 'BEGIN{while((getline l < "{O}/notes.txt")>0) print l}'""", project) == taxonomy.ASK

    def test_pipe_runs_a_command(self, project):
        result = classify_command("""awk '{print | cmd}' notes.txt""")
        assert result.final_decision == taxonomy.ASK
        assert result.stages[0].action_type == taxonomy.LANG_EXEC


class TestLiftedWords:
    @pytest.mark.parametrize("command", [
        "cat ./*.txt", 'cat "{P}"/*.txt', "cat ./n?tes.txt", "cat ./[nm]otes.txt",
        "cat \"it's here.txt\"", "cat 'a;b&c!d=e.txt'", "cat ./*.nomatch",
    ])
    def test_literal_or_listed_word_lifted(self, project, command):
        assert _decide(command, project) == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        'cat "{P}"/$X/notes.txt', 'cat "{P}"/{a,../../Other}/notes.txt', 'cat "{P}"/.*', "cat ./.?",
        "cat ../Other/*.txt", "cat ./**/notes.txt", "cat ./[[:alpha:]]otes.txt", "cat ./a=~/notes.txt",
        "cat ./x^y", "cat $(echo ../Other)/notes.txt",
    ])
    def test_word_that_may_expand_outside_asks(self, project, command):
        assert _decide(command, project) == taxonomy.ASK

    def test_glob_matching_a_symlink_out_asks(self, project):
        os.symlink(project.parent.parent / "Other" / "notes.txt", project / "link.txt")
        assert _decide("cat ./*.txt", project) == taxonomy.ASK
        assert _decide("cat ./notes.*", project) == taxonomy.ALLOW

    def test_glob_match_keeps_built_in_protection(self, project):
        (project / ".env").write_text("K=v\n")
        result = classify_command("cat ./.e*")
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["credential_paths"]


class TestCdBeforeLoop:
    def test_cd_with_space_then_loop(self, project):
        (project / "guidelines" / "a").mkdir(parents=True)
        (project / "guidelines" / "a" / "b.txt").write_text("x\n")
        command = f'cd "{project}/guidelines" && grep -n x a/b.txt; for p in A B; do echo $p; done'
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_reason_comes_from_the_floored_stage(self, project):
        other = project.parent.parent / "Other"
        result = classify_command(f'cd "$X" && cat a.txt; cat {other}/notes.txt')
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]
        assert "sensitive path" in result.reason


class TestLoopVariables:
    @pytest.fixture
    def shared_docs(self, project):
        for name in ("A", "B"):
            (project / "Shared Docs" / name).mkdir(parents=True)
            (project / "Shared Docs" / name / "x.pdf").write_text("%PDF\n")
        return project

    @pytest.mark.parametrize("command", [
        'for p in A B; do cat "Shared Docs/$p/x.pdf"; done',
        'for p in A B; do cat "Shared Docs/${p}/x.pdf"; done',
        'for p in A B\ndo\ncat "Shared Docs/$p/x.pdf"\ndone',
        'for p (A B) cat "Shared Docs/$p/x.pdf"',
        'for p in A B; do cat "Shared Docs/$p/x.pdf" > "out-$p.txt"; done',
        'for p in A B; do echo "$p: $(cat "Shared Docs/$p/x.pdf")"; done',
        'p=../../Other; for p in A B; do echo $p; done; cat "Shared Docs/$p/x.pdf"',
        # * gives the project's own entries (never . or ..): each stays inside.
        'for p in A *; do cat "Shared Docs/$p/x.pdf"; done',
    ])
    def test_every_value_inside_allowed(self, shared_docs, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        'for p in A ../../Other; do cat "Shared Docs/$p/notes.txt"; done',
        'for p in A $Y; do cat "Shared Docs/$p/x.pdf"; done',
        'for p in ../../*; do cat "$p/notes.txt"; done',     # matches ../../Other
        'for p in A **; do cat "Shared Docs/$p/x.pdf"; done',   # recursive: not listed
        'for p in A B; do cat "Shared Docs/${p:-../../Other}/x.pdf"; done',
        'for p in A B; do cat x > "../../Other/$p"; done',
        'p=x; for p in $(ls); do cat "Shared Docs/$p/x.pdf"; done',
        "for p in cat rm; do $p notes.txt; done",
    ])
    def test_a_value_outside_or_unknown_asks(self, shared_docs, command):
        assert classify_command(command).final_decision == taxonomy.ASK

    @pytest.mark.parametrize("command", [
        'D="Shared Docs/A"; cat "$D/x.pdf"',
        'D="Shared Docs"; for p in A B; do cat "$D/$p/x.pdf"; done',
        # Quoted, so never split: "Shared ../../Other/notes.txt" is Other/notes.txt in the project.
        'D="Shared ../../Other"; cat "$D/notes.txt"',
    ])
    def test_spaced_value_inside_a_word_read_joined(self, shared_docs, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        # Unquoted, joined stays inside; split, ../../Other/notes.txt does not.
        'D="Shared ../../Other"; cat $D/notes.txt',
        'D="../../Other x"; cat $D/notes.txt',
        'D="../../Other x"; cat "$D/notes.txt"',  # quoted, joined: Other x/notes.txt, outside
    ])
    def test_spaced_value_reading_outside_asks(self, shared_docs, command):
        assert classify_command(command).final_decision == taxonomy.ASK

    def test_assigned_variable_already_resolved(self, project):
        other = project.parent.parent / "Other"
        assert classify_command("H=notes.txt; cat $H").final_decision == taxonomy.ALLOW
        assert classify_command(f"H={other}/notes.txt; cat $H").final_decision == taxonomy.ASK

    @pytest.mark.parametrize("words,expected", [
        (["for", "p", "in", "A", "B"], [("p", ("A", "B"))]),
        (["for", "p", "(A", "B)", "cat", "$p"], [("p", ("A", "B"))]),
        (["do", "for", "q", "in", "x"], [("q", ("x",))]),
        (["for", "p"], [("p", None)]),
        (["for", "p", "in", "a b", "*"], [("p", ("a b", "*"))]),  # the words; shell_loops.values expands them
        (["for", "a", "b", "in", "1", "2"], [("a", None), ("b", None)]),
        (["for", "((i=0;", "i<3;", "i++))"], []),
        (["echo", "for", "p", "in", "x"], []),
    ])
    def test_bindings(self, words, expected):
        assert shell_loops.bindings(words) == expected


class TestInlineProgramPaths:
    INLINE_ASK = "inline code, content scan clean"

    @pytest.fixture
    def guidelines(self, project):
        (project / "guidelines" / "_text").mkdir(parents=True)
        (project / "guidelines" / "_text" / "x.txt").write_text("x\n")
        return project / "guidelines"

    @pytest.mark.parametrize("command", [
        """cd "{G}" && python3 -c "print(open('_text/x.txt').read())" """,
        """python3 -c "print(open('guidelines/_text/x.txt').read())" """,
        "cd \"{G}\" && python3 - <<'PY'\n"
        "p='HANDOFF.md'; t='Done: see `_proposals/a/b.md`.\\n'\nopen(p,'w').write(t)\nPY",
    ])
    def test_in_project_literals_lifted(self, guidelines, command):
        result = classify_command(command.replace("{G}", str(guidelines)))
        assert result.final_decision == taxonomy.ASK
        assert self.INLINE_ASK in result.reason
        assert result.floor_rules() == []

    @pytest.mark.parametrize("command", [
        """python3 -c "open('../Other/notes.txt').read()" """,
        "python3 - <<'PY'\nopen('../Other/notes.txt').read()\nPY",
        """python3 -c "open('{P}/../../Other/notes.txt').read()" """,
    ])
    def test_literal_outside_project_floored(self, project, command):
        result = classify_command(command.replace("{P}", str(project)))
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]

    def test_literal_naming_a_built_in_sensitive_path(self, project):
        result = classify_command("python3 - <<'PY'\nimport os\nopen(os.path.expanduser('~/.bashrc'),'w')\nPY")
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]

    def test_protected_write_in_code(self, project):
        assert classify_command("""python3 -c "open('.git/hooks/pre-commit','w')" """).floor_rules() == ["git_exec"]


class TestBareFileNames:
    @pytest.fixture
    def in_other(self, project, monkeypatch):
        """The cwd is the sibling Other: sensitive, outside the project, not lifted."""
        monkeypatch.chdir(project.parent.parent / "Other")
        return project

    @pytest.mark.parametrize("command", [
        "cat notes.txt", "head -n 5 notes.txt", "rm a.txt notes.txt", "cat < notes.txt",
        "grep -n foo notes.txt", "sed s/a/b/ notes.txt", "awk -v x=1 '{print}' y=2 notes.txt",
        "sort -o /tmp/out.txt notes.txt", "chmod 644 notes.txt", "cp notes.txt /tmp/copy.txt", "ls sub",
    ])
    def test_bare_file_in_sensitive_cwd_asks(self, in_other, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert "sensitive path" in result.reason

    @pytest.mark.parametrize("command", [
        "echo hello", "head -n5 /etc/hostname", "cat <<< hello", "cat <<EOF\nhello world\nEOF", "ls",
        "grep -n notes.txt /etc/hostname", "printf '%s\\n' notes.txt",
    ])
    def test_words_that_are_not_files_left_alone(self, in_other, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", ["cat notes.txt", "head -n 5 notes.txt", "rm notes.txt",
                                         "cp notes.txt copy.txt"])
    def test_bare_file_in_lifted_project_allowed(self, project, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_bare_file_after_unfollowable_cd_asks(self, project):
        result = classify_command('cd "$X" && cat notes.txt')
        assert result.final_decision == taxonomy.ASK
        assert "cd nuh cannot follow" in result.reason

    @pytest.mark.parametrize("tokens,expected", [
        (["cat", "a", "./b", "c/d"], ["a"]),
        (["head", "-n", "5", "-c3", "f"], ["f"]),
        (["sort", "-k", "2", "-o", "out", "in"], ["out", "in"]),
        (["chmod", "644", "f"], ["f"]),
        (["chmod", "--reference=r", "f"], ["f"]),
        (["chmod", "-w", "f"], ["f"]),
        (["sort", "--output=out", "-oout2", "in"], ["out", "out2", "in"]),
        (["grep", "-e", "pat", "-f", "pats", "f"], ["pats", "f"]),
        (["awk", "-F", ":", "{print}", "v=1", "f"], ["f"]),
        (["cat", "--", "-n"], ["-n"]),
        (["cat", "-"], []),
        (["echo", "hello"], []),
    ])
    def test_bare_files(self, tokens, expected):
        assert file_operands.bare_files(tokens) == expected


class TestPrintedText:
    @pytest.mark.parametrize("command", [
        'echo "== HB majority/robert/unanimity/floor"', "printf '%s/%s\\n' a b", 'echo "see ../../Other/notes.txt"',
        'cd "$X" && echo "a/b"',
    ])
    def test_printed_slash_is_no_path(self, project, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_redirect_of_printed_text_still_checked(self, project):
        other = project.parent.parent / "Other"
        assert classify_command(f'echo "a/b" > {other}/x').final_decision == taxonomy.ASK


class TestOperands:
    @pytest.mark.parametrize("tokens,expected", [
        (["sed", "s/a/b/", "f"], {1}),
        (["sed", "-n", "-e", "s/a/b/", "f"], {3}),
        (["sed", "-f", "script.sed", "f"], set()),
        (["sed", "-i.orig-file", "s/a/b/", "f"], {2}),
        (["awk", "-F", ":", "-v", "x=1", "{print}", "f"], {5}),
        (["gawk", "-f", "p.awk", "f"], set()),
        (["grep", "-rn", "pat", "dir"], {2}),
        (["grep", "-A3", "pat", "f"], {2}),
        (["grep", "-ie", "pat", "f"], {2}),
        (["grep", "--regexp=pat", "f"], set()),
        (["grep", "--", "-pat", "f"], {2}),
        (["rg", "-g", "*.py", "pat", "."], {3}),
        (["cat", "a/b"], set()),
    ])
    def test_script_operands(self, tokens, expected):
        assert operands.script_operands(tokens) == expected

    def test_unknown_option_reads_its_value_as_the_pattern(self):
        # A misread checks the real pattern as a path: too much, never too little.
        assert operands.script_operands(["grep", "-X", "egrep", "pat", "f"]) == {2}

    @pytest.mark.parametrize("script,expected", [
        ("s/a/b/w out.txt", [("out.txt", True)]),
        ("1r in.txt", [("in.txt", False)]),
        ("/x/W log\n$R more", [("log", True), ("more", False)]),
        ("w /dev/stdout", []),
        ("s/a/b/g", []),
    ])
    def test_sed_script_files(self, script, expected):
        assert operands.sed_script_files(script) == expected
