"""Mark quoted characters before shlex removes the quotes, preserving the original text's length."""

import shlex

from nuh.shell_heredoc import escaped_quote_close

# Stands in for a quoted or escaped ">"; restored before a word is used.
QUOTED_GT = "\x00"


def split(text: str, *, source: str | None = None) -> list[str]:
    """Shell quote removal, including double-quoted escapes shlex leaves intact.

    Marked spellings use the original *source* to remove the identical bytes:
    an escaped dollar stays masked even though its backslash disappears.
    Single-quoted backslashes and double-quoted ``\\q`` remain literal.
    """
    original = text if source is None else source
    removed: set[int] = set()
    quote = ""
    i = 0
    while i < len(original):
        char = original[i]
        if char == "\\" and quote != "'" and i + 1 < len(original):
            following = original[i + 1]
            if quote == '"' and following in "$`":
                removed.add(i)
            if following == "\n":
                removed.update((i, i + 1))
            i += 2
            continue
        if char in "'\"":
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
        i += 1
    return shlex.split("".join(char for at, char in enumerate(text) if at not in removed))


def mark_quoted_gt(text: str) -> str:
    """*text* with every quoted or escaped ``>`` replaced by ``QUOTED_GT``.

    Such a ``>`` is a literal character, never a redirect. The result has
    the same length and splits into the same shell words. *text* must hold
    no ``$'…'`` strings (``shell_ansi.requote`` first) and no ``QUOTED_GT``
    of its own (callers then keep the unmarked text).
    """
    return mark_quoted(text, frozenset(">"), QUOTED_GT) if ">" in text else text


def mark_quoted(text: str, chars: frozenset[str], mark: str, *, literal_only: bool = False) -> str:
    """Replace quoted/escaped *chars* with *mark*, without changing the shell word boundaries.

    The preconditions of ``mark_quoted_gt`` hold here too. With *literal_only*,
    double quotes mark only escaped characters: dollars there still expand.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n and text[i + 1] in chars:
                out[i + 1] = mark
            i += 2
        elif c in "'\"":
            close = text.find("'", i + 1) if c == "'" else escaped_quote_close(text, i + 1, n, '"')
            end = n if close < 0 else close
            for k in range(i + 1, end):
                if text[k] in chars and (not literal_only or c == "'"):
                    out[k] = mark
            if literal_only and c == '"':
                _mark_double_escapes(text, i + 1, end, chars, mark, out)
            i = end + 1
        else:
            i += 1
    return "".join(out)


def _mark_double_escapes(text: str, start: int, end: int, chars: frozenset[str], mark: str, out: list[str]) -> None:
    """Mark only the backslash escapes the shell recognizes inside double quotes."""
    k = start
    while k < end:
        if text[k] == "\\" and k + 1 < end and text[k + 1] in '$`"\\\n':
            if text[k + 1] in chars:
                out[k + 1] = mark
            k += 2
        else:
            k += 1
