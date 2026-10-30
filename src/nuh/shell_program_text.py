"""Bytes handed to a nested parser after exactly one outer expansion.

Arguments carry word_quoting's expansion mask. Heredocs get the same mask
before escape removal: body quotes are ordinary characters, escaped dollars
are literal, and a quoted delimiter masks the whole body. Stored scripts
are already program bytes and must not receive invocation-time expansion.
"""

import re

from nuh import shell_arith, shell_expand, shell_heredoc, shell_unresolved, word_quoting
from nuh.shell_state import _VAR_REF_RE, State


def heredoc(text: str, doc: shell_heredoc.Heredoc) -> str:
    """One heredoc's text, retaining which dollars/backticks the outer shell expands."""
    body = shell_heredoc.heredoc_text(text, doc)
    if doc.quoted:
        return literal(body)
    raw = text[doc.body_start:doc.body_end].removesuffix("\n")
    spelling = shell_heredoc._BODY_ESCAPE.sub(
        lambda match: "" if match.group(1) == "\n" else word_quoting.encode(match.group(1)), raw)
    # Heredoc expansion never splits/globs the result. Retain the mask even
    # when a value contains spaces or fresh dollars for the child to parse.
    return word_quoting.unsplit(word_quoting.spelled(body, spelling))


def literal(text: str) -> str:
    """Already-produced program bytes, with no outer expansions remaining."""
    return word_quoting.spelled(text, word_quoting.encode(text))


def expand(text: str, state: State) -> tuple[str, str]:
    """Return (program bytes, unreadable reason) using the outer *state*, never the child environment.

    Ordinary argument expansion has already replaced shell-local values;
    their masks prevent recursive expansion. Resolve inherited values through
    State.known_value as path expansion does, before inner quotes can hide
    any unknown reference. No command is executed to discover its output.
    """
    active = word_quoting.expansion(text)
    if "__nuh_psub_" in active or "$(" in active.replace("$((", "") or "`" in active:
        return "", "program comes from command substitution output"
    expanded = shell_expand.expand_target(text, state, [])
    active = word_quoting.expansion(expanded)

    def substitute(match: re.Match[str]) -> str:
        value = state.known_value(match.group(1) or match.group(3))
        return match.group(0) if value is None or match.group(2) else word_quoting.encode(value)

    active = _VAR_REF_RE.sub(substitute, active)
    computed = shell_arith.expand(active, state.known_value)
    if computed is None:
        return "", "program uses arithmetic expansion nuh cannot resolve"
    masked = word_quoting.spelled(word_quoting.decode(computed), computed)
    unknown = shell_unresolved.unresolved_uses(masked, state)
    # Even numeric shell parameters need actual bytes before nested parsing;
    # the usual path checks intentionally exempt these nonempty values.
    unknown += [match.group(1) or match.group(3) for match in _VAR_REF_RE.finditer(computed)]
    if unknown:
        return "", shell_unresolved.unresolved_reason(list(dict.fromkeys(unknown)), state)
    # Outer quoting ends here. The nested parser must read these bytes afresh.
    return str(masked), ""
