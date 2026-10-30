"""A stage's action type and policy turned into a decision.

Fixed policies map straight through; the context policy resolves the
stage's target (a file, a host, a script or inline program) with
``context``. Also how a lang_exec stage's script path or inline code is
read from its argv.
"""

import os

from nuh import (context, exec_source, file_operands, floor, lang_exec_context, operands, program_identity,
                 shell_program_text, shell_state, shell_unresolved, taxonomy, word_quoting)
from nuh.bash_subs import _FD_PATH_PREFIX, _PSUB_PREFIX
from nuh.bash_types import _escalate, _shell_words, Stage, StageResult


_StageProgram = tuple[str | None, str | None, str]  # (script path, inline code, FROM_* label)


def _stage_program(stage: Stage) -> _StageProgram | None:
    """``(script path, inline code, source)`` of a lang_exec stage whose program is not in argv.

    A script written by a heredoc just before it (``script_writes``), or a
    heredoc, here-string or ``<`` file on stdin. None: resolve from argv.
    """
    if stage.script_body is not None:
        return None, stage.script_body, exec_source.FROM_WRITTEN_SCRIPT
    found = exec_source.stdin_program(_shell_words(stage), stage.heredoc_literal)
    if found is None:
        return None
    return found[0], found[1], exec_source.FROM_STDIN


def _lang_exec_program(
    tokens: list[str], program: _StageProgram | None,
) -> tuple[str | None, exec_source.InlineProgram | None, str]:
    """``(script path, inline program, unreadable reason)`` a lang_exec stage runs.

    *program* comes from ``_stage_program``; without it the program is read
    from argv (a script operand or ``-c``/``-e`` code). Both are None for a
    program nuh cannot read.
    """
    awk_program = (operands.script_texts(tokens)
                   if tokens and os.path.basename(tokens[0]) in _AWK_COMMANDS else [])
    if program is not None:
        target_path, code, source = program
    elif awk_program:
        # awk's program operand is its code, not a script path (an awk -f file is).
        target_path, code, source = None, word_quoting.joined(awk_program, "\n"), exec_source.FROM_ARGUMENT
    else:
        target_path = _resolve_script_path(tokens)
        code = _extract_inline_code(tokens) if target_path is None else None
        source = exec_source.FROM_ARGUMENT
    if target_path is not None or not code or not tokens:
        return target_path, None, ""
    if source != exec_source.FROM_WRITTEN_SCRIPT:
        code, unreadable = shell_program_text.expand(code, shell_state.current())
        if unreadable:
            return None, None, unreadable
    return None, exec_source.InlineProgram(os.path.basename(tokens[0]), code, source), ""


def _apply_policy(sr: StageResult, program: _StageProgram | None = None,
                  context_tokens: list[str] | None = None) -> None:
    """Map default_policy to decision + reason. Mutates sr in place.

    *program* is a lang_exec stage's program when it does not come from argv
    (see ``_stage_program``). A lang_exec stage's inline program is recorded
    on *sr* whatever the policy, so an LLM review of the stage can show it.
    *context_tokens* are the words a context policy reads its target from
    (default *sr*'s tokens): heredoc body text names no target.
    """
    target_path, unreadable = None, ""
    if sr.action_type == taxonomy.LANG_EXEC:
        target_path, sr.inline_program, unreadable = _lang_exec_program(sr.tokens, program)
    if sr.default_policy in (taxonomy.ALLOW, taxonomy.BLOCK, taxonomy.ASK):
        sr.decision = sr.default_policy
        sr.reason = f"{sr.action_type} → {sr.default_policy}"
    elif sr.default_policy == taxonomy.CONTEXT:
        if sr.action_type == taxonomy.LANG_EXEC:
            verdict = _resolve_lang_exec(target_path, sr.inline_program)
        else:
            verdict, sr.context_target = _resolve_context(
                sr.action_type, sr.tokens if context_tokens is None else context_tokens)
        sr.decision, sr.reason = verdict
        sr.floor = floor.rule_of(verdict)
    else:
        sr.decision = taxonomy.ASK
        sr.reason = f"unknown policy: {sr.default_policy}"
    if unreadable:
        from nuh.config import get_config
        sr.unreadable_program = True
        policy = taxonomy.get_policy(taxonomy.UNKNOWN, get_config().actions)
        _escalate(sr, (taxonomy.BLOCK if policy == taxonomy.BLOCK else taxonomy.ASK, unreadable))
        if sr.decision != taxonomy.BLOCK:
            sr.reason = unreadable


def _resolve_context(action_type: str, tokens: list[str]) -> tuple[tuple[str, str], str]:
    """Resolve 'context' policy by checking filesystem or network context: the verdict and the path it judged.

    A filesystem stage is judged by each of its targets (``_context_targets``),
    the strictest verdict winning; its path is the one the hint names. Other
    types judge no path (""). lang_exec goes through ``_resolve_lang_exec``
    instead.
    """
    if action_type in (taxonomy.NETWORK_OUTBOUND, taxonomy.NETWORK_WRITE):
        return _resolve_network_outputs(action_type, tokens)
    if action_type not in (taxonomy.FILESYSTEM_READ, taxonomy.FILESYSTEM_WRITE, taxonomy.FILESYSTEM_DELETE):
        return context.resolve_context(action_type, tokens=tokens), ""
    targets = _context_targets(action_type, tokens)
    if not targets:
        return (taxonomy.ALLOW, f"{action_type}: no files modified"), ""
    first, *rest = targets
    worst, worst_target = _resolve_target_context(action_type, tokens, first), first
    for target_path in rest:
        verdict = _resolve_target_context(action_type, tokens, target_path)
        new, old = taxonomy.STRICTNESS.get(verdict[0], 2), taxonomy.STRICTNESS.get(worst[0], 2)
        if new > old or (new == old and floor.outranks(floor.rule_of(verdict), floor.rule_of(worst))):
            worst, worst_target = verdict, target_path
    return worst, worst_target


def _resolve_network_outputs(action_type: str, tokens: list[str]) -> tuple[tuple[str, str], str]:
    """A known host does not authorize local output files; retain the stricter verdict."""
    worst = ((taxonomy.ALLOW, "local transfer") if file_operands.local_transfer(tokens)
             else context.resolve_context(action_type, tokens=tokens))
    target = ""
    for word in file_operands.modified_files(tokens) or []:
        verdict = _resolve_target_context(taxonomy.FILESYSTEM_WRITE, tokens, word)
        if (taxonomy.STRICTNESS.get(verdict[0], 2) > taxonomy.STRICTNESS.get(worst[0], 2)
                or worst[1] == "local transfer"):
            worst, target = verdict, word
    return worst, target


def _context_targets(action_type: str, tokens: list[str]) -> list[str]:
    """The paths a filesystem stage's context is judged by.

    Every file a write or delete modifies when ``file_operands`` knows the
    command (``rm ~/a ./b`` removes both), else the primary target.
    """
    if action_type != taxonomy.FILESYSTEM_READ:
        modified = file_operands.modified_files(tokens)
        if modified is not None:
            # dd without of= uses stdout. Other empty lists may be commands
            # awaiting operands (including an xargs payload), so still ask.
            return modified or ([] if os.path.basename(tokens[0]) == "dd" else [""])
    return [_extract_primary_target(tokens)]


def _resolve_target_context(action_type: str, tokens: list[str], target_path: str) -> tuple[str, str]:
    """The filesystem context of one target of a stage."""
    if action_type != taxonomy.FILESYSTEM_DELETE and target_path.startswith(_FD_PATH_PREFIX):
        # An already-open descriptor (process substitution pipe): nothing
        # on disk is read or written, matching _check_redirect's safe sinks.
        return taxonomy.ALLOW, f"{action_type}: file descriptor target"
    state = shell_state.current()
    unresolved = shell_unresolved.unresolved_uses(target_path, state)
    if unresolved:
        # Where it points is unknown; the path checks (_check_extracted_paths) still see what is known.
        return taxonomy.ASK, shell_unresolved.unresolved_reason(unresolved, state)
    return context.resolve_context(action_type, tokens=tokens, target_path=target_path)


def _resolve_lang_exec(
    target_path: str | None, program: exec_source.InlineProgram | None,
) -> tuple[str, str]:
    """Resolve a lang_exec stage's context from its script path or inline program.

    A script path through a variable nuh cannot know is unknown; what the
    rest of the path names is still checked with the stage's other paths.
    """
    if target_path is not None:
        state = shell_state.current()
        unresolved = shell_unresolved.unresolved_uses(target_path, state)
        if unresolved:
            return taxonomy.ASK, shell_unresolved.unresolved_reason(unresolved, state)
    inline_code = program.code if program is not None else None
    if target_path is None and inline_code and _PSUB_PREFIX in inline_code:
        # Program text holding a substitution's output is unknown until
        # it runs: `python3 -c "$(cat x.py)"` scans clean but runs x.py.
        return taxonomy.ASK, "lang_exec: inline code includes command substitution output"
    return lang_exec_context.resolve_lang_exec_context(target_path, inline_code=inline_code)


def _extract_primary_target(tokens: list[str]) -> str:
    """Extract the primary filesystem target from command tokens.

    Heuristic: last non-flag argument that looks like a path. A /dev/fd/
    argument is returned only when nothing else is, so a real file beside a
    process substitution stays the checked target.
    """
    candidates = []
    last_non_flag = ""
    fd_target = ""
    scripts = operands.script_operands(tokens)
    for i, tok in enumerate(tokens[1:], 1):  # skip command name
        if tok.startswith("-") or i in scripts:
            continue
        if tok.startswith(_FD_PATH_PREFIX):
            fd_target = tok
            continue
        last_non_flag = tok
        if "/" in tok or tok.startswith("~") or tok.startswith("."):
            candidates.append(tok)
    # Return last path-like candidate, or fall back to last non-flag arg
    # (handles bare relative paths like "new_dir")
    return candidates[-1] if candidates else (last_non_flag or fd_target)


def _resolve_script_path(tokens: list[str]) -> str | None:
    """Extract script file path from interpreter command tokens.

    Returns resolved path (even if file doesn't exist) so context resolver
    can distinguish "file not found" from "inline execution" (None).
    Handles: python script.py, python -W ignore script.py, python -m module,
    ./script.py, etc. Returns None for inline code (python -c).
    """
    if not tokens:
        return None

    cmd = os.path.basename(tokens[0])

    from nuh.taxonomy import _INLINE_FLAGS, _MODULE_FLAGS, _VALUE_FLAGS, _normalize_interpreter, is_exec_sink
    cmd = _normalize_interpreter(cmd)

    # ./script.sh args — a path that is not an interpreter is the script itself
    # (direct execution); its arguments are data, never the script to inspect.
    # So is a path named like an interpreter that is not one (program_identity).
    if os.path.basename(tokens[0]) != tokens[0] and (
            not is_exec_sink(tokens[0]) or not program_identity.is_tool(tokens[0])):
        return os.path.realpath(tokens[0]) if os.path.isfile(tokens[0]) else tokens[0]

    inline = _INLINE_FLAGS.get(cmd, set())
    module = _MODULE_FLAGS.get(cmd, set())
    value_flags = _VALUE_FLAGS.get(cmd, set())

    skip_next = False
    for i, tok in enumerate(tokens[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if tok in inline:
            return None  # inline code, no file
        if tok in module and i + 1 < len(tokens):
            return _resolve_module_path(tokens[i + 1])
        if tok in value_flags:
            skip_next = True  # skip flag + its value argument
            continue
        if tok.startswith("-"):
            continue
        # Return resolved path even if file doesn't exist — context resolver
        # distinguishes "file not found" from "inline execution" (None).
        # ~/x.py and $VENV/x.py are expanded by the resolver, not joined to the cwd.
        if os.path.isabs(tok) or tok.startswith(("~", "$")):
            return tok
        cwd = os.getcwd()
        return os.path.join(cwd, tok)

    # ./script.py — tokens[0] is the script itself (direct execution)
    if cmd != tokens[0]:
        return os.path.realpath(tokens[0]) if os.path.isfile(tokens[0]) else tokens[0]

    return None


def _extract_inline_code(tokens: list[str]) -> str | None:
    """Extract inline code string from interpreter tokens (python -c '...', node -e '...').

    Returns the code string following an inline flag, or None if no inline
    flag found or no code argument follows it.
    """
    if not tokens or len(tokens) < 2:
        return None

    cmd = os.path.basename(tokens[0])

    from nuh.taxonomy import _INLINE_FLAGS, _VALUE_FLAGS, _normalize_interpreter
    cmd = _normalize_interpreter(cmd)

    inline = _INLINE_FLAGS.get(cmd, set())
    if not inline:
        return None
    value_flags = _VALUE_FLAGS.get(cmd, set())

    skip_next = False
    for i, tok in enumerate(tokens[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if tok in value_flags:
            skip_next = True
            continue
        if tok in inline:
            if i + 1 < len(tokens):
                return tokens[i + 1]
            return None  # bare flag with no code argument
        if tok.startswith("-"):
            continue
    return None


def _resolve_module_path(module_name: str) -> str | None:
    """Best-effort resolution of python -m module_name to a file path."""
    cwd = os.getcwd()
    pkg_main = os.path.join(cwd, module_name, "__main__.py")
    if os.path.isfile(pkg_main):
        return pkg_main
    mod_file = os.path.join(cwd, module_name + ".py")
    if os.path.isfile(mod_file):
        return mod_file
    return None


_AWK_COMMANDS = frozenset({"awk", "gawk", "mawk", "nawk"})
