"""``find``'s command line: its starting points, tests and the commands ``-exec`` runs.

The ``-exec`` handling follows manuelschipper/nah (MIT), 0.x e084922a and
0aa66d44: each payload is a command of its own, classified in full.
"""

import os
import re

from nuh import word_quoting

EXEC_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_GLOBAL_OPTIONS = frozenset({"-H", "-L", "-P"})
_EXPRESSION_START = frozenset({"(", ")", "!", ","})
TESTS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex", "-type",
    "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-size", "-empty", "-user",
    "-group", "-perm", "-links", "-inum", "-samefile", "-lname", "-ilname", "-uid", "-gid",
})


def is_find(tokens: list[str]) -> bool:
    return bool(tokens) and os.path.basename(tokens[0]) == "find"


def roots(tokens: list[str]) -> list[str]:
    """The starting points of a ``find`` command line (``.`` when none is given)."""
    found: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GLOBAL_OPTIONS or tok.startswith("-O") and tok[2:].isdigit():
            i += 1
            continue
        if tok == "-D":
            i += 2
            continue
        if tok.startswith("-") or tok in _EXPRESSION_START:
            break
        found.append(tok)
        i += 1
    return found or ["."]


def exec_clauses(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """``(action, command)`` for each ``-exec``/``-execdir``/``-ok``/``-okdir``; the command may be empty."""
    clauses = []
    i = 1
    while i < len(tokens):
        if tokens[i] in EXEC_ACTIONS:
            action, command = tokens[i], []
            i += 1
            while i < len(tokens) and tokens[i] not in (";", "+"):
                command.append(tokens[i])
                i += 1
            clauses.append((action, command))
        i += 1
    return clauses


def without_exec(tokens: list[str]) -> list[str]:
    """*tokens* with every exec action and its command removed."""
    dropped = exec_indices(tokens)
    return [tok for i, tok in enumerate(tokens) if i not in dropped]


def exec_indices(tokens: list[str]) -> set[int]:
    """Indices of every exec action in *tokens*, with its command and terminator."""
    found: set[int] = set()
    i = 0
    while i < len(tokens):
        if tokens[i] in EXEC_ACTIONS:
            while i < len(tokens) and tokens[i] not in (";", "+"):
                found.add(i)
                i += 1
            found.add(i)
        i += 1
    return found


def has_test(tokens: list[str]) -> bool:
    """True when the expression selects files (a name, type, time… test), so not everything under a root."""
    return any(t in TESTS for t in tokens)


def payload_for_root(command: list[str], root: str) -> list[str]:
    """*command* with ``{}`` standing for a file under *root*, so path checks see where it is."""
    placeholder = os.path.join(root, "{}") if root != "/" else "/{}"
    return [_payload_word(word, placeholder) for word in command]


def _payload_word(word: str, placeholder: str) -> str:
    """Substitute find's placeholder without dropping or misaligning the outer expansion mask."""
    if "{}" not in word:
        return word
    text, spelling = word.replace("{}", placeholder), word_quoting.encoding(word)
    if spelling is None:
        return text
    # Quotes can encode the braces. Use offsets in the original text, not spelling.replace().
    for match in reversed(list(re.finditer(r"\{\}", word))):
        spelling = spelling[:match.start()] + word_quoting.encode(placeholder) + spelling[match.end():]
    return word_quoting.spelled(text, spelling)
