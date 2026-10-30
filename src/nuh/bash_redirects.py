"""Output redirects of a Bash stage, each classified as a filesystem write.

The redirect target is checked like any write, and the literal text the
command writes (echo, printf, cat here-strings, heredocs) is content-scanned.
"""

import os
from dataclasses import replace

from nuh import (catastrophic, context, floor, floor_rules, sensitive_paths, shell_env, shell_expand,
                 shell_program_text, shell_state, shell_unresolved, shell_walk, shell_wrapper, taxonomy, word_quoting)
from nuh.bash_decompose import _make_stage
from nuh.bash_parse import _parse_stages
from nuh.bash_policy import _apply_policy
from nuh.bash_subs import _FD_PATH_PREFIX, _PSUB_PREFIX
from nuh.bash_types import _escalate, _STDOUT_FDS, Redirect, Stage, StageResult
from nuh.content import format_content_message, scan_content
from nuh.floor import strictest as _strictest
from nuh.passthrough_wrappers import _strip_command_builtin, _strip_passthrough_wrapper


# Safe redirect sinks — /dev/ special files that are not real file writes.
# Excludes block devices (/dev/sda, /dev/disk*) which are dangerous.
_REDIRECT_SAFE_SINKS = frozenset({"/dev/null", "/dev/stderr", "/dev/stdout", "/dev/tty"})


def _extract_here_string_operand(args: list[str]) -> str:
    """Return the literal operand from a here-string argv suffix, if present."""
    if not args:
        return ""

    for i, tok in enumerate(args):
        if tok == "<<<" and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith("<<<") and len(tok) > 3:
            return tok[3:]
    return ""


def _extract_wrapped_redirect_literal(inner: str, start: shell_state.State) -> str:
    """Extract redirect literal text from a single inner shell command string."""
    with word_quoting.scope():
        stages = _parse_stages(inner)
        if stages is None or len(stages) != 1 or stages[0].operator:
            return ""
        stage = stages[0]
        step = shell_walk.walk(stages, start)[0]
        readings = [[piece for tok in step.tokens for piece in shell_expand.apply_variant(tok, variant)]
                    for variant in step.variants] or [step.tokens]
        with shell_state.entered(step.state), shell_env.running(stage.prefix_env):
            return "\n".join(_extract_redirect_literal(replace(stage, tokens=tokens)) for tokens in readings)


def _extract_redirect_literal(stage: Stage) -> str:
    """Best-effort extraction of literal text written by redirects."""
    if stage.heredoc_literal:
        return stage.heredoc_literal

    tokens = stage.tokens
    if not tokens:
        return ""

    cmd = os.path.basename(tokens[0])
    args = tokens[1:]

    passthrough_tokens = _strip_passthrough_wrapper(tokens)
    if passthrough_tokens is not None:
        inner_stage = _make_stage(passthrough_tokens, stage.operator) or Stage(
            tokens=passthrough_tokens, operator=stage.operator
        )
        with shell_env.wrapped(tokens):
            return _extract_redirect_literal(inner_stage)

    if cmd == "echo":
        i = 0
        while i < len(args):
            tok = args[i]
            if tok.startswith("-") and len(tok) > 1 and set(tok[1:]) <= {"n", "e", "E"}:
                i += 1
                continue
            break
        return " ".join(args[i:])

    if cmd == "printf":
        return " ".join(args)

    if cmd == "command":
        inner_tokens = _strip_command_builtin(tokens)
        if inner_tokens:
            return _extract_redirect_literal(Stage(tokens=inner_tokens, operator=stage.operator))

    if cmd in (*shell_wrapper._SHELL_WRAPPERS, "eval"):
        is_wrapper, inner = shell_wrapper.is_shell_wrapper(tokens)
        if is_wrapper and inner:
            inner, unreadable = shell_program_text.expand(inner, shell_state.current())
            if unreadable:
                return ""
            start = shell_state.command_env()
            if cmd != "eval":
                start = shell_state.fresh_shell(start)
            return _extract_wrapped_redirect_literal(inner, start)

    if cmd == "cat":
        i = 0
        while i < len(args):
            tok = args[i]
            if tok == "--":
                i += 1
                break
            if tok.startswith("-") and tok != "<<<" and not tok.startswith("<<<"):
                i += 1
                continue
            break
        if i < len(args):
            return _extract_here_string_operand(args[i:])

    return ""


def _classify_redirect_write(stage: Stage, redirect: Redirect, user_actions: dict[str, str] | None) -> StageResult:
    """Classify *stage*'s output *redirect* as a filesystem write."""
    sr = StageResult(tokens=stage.tokens)
    sr.action_type = taxonomy.FILESYSTEM_WRITE
    sr.default_policy = taxonomy.get_policy(taxonomy.FILESYSTEM_WRITE, user_actions)
    _apply_policy(sr)

    verdict = _check_redirect(redirect.target)
    if sr.default_policy == taxonomy.CONTEXT:
        sr.decision, reason = verdict
        sr.reason = f"redirect target: {reason}"
        sr.floor = floor.rule_of(verdict)
    elif floor.rule_of(verdict):
        # Floor rules hold whatever the user's filesystem_write policy is.
        _escalate(sr, floor.Verdict(verdict[0], f"redirect target: {verdict[1]}", floor.rule_of(verdict)))

    literal = _extract_redirect_literal(stage) if redirect.fd in _STDOUT_FDS else ""
    matches = scan_content(literal)
    if matches:
        content_decision = max(
            (m.policy for m in matches),
            key=lambda p: taxonomy.STRICTNESS.get(p, 2),
        )
        if taxonomy.STRICTNESS.get(content_decision, 0) > taxonomy.STRICTNESS.get(sr.decision, 0):
            sr.decision = content_decision
            sr.reason = format_content_message("Write", matches)

    return sr


def _apply_redirect_guard(
    stage: Stage,
    sr: StageResult,
    *,
    user_actions: dict[str, str] | None = None,
) -> StageResult:
    """Escalate a stage result when the stage redirects output to disk.

    Each redirect is a write of its own. A write result replaces a less
    strict command result, and an allowed one when it takes stdout (``echo x
    > f`` is a write). A stderr redirect never hides the command: in ``curl …
    2>/dev/null | sh`` the pipe rules still see curl. The result that loses
    is kept in ``inner_stages``, so the LLM gate and prompt still see the
    command (``python3 -c … > out.log`` is lang_exec too).
    """
    for redirect in stage.redirects:
        redirect_sr = _classify_redirect_write(stage, redirect, user_actions)
        redirect_sr.redirect_target = redirect.target
        redirect_strictness = taxonomy.STRICTNESS.get(redirect_sr.decision, 0)
        current_strictness = taxonomy.STRICTNESS.get(sr.decision, 0)

        if (redirect_strictness > current_strictness
                or (sr.decision == taxonomy.ALLOW and redirect.fd in _STDOUT_FDS)
                or (redirect_strictness == current_strictness and redirect_sr.floor and not sr.floor)):
            redirect_sr.inner_stages.append(sr)
            sr = redirect_sr
        else:
            sr.inner_stages.append(redirect_sr)
    return sr


def _check_redirect(target: str) -> tuple[str, str]:
    """Check redirect target as a filesystem write."""
    if not target:
        return taxonomy.ALLOW, ""
    if target in _REDIRECT_SAFE_SINKS or target.startswith(_FD_PATH_PREFIX):
        return taxonomy.ALLOW, ""
    if _PSUB_PREFIX in target:
        return taxonomy.ASK, "redirect target comes from command output"
    if target.startswith(floor_rules.NETWORK_DEVICE_PREFIXES):
        return floor.Verdict(taxonomy.ASK, f"redirect to a raw network socket: {target}", "network_shell")
    state = shell_state.current()
    path = shell_unresolved.mask_unresolved(target, state)
    basic = _strictest([v for v in (catastrophic.check_write_target(target),
                                    sensitive_paths.check_protected_write(path),
                                    sensitive_paths.check_path_basic_raw(path)) if v])
    if basic[0] != taxonomy.ALLOW:
        decision, reason = basic
        # reason is "targets X: detail" — rewrite as "redirect to X: detail"
        display = reason.replace("targets ", "", 1) if reason.startswith("targets ") else reason
        return floor.Verdict(decision, f"redirect to {display}", floor.rule_of(basic))
    unresolved = shell_unresolved.unresolved_uses(target, state)
    if unresolved:
        return taxonomy.ASK, shell_unresolved.unresolved_reason(unresolved, state)

    return context.resolve_filesystem_context(target)
