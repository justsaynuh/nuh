"""Write-then-run: a script written from a heredoc and run by the next command.

``cat > x.sh <<'EOF' … EOF`` followed by ``bash x.sh`` runs a file that does
not exist yet when the hook runs, so the script path cannot be inspected.
The program is the heredoc body, so the run is classified against it
instead: a shell script command by command, like ``bash -c``; any other
interpreter's program as inline code, like ``python3 -c``.

The link is deliberately narrow, so nothing can change the file between the
write and the run:

- the writer is exactly ``cat [-] <<DELIM > path`` (any order, ``>`` or
  ``>|``), with one heredoc, followed by ``;``, ``&&`` or a newline;
- the run is the very next command (one ``chmod MODE path`` may come
  between) and names the same path as its script;
- the path has no expansions or globs, and does not exist at hook time.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Protocol

from nuh import exec_source, shell_ansi, shell_heredoc, shell_lex

_SEQUENTIAL = frozenset({";", "&&"})
_SHELLS = frozenset({"bash", "sh", "dash", "zsh"})
_STDOUT_REDIRECT = re.compile(r"^1?>(?![>&])\|?(.*)$")  # `>` / `>|`, not `>>` or `>&`
_UNSTABLE_PATH = re.compile(r"[$`*?\[\]{}]|__nuh_psub_")


class _StageLike(Protocol):
    tokens: list[str]
    script_body: str | None


@dataclass(frozen=True)
class Written:
    """A script file the previous command wrote from a heredoc."""

    path: str  # absolute, normalized
    body: str


def track(pending: Written | None, stage_str: str, op: str, group: list[_StageLike]) -> Written | None:
    """Link *group* to *pending* when it runs that script; return what the next stage may run.

    *group* holds the decomposed stages of the raw stage *stage_str*, which is
    followed by operator *op*. A run of the pending script gets its
    ``script_body`` set.
    """
    # A redirect splits one command into several stages with the same argv.
    commands = [stage for stage in group if stage.tokens]
    if pending is not None and commands:
        if all(_run_path(stage.tokens) == pending.path for stage in commands):
            for stage in commands:
                stage.script_body = pending.body
            return None
        if op in _SEQUENTIAL and all(_chmod_path(stage.tokens) == pending.path for stage in commands):
            return pending
    return _heredoc_write(stage_str, op)


def shell_program(tokens: list[str], body: str) -> str | None:
    """*body* when a shell runs the written script, else None.

    ``bash x.sh`` and ``./x.sh`` with a shell ``#!`` line (or none: the
    calling shell runs it) execute *body* as shell commands, so it is
    classified like ``bash -c``, command by command.
    """
    if not tokens:
        return None
    if "/" in tokens[0] and exec_source.program_source(tokens) is None:
        name = _shebang_interpreter(body)
    else:
        name = exec_source.interpreter_name(tokens[0])
    return body if name in _SHELLS else None


def _shebang_interpreter(body: str) -> str:
    """Interpreter named by *body*'s ``#!`` line (``env`` resolved); "sh" without one."""
    first = body.split("\n", 1)[0]
    words = first[2:].split() if first.startswith("#!") else []
    if not words:
        return "sh"
    if os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
        if not words:
            return ""
    return exec_source.interpreter_name(words[0])


def _heredoc_write(stage_str: str, op: str) -> Written | None:
    """The script *stage_str* writes from its heredoc, or None if it is not such a write."""
    if op not in _SEQUENTIAL or "<<" not in stage_str:
        return None
    docs = shell_lex.scan(stage_str).heredocs
    if len(docs) != 1:
        return None
    doc = docs[0]
    # Same splits as bash_parse._stage_words, which already succeeded on this stage.
    words = (shlex.split(shell_ansi.requote(stage_str[:doc.body_start]))
             + shlex.split(shell_ansi.requote(stage_str[doc.end:])))
    target = _cat_heredoc_target(words)
    path = _stable_path(target) if target else None
    if path is None or os.path.exists(path):
        return None
    return Written(path, shell_heredoc.heredoc_text(stage_str, doc))


def _cat_heredoc_target(words: list[str]) -> str | None:
    """Target of ``cat [-] <<DELIM > target`` (words in any order), else None."""
    if not words or words[0] != "cat":
        return None
    target: str | None = None
    heredoc = False
    i = 1
    while i < len(words):
        word = words[i]
        redirect = _STDOUT_REDIRECT.match(word)
        if word == "-":
            i += 1
        elif word.startswith("<<") and not word.startswith("<<<") and not heredoc:
            heredoc = True
            delimiter = word[2:].removeprefix("-")
            i += 1 if delimiter else 2  # `<< EOF` puts the delimiter in its own word
        elif redirect and target is None:
            target = redirect.group(1) or (words[i + 1] if i + 1 < len(words) else "")
            i += 1 if redirect.group(1) else 2
        else:
            return None
    return target if heredoc and target else None


def _run_path(tokens: list[str]) -> str | None:
    """Script file *tokens* run (``bash x.sh``, ``python3 x.py``, ``./x.sh``), else None."""
    if not tokens:
        return None
    source = exec_source.program_source(tokens)
    if source is not None:
        return _stable_path(source.value) if source.kind == exec_source.SCRIPT else None
    return _stable_path(tokens[0]) if "/" in tokens[0] else None


def _chmod_path(tokens: list[str]) -> str | None:
    """File of a plain ``chmod MODE file``, else None."""
    if len(tokens) != 3 or tokens[0] != "chmod":
        return None
    return _stable_path(tokens[2])


def _stable_path(word: str) -> str | None:
    """Absolute path *word* names, or None when the shell would expand it."""
    if not word or _UNSTABLE_PATH.search(word):
        return None
    return os.path.normpath(os.path.join(os.getcwd(), os.path.expanduser(word)))
