"""In-project Bash under project_lifts_sensitive: text that is data, not a path (heredoc bodies, code strings)."""

import pytest

from nuh import taxonomy
from nuh.bash import classify_command

HTML_BODY = ("<style>\nbody { font-family: \"Liberation Sans\", Arial, sans-serif; }\n</style>\n"
             "<h1>Section 4 Motions</h1>\n<div class=\"url\">example.org/section-4/</div>\n")


def _run(command: str, project):
    other = project.parent.parent / "Other"
    return classify_command(command.replace("{P}", str(project)).replace("{O}", str(other)))


class TestHeredocBodyText:
    @pytest.mark.parametrize("command", [
        f"mkdir -p .scratch/x && cat > .scratch/x/p.html <<'HTML'\n{HTML_BODY}HTML",
        f"cat > .scratch/p.html <<HTML\n{HTML_BODY}HTML",
        f"cat <<'HTML' > .scratch/p.html\n{HTML_BODY}HTML",
        f"tee .scratch/p.html <<'HTML'\n{HTML_BODY}HTML",
        "cat <<'EOF'\nsee ../../Other/notes.txt\nEOF",
    ])
    def test_body_fed_to_cat_is_text(self, project, command):
        assert _run(command, project).final_decision == taxonomy.ALLOW

    def test_redirect_target_still_checked(self, project):
        result = _run(f"cat > {{O}}/p.html <<'HTML'\n{HTML_BODY}HTML", project)
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]

    @pytest.mark.parametrize("command", [
        "cat <<'EOF' | xargs rm\n{O}/notes.txt\nEOF",
        "xargs cat <<'EOF'\n{O}/notes.txt\nEOF",
        "sed -f - notes.txt <<'EOF'\nw {O}/out\nEOF",
    ])
    def test_body_a_command_may_read_as_names_still_checked(self, project, command):
        result = _run(command, project)
        assert result.final_decision == taxonomy.ASK
        assert "sensitive path" in result.reason

    def test_body_naming_an_outside_path_is_not_a_tee_output(self, project):
        # tee writes each operand: the body's words are its stdin, never operands it writes.
        assert _run("tee .scratch/p.txt <<'EOF'\nsee {O}/notes.txt\nEOF", project).final_decision == taxonomy.ALLOW

    def test_tee_operand_outside_still_checked_with_a_body(self, project):
        result = _run("tee .scratch/p.txt {O}/p.txt <<'EOF'\nhi\nEOF", project)
        assert result.final_decision == taxonomy.ASK
        assert "sensitive path" in result.reason

    def test_substitution_in_unquoted_body_still_runs(self, project):
        result = _run("cat > .scratch/p.txt <<EOF\nkey: $(cat {O}/notes.txt)\nEOF", project)
        assert result.final_decision == taxonomy.ASK
        assert "substitution" in result.reason
        assert result.floor_rules() == ["sensitive_paths"]


class TestCodeStringMarkup:
    INLINE_ASK = "inline code, content scan clean"

    @pytest.mark.parametrize("command", [
        "python3 - <<'PY'\np='.scratch/p.html'\nt=open(p).read()\n"
        "for a,b in [(\"<h1>Section 4 Motions</h1>\",\"<h1>Book-Fair Motions</h1>\"),\n"
        "            (\"Section 4's three motions.\",\"Section 4's two motions.\")]:\n"
        "    t=t.replace(a,b)\nopen(p,'w').write(t)\nPY",
        """python3 -c "print('<b>a</b> | (x) ^ #y')" """,
    ])
    def test_markup_in_a_string_is_lifted(self, project, command):
        result = _run(command, project)
        assert result.final_decision == taxonomy.ASK
        assert self.INLINE_ASK in result.reason
        assert result.floor_rules() == []

    @pytest.mark.parametrize("command", [
        """python3 -c "open('../../Other/a</b>.txt').read()" """,
        """python3 -c "open('{O}/<x>.txt').read()" """,
        """python3 -c "d='x'; open(f'{d}/notes.txt').read()" """,
        """perl -e 'open(F, ">../../Other/x"); print F 1' """,
        """perl -e 'open(F, "+<{O}/notes.txt")' """,
    ])
    def test_string_that_may_name_a_path_outside_floored(self, project, command):
        result = _run(command, project)
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["sensitive_paths"]
