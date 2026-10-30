"""The paths a Bash stage names, and the checks they get.

Which words are paths (not scripts, patterns, printed text or heredoc
text), the path-like string literals of an inline program, and the
sensitive, protected and catastrophic checks every one of them gets
whatever the stage's policy.
"""

import os
import re

from nuh import (awk_prog, catastrophic, exec_source, file_operands, find_args, floor, operands, paths, sensitive_paths,
                 shell_state, shell_unresolved, taxonomy)
from nuh.bash_policy import _AWK_COMMANDS, _extract_primary_target
from nuh.bash_redirects import _check_redirect
from nuh.bash_subs import _FD_PATH_PREFIX, _PSUB_INDEX_RE, _PSUB_PREFIX
from nuh.bash_types import _shell_words, Stage, StageResult
from nuh.floor import strictest as _strictest


# Commands whose arguments are not paths they touch.
_NO_PATH_ARGS = frozenset({"echo", "printf", "print", "cd", "pushd", "popd", "true", "false", ":"})


def _substituted_path(tokens: list[str], action_type: str) -> str:
    """The first argument that is a path built from a command substitution's output, or "".

    ``"$(echo ~)/Documents"`` names a path only the command's output decides,
    like a variable nuh cannot resolve. A bare substitution counts only as a
    delete target (``rm -rf $(pwd)``): elsewhere it is as often a message or a
    value. Process substitutions (``/dev/fd/…``) are pipes, not paths.
    """
    if not tokens or os.path.basename(tokens[0]) in _NO_PATH_ARGS:
        return ""
    scripts = operands.script_operands(tokens)
    for i, tok in enumerate(tokens[1:], 1):
        if _PSUB_PREFIX not in tok or tok.startswith(_FD_PATH_PREFIX) or i in scripts:
            continue
        value = tok.split("=", 1)[1] if tok.startswith("-") and "=" in tok else tok
        rest = _PSUB_INDEX_RE.sub("", value)
        if "/" in rest or rest.startswith("~") or (not rest and action_type == taxonomy.FILESYSTEM_DELETE):
            return _PSUB_INDEX_RE.sub("$(…)", tok)
    return ""


_FILESYSTEM_TYPES = frozenset({taxonomy.FILESYSTEM_READ, taxonomy.FILESYSTEM_WRITE, taxonomy.FILESYSTEM_DELETE})
# A URL is no path (file:// aside): ``curl "http://localhost:$PORT/x"`` touches no file.
_URL_RE = re.compile(r"(?!file:)[A-Za-z][A-Za-z0-9+.-]*://")


def _path_operands(stage: Stage, sr: StageResult) -> tuple[list[str], list[str]]:
    """``(shell words, code string literals)`` *stage* names as paths.

    The shell words are path arguments, filesystem targets, scripts and
    redirect targets; the literals are the path-like strings of the inline
    program it runs (program text: a ``$x`` there may be the program's own
    variable, not the shell's).
    """
    words = _shell_words(stage)
    scripts = _not_path_words(words)
    if find_args.is_find(words):
        # Each -exec payload is a command of its own, its paths checked there (_classify_find_exec).
        scripts |= find_args.exec_indices(words)
    code = next((found for r in sr.walk() if (found := _inline_code_words(stage, r)) is not None), None)
    if code is not None:
        scripts |= code[0]
    candidates = [r.target for r in stage.redirects]
    candidates += [w for i, w in enumerate(words[1:], 1)
                   if i not in scripts and ("/" in w or w.startswith(".") or sensitive_paths.is_sensitive_name(w))]
    candidates += file_operands.files(words)
    candidates += [name for name, _write in operands.sed_files(words)]
    if any(r.action_type in _FILESYSTEM_TYPES and r.default_policy == taxonomy.CONTEXT for r in sr.walk()):
        # The context may be a redirect's (``echo "v $V" > f``): echo's text is still no path.
        primary = _extract_primary_target(words)
        if any(w == primary for i, w in enumerate(words[1:], 1) if i not in scripts):
            candidates.append(primary)
    program = exec_source.program_source(words)
    if program is not None and program.kind == exec_source.SCRIPT:
        candidates.append(program.value)
    return candidates, _code_path_literals(code[1]) if code is not None else []


# Action types that only read the paths they name.
_READ_ONLY_TYPES = frozenset({taxonomy.FILESYSTEM_READ, taxonomy.GIT_SAFE, taxonomy.NETWORK_DIAGNOSTIC,
                              taxonomy.DB_READ, taxonomy.BEADS_SAFE})
# Commands that rename, remove or re-permission their operands, so a parent
# directory of a protected path is as good as the path itself.
_RENAME_DELETE_CMDS = frozenset({"mv", "rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp",
                                 "chattr", "setfacl"})


# Builtins that only print their arguments: a slash in one is text, never a file touched.
_PRINT_ONLY = frozenset({"echo", "printf", "print"})


def _not_path_words(tokens: list[str]) -> set[int]:
    """Indices of *tokens* that are not paths: a script or pattern (``operands``), any echo/printf/print argument."""
    if tokens and os.path.basename(tokens[0]) in _PRINT_ONLY:
        return set(range(1, len(tokens)))
    return operands.script_operands(tokens)


def _looks_like_path(word: str) -> bool:
    return "/" in word or word.startswith(("~", ".")) or sensitive_paths.is_sensitive_name(word)


# A quoted string in program text: '…', "…" or `…`, backslash escapes skipped.
_CODE_STRING_RE = re.compile(r"""(['"`])((?:\\.|(?!\1)[^\\])*)\1""", re.DOTALL)


# Splits a string literal into the words a path could be: whitespace (and the
# \n, \t, \r escapes), quotes and backticks. Each piece of a path split this
# way keeps its directory prefix (the first) or climbs at least as far with
# its ``..`` as the whole would (the rest start inside a component).
_LITERAL_SPLIT_RE = re.compile(r"(?:\s|\\[ntr]|['\"`])+")
# A Perl or Ruby open mode in front of a file name: ">../x", "+<x", "<&=x", "|cmd".
_OPEN_MODE_RE = re.compile(r"^[+<>|&=-]+")


def _code_path_literals(code: str) -> list[str]:
    """The path-like words of the string literals in an inline program's *code* (``open('_text/x.txt')``).

    Prose in a string (``'see `_proposals/x.md`.'``) gives its path-like words.
    A word with an open mode in front (Perl's ``open(F, ">../x")``) gives the
    name after the mode as well.
    """
    pieces = [piece for m in _CODE_STRING_RE.finditer(code) for piece in _LITERAL_SPLIT_RE.split(m.group(2))]
    pieces += [_OPEN_MODE_RE.sub("", piece) for piece in pieces if _OPEN_MODE_RE.match(piece)]
    return [piece for piece in pieces if piece and _looks_like_path(piece)]


def _inline_code_words(stage: Stage, sr: StageResult) -> tuple[set[int], str] | None:
    """``(token indices, code)`` of the inline program *sr* runs (``python3 -c CODE``, a heredoc on stdin).

    The program text is code, not a path: its words are left out of path
    extraction and the path-like string literals in it checked instead.
    None when *sr* runs no inline program.
    """
    program = sr.inline_program
    if program is None:
        return None
    tokens = stage.tokens
    indices = {i for i, tok in enumerate(tokens) if i and tok == program.code}
    if program.source == exec_source.FROM_STDIN and stage.body_words:
        indices |= set(range(len(tokens) - stage.body_words, len(tokens)))
    return indices, program.code


# Commands that pass the text on their stdin through to stdout or the files they
# name, and never read a file name, option or program from it.
_STDIN_AS_TEXT = frozenset({"cat", "tee", "head", "tail", "tr", "cut", "nl"})


def _body_text_words(stage: Stage) -> set[int]:
    """Indices of the heredoc body words of *stage* that are only text, not paths.

    ``cat > p.html <<'EOF'`` writes the body to ``p.html``: a ``</h1>`` in it
    names no file. That holds while the command is one of _STDIN_AS_TEXT and no
    pipe carries its output on (``cat <<EOF | xargs rm`` deletes the body's
    words). The words stay in the tokens: a substitution in an unquoted body
    still runs and is classified.
    """
    if not stage.body_words or stage.operator == "|" or not stage.tokens:
        return set()
    if os.path.basename(stage.tokens[0]) not in _STDIN_AS_TEXT:
        return set()
    return set(range(len(stage.tokens) - stage.body_words, len(stage.tokens)))


def _check_extracted_paths(tokens: list[str], action_type: str = "",
                           code: tuple[set[int], str] | None = None, files: list[str] | None = None,
                           text: set[int] | None = None) -> tuple[str, str]:
    """Check all path-like tokens against sensitive and protected paths. Most restrictive wins.

    Unless *action_type* only reads, each path is also checked as a write to
    agent hook wiring, nuh config and project config (floored asks). A sed
    script, awk program or grep/rg pattern is not a path (``operands``); the
    files a sed script reads and writes are, its writes checked as writes.
    *code* is the inline program (``_inline_code_words``): its words are
    replaced by the path-like string literals in it. *files* are the grammar's
    file operands and option values (``file_operands``). *text* are
    indices of heredoc body words that are only text (``_body_text_words``).
    A variable set in this command to a value nuh cannot resolve is masked
    (``shell_unresolved.mask_unresolved``): ``"$W"`` is unknown, not a file in the
    working directory.
    """
    from nuh.config import is_path_allowed  # lazy import to avoid circular

    project_root = paths.get_project_root()
    modifies = action_type not in _READ_ONLY_TYPES
    parents = action_type == taxonomy.FILESYSTEM_DELETE or (
        bool(tokens) and os.path.basename(tokens[0]) in _RENAME_DELETE_CMDS)
    package_targets = _package_write_targets(tokens)
    # Heredoc body text (``tee f <<EOF``) is data on stdin, not operands the grammar may select.
    argv = [tok for i, tok in enumerate(tokens) if i not in (text or set())]
    modified = file_operands.modified_files(argv)
    inputs = set(file_operands.input_files(argv))
    verdicts: list[tuple[str, str]] = []
    scripts = _not_path_words(tokens) | (code[0] if code is not None else set()) | (text or set())
    # (word, a write, a code string literal rather than a shell word)
    # Known commands' selectors supply their actual writes below (including rebased tar/patch paths).
    candidates = [(tok, modifies and modified is None, False) for i, tok in enumerate(tokens[1:], 1)
                  if i not in scripts and not tok.startswith("-") and _looks_like_path(tok)]
    candidates += [(name, modifies or write, False) for name, write in operands.sed_files(tokens)]
    candidates += [(literal, modifies, True) for literal in (_code_path_literals(code[1]) if code is not None else [])]
    candidates += [(word, modifies and word not in inputs, False) for word in files or [] if _PSUB_PREFIX not in word]
    candidates += [(word, modifies, False) for word in modified or []
                   if word and _PSUB_PREFIX not in word]

    state = shell_state.current()
    for tok, writes, literal in candidates:
        path = tok if literal else shell_unresolved.mask_unresolved(tok, state)
        if writes:
            package = package_targets is None or tok in package_targets
            protected = sensitive_paths.check_protected_write(path, parents=parents, package=package)
            if protected:
                verdicts.append(protected)
        # allow_paths exempts the path from the user's own entries, never a built-in one (as check_path does)
        exempt = sensitive_paths.user_entries() if is_path_allowed(path, project_root) else frozenset()
        basic = sensitive_paths.check_path_basic_raw(path, code=literal, exempt=exempt)
        if basic:
            verdicts.append(basic)
    verdicts += [_check_redirect(name) for name, write in operands.sed_files(tokens) if write]
    verdicts += catastrophic.stage_verdicts(tokens, action_type)
    return _strictest(verdicts)


def _check_awk_files(tokens: list[str], user_actions: dict[str, str] | None) -> tuple[str, str]:
    """The files an awk program's text writes (``print > "f"``) and reads (``getline < "f"``), checked.

    A write is checked like a shell redirect under the filesystem_write
    policy; a file named by an expression is unknown, so it asks under the
    context policy. Program files (``-f``) are not read.
    """
    if not tokens or os.path.basename(tokens[0]) not in _AWK_COMMANDS:
        return taxonomy.ALLOW, ""
    policy = taxonomy.get_policy(taxonomy.FILESYSTEM_WRITE, user_actions)
    verdicts: list[tuple[str, str]] = []
    for program in operands.script_texts(tokens):
        reach = awk_prog.reach(program)
        for target in reach.writes:
            if target is None:
                if policy != taxonomy.ALLOW:
                    verdicts.append((taxonomy.ASK if policy == taxonomy.CONTEXT else policy,
                                     "awk writes to a file named by an expression"))
                continue
            verdict = _check_redirect(target)
            if policy == taxonomy.CONTEXT or floor.rule_of(verdict):
                verdicts.append(floor.Verdict(verdict[0], f"awk output file: {verdict[1]}", floor.rule_of(verdict)))
        verdicts += [v for v in (sensitive_paths.check_path_basic_raw(name) for name in reach.reads) if v]
    return _strictest(verdicts)

# Commands that read every operand but the last (the destination, or -t DIR).
_COPY_CMDS = frozenset({"cp", "rsync", "scp", "install", "ln"})


def _package_write_targets(tokens: list[str]) -> set[str] | None:
    """Operands that count as writes to nuh's own code; None for all of them.

    git writes its checkout itself (merge, checkout, pull): not a tool write,
    so none. A copy reads its sources: only the destination.
    """
    cmd = os.path.basename(tokens[0]) if tokens else ""
    if cmd == "git":
        return set()
    if cmd not in _COPY_CMDS:
        return None
    return set(file_operands.modified_files(tokens) or [])
