"""One command's shell words into a ``Stage``: redirects, here-strings, leading assignments.

Operator splitting happens before this, on the raw text (``bash_parse``).
"""

import os
import re
import shlex

from nuh import floor_rules, input_redirects, shell_braces, shell_quote_marks, taxonomy, word_quoting, zsh_syntax
from nuh.bash_types import _STDOUT_FDS, Redirect, Stage
from nuh.floor_rules import is_env_assignment as _is_env_assignment


def _parse_output_redirect(tok: str) -> tuple[str, bool, str, bool, str] | None:
    """Parse shell output redirect tokens.

    Supports operator-only and glued forms for >, >>, and >|, including
    fd-prefixed variants like 1>, 2>>, 1>|, combined stdout/stderr forms like
    &> and &>>, and descriptor-duplication redirects like >&2 or 2>&1.

    Returns ``(fd, append, target, needs_target, kind)`` where ``kind`` is one
    of:
    - ``"file"`` for redirects that write to a path-like target
    - ``"dup"`` for descriptor duplication / close redirects
    - ``"dup_or_file"`` for operator-only ``>&`` forms that need the next token
    """
    if not tok:
        return None

    if tok.startswith("&"):
        fd = "&"
        rest = tok[1:]
    else:
        i = 0
        while i < len(tok) and tok[i].isdigit():
            i += 1

        fd = tok[:i]
        rest = tok[i:]

    if rest == ">&":
        return fd, False, "", True, "dup_or_file"
    if rest.startswith(">&") and len(rest) > 2:
        target = rest[2:]
        if target == "-" or target.isdigit():
            return fd, False, target, False, "dup"
        if fd in ("", "1"):
            fd = "&"
        return fd, False, target, False, "file"

    for op, append in ((">>", True), (">|", False), (">", False)):
        if rest == op:
            return fd, append, "", True, "file"
        if rest.startswith(op) and len(rest) > len(op):
            return fd, append, rest[len(op):], False, "file"
    return None


def _split_embedded_output_redirect(tok: str) -> tuple[str, str] | None:
    """Split a token like ``ok>file`` into argv and redirect pieces.

    ``shlex.split`` leaves fully glued redirects attached to the preceding word,
    so shell forms like ``echo ok>file`` arrive as ``["echo", "ok>file"]``.
    This helper peels off the first output redirect operator so ``_decompose``
    can treat it exactly like the spaced form.
    """
    if not tok:
        return None

    for op in (">>", ">|", ">"):
        idx = tok.find(op)
        if idx > 0:
            return tok[:idx], tok[idx:]
    return None


def _literal_word(tok: str) -> str:
    """*tok* with quoted ``>`` restored (``_split_shell_words`` marks them)."""
    if shell_quote_marks.QUOTED_GT not in tok:
        return tok
    spelling = word_quoting.encoding(tok)
    restored = tok.replace(shell_quote_marks.QUOTED_GT, ">")
    return word_quoting.spelled(restored, spelling) if spelling is not None else restored


def _decompose(
    tokens: list[str],
    operator: str = "",
    action_hint: str = "",
    action_reason: str = "",
    heredoc_literal: str = "",
) -> list[Stage]:
    """Split compound-command syntax out of a stage, then decompose each command.

    ``{ a | b }``, ``() { … }``, ``if X { … }``, ``repeat N cmd`` and the
    do/then keyword forms yield their simple commands. Only the last one
    keeps *operator*: a pipe after ``… }`` feeds the brace group's output.
    """
    segments = zsh_syntax.split_compound(tokens)
    stages: list[Stage] = []
    for idx, (seg_tokens, seg_reason) in enumerate(segments):
        stages.extend(_decompose_simple(
            seg_tokens,
            operator=operator if idx == len(segments) - 1 else ";",
            action_hint=taxonomy.UNKNOWN if seg_reason else action_hint,
            action_reason=seg_reason or action_reason,
            heredoc_literal=heredoc_literal,
        ))
    return stages


def _decompose_simple(
    tokens: list[str],
    operator: str = "",
    action_hint: str = "",
    action_reason: str = "",
    heredoc_literal: str = "",
) -> list[Stage]:
    """Process tokens for a single pipeline stage. Detect redirects and here-strings.

    Operator splitting is handled upstream by ``shell_split.split_on_operators`` on the
    raw command string where quoting context is preserved (FD-095).  This
    function only handles here-strings and redirects within a single stage.
    A redirect anywhere in the words (``git 2>/dev/null status``) is taken
    out of the argv and recorded on the one stage the command makes. A
    quoted ``>`` (marked ``shell_quote_marks.QUOTED_GT``) is a plain character: it is
    restored, never parsed as a redirect. Words and redirect targets are
    brace-expanded (``shell_braces``); a target that expands to several
    files is a redirect to each (zsh writes them all, bash refuses).
    """
    current_tokens: list[str] = []
    redirects: list[Redirect] = []
    unread: list[str] = []  # words whose brace expansion nuh cannot read
    stdout_redirected = False
    pending: list[str] = []  # an input redirect operator waiting for its file word
    tokens = _spaced_input_redirects(tokens)
    masks = input_redirects.masked(tokens)
    i = 0

    while i < len(tokens):
        tok = tokens[i]

        # An input redirect's operator word (`<`, `3<`, `<>`): its file is
        # the next word. `<>` is no output redirect glued to a `<` word.
        if input_redirects.operator_only(tok, masks[i]):
            current_tokens += pending
            pending[:] = [tok]
            i += 1
            continue

        # Handle glued here-string operators so forms like cat -n<<<'secret',
        # bash -s<<<'script', and cat --<<<'payload' are tokenized like
        # their spaced equivalents.
        if "<<<" in tok and tok != "<<<":
            at = tok.index("<<<")
            prefix, suffix = tok[:at], tok[at + 3:]
            if prefix:
                _append_word(current_tokens, _literal_word(prefix), unread, pending)
            current_tokens.append("<<<")
            if suffix:
                current_tokens.append(_literal_word(suffix))
            i += 1
            continue

        # Redirect detection: > foo, >> foo, >| foo, >foo, >>foo, >|foo,
        # fd-prefixed variants like 1> foo or 2>>foo, and fully glued shell
        # forms like ok>foo where shlex leaves the redirect attached to argv.
        parsed_redirect = _parse_output_redirect(tok)
        if parsed_redirect is None:
            embedded_redirect = _split_embedded_output_redirect(tok)
            if embedded_redirect is not None:
                prefix, redirect_tok = embedded_redirect
                _append_word(current_tokens, _literal_word(prefix), unread, pending)
                parsed_redirect = _parse_output_redirect(redirect_tok)
        if parsed_redirect is not None:
            redirect_fd, redirect_append, target, needs_target, redirect_kind = parsed_redirect
            target = _literal_word(target)
            step = 1
            if needs_target:
                target = _literal_word(tokens[i + 1]) if i + 1 < len(tokens) else ""
                step = 2
                if redirect_kind == "dup_or_file":
                    if target == "-" or target.isdigit():
                        redirect_kind = "dup"
                    else:
                        redirect_kind = "file"
                        if redirect_fd in ("", "1"):
                            redirect_fd = "&"
            if redirect_fd in _STDOUT_FDS:
                stdout_redirected = True
            if redirect_kind != "dup" and target:
                redirects += [Redirect(redirect_fd, piece, redirect_append)
                              for piece in _brace_pieces(target, unread)]
            i += step
            continue

        _append_word(current_tokens, _literal_word(tok), unread, pending)
        i += 1

    current_tokens = _redirects_last(current_tokens + pending)
    # `<>` opens its file for writing too (on stdin unless a descriptor is named).
    redirects += [Redirect(fd, target) for fd, target in input_redirects.read_writes(current_tokens)]

    # Attach the operator from the raw-string split, unless a stdout redirect
    # has already consumed the pipe payload.
    final_operator = "" if stdout_redirected and operator == "|" else operator
    stage = _make_stage(current_tokens, final_operator, action_hint=action_hint,
                        action_reason=action_reason)
    if stage is None and redirects:
        # No command words: `> f` truncates f, `{ …; } > f` and `done > f`
        # write the group's output.
        stage = Stage(tokens=[], operator=final_operator)
    if stage is None:
        return []
    stage.redirects = redirects
    stage.heredoc_literal = heredoc_literal
    stage.brace_unread = unread[0] if unread else ""
    return [stage]


def _spaced_input_redirects(tokens: list[str]) -> list[str]:
    """*tokens* with each input redirect glued to a word split off, as spaced (``3<f`` → ``3<``, ``f``).

    ``ok<f`` is the word ``ok`` and the redirect ``< f``, as ``ok>f`` is for
    output. Every check then reads one spelling of a redirect
    (``input_redirects``). The file word is decomposed like any other:
    ``<f>g`` is ``< f`` and ``>g``.
    """
    return [piece for tok, mask in zip(tokens, input_redirects.masked(tokens))
            for piece in input_redirects.spaced(tok, mask)]


def _redirects_last(words: list[str]) -> list[str]:
    """*words* with each input redirect after the command word moved to the end, in order.

    The shell applies a redirect wherever it stands: ``awk -v <f x=1 p`` runs
    ``awk -v x=1 p`` with f on stdin, so no option or script grammar may take
    an operator or its file for an argument. A redirect before the command
    word stays where it is: the stage then names no command nuh knows.
    """
    redirects = input_redirects.find(words)
    argv: list[str] = []
    moved: list[str] = []
    command = False
    i = 0
    while i < len(words):
        found = redirects.get(i)
        if found is None:
            command = command or not _is_env_assignment(words[i])
            argv.append(words[i])
            i += 1
            continue
        (moved if command else argv).extend(words[i:i + found.width])
        i += found.width
    return argv + moved


def _brace_pieces(word: str, unread: list[str]) -> list[str]:
    """*word* brace-expanded; kept whole, and added to *unread*, when nuh cannot read its braces."""
    found = shell_braces.pieces(word)
    if found is None:
        unread.append(word)
        return [word]
    return found


def _append_word(current: list[str], word: str, unread: list[str], pending: list[str] | None = None) -> None:
    """Add *word* to the command words *current*, brace-expanded unless the shell leaves it as written.

    A here-string (after ``<<<``) and a leading assignment (``X={a,b} cmd``)
    are not brace-expanded by bash or zsh. The file of the input redirect
    operator in *pending* (taken from it) gets the operator before each
    piece: zsh opens every file a brace target expands to.
    """
    if pending:
        operator = pending.pop()
        for piece in _brace_pieces(word, unread):
            current += [operator, piece]
    elif current[-1:] == ["<<<"] or (_is_env_assignment(word) and all(map(_is_env_assignment, current))):
        current.append(word)
    else:
        current += _brace_pieces(word, unread)


def _env_var_has_exec(value: str) -> bool:
    """Check if an env var value contains an exec sink as its base command.

    Returns True (fail-closed) on parse errors to avoid silently allowing
    malformed values through.
    """
    if not value:
        return False
    try:
        tokens = shlex.split(value)
    except ValueError:
        return True  # Fail-closed: unparseable value → escalate
    if not tokens:
        return False
    base = os.path.basename(tokens[0])
    return taxonomy.is_exec_sink(base)


def _make_stage(
    tokens: list[str],
    operator: str,
    action_hint: str = "",
    action_reason: str = "",
) -> Stage | None:
    """Create a Stage from tokens, stripping env var assignments.

    The environment the stage sets is checked against the floor rules first
    (``Stage.env_floor``), since the assignments are gone afterwards.
    """
    stage = _make_stage_from_tokens(tokens, operator, action_hint, action_reason)
    if stage is not None:
        stage.env_floor = floor_rules.env_floor(tokens)
    return stage


def _make_stage_from_tokens(
    tokens: list[str],
    operator: str,
    action_hint: str,
    action_reason: str,
) -> Stage | None:
    """``_make_stage`` without the floor check.

    Inspects the values of a command's env assignments for exec sinks before
    stripping — if any value invokes a shell interpreter, the stage keeps all
    tokens so it classifies as lang_exec (ask) rather than silently allowing
    the trailing command. A stage of assignments only keeps its tokens: it
    sets shell variables (``_classify_assignments``).
    """
    if not tokens:
        return None
    # Skip leading env assignments (FOO=bar cmd ...)
    start = next((k for k, tok in enumerate(tokens) if not _is_env_assignment(tok)), len(tokens))
    if start == len(tokens):
        return Stage(tokens=tokens, operator=operator,
                     action_hint=action_hint, action_reason=action_reason)
    if any(_env_var_has_exec(tok.split("=", 1)[1]) for tok in tokens[:start]):
        # Dangerous env var — flag as lang_exec
        return Stage(tokens=tokens, operator=operator, action_hint=taxonomy.LANG_EXEC)
    split_reason = zsh_syntax.forced_split_reason(tokens[start])
    if split_reason:
        action_hint, action_reason = taxonomy.UNKNOWN, split_reason
    return Stage(tokens=_expand_zsh_equals(tokens[start:]), operator=operator,
                 action_hint=action_hint, action_reason=action_reason, prefix_env=tuple(tokens[:start]))


_ZSH_EQUALS_RE = re.compile(r"^=([A-Za-z0-9_][A-Za-z0-9_.+-]*)$")


def _expand_zsh_equals(tokens: list[str]) -> list[str]:
    """Rewrite zsh ``=cmd`` in command position to ``cmd``.

    With zsh's default EQUALS option, ``=sh`` expands to the path of ``sh``,
    so ``curl … | =sh`` runs a shell exactly like ``curl … | sh``.
    """
    match = _ZSH_EQUALS_RE.match(tokens[0])
    if match is None:
        return tokens
    return [match.group(1), *tokens[1:]]


def _wrapped_stage(stage: Stage, tokens: list[str]) -> Stage:
    """Stage for the command a passthrough wrapper runs; it reads the wrapper's heredocs."""
    inner = _make_stage(tokens, stage.operator) or Stage(tokens=tokens, operator=stage.operator)
    inner.heredoc_literal = stage.heredoc_literal
    inner.heredoc_bodies = stage.heredoc_bodies
    inner.stdin_ambiguous = stage.stdin_ambiguous
    inner.script_body = stage.script_body
    # A wrapper is stripped off the front; the body words stay at the tail.
    inner.body_words = min(stage.body_words, len(inner.tokens))
    return inner
