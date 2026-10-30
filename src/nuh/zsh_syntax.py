"""zsh constructs the bash-oriented parser must not under-classify.

Commands nuh guards run in zsh, so zsh-only syntax that executes code or
hides the real command has to be surfaced:

- glob qualifiers that run code: ``*(e:'cmd':)``, ``*(e{cmd})``, ``*(+func)``
- parameter flags that evaluate: ``${(e)var}``; forced splitting ``${=var}``
- precommand modifiers: ``noglob``, ``nocorrect``, ``builtin``, ``exec``, ``-``
- compound command bodies: ``() { … }``, ``for …; { … }``, ``if [[ … ]] { … }``,
  ``repeat N { … }``, ``{ … } always { … }`` (and the bash ``do``/``then``
  forms, which share the keywords)

This module only reads text and tokens; bash.py classifies what it finds.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# --- compound commands ------------------------------------------------------

_SEPARATOR_WORDS = {"{", "}", "do", "done", "then", "else", "fi", "always", "!", "()", "(){"}
_CONDITION_WORDS = {"if", "elif", "while", "until"}
_LOOP_HEADER_WORDS = {"for", "foreach", "select"}
_BODY_OPENERS = {"{", "do", "(){"}

Segment = tuple[list[str], str]  # (simple-command tokens, "" or reason to ask)


def split_compound(tokens: list[str]) -> list[Segment]:
    """Split one stage's tokens into the simple commands it runs.

    Reserved words and braces in command position are dropped; condition
    commands (``if X``) and bodies become their own segments. ``case`` and
    unterminated ``[[`` keep the whole stage with a reason, so it asks.
    """
    segments: list[Segment] = []
    current: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("<<") and not tok.startswith("<<<") and current:
            # Heredoc: shlex leaves the body as tokens; braces and keywords
            # in it are data, not syntax.
            current.extend(tokens[i:])
            break
        if not current:
            skip = _command_position_skip(tokens, i)
            if skip is None:
                return [(tokens, _UNPARSED_REASON.get(tok, f"compound command '{tok}' not analyzed"))]
            if tok == "[[" and skip:
                segments.append((["[", *tokens[i + 1:i + skip - 1], "]"], ""))
                i += skip
                continue
            if skip:
                i += skip
                continue
        if tok == "}":
            # zsh closes a brace group even after arguments: `{ cmd arg }`.
            segments.append((current, ""))
            current = []
            i += 1
            continue
        current.append(tok)
        i += 1
    if current:
        segments.append((current, ""))
    return [seg for seg in segments if seg[0]]


_COMPOUND_WORDS = (_SEPARATOR_WORDS | _CONDITION_WORDS | _LOOP_HEADER_WORDS
                   | {"case", "esac", "repeat", "function", "end"}) - {"!"}


def has_compound_syntax(tokens: list[str]) -> bool:
    """True when one stage's *tokens* open, continue or close compound syntax.

    ``if``/``then``/``fi``, loops, ``{ … }``, ``case`` and function
    definitions: commands inside may run any number of times, or not at all.
    """
    if not tokens:
        return False
    head = tokens[0]
    return head in _COMPOUND_WORDS or (head.endswith("()") and len(head) > 2) or "}" in tokens


_OPENER_WORDS = {"if", "while", "until", "for", "foreach", "select", "case", "repeat", "function", "{", "(){"}
_CLOSER_WORDS = {"fi", "done", "esac", "end", "}"}


def compound_depth_change(tokens: list[str]) -> tuple[int, int]:
    """``(opened, closed)``: compound constructs one command's *tokens* open and close.

    Read so that an error only keeps commands inside compound syntax longer:
    an opening word counts anywhere (``echo if`` overcounts), a closing word
    only first in the command (``echo done`` closes nothing), and syntax
    with no closing word of its own (zsh ``for x (a b) cmd``, ``repeat 3 cmd``,
    ``if [[ … ]] { … }``) stays open. A function header counts as opening.
    """
    opened = sum(1 for tok in tokens if tok in _OPENER_WORDS or (tok.endswith("()") and len(tok) > 2))
    closed = 1 if tokens and tokens[0] in _CLOSER_WORDS else 0
    return opened, closed


_UNPARSED_REASON = {
    "case": "case statement: body not analyzed",
    "[[": "unterminated [[ … ]] condition",
}


def _command_position_skip(tokens: list[str], i: int) -> int | None:
    """Tokens to drop at command position *i*; 0 keeps the token, None = unparsable."""
    tok = tokens[i]
    if tok in _SEPARATOR_WORDS or tok in _CONDITION_WORDS:
        return 1
    if tok == "case":
        return None
    if tok == "[[":
        try:
            return tokens.index("]]", i + 1) - i + 1
        except ValueError:
            return None
    if tok == "repeat":
        return 2  # `repeat N`: the count is not a command
    if tok == "function":
        return 3 if i + 2 < len(tokens) and tokens[i + 2] == "()" else 2
    if tok.endswith("()") and len(tok) > 2:
        return 1  # `name() { … }` definition header
    if tok in _LOOP_HEADER_WORDS:
        j = i + 1
        while j < len(tokens) and tokens[j] not in _BODY_OPENERS:
            j += 1
        if j == len(tokens):
            j = _short_loop_body(tokens, i)
        return j - i
    return 0


def _short_loop_body(tokens: list[str], i: int) -> int:
    """Index where the body of a zsh short loop starts (``for x (a b) cmd``).

    Without a parenthesised word list the whole stage is header (the body
    follows in a later stage), so this is ``len(tokens)``.
    """
    start = next((k for k in range(i + 2, len(tokens)) if tokens[k].startswith("(")), None)
    if start is None:
        return len(tokens)
    end = next((k for k in range(start, len(tokens)) if tokens[k].endswith(")")), len(tokens) - 1)
    return end + 1


# --- precommand modifiers ---------------------------------------------------

_PRECOMMANDS = {"noglob", "nocorrect", "builtin", "-"}


def strip_precommand(tokens: list[str]) -> list[str] | None:
    """Strip one zsh precommand modifier, returning the real command (or None)."""
    if not tokens:
        return None
    head = tokens[0]
    if head in _PRECOMMANDS:
        return tokens[1:] or None
    if head != "exec":
        return None
    i = 1
    while i < len(tokens):
        if tokens[i] in ("-c", "-l"):
            i += 1
        elif tokens[i] == "-a":
            i += 2
        elif tokens[i] == "--":
            i += 1
            break
        else:
            break
    return tokens[i:] or None


# --- parameter expansion flags ----------------------------------------------

_PARAM_FLAGS_RE = re.compile(r"\$\{\(([^)]*)\)")
_FORCED_SPLIT_RE = re.compile(r"^\$\{?=")
_FLAG_ARG_LETTERS = set("sjlrZIg_")
_PAIRS = {"{": "}", "[": "]", "(": ")", "<": ">"}


def param_eval_reason(command: str) -> str:
    """Reason text when *command* uses an evaluating ``${(…e…)name}`` expansion."""
    text = _without_single_quotes(command)
    # Stop at the last ")": past it no match can close, and without the bound
    # every "${(" on an unclosed line rescans to the end (quadratic).
    for match in _PARAM_FLAGS_RE.finditer(text, 0, text.rfind(")") + 1):
        flags = _flag_letters(match.group(1))
        if flags is None or "e" in flags:
            return f"zsh parameter evaluation: ${{({match.group(1)})…}}"
    return ""


def forced_split_reason(token: str) -> str:
    """Reason text when a command name comes from ``${=var}`` / ``$=var``."""
    if _FORCED_SPLIT_RE.match(token):
        return f"command name from zsh word splitting ({token}) is unknowable"
    return ""


def _without_single_quotes(text: str) -> str:
    """Blank out single-quoted regions (literal text in zsh)."""
    return re.sub(r"'[^']*'", lambda m: " " * len(m.group(0)), text)


def _flag_letters(flags: str) -> str | None:
    """Flag letters of ``${(flags)…}``, skipping delimited arguments; None if malformed."""
    letters: list[str] = []
    i = 0
    while i < len(flags):
        ch = flags[i]
        letters.append(ch)
        i += 1
        if ch in _FLAG_ARG_LETTERS and i < len(flags) and not flags[i].isalnum():
            while i < len(flags) and not flags[i].isalnum():
                end = _delimited_end(flags, i)
                if end is None:
                    return None
                i = end
                if ch not in "lr":
                    break
    return "".join(letters)


# --- glob qualifiers --------------------------------------------------------

_QUAL_SIMPLE = set("/.@=p*rwxAIERWXsStNDMTUGn^-,Fb")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NOT_WORD_PREV = set(" \t\n$<>=(|;&")
_WORD_END = set(" \t\n;|&<>)")

GLOB_CODE = "glob_qual"
GLOB_FUNC = "glob_func"
GLOB_FAILED = "failed_glob"


def glob_qualifier_subs(
    command: str,
    covered: list[tuple[int, int]],
    match_parens: Callable[[str, int], int],
) -> list[tuple[str, int, int, str]]:
    """Find glob qualifier groups that run code, as substitution tuples.

    Returns ``(inner, start, end, kind)`` where kind is GLOB_CODE (inner is
    the shell code), GLOB_FUNC (inner names the functions) or GLOB_FAILED (a
    trailing group mentioning ``e``/``+`` that could not be parsed).
    Regions in *covered* (already-extracted substitutions) are skipped.
    """
    results: list[tuple[str, int, int, str]] = []
    i = 0
    n = len(command)
    while i < n:
        inside = next((end for start, end in covered if start <= i < end), None)
        if inside is not None:
            i = inside
            continue
        c = command[i]
        if c == "'":
            j = command.find("'", i + 1)  # quoted text is never globbed
            i = j + 1 if j >= 0 else n
            continue
        if c == '"':
            i = _double_quote_end(command, i)
            continue
        if c == "\\":
            i += 2
            continue
        if c == "(" and i > 0 and command[i - 1] not in _NOT_WORD_PREV:
            close = match_parens(command, i)
            if close >= 0 and (close + 1 == n or command[close + 1] in _WORD_END):
                found = _qualifier_sub(command[i + 1:close], i, close + 1)
                if found:
                    results.append(found)
                i = close + 1
                continue
        i += 1
    return results


def _double_quote_end(text: str, i: int) -> int:
    """Index just past the double-quoted region opening at text[i]."""
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    return len(text)


def _qualifier_sub(content: str, start: int, end: int) -> tuple[str, int, int, str] | None:
    if content.startswith("#") and not content.startswith("#q"):
        return None  # globbing flags such as (#i), not qualifiers
    body = content[2:] if content.startswith("#q") else content
    if _has_unquoted(body, "|("):
        # zsh: a trailing group containing `|` or `(` is a pattern
        # (alternation), not a qualifier list, so nothing in it runs.
        return None
    parsed = _parse_qualifiers(body)
    if parsed is None:
        if "e" in body or "+" in body:
            return (content, start, end, GLOB_FAILED)
        return None
    codes, funcs = parsed
    if codes:
        inner = "; ".join(codes + [f"+{name}" for name in funcs])
        return (inner, start, end, GLOB_CODE)
    if funcs:
        return (" ".join(funcs), start, end, GLOB_FUNC)
    return None


def _has_unquoted(text: str, chars: str) -> bool:
    """True when any of *chars* occurs in *text* outside quotes."""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            end = text.find("'", i + 1)
            i = end + 1 if end >= 0 else len(text)
            continue
        if ch == '"':
            i = _double_quote_end(text, i)
            continue
        if ch == "\\":
            i += 2
            continue
        if ch in chars:
            return True
        i += 1
    return False


def _parse_qualifiers(body: str) -> tuple[list[str], list[str]] | None:
    """Walk a qualifier list; return (code strings, function names) or None."""
    codes: list[str] = []
    funcs: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == ":":
            break  # history-style modifiers (:h, :t, :s/x/y/) run no code
        if ch in _QUAL_SIMPLE:
            i += 1
            continue
        nxt = _qualifier_step(body, i, codes, funcs)
        if nxt is None:
            return None
        i = nxt
    return codes, funcs


def _qualifier_step(body: str, i: int, codes: list[str], funcs: list[str]) -> int | None:
    """Consume one argument-taking qualifier at *i*; return the next index."""
    ch = body[i]
    rest = i + 1
    if ch == "e" or (ch == "+" and not _IDENT_RE.match(body, rest)):
        end = _delimited_end(body, rest)
        if end is None:
            return None
        codes.append(_unquote(body[rest + 1:end - 1]))
        return end
    if ch == "+":
        match = _IDENT_RE.match(body, rest)
        funcs.append(match.group(0))
        return match.end()
    if ch == "%":
        return rest + 1 if rest < len(body) and body[rest] in "bc" else rest
    if ch in "oO":
        return rest + 1 if rest < len(body) else None
    if ch in "ugfP" and rest < len(body) and not body[rest].isalnum() and body[rest] not in "=+-":
        return _delimited_end(body, rest)
    if ch in "amcLldYfug":
        return _numeric_arg_end(body, rest, ch)
    if ch == "[":
        close = body.find("]", rest)
        return close + 1 if close >= 0 else None
    return None


def _numeric_arg_end(body: str, i: int, ch: str) -> int | None:
    """End of `[unit][+-=]digits` after a size/time/count qualifier."""
    units = {"a": "Mwhms", "m": "Mwhms", "c": "Mwhms", "L": "kKmMpPgGtT"}.get(ch, "")
    if i < len(body) and body[i] in units:
        i += 1
    if i < len(body) and body[i] in "+-=":
        i += 1
    start = i
    while i < len(body) and body[i].isdigit():
        i += 1
    return i if i > start else None


def _delimited_end(text: str, i: int) -> int | None:
    """Index just past the delimited argument opening at text[i], or None."""
    if i >= len(text):
        return None
    opener = text[i]
    closer = _PAIRS.get(opener, opener)
    depth = 1
    j = i + 1
    while j < len(text):
        ch = text[j]
        if ch in "'\"":
            end = text.find(ch, j + 1)
            if end < 0:
                return None
            j = end + 1
            continue
        if ch == "\\":
            j += 2
            continue
        if ch == closer:
            depth -= 1
            if depth == 0:
                return j + 1
        elif ch == opener and opener != closer:
            depth += 1
        j += 1
    return None


def _unquote(code: str) -> str:
    """Drop one level of quotes around code that is a single quoted word."""
    stripped = code.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "'\"":
        inner = stripped[1:-1]
        if stripped[0] not in inner:
            return inner
    return code
