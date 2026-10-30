"""project_lifts_sensitive and quoted glob characters in a word that also uses a variable.

Inside double quotes the value of ``$S`` is neither split nor globbed, so a
quoted ``(`` stays a letter whatever S holds, spaces included; only where
the value leads (``..``, an absolute path) decides the lift. An unquoted
``$S`` is split and globbed, so its value must hold no such character. In
the same word, unquoted braces still expand and an unquoted ``*`` still
globs, each piece or match checked. A variable nuh cannot resolve, or a
command substitution, never lifts.
"""

import pytest

from nuh import taxonomy
from nuh.bash import classify_command


@pytest.fixture
def tree(project):
    (project / "A" / "x (y)").mkdir(parents=True)
    (project / "A" / "x (y)" / "a.pdf").write_text("x")
    (project / "A" / "w (z)").mkdir()
    for link in (project / "A" / "xlink", project / "A" / "w (z)" / "link.pdf"):
        link.symlink_to(project.parent.parent / "Other" / "notes.txt")
    return project


@pytest.mark.parametrize("command", [
    'S=A && cmp "$S/x (y)/a.pdf" b.pdf',
    'for f in a; do cmp "A/x (y)/$f.pdf" b.pdf; done',
    'S=A && rm "$S/x (y)/a.pdf"',
    'for f in a b; do cat "A/x (y)/$f.txt"; done',
    'S=A && for f in a b; do cat "$S/x (y)/$f.txt"; done',
    'S=A && cat $S"/x (y)/a.txt"',                 # unquoted, but the value holds nothing to split or glob
    'S="*" && cat "$S/x (y)"',                     # quoted: the * is a letter
    'S="(y)" && cat "A/x $S/a.pdf"',               # quoted: the ( from the value is a letter
    'S=A && echo hi > "$S/x (y)/a.txt"',
    'S="a b" && cat "$S/x (y)"',                   # quoted: the space never splits
    'S="A/x (y)" && cat "$S"',
    'S=A && rm "$S/x (y)/R-"{a,b}"_P.pdf"',        # the braces expand, the parentheses stay letters
    'S=A && cat "$S/x (y)/"{"q,../../../../Other/notes",r}.txt',  # a quoted , separates nothing
    'S=A && mv "$S/x (y)/"*.pdf "$S/"',            # the * globs inside the quoted directory
])
def test_quoted_characters_with_resolved_variables_lift(tree, command):
    assert classify_command(command).final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("command", [
    'for f in a ../../../../Other/notes; do cat "A/x (y)/$f.txt"; done',  # one loop value leaves the project
    'S=../.. && cat "$S/x (y)"',
    'f=a && cat "A/x (y)/$f_Proposal.pdf"',        # $f_Proposal, not $f
    'cat "A/x (y)/$X.pdf"',                        # never set
    'S="a b" && cat $S"/x (y)"',                   # unquoted: split
    'S="*" && cat $S"/x (y)"',                     # unquoted: globbed
    'cat "$(echo A)/x (y)"',
    'cat "`echo A`/x (y)"',
    'S="*" && cat "A/x$S" A/x$S',                  # the same word quoted, then globbed: matches A/xlink
    'S="*" && T="*" && cat "A/x$S" A/x$T',         # two words expanding to one text, the second globbed
    'for f in A/x*; do cat "$f"; done',            # a quoted loop value is still the match it names
    'S="A x" && cat $S"/x (y)"',                   # unquoted: split, (y) unquoted in the second word
    'S="A/x (y)" && cat $S',
    'S="a b/../../.." && cat "$S/x (y)"',          # quoted, never split, and still leaves the project
    'S=A && cat "$S/x (y)/"{a,../../../../Other/notes}.txt',  # one brace piece leaves the project
    'S=A && cat "$S/w (z)/"*.pdf',                 # a match is a symlink out of the project
])
def test_unresolved_escaping_or_globbed_values_ask(tree, command):
    assert classify_command(command).final_decision == taxonomy.ASK


def test_the_same_word_without_parentheses_already_lifted(tree):
    """The parentheses changed nothing about where the path goes, so neither may the decision."""
    plain = classify_command('S=A && rm "$S/x/a.pdf"').final_decision
    assert plain == classify_command('S=A && rm "$S/x (y)/a.pdf"').final_decision == taxonomy.ALLOW


# Two commands from a real log, folder names changed; spaces, parentheses and hyphens kept.
MOTIONS = "2026-04-21 Planning Group - Section 4 Book-Fair Motions"
APPROVED = "1 - As approved by Section 4 (March)"
REVISED = "2 - Revised for the Spring Meeting (April)"
PROPOSALS = ["Budget", "Venue-Change", "Volunteer-Roles"]
CD = 'cd "{P}/Shared Docs/Sync - Personal/AGM Proposals"'
LOGGED = [
    CD + ' && S="2026-04-21 Planning Group - Section 4 Book-Fair Motions" && for f in Budget Venue-Change '
    'Volunteer-Roles; do cmp "2026-03-13 AGM/Proposals for Submission/Book-Fair-${f}/Book-Fair-${f}_Proposal.pdf" '
    '"$S/1 - As approved by Section 4 (March)/Book-Fair-${f}_Proposal.pdf"; done',
    CD + ' && S="2026-04-21 Planning Group - Section 4 Book-Fair Motions" && rm "$S/1 - As approved by Section 4 '
    '(March)/Book-Fair-"{Budget,Venue-Change,Volunteer-Roles}"_Proposal.pdf" && rmdir "$S/1 - As '
    'approved by Section 4 (March)" && mv "$S/2 - Revised for the Spring Meeting (April)/"*.pdf "$S/" && '
    'rmdir "$S/2 - Revised for the Spring Meeting (April)"',
]


@pytest.mark.parametrize("command", LOGGED)
def test_logged_commands_in_the_project_lift(project, command):
    agm = project / "Shared Docs" / "Sync - Personal" / "AGM Proposals"
    for name in PROPOSALS:
        submitted = agm / "2026-03-13 AGM" / "Proposals for Submission" / f"Book-Fair-{name}"
        submitted.mkdir(parents=True)
        (submitted / f"Book-Fair-{name}_Proposal.pdf").write_text("x")
        for folder in (APPROVED, REVISED):
            (agm / MOTIONS / folder).mkdir(parents=True, exist_ok=True)
            (agm / MOTIONS / folder / f"Book-Fair-{name}_Proposal.pdf").write_text("x")
    result = classify_command(command.replace("{P}", str(project)))
    assert result.final_decision == taxonomy.ALLOW, result.reason
