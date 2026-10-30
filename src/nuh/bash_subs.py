"""Shell substitutions in a Bash command: found, replaced by placeholders, results folded back.

``$(…)``, backticks, ``<(…)``/``>(…)`` and code-running zsh glob qualifiers
are taken out before the command is split (FD-103) and replaced by
``__nuh_psub_N__``; each inner command is classified on its own, and the
strictest result is folded into the stage that holds its placeholder.
"""

import re

from nuh import shell_lex, taxonomy, word_quoting, zsh_syntax
from nuh.bash_types import _words_with_targets, Stage, StageResult


_PSUB_PREFIX = "__nuh_psub_"
_PSUB_SUFFIX = "__"
_FD_PATH_PREFIX = "/dev/fd/"


_PSUB_INDEX_RE = re.compile(re.escape(_PSUB_PREFIX) + r"(\d+)" + re.escape(_PSUB_SUFFIX))


def _match_parens(command: str, start: int) -> int:
    """Index of the ``)`` closing the ``(`` at *start*, or -1 (fail-closed).

    Quote-, substitution- and heredoc-aware; see ``shell_lex``.
    """
    return shell_lex.match_parens(command, start)


def _extract_substitutions(command: str) -> list[tuple[str, int, int, str]]:
    """Extract shell substitution syntax from *command*.

    Returns a list of ``(inner_command, start, end, kind)`` tuples where
    *kind* is one of ``"process_in"``, ``"process_out"``, ``"command"``,
    ``"backtick"``, or ``"failed"`` (unbalanced — fail-closed). Quoting,
    escapes and heredoc bodies follow shell rules; see ``shell_lex``.
    """
    return shell_lex.scan(command).subs


def _all_substitutions(command: str) -> list[tuple[str, int, int, str]]:
    """Shell substitutions plus zsh glob qualifiers that run code.

    Heredoc bodies are never globbed (whatever the delimiter quoting), so the
    glob qualifier scan skips them along with the substitutions.
    """
    lexed = shell_lex.scan(command)
    if word_quoting.quoted_active():
        # Written bodies also retain the stricter reading in which quoted
        # dollars and backticks introduce substitutions.
        lexed.subs = shell_lex.expansions(command)
    covered = [(start, end) for _inner, start, end, _kind in lexed.subs]
    covered += [(doc.body_start, doc.end) for doc in lexed.heredocs]
    return lexed.subs + zsh_syntax.glob_qualifier_subs(command, covered, _match_parens)


def _unreadable_code_reason(subs: list[tuple[str, int, int, str]], command: str) -> str:
    """Reason to fail closed when *command* runs code nuh cannot read, else ""."""
    kinds = {kind for *_rest, kind in subs}
    if shell_lex.FAILED in kinds:
        return "unbalanced substitution"
    if zsh_syntax.GLOB_FAILED in kinds:
        return "unparseable zsh glob qualifier (may run code)"
    return zsh_syntax.param_eval_reason(shell_lex.blank_literal_heredocs(command))


def _replace_substitutions(
    command: str,
    subs: list[tuple[str, int, int, str]],
) -> str:
    """Replace extracted substitution ranges with ``__nuh_psub_N__`` placeholders.

    Process substitutions become ``/dev/fd/__nuh_psub_N__``: the shell passes
    the command a ``/dev/fd/N`` pipe, never a file under the working
    directory, so the placeholder must not be boundary-checked as one.
    Processes in reverse offset order so earlier indices remain valid.
    """
    indexed = sorted(enumerate(subs), key=lambda t: t[1][1], reverse=True)
    result = command
    for idx, (_inner, start, end, kind) in indexed:
        prefix = _FD_PATH_PREFIX if kind in ("process_in", "process_out") else ""
        result = result[:start] + f"{prefix}__nuh_psub_{idx}__" + result[end:]
    return result


def _tighten_from_inner(
    stage: Stage,
    sr: StageResult,
    inner_results: dict[int, StageResult],
) -> None:
    """Escalate *sr* if an inner substitution result is stricter.

    Scans *stage.tokens* for ``__nuh_psub_N__`` placeholders (which may be
    embedded inside larger tokens after shlex processing), looks up the
    corresponding inner ``StageResult``, and overwrites *sr* if the inner
    decision is more restrictive.  Never weakens.  Every inner result found
    is kept in ``sr.inner_stages``.
    """
    worst: StageResult | None = None
    worst_s = -1
    for tok in _words_with_targets(stage):
        pos = 0
        while True:
            start = tok.find(_PSUB_PREFIX, pos)
            if start < 0:
                break
            end = tok.find(_PSUB_SUFFIX, start + len(_PSUB_PREFIX))
            if end < 0:
                break
            try:
                idx = int(tok[start + len(_PSUB_PREFIX) : end])
            except ValueError:
                pos = end + len(_PSUB_SUFFIX)
                continue
            ir = inner_results.get(idx)
            if ir is not None:
                sr.inner_stages.append(ir)
                s = taxonomy.STRICTNESS.get(ir.decision, 2)
                if s > worst_s:
                    worst_s = s
                    worst = ir
            pos = end + len(_PSUB_SUFFIX)
    if worst is None:
        return
    current_s = taxonomy.STRICTNESS.get(sr.decision, 0)
    if worst_s > current_s:
        sr.action_type = worst.action_type
        sr.default_policy = worst.default_policy
        sr.decision = worst.decision
        sr.reason = f"substitution: {worst.reason}"
