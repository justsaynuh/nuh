"""Shell variables assigned in a command: parsed, expanded in later words, and resolved.

``expand_words``/``expand_target`` replace the uses of variables assigned
earlier (``shell_state.State``); ``word_variants`` gives the readings a stage
is checked in (loop values, spaced values joined and split); ``_assign_one``
records an assignment. See the ``shell_state`` docstring for the rules.
"""

from __future__ import annotations

import os
import re

from nuh import shell_arith, word_quoting
from nuh.shell_state import (_DIRECTORY_VARS, _NON_PATH_PARAMS, _row_refs, _shell_own, _VAR_REF_RE, State, SubValue,
                             Variant)


# --- assignments and variable expansion ------------------------------------

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGNMENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)
_SETTERS = frozenset({"export", "declare", "typeset", "local", "readonly"})


_UNRESOLVABLE = re.compile(r"[$`]|__nuh_psub_")


def assignments(tokens: list[str]) -> list[tuple[str, str]] | None:
    """``(name, value)`` pairs of a stage that only sets variables, else None.

    ``A=1 B=2`` and ``export A=1 B`` (also declare/typeset/local/readonly
    without options); a bare name only marks a variable and is left out.
    """
    if not tokens:
        return None
    if all(_ASSIGNMENT_RE.fullmatch(tok) for tok in tokens):
        args = tokens
    elif tokens[0] in _SETTERS and len(tokens) > 1 and all(
            _ASSIGNMENT_RE.fullmatch(tok) or _NAME_RE.fullmatch(tok) for tok in tokens[1:]):
        args = tokens[1:]
    else:
        return None
    return [(m.group(1), word_quoting.part(arg, m.start(2))) for arg in args
            if (m := _ASSIGNMENT_RE.fullmatch(arg))]


def expand_words(words: list[str], state: State,
                 spaced: list[str] | None = None) -> tuple[list[str], list[str]]:
    """*words* with ``$NAME``/``${NAME}`` of variables assigned earlier replaced.

    Returns ``(words, unresolved names)``. A use is unresolved when the value
    is, when it carries an operator (``${F:-x}``), or when a value with
    whitespace sits inside a larger word; with *spaced* given, that last use
    is left as written and its name appended there instead (``word_variants``
    reads it both ways). A word that is just ``$F`` splits on whitespace
    unless it was quoted (``word_quoting.unquoted_names``). Words
    holding a substitution placeholder are left as they are. ``$((…))`` is
    computed when its operands are known (``shell_arith``); one reading an
    unresolvable variable makes that use unresolved. A default of a variable
    not set in this command (``${X:-~/x}``) is read off (``_outside_default``).
    """
    if not any("$" in word for word in words):
        return words, []
    variables = state.variables()
    out: list[str] = []
    unresolved: list[str] = []
    for index, word in enumerate(words):
        # Here-strings expand their operand without field splitting or globbing,
        # including an unquoted variable that supplies the complete program.
        if index and re.fullmatch(r"\d*<<<", words[index - 1]):
            word = word_quoting.unsplit(word)
        if "$" not in word:
            out.append(word)
            continue
        whole = _VAR_REF_RE.fullmatch(word_quoting.expansion(word))
        if whole and not whole.group(2):
            name = whole.group(1) or whole.group(3)
            value = variables.get(name, "")
            if name in variables and value is not None and "__nuh_psub_" not in word:
                bare = word_quoting.unquoted_names(word)
                pieces = (value.split() or [""]) if bare is None or name in bare else [value]
                if pieces == [value]:
                    pieces = [word_quoting.expanded(word, value, {name: value})]
                else:
                    pieces = [word_quoting.spelled(piece, word_quoting.value_spelling(word, 0, piece))
                              for piece in pieces]
                out.extend(pieces)
                continue
        expanded = _expand_word(word, state, unresolved, spaced)
        out.append(_expand_arithmetic(expanded, variables, unresolved))
    return out, unresolved


def expand_target(word: str, state: State, unresolved: list[str]) -> str:
    """A redirect target with the variables assigned earlier expanded, as ``expand_words`` does a word.

    Never split: bash refuses an unquoted target that would split
    (ambiguous redirect), so the value joined is the only file written.
    Names nuh cannot resolve are appended to *unresolved*.
    """
    if "$" not in word:
        return word
    variables = state.variables()
    spaced: list[str] = []
    expanded = _expand_word(word, state, unresolved, spaced)
    if spaced:
        expanded = apply_variant(expanded, Variant({name: variables[name] or "" for name in spaced}))[0]
    return _expand_arithmetic(expanded, variables, unresolved)


def _expand_arithmetic(word: str, variables: dict[str, str | None], unresolved: list[str]) -> str:
    """*word* with its ``$((…))`` computed from the known *variables*, else as written."""
    active = word_quoting.expansion(word)
    if "$((" not in active:
        return word
    computed = shell_arith.expand(active, lambda name: variables.get(name))
    if computed is not None:
        result = word_quoting.decode(computed)
        spelling = word_quoting.encoding(word)
        if spelling is None:
            word_quoting.unknown(result)
            return result
        for start, end in reversed(shell_arith.spans(active)):
            number = shell_arith.expand(active[start:end], lambda name: variables.get(name))
            spelling = spelling[:start] + number + spelling[end:]
        return word_quoting.spelled(result, spelling)
    unresolved += [name for name in sorted(shell_arith.names(active)) if name in variables and variables[name] is None]
    return word


def _expand_word(word: str, state: State, unresolved: list[str], spaced: list[str] | None) -> str:
    """*word* with known variables replaced at their original spans, retaining every copied quote mark."""
    keep = "__nuh_psub_" in word
    variables = state.variables()
    spelling = word_quoting.encoding(word) or word

    def substitute(match: re.Match[str]) -> str | None:
        name = match.group(1) or match.group(3)
        if name not in variables and name in _DIRECTORY_VARS:
            # The directory nuh followed; unknown or with an operator it stays for _unknown_use.
            directory = state.shell_value(name)
            if directory is None or match.group(2) or keep:
                return None
            return word_quoting.value_spelling(word, match.start(), directory)
        if name not in variables:
            outside = match.group(0) if keep else _outside_default(match, state)
            if outside == match.group(0):
                return None
            default = _DEFAULT_OP_RE.fullmatch(match.group(2) or "")
            if default is not None and outside == default.group(2):
                start = match.start(2) + default.start(2)
                return spelling[start:match.end() - 1]
            return spelling[match.start()] + word_quoting.encode("{" + name + "}")
        value = variables[name]
        if value is not None and not match.group(2) and any(ch.isspace() for ch in value) and spaced is not None:
            spaced.append(name)
            return None
        if value is None or match.group(2) or any(ch.isspace() for ch in value):
            unresolved.append(name)
            return None
        if keep:
            return None
        return word_quoting.value_spelling(word, match.start(), value)

    return word_quoting.substitute(word, _VAR_REF_RE, substitute)


_DEFAULT_OP_RE = re.compile(r"(:?)[-=](.*)", re.DOTALL)


def _outside_default(match: re.Match[str], state: State) -> str:
    """A ``${X:-word}``/``${X-word}`` (or ``:=``/``=``) of a variable not set in this command, read off.

    With a literal *word*: ``${X}`` when nuh's environment sets X (to a
    non-empty value, for ``:-``), so the path checks expand it as before;
    otherwise *word*, the value the agent's shell takes unless something nuh
    never sees (an rc file) sets X. Anything else stays as written.
    """
    name, operator = match.group(1) or match.group(3), match.group(2)
    default = _DEFAULT_OP_RE.fullmatch(operator or "")
    if (default is None or name in state.row_names() or name in state.opaque or _shell_own(name)
            or name in _NON_PATH_PARAMS or _UNRESOLVABLE.search(default.group(2))):
        return match.group(0)
    value = os.environ.get(name)
    if value is not None and (value or not default.group(1)):
        return "${" + name + "}"
    return default.group(2)


_MAX_VARIANTS = 256


def _loop_combos(words: list[str], state: State) -> list[dict[str, str]]:
    """Each combination of the loop values *words* use (``[{}]`` for none); empty when nuh will not expand them.

    The combinations are the rows narrowed to the names used, so values a
    loop body computed stay paired with the loop value they came from. Not
    expanded (the words stay as written, an unknown variable) when a loop
    variable names the command itself, carries an operator (``${p:-x}``), or
    the combinations exceed _MAX_VARIANTS.
    """
    row_names = state.row_names()
    used: set[str] = set()
    for word in words:
        names, operator = _row_refs(word, row_names)
        if operator:
            return []
        used |= names
    if not used:
        return [{}]
    if _row_refs(words[0], row_names)[0]:
        return []
    combos = list(dict.fromkeys(tuple(pair for pair in row if pair[0] in used) for row in state.rows))
    return [dict(combo) for combo in combos] if len(combos) <= _MAX_VARIANTS else []


def word_variants(words: list[str], state: State, spaced: list[str]) -> tuple[list[Variant], list[str]]:
    """The readings to check *words* in, and the *spaced* names that stay unresolved.

    One reading per combination of loop values (``_loop_combos``); a
    *spaced* value (whitespace, inside a larger word, from ``expand_words``)
    doubles them: joined as a quoted word would be, split as an unquoted
    one. A value a loop body computed with whitespace in it is read both
    ways too. No readings when the words need none. A spaced variable naming
    the command, or too many readings, leaves the spaced names unresolved.
    """
    if not words:
        return [], spaced
    combos = _loop_combos(words, state)
    spaced_rows = sorted({name for combo in combos for name, value in combo.items()
                          if any(ch.isspace() for ch in value)})
    names = frozenset(spaced) | frozenset(spaced_rows)
    if names and (any((m.group(1) or m.group(3)) in names for m in _VAR_REF_RE.finditer(words[0]))
                  or not combos or 2 * len(combos) > _MAX_VARIANTS):
        return ([Variant(c) for c in combos if c] if combos != [{}] else []), spaced + spaced_rows
    if not names:
        return [Variant(c) for c in combos if c], []
    values = {name: state.variables()[name] or "" for name in spaced}
    return [Variant({**c, **values}, split) for c in combos for split in (frozenset(), names)], []


def apply_variant(word: str, variant: Variant) -> list[str]:
    """*word* in *variant*: variables replaced, arithmetic computed, split when it uses a split name unquoted.

    A use inside double quotes is never split (``word_quoting.unquoted_names``),
    and an unsplit result carries *word*'s quoting (``word_quoting.expanded``).
    """
    active = word_quoting.expansion(word)
    used = {m.group(1) or m.group(3) for m in _VAR_REF_RE.finditer(active) if not m.group(2)}
    bare = word_quoting.unquoted_names(word)
    split = used & variant.split if bare is None else used & variant.split & bare
    text = _VAR_REF_RE.sub(lambda m: variant.values.get(m.group(1) or m.group(3), m.group(0))
                           if not m.group(2) else m.group(0), active)
    text = word_quoting.decode(text)
    text = word_quoting.expanded(word, text, {name: variant.values[name] for name in used & variant.values.keys()})
    text = _expand_arithmetic(text, variant.values, [])
    if split:
        pieces = (word_quoting.encoding(text) or text).split() or [""]
        return [word_quoting.spelled(word_quoting.decode(piece), piece) for piece in pieces]
    return [text]


_WHOLE_SUBSTITUTION_RE = re.compile(r"__nuh_psub_(\d+)__")


def _resolve_value(value: str, state: State, sub_value: SubValue | None = None) -> str | None:
    """An assigned *value* with earlier variables (and ``$HOME``) expanded; None if unresolvable.

    The value of an assignment is never split, so a variable holding
    whitespace expands in place. ``$((…))`` is computed (``shell_arith``). A
    value that is a whole substitution is what *sub_value* says it prints
    (``shell_ls``).
    """
    value = os.path.expanduser(word_quoting.expansion(value))
    whole = _WHOLE_SUBSTITUTION_RE.fullmatch(value)
    if whole is not None:
        printed = sub_value(int(whole.group(1)), state) if sub_value is not None else None
        return None if printed is None or _UNRESOLVABLE.search(printed) else printed
    variables = state.variables()
    unknown = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal unknown
        name = match.group(1) or match.group(3)
        if name not in variables:
            return match.group(0)
        if variables[name] is None or match.group(2):
            unknown = True
            return match.group(0)
        return word_quoting.encode(variables[name] or "")

    text = _VAR_REF_RE.sub(substitute, value)
    computed = shell_arith.expand(text, lambda name: variables.get(name)) if "$((" in text else text
    if unknown or computed is None:
        return None
    text = re.sub(r"\$\{HOME\}|\$HOME\b", lambda _m: os.path.expanduser("~"), computed)
    return None if _UNRESOLVABLE.search(text) else word_quoting.decode(text)


def _assign_one(state: State, name: str, value: str, sub_value: SubValue | None) -> State:
    """*state* after ``name=value``.

    A value using loop values is resolved in each row: the same everywhere
    is a plain variable, different ones a new column paired with the loop
    values they came from, and any row nuh cannot resolve leaves it unresolved.
    """
    row_names = state.row_names()
    if not (row_names and _row_refs(value, row_names)[0]):
        return state.with_vars({name: _resolve_value(value, state, sub_value)})
    results = [_resolve_value(value, state.with_row(row), sub_value) for row in state.rows]
    if any(result is None for result in results):
        return state.with_vars({name: None})
    if len(set(results)) == 1:
        return state.with_vars({name: results[0]})
    return state.with_column(name, [result or "" for result in results])
