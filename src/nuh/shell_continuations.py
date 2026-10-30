"""Remove the line continuations the shell removes, before nuh reads any token.

Bash and zsh drop a backslash-newline while reading input, before any token
exists: ``"$HOME/.ss\\<newline>h"`` is ``$HOME/.ssh`` and
``$\\<newline>(cmd)`` runs ``cmd``. It stays only inside single quotes,
``$'…'`` (where the shells disagree, and ``shell_ansi`` asks), a comment
(a backslash ending one is part of it) and a quoted-delimiter heredoc body;
``shell_lex`` finds the others.

Whether a backslash-newline is a continuation depends on the context the
text before it sets up, and removing one can change the context after it:
``<\\<newline><'EOF'`` becomes a quoted heredoc, whose body keeps its
backslash-newlines. So each scan removes the continuations up to the first
one whose removal could change anything after it, and the text is scanned
again from the start.
"""

from __future__ import annotations

import string

from nuh import deadline, shell_lex

# A continuation between two of these joins two pieces of one word. They take
# part in no quoting, expansion, operator or comment, so joining them leaves
# the reading of everything after the join as it was.
_INERT = frozenset(string.ascii_letters + string.digits + " \t-_./,:+@%")


def join(text: str) -> str:
    """*text* with every line continuation the shell removes taken out."""
    while "\\\n" in text:
        deadline.check("joining line continuations")  # each rescan is linear; text built to need many asks
        found = shell_lex.continuations(text)
        if not found or not found[0]:
            return text  # none left; or too deep to scan, which scan() fails closed
        indices, in_bodies = found
        batch = []
        for i in indices:
            batch.append(i)
            if not _inert(text, i) or i in in_bodies:
                # Its removal may change what follows (``$\\<nl>(``, ``<\\<nl><``, a
                # heredoc delimiter line ``EO\\<nl>F``): rescan before going on.
                break
        for i in reversed(batch):
            text = text[:i] + text[i + 2:]
    return text


def _inert(text: str, i: int) -> bool:
    """True when the characters either side of the continuation at *i* are both inert."""
    before = text[i - 1] if i > 0 else " "
    after = text[i + 2] if i + 2 < len(text) else " "
    return before in _INERT and after in _INERT
