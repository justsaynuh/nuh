"""project_lifts_sensitive and quoted glob characters: a quoted ``(`` is a letter, an unquoted one is syntax.

The words reach nuh after shlex, so ``"Sub Dir (x)"`` and ``Sub Dir (x)``
look alike. word_quoting records which of their characters were quoted; a
word with every such character quoted names its spelling and nothing else.
"""

import pytest

from nuh import taxonomy
from nuh.bash import classify_command

# Directory names inside the project, each with characters a shell expands when unquoted.
NAMES = ["Sub Dir (x)", "Br [a]", "St*r", "Q?", "Cb {a,b}", "T~ilde", "H#ash", "C^aret", "P|pe <a>"]


@pytest.fixture
def tree(project):
    for name in NAMES:
        (project / "Shared Docs" / name).mkdir(parents=True)
    (project.parent.parent / "Other" / "Sub (x)").mkdir(parents=True)
    return project


def _decide(command: str, project) -> str:
    other = project.parent.parent / "Other"
    return classify_command(command.replace("{P}", str(project)).replace("{O}", str(other))).final_decision


@pytest.mark.parametrize("command", [
    'ls "{P}/Shared Docs"',
    'ls "{P}/Shared Docs/Sub Dir (x)"',
    'cd "Shared Docs/Sub Dir (x)" && ls',
    "ls 'Shared Docs/Sub Dir (x)'",
    r"ls Shared\ Docs/Sub\ Dir\ \(x\)",
    'cd "{P}/Shared Docs/Sub Dir (x)" && cat notes.txt - | head',
    """sh -c 'ls "Shared Docs/Sub Dir (x)"'""",
    *(f'ls "Shared Docs/{name}"' for name in NAMES),
    *(f"ls 'Shared Docs/{name}'" for name in NAMES),
])
def test_quoted_characters_are_literal_and_lift(tree, command):
    assert _decide(command, tree) == taxonomy.ALLOW


@pytest.mark.parametrize("command", [
    "ls Shared\\ Docs/Sub\\ Dir\\ (x)",          # zsh: (x) is glob grouping
    "ls Shared\\ Docs/*(/)",                      # zsh glob qualifier
    "ls @(Shared*)",                            # bash extglob, ksh
    "ls !(notes.txt)",                         # bash extglob
    'ls "Shared Docs"/{a,../../Other}',           # brace expansion
    'ls "Shared Docs"/Br\\ [a]/../../../../Other',  # a glob whose match is listed, then ..: outside
    "ls ~+/notes.txt",                         # ~+ is $PWD only in some shells
    'ls "Shared Docs/Sub Dir (x)" Shared\\ Docs/Sub\\ Dir\\ (x)',  # the same text quoted and unquoted
    'ls "$UNSET_DIR/Shared Docs/Sub Dir (x)"',  # $ expands inside double quotes
    """sh -c 'ls Shared\\ Docs/Sub\\ Dir\\ (x)'""",  # quoted for the outer shell, unquoted for sh
])
def test_unquoted_or_expanding_words_are_not_lifted(tree, command):
    """Each asks, or blocks (``!(…)`` reads as a zsh glob qualifier, which may run code)."""
    assert _decide(command, tree) != taxonomy.ALLOW


@pytest.mark.parametrize("command", [
    'ls "{O}/Sub (x)"',
    "ls '../../Other/Sub (x)'",
    'cd "../../Other/Sub (x)" && ls',
])
def test_quoted_parens_outside_the_root_still_ask(tree, command):
    assert _decide(command, tree) == taxonomy.ASK
