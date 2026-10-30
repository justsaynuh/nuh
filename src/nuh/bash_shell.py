"""Shell and wrapper programs classified through the shared nested-program handoff."""

import os

from nuh import (catastrophic, exec_source, found_files, script_writes, shell_continuations, shell_env,
                 shell_program_text, shell_state, shell_wrapper, taxonomy, word_quoting)
from nuh.bash_decompose import _wrapped_stage
from nuh.bash_parse import _parse_stages
from nuh.bash_policy import _apply_policy
from nuh.bash_programs import (_eval_runs_nothing, _heredoc_shell_program, _SOURCE_COMMANDS, _sourced_shell_code)
from nuh.bash_subs import _all_substitutions, _PSUB_PREFIX, _replace_substitutions, _unreadable_code_reason
from nuh.bash_types import (_MAX_UNWRAP_DEPTH, _obfuscated_result, _runs_found_result, _shell_words,
                            _escalate_unknown, Stage, StageResult)
from nuh.bash_compose import _floor_rank
from nuh.passthrough_wrappers import _strip_command_builtin, _strip_passthrough_wrapper, _strip_time_wrapper
from nuh.xargs_args import _strip_xargs


def _unwrap_shell(
    stage: Stage,
    depth: int,
    *,
    global_table: list | None,
    builtin_table: list | None,
    project_table: list | None,
    user_actions: dict[str, str] | None,
    profile: str = "full",
) -> StageResult | None:
    """Try shell unwrapping. Returns StageResult if handled, None if not a wrapper."""
    from nuh.bash_stage import _classify_find_exec, _classify_runner, _classify_stage, _classify_task

    tokens = stage.tokens

    if depth >= _MAX_UNWRAP_DEPTH:
        return _obfuscated_result(tokens, "excessive shell nesting", user_actions)

    # command builtin unwrap
    if tokens and tokens[0] == "command":
        inner = _strip_command_builtin(tokens)
        if inner:
            inner_stage = Stage(tokens=inner, operator=stage.operator, heredoc_literal=stage.heredoc_literal,
                                body_words=min(stage.body_words, len(inner)),
                                stdin_ambiguous=stage.stdin_ambiguous, script_body=stage.script_body,
                                heredoc_bodies=stage.heredoc_bodies)
            return _classify_stage(inner_stage, depth + 1, global_table=global_table,
                                   builtin_table=builtin_table, project_table=project_table,
                                   user_actions=user_actions, profile=profile)
        return None  # Introspection or bare — fall through to classify

    if tokens and os.path.basename(tokens[0]) == "time":
        passthrough_tokens = _strip_time_wrapper(tokens)
        if passthrough_tokens is not None:
            inner_stage = _wrapped_stage(stage, passthrough_tokens)
            return _classify_stage(inner_stage, depth + 1, global_table=global_table,
                                   builtin_table=builtin_table, project_table=project_table,
                                   user_actions=user_actions, profile=profile)
        sr = StageResult(tokens=tokens)
        sr.action_type = taxonomy.UNKNOWN
        sr.default_policy = taxonomy.get_policy(taxonomy.UNKNOWN, user_actions)
        _apply_policy(sr)
        sr.reason = "unsupported time wrapper flags"
        return sr

    # env/nice passthrough wrappers
    passthrough_tokens = _strip_passthrough_wrapper(tokens)
    if passthrough_tokens is not None:
        inner_stage = _wrapped_stage(stage, passthrough_tokens)
        with shell_env.wrapped(tokens) as unreadable:
            result = _classify_stage(inner_stage, depth + 1, global_table=global_table,
                                     builtin_table=builtin_table, project_table=project_table,
                                     user_actions=user_actions, profile=profile)
            if unreadable:
                result = _escalate_unknown(result, unreadable, user_actions)
                result.unreadable_program = True
            return result

    # xargs unwrap (FD-089)
    if tokens and tokens[0] == "xargs":
        replace, plain = found_files.xargs_replace(tokens)
        runs = _strip_xargs(plain)
        if runs is not None and found_files.runs_found_file(
                runs if replace else [*runs, found_files.PLACEHOLDER], replace or found_files.PLACEHOLDER):
            return _runs_found_result(tokens, "xargs runs the files it reads (unknown until run)", user_actions)
        inner_tokens = _strip_xargs(tokens)
        if inner_tokens is None:
            return None  # bare xargs, -I/-J, or unknown flag → fall through
        source = exec_source.program_source(inner_tokens)
        explicit_shell = (source is not None and source.kind == exec_source.INLINE
                          and source.interpreter in shell_wrapper._SHELL_WRAPPERS)
        if taxonomy.is_exec_sink(inner_tokens[0]) and not explicit_shell:
            # Only an explicit shell -c program is known before xargs reads its input.
            sr = StageResult(tokens=tokens)
            sr.action_type = taxonomy.LANG_EXEC
            sr.default_policy = taxonomy.get_policy(taxonomy.LANG_EXEC, user_actions)
            _apply_policy(sr)
            sr.reason = f"xargs wraps exec sink: {inner_tokens[0]}"
            return sr
        inner_stage = Stage(tokens=inner_tokens, operator=stage.operator)
        result = _classify_stage(inner_stage, depth + 1, global_table=global_table,
                                 builtin_table=builtin_table, project_table=project_table,
                                 user_actions=user_actions, profile=profile)
        if explicit_shell:
            # Preserve xargs's stdin-dependent execution policy; the known text can only tighten it.
            sr = StageResult(tokens=tokens, action_type=taxonomy.LANG_EXEC, inner_stages=[result],
                             default_policy=taxonomy.get_policy(taxonomy.LANG_EXEC, user_actions))
            _apply_policy(sr)
            sr.reason = f"xargs wraps exec sink: {inner_tokens[0]}"
            return result if taxonomy.STRICTNESS[result.decision] > taxonomy.STRICTNESS[sr.decision] else sr
        return result

    # Task runners (make T, npm run T, just T, task T): the recipe's lines.
    # Package and environment runners (uv run, npx, bundle exec, …): the inner command.
    _rkw = dict(global_table=global_table, builtin_table=builtin_table, project_table=project_table,
                user_actions=user_actions, profile=profile)
    # find -exec/-execdir/-ok payloads: each one a command of its own.
    run = (_classify_find_exec(stage, depth, _rkw) or _classify_task(stage, depth, _rkw)
           or _classify_runner(stage, depth, _rkw))
    if run is not None:
        return run

    # Only shell words can be `-c` or `<<<`: in `sh <<'EOF'` a body line
    # `true -c ls` is part of the program the shell runs, not its argv.
    is_wrapper, inner = shell_wrapper.is_shell_wrapper(_shell_words(stage))
    if not is_wrapper:
        inner = _heredoc_shell_program(stage)
    if inner is None:
        inner = _sourced_shell_code(stage)
    if inner is None and stage.script_body is not None:
        inner = script_writes.shell_program(tokens, stage.script_body)
    if tokens[0] == "eval" and _eval_runs_nothing(_shell_words(stage)):
        return StageResult(tokens=tokens, action_type=taxonomy.FILESYSTEM_READ, default_policy=taxonomy.ALLOW,
                           decision=taxonomy.ALLOW, reason="eval with no command: nothing runs")
    if inner is None:
        return None
    if tokens[0] == "eval" and ("$(" in inner or "`" in inner or _PSUB_PREFIX in inner):
        return _obfuscated_result(tokens, "eval with command substitution", user_actions)
    if stage.script_body is not None and inner is stage.script_body:
        tried = [_classify_program(str(inner), stage, depth, _rkw)]
        with word_quoting.active_quoted():
            tried.append(_classify_program(str(inner), stage, depth, _rkw))
        tried = [result for result in tried if result is not None]
        if not tried:
            return None
        result = max(tried, key=lambda r: (taxonomy.STRICTNESS.get(r.decision, 2), _floor_rank(r)))
        result.inner_stages += [other for other in tried if other is not result]
        return result
    inner, unreadable = shell_program_text.expand(inner, shell_state.current())
    if unreadable:
        policy = taxonomy.get_policy(taxonomy.UNKNOWN, user_actions)
        return StageResult(tokens=tokens, action_type=taxonomy.UNKNOWN, default_policy=policy,
                           decision=taxonomy.BLOCK if policy == taxonomy.BLOCK else taxonomy.ASK, reason=unreadable,
                           unreadable_program=True)
    return _classify_program(inner, stage, depth, _rkw)


def _classify_program(inner: str, stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """Inspect one shell program in its child environment, after outer expansion."""
    tokens = stage.tokens
    user_actions = kw.get("user_actions")
    inner = shell_continuations.join(inner)  # the inner shell reads its text afresh

    if catastrophic.contains_fork_bomb(inner):
        return StageResult(tokens=tokens, action_type=taxonomy.UNKNOWN, default_policy=taxonomy.BLOCK,
                           decision=taxonomy.BLOCK, reason="catastrophic fork bomb", floor=catastrophic.RULE)

    # Check for $() or backticks in eval — obfuscated.
    # Also check for placeholders: top-level extraction already replaced
    # $(…) with __nuh_psub_N__ before _unwrap_shell runs.
    if tokens[0] == "eval" and ("$(" in inner or "`" in inner or _PSUB_PREFIX in inner):
        return _obfuscated_result(tokens, "eval with command substitution", user_actions)

    # --- FD-103: extract all substitutions from inner before splitting ---
    inner_active = _all_substitutions(inner)
    failed_reason = _unreadable_code_reason(inner_active, inner)
    if failed_reason:
        return _obfuscated_result(tokens, failed_reason, user_actions)
    inner_sanitized = _replace_substitutions(inner, inner_active) if inner_active else inner

    _ikw = kw

    # Split the raw inner string while quoting context is available (FD-095).
    inner_stages = _parse_stages(inner_sanitized)
    if inner_stages is None:
        return _obfuscated_result(tokens, "unparseable inner command", user_actions)
    if not inner_stages:
        return None
    # Lazy import: the program a shell runs is a whole command sequence, and the
    # sequence classifier (bash) is built on this module's stage classifier, so
    # it cannot be imported here at load time without an import cycle.
    from nuh.bash import _classify_inner, _program_rce_reason
    rce_reason = _program_rce_reason(inner_stages, inner_active, inner, depth + 1, _ikw)
    if rce_reason:
        sr = StageResult(tokens=tokens, action_type=taxonomy.LANG_EXEC,
                         default_policy=taxonomy.BLOCK, decision=taxonomy.BLOCK)
        sr.reason = f"unwrapped: {rce_reason}"
        return sr
    # eval and source run in this shell; a shell or script starts a new one. Either sees the prefix assignments.
    start = shell_state.command_env()
    if tokens[0] not in ("eval", *_SOURCE_COMMANDS):
        start = shell_state.fresh_shell(start)
    return _classify_inner(inner_stages, stage, depth + 1, inner_active, depth + 1, _ikw, start)
