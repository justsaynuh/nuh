"""Bash command text into the list of stages nuh classifies.

Splits on operators (``shell_split``), separates heredoc bodies from shell
words, decomposes each command (``bash_decompose``), and records the
compound syntax, loop bindings and subshells around each stage.
"""

import re

from nuh import (script_writes, shell_ansi, shell_lex, shell_loops, shell_program_text, shell_quote_marks,
                 shell_split, word_quoting, zsh_syntax)
from nuh.bash_decompose import _decompose, _literal_word
from nuh.bash_subs import _PSUB_PREFIX
from nuh.bash_types import _STDOUT_FDS, _words_with_targets, Stage


def _extract_heredoc_literal(stage_str: str) -> str:
    """The last stdin heredoc body of the raw stage string ("" if none)."""
    if "<<" not in stage_str or "\n" not in stage_str:
        return ""
    # Only the last stdin heredoc supplies the program. Concatenating an
    # unrelated body can put real code inside an artificial quoted string.
    docs = [doc for doc in shell_lex.scan(stage_str).heredocs
            if not re.search(r"[1-9][0-9]*$", stage_str[:doc.operator])]
    return shell_program_text.heredoc(stage_str, docs[-1]) if docs else ""


def _stage_words(stage_str: str) -> tuple[list[str], list[str]]:
    """Split one stage into (shell words, heredoc body words).

    A body is data, not shell words: it is split on whitespace only, so an
    apostrophe in it cannot make the stage unparseable and a ``>`` in it is
    no redirect. ``$'…'`` strings in the shell words are decoded first (see
    ``shell_ansi``). A quoted or escaped ``>`` in a word comes back as
    ``shell_quote_marks.QUOTED_GT``, so ``_decompose_simple`` knows it is no redirect
    (it restores the ``>``). Raises ``ValueError`` when the shell words are
    unbalanced or hold a ``$'…'`` escape nuh cannot decode.
    """
    words: list[str] = []
    body: list[str] = []
    pos = 0
    for doc in shell_lex.scan(stage_str).heredocs:
        words += _split_shell_words(stage_str[pos:doc.body_start])
        body += stage_str[doc.body_start:doc.body_end].split()
        pos = doc.end
    return words + _split_shell_words(stage_str[pos:]), body


def _split_shell_words(text: str) -> list[str]:
    """``shlex`` words of *text*, quoted ``>`` marked (see ``_stage_words``); their quoting recorded (word_quoting)."""
    requoted = shell_ansi.requote(text)
    if shell_quote_marks.QUOTED_GT not in requoted:
        requoted = shell_quote_marks.mark_quoted_gt(requoted)
    words = shell_quote_marks.split(requoted)
    word_quoting.record(requoted, words)
    return words


_HEREDOC_OP_RE = re.compile(r"^\d*<<(?!<)")


def _attach_body_words(group: list[Stage], body: list[str]) -> None:
    """Append heredoc *body* words to the tokens of the stages holding a ``<<``.

    They stay visible there: substitution placeholders from an
    unquoted-delimiter body, paths the body names, and the stdin program of
    ``python3 - <<'EOF'`` (where they always form the tail of the tokens).
    """
    if not body:
        return
    holders = [s for s in group if any(_HEREDOC_OP_RE.match(tok) for tok in s.tokens)] or group[-1:]
    for stage in holders:
        stage.tokens = stage.tokens + body
        stage.body_words += len(body)


class _Scopes:
    """The compound constructs open at a command, each with its own id (a loop's body is one)."""

    _UNREADABLE = -1  # after an unparseable command, compound syntax is taken to stay open

    def __init__(self) -> None:
        self.stack: list[int] = []
        self._opened = 0

    def update(self, opened: int, closed: int) -> None:
        """A command closed *closed* constructs (it starts with the closing word), then opened *opened*."""
        for _ in range(closed):
            if self.stack and self.stack[-1] != self._UNREADABLE:
                self.stack.pop()
        for _ in range(opened):
            self._opened += 1
            self.stack.append(self._opened)

    def unreadable(self) -> None:
        self.stack.append(self._UNREADABLE)


def _build_stages(commands: list[shell_split.Command], *, lenient: bool) -> list[Stage] | None:
    """Tokenize and decompose the commands of ``shell_split.split_commands``.

    A stage shlex cannot read becomes an ``unparseable`` stage (always ask)
    when *lenient*, so the other stages are still classified; otherwise the
    whole text is unparseable and None comes back. Subshell parens go on
    the first and last stage a command makes; a command that makes none
    (``fi``, ``}``) passes them on.
    """
    stages: list[Stage] = []
    written: script_writes.Written | None = None
    opens = 0  # subshells opened before a command that made no stage
    scopes = _Scopes()  # compound constructs open before this command (a loop header makes no stage)
    loops: list[tuple[str, tuple[str, ...] | None, int]] = []  # bound by headers not yet given to a stage
    carried: list[str] = []  # substitution words no stage kept, not yet given to one
    for command in commands:
        stage_str, op = command.text.strip(), command.operator
        opens += command.opens
        group: list[Stage] = []
        inside = bool(scopes.stack)
        if stage_str:
            try:
                words, body = _stage_words(stage_str)
            except ValueError:
                if not lenient:
                    return None
                group = [Stage(tokens=stage_str.split(), operator=op, unparseable=True)]
                written = None
                scopes.unreadable()  # it may open compound syntax nuh never sees closed
                inside = True
            else:
                group = _decompose(words, operator=op, heredoc_literal=_extract_heredoc_literal(stage_str))
                for stage in group:
                    stage.stdin_ambiguous = sum(bool(re.match(r"^0?<(?!\()", word_quoting.encoding(word) or word))
                                                for word in words) > 1
                    if stage.stdin_ambiguous:
                        stage.heredoc_bodies = tuple(shell_program_text.heredoc(stage_str, doc)
                                                     for doc in shell_lex.scan(stage_str).heredocs)
                _attach_body_words(group, body)
                opened, closed = zsh_syntax.compound_depth_change(words)
                inside = bool(scopes.stack or opened or closed)
                scopes.update(opened, closed)
                loops += [(name, values, scopes.stack[-1]) for name, values in shell_loops.bindings(words)]
                carried += _dropped_substitutions(words, group)
                _pipe_group_output(stages, group, op)
                written = script_writes.track(written, stage_str, op, group)
        for stage in group:
            stage.compound = inside
            stage.scopes = tuple(scopes.stack)
        if group:
            group[0].loop_vars, loops = tuple(loops), []
            group[0].carried, carried = tuple(carried), []
            group[0].opens, opens = opens, 0
            group[-1].closes = command.closes
            stages.extend(group)
        elif command.closes and stages:
            stages[-1].closes += command.closes
            stages[-1].operator = op
    if carried and stages:
        stages[-1].carried += tuple(carried)
    return stages


def _dropped_substitutions(words: list[str], group: list[Stage]) -> list[str]:
    """The words of one command holding a substitution placeholder that no stage of *group* kept."""
    kept = {word for stage in group for word in _words_with_targets(stage)}
    return [word for word in map(_literal_word, words) if _PSUB_PREFIX in word and word not in kept]


def _pipe_group_output(stages: list[Stage], group: list[Stage], op: str) -> None:
    """Hand a pipe after ``}``, ``done`` or ``fi`` to the group's last command.

    In ``{ curl …; } | sh`` the pipe sits on a stage with no command left
    once the closing word is dropped; the composition rules need it on the
    command whose output it carries. A stdout redirect on the group (``} > f |``)
    means nothing reaches the pipe.
    """
    if op != "|" or any(s.tokens for s in group):
        return
    if any(r.fd in _STDOUT_FDS for s in group for r in s.redirects):
        return
    for prev in reversed(stages):
        if prev.tokens:
            prev.operator = "|"
            return


def _parse_stages(text: str) -> list[Stage] | None:
    """Split *text* on operators and decompose each stage; None if shlex fails."""
    return _build_stages(shell_split.split_commands(text), lenient=False)
