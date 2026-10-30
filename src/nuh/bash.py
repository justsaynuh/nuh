"""Bash command classifier — tokenize, decompose, classify, compose.

The entry point and the command-sequence level: each stage classified in
the shell state it runs in (``shell_state``) with the substitutions it
holds, and the remote-code checks across stages. One stage is classified
in ``bash_stage``.
"""

import sys
from dataclasses import replace

from nuh import (catastrophic, deadline, find_args, glob_options, hidden_chars, paths, shell_continuations,
                 shell_expand, shell_lex, shell_ls, shell_split, shell_state, shell_unresolved, shell_walk, taxonomy,
                 word_quoting, zsh_syntax)
from nuh.bash_compose import _aggregate, _check_composition, _floor_rank
from nuh.bash_parse import _build_stages, _parse_stages
from nuh.bash_paths import _substituted_path
from nuh.bash_programs import _command_name_sub, _program_text, _reads_program_from_pipe
from nuh.bash_stage import _check_find_parents, _classify_stage
from nuh.bash_subs import (_all_substitutions, _PSUB_INDEX_RE, _PSUB_PREFIX, _PSUB_SUFFIX, _replace_substitutions,
                           _tighten_from_inner, _unreadable_code_reason)
from nuh.bash_types import (_escalate_unknown, _MAX_UNWRAP_DEPTH, _obfuscated_result, _words_with_targets,
                            ClassifyResult, Redirect, Stage, StageResult)
from nuh.xargs_args import _xargs_deletes


def classify_command(command: str) -> ClassifyResult:
    """Main entry point: classify a bash command string.

    A command holding hidden characters (``hidden_chars``) asks whatever its
    stages say, unless one of them blocks: what a person or the LLM would
    approve is not what runs.
    """
    with word_quoting.scope(), glob_options.scope(command):
        result = _classify_command_text(command)
    if result.final_decision != taxonomy.BLOCK and hidden_chars.find(command) >= 0:
        result.final_decision = taxonomy.ASK
        result.reason = hidden_chars.reason(command, "command")
        result.composition_rule = ""
        result.floor = hidden_chars.RULE
    return result


def _classify_command_text(command: str) -> ClassifyResult:
    """``classify_command`` before the hidden-character floor."""
    result = ClassifyResult(command=command)

    if not command.strip():
        result.final_decision = taxonomy.ALLOW
        result.reason = "empty command"
        return result

    # The shell removes line continuations before reading any token.
    command = shell_continuations.join(command)

    # --- FD-103: extract all substitutions before splitting ---
    # Substitutions can contain pipes that split_on_operators would
    # incorrectly split on.  Extract first, replace with placeholders,
    # then classify inner commands separately.
    all_subs = _all_substitutions(command)
    deadline.check("Bash parsing")
    # Fail-closed: unbalanced substitution, unreadable glob qualifier code or
    # zsh ${(e)…} evaluation → block
    failed_reason = _unreadable_code_reason(all_subs, command)
    if failed_reason:
        result.final_decision = taxonomy.BLOCK
        result.reason = failed_reason
        return result
    if catastrophic.contains_fork_bomb(shell_lex.blank_literal_heredocs(command)):
        result.final_decision = taxonomy.BLOCK
        result.reason = "catastrophic fork bomb"
        result.floor = catastrophic.RULE
        return result
    active_subs = all_subs
    sanitized = _replace_substitutions(command, active_subs) if active_subs else command

    _kw = _classify_kwargs()

    # Split on top-level shell operators while quoting context is available,
    # then shlex.split each stage independently (FD-095). A stage shlex cannot
    # read asks, and the others are still classified so a block is not lost.
    stages = _build_stages(shell_split.split_commands(sanitized), lenient=True) or []

    if not stages:
        result.final_decision = taxonomy.ALLOW
        result.reason = "empty command"
        return result

    # Classify each stage in the shell state it runs in, with the
    # substitutions it holds (FD-103).
    paths.get_project_root()  # resolved before any stage changes directory
    result.stages = _classify_sequence(stages, active_subs, 0, 1, _kw, shell_state.current())

    # An interpreter whose program text comes from the network is remote code
    # execution no matter how it is supplied ($(curl), <(curl), <<< …).
    rce_reason = _program_rce_reason(stages, active_subs, command, 0, _kw)
    if rce_reason:
        result.final_decision = taxonomy.BLOCK
        result.reason = rce_reason
        result.composition_rule = "network | exec"
        return result

    # Check pipe composition rules
    deadline.check("Bash classification")
    comp_decision, comp_reason, comp_rule = _check_composition(result.stages, stages)
    # Aggregate: most restrictive wins; a pipe composition raises it, never lowers a stage's block.
    _aggregate(result)
    if comp_decision and taxonomy.STRICTNESS.get(comp_decision, 2) >= taxonomy.STRICTNESS.get(result.final_decision, 2):
        result.final_decision = comp_decision
        result.reason = comp_reason
        result.composition_rule = comp_rule
    return result


def _classify_kwargs() -> dict:
    """Config-derived keyword arguments for ``_classify_stage`` (three-table lookup)."""
    kw: dict = dict(global_table=None, builtin_table=None, project_table=None,
                    user_actions=None, profile="full")
    try:
        from nuh.config import get_config  # lazy import
        cfg = get_config()
        kw["profile"] = cfg.profile
        if cfg.classify_global:
            kw["global_table"] = taxonomy.build_user_table(cfg.classify_global)
        kw["builtin_table"] = taxonomy.get_builtin_table(cfg.profile)
        if cfg.classify_project:
            kw["project_table"] = taxonomy.build_user_table(cfg.classify_project)
        if cfg.actions:
            kw["user_actions"] = cfg.actions
    except Exception as e:
        sys.stderr.write(f"nuh: config load error: {e}\n")
    return kw


def _classify_sequence(
    stages: list[Stage],
    subs: list[tuple[str, int, int, str]],
    depth: int,
    sub_depth: int,
    kw: dict,
    start: shell_state.State,
) -> list[StageResult]:
    """Classify *stages* in order, each in the shell state it runs in (``shell_state``).

    Variables assigned earlier are expanded in each stage's words, and the
    stage is classified in the working directory earlier cds left, along
    with the substitutions its words hold (classified at *sub_depth*). A use
    of a variable nuh cannot resolve, and a relative path after a cd nuh
    cannot follow, make the stage unknown.
    """
    def sub_value(idx: int, state: shell_state.State) -> str | None:
        """What substitution *idx* prints run in *state*, when nuh can tell (``shell_ls``)."""
        if idx >= len(subs) or subs[idx][3] not in ("command", "backtick"):
            return None
        return shell_ls.single_path(subs[idx][0], state.cwd)

    results: list[StageResult] = []
    for i, (stage, step) in enumerate(zip(stages, shell_walk.walk(stages, start, sub_value))):
        previous = stages[i - 1] if i else None
        stage.tokens = step.tokens
        stage.redirects = [replace(r, target=target) for r, target in zip(stage.redirects, step.targets)]
        if not step.variants:
            sr = _classify_step(stage, step, previous, subs, depth, sub_depth, kw)
        else:
            # Loop values, or a spaced value joined and split: check each reading, keep the strictest.
            written = (stage.tokens, stage.redirects)
            tried = []
            for variant in step.variants:
                stage.tokens, stage.redirects = _with_variant(*written, stage.body_words, variant)
                tried.append((_classify_step(stage, step, previous, subs, depth, sub_depth, kw),
                              stage.tokens, stage.redirects))
            sr, stage.tokens, stage.redirects = max(
                tried, key=lambda t: (taxonomy.STRICTNESS.get(t[0].decision, 2), _floor_rank(t[0])))
            sr.inner_stages += [other for other, _tokens, _redirects in tried if other is not sr]
        deadline.note(sr.decision, sr.reason)
        results.append(sr)
    return results


def _with_variant(tokens: list[str], redirects: list[Redirect], body_words: int,
                  variant: shell_state.Variant) -> tuple[list[str], list[Redirect]]:
    """*tokens* (heredoc body words aside) and redirect targets read in *variant*.

    A redirect target is never split: bash refuses an unquoted one that would
    split (ambiguous redirect), so the joined name is the only one written.
    """
    words = len(tokens) - body_words
    joined = replace(variant, split=frozenset())
    return ([piece for tok in tokens[:words] for piece in shell_expand.apply_variant(tok, variant)] + tokens[words:],
            [replace(r, target=shell_expand.apply_variant(r.target, joined)[0]) for r in redirects])


def _classify_step(stage: Stage, step: shell_state.Step, previous: Stage | None,
                   subs: list[tuple[str, int, int, str]], depth: int, sub_depth: int, kw: dict) -> StageResult:
    """Classify *stage* (its words expanded) in *step*'s shell state, with the substitutions it holds."""
    with shell_state.entered(step.state):
        sr = _classify_stage(stage, depth, **kw)
        if previous is not None and previous.operator == "|" and _xargs_deletes(stage.tokens):
            _check_find_parents(find_args.roots(previous.tokens) if find_args.is_find(previous.tokens) else [], sr)
        indices = {int(m) for word in _words_with_targets(stage) for m in _PSUB_INDEX_RE.findall(word)}
        sub_results = _classify_substitutions(subs, sub_depth, kw, indices)
        if sub_results:
            _tighten_from_inner(stage, sr, sub_results)
    if step.unresolved:
        reason = shell_unresolved.unresolved_reason(list(dict.fromkeys(step.unresolved)), step.state)
        sr = _escalate_unknown(sr, reason, kw.get("user_actions"))
    built = _substituted_path(stage.tokens, sr.action_type)
    if built:
        sr = _escalate_unknown(sr, f"path comes from command output: {built}", kw.get("user_actions"))
    return sr


def _classify_substitutions(
    subs: list[tuple[str, int, int, str]],
    depth: int,
    kw: dict,
    indices: set[int],
) -> dict[int, StageResult]:
    """Classify the inner command of each substitution in *indices* (FD-103)."""
    results: dict[int, StageResult] = {}
    for idx, (inner_cmd, _start, _end, kind) in enumerate(subs):
        if idx not in indices:
            continue
        inner_cmd = inner_cmd.strip()
        if not inner_cmd:
            continue
        if kind == zsh_syntax.GLOB_FUNC:
            # The function body is invisible to nuh: always at least ask.
            sr = StageResult(tokens=[inner_cmd], action_type=taxonomy.UNKNOWN,
                             default_policy=taxonomy.ASK, decision=taxonomy.ASK)
            sr.reason = f"zsh glob qualifier calls shell function: +{inner_cmd}"
            results[idx] = sr
            continue
        sr = _classify_substitution_text(inner_cmd, idx, depth, kw)
        if sr is not None:
            results[idx] = sr
    return results


def _classify_substitution_text(text: str, idx: int, depth: int, kw: dict) -> StageResult | None:
    """Classify substitution *idx*'s command *text*, its own substitutions included.

    ``echo $(echo $(rm -rf ~))`` runs the innermost command too, so nested
    substitutions are extracted and classified recursively (one depth level
    per nesting level, capped like shell unwrapping); the strictest wins.
    """
    user_actions = kw.get("user_actions")
    if depth >= _MAX_UNWRAP_DEPTH:
        return _obfuscated_result([text], "excessive substitution nesting", user_actions)
    text = shell_continuations.join(text)
    subs = _all_substitutions(text)
    failed_reason = _unreadable_code_reason(subs, text)
    if failed_reason:
        return _obfuscated_result([text], failed_reason, user_actions)
    stages = _parse_stages(_replace_substitutions(text, subs) if subs else text)
    if stages is None:
        return _obfuscated_result([text], "unparseable substitution", user_actions)
    if not stages:
        return None
    rce_reason = _program_rce_reason(stages, subs, text, depth, kw)
    if rce_reason:
        return StageResult(tokens=[text], action_type=taxonomy.LANG_EXEC, default_policy=taxonomy.BLOCK,
                           decision=taxonomy.BLOCK, reason=rce_reason)
    placeholder = Stage(tokens=[f"{_PSUB_PREFIX}{idx}{_PSUB_SUFFIX}"])
    return _classify_inner(stages, placeholder, depth, subs, depth + 1, kw, shell_state.current())


_NETWORK_TYPES = (taxonomy.NETWORK_OUTBOUND, taxonomy.NETWORK_WRITE)


def _text_has_network(text: str, depth: int, kw: dict) -> bool:
    """True when command string *text* runs a network command, substitutions included.

    Text that is unparseable or nested too deep returns False: the regular
    substitution classification already turns those into obfuscated blocks,
    so this answers only "is readable program text remote".
    """
    if depth >= _MAX_UNWRAP_DEPTH:
        return False
    text = shell_continuations.join(text)
    subs = _all_substitutions(text)
    if _unreadable_code_reason(subs, text):
        return False
    if any(_text_has_network(inner, depth + 1, kw) for inner, *_rest in subs):
        return True
    stages = _parse_stages(_replace_substitutions(text, subs) if subs else text)
    if not stages:
        return False
    return any(sr.action_type in _NETWORK_TYPES
               for s in stages for sr in _classify_stage(s, depth + 1, **kw).walk())


def _program_rce_reason(
    stages: list[Stage],
    subs: list[tuple[str, int, int, str]],
    text: str,
    depth: int,
    kw: dict,
) -> str:
    """Return a block reason when an interpreter executes network-supplied code.

    Covers program text from a substitution (``python -c "$(curl …)"``,
    ``bash <(curl …)``, ``sh <<< "$(curl …)"``) and an output process
    substitution that feeds network data to a stdin-reading interpreter
    (``curl … > >(sh)``). Returns "" when neither applies.
    """
    net_cache: dict[int, bool] = {}

    def sub_is_network(idx: int) -> bool:
        if idx not in net_cache:
            net_cache[idx] = idx < len(subs) and _text_has_network(subs[idx][0], depth + 1, kw)
        return net_cache[idx]

    for stage in stages:
        name_idx = _command_name_sub(stage.tokens)
        if name_idx is not None and sub_is_network(name_idx):
            return "remote code execution: command name comes from the network"
        interpreter, program = _program_text(stage.tokens)
        if any(sub_is_network(int(m)) for m in _PSUB_INDEX_RE.findall(program)):
            return f"remote code execution: {interpreter} program comes from the network"
    for inner_cmd, _start, _end, kind in subs:
        if kind != "process_out":
            continue
        inner = _parse_stages(shell_continuations.join(inner_cmd.strip()))
        if inner and _reads_program_from_pipe(inner[0].tokens) and _text_has_network(text, depth + 1, kw):
            return f"remote code execution: {inner[0].tokens[0]} receives network input via >(…)"
    return ""


def _classify_inner(
    inner_stages: list[Stage],
    outer_stage: Stage,
    depth: int,
    subs: list[tuple[str, int, int, str]],
    sub_depth: int,
    kw: dict,
    start: shell_state.State,
) -> StageResult:
    """Classify the pre-decomposed stages of an unwrapped shell or a substitution.

    *subs* are the substitutions their words hold (classified at
    *sub_depth*); *start* is the shell state they begin in.
    """
    inner_results = _classify_sequence(inner_stages, subs, depth, sub_depth, kw, start)
    if len(inner_results) == 1:
        return inner_results[0]

    # Check pipe composition rules on inner pipeline
    comp_decision, comp_reason, comp_rule = _check_composition(inner_results, inner_stages)
    worst_strictness = max(taxonomy.STRICTNESS.get(sr.decision, 2) for sr in inner_results)
    if comp_decision and taxonomy.STRICTNESS.get(comp_decision, 2) >= worst_strictness:
        sr = StageResult(tokens=outer_stage.tokens, inner_stages=inner_results)
        sr.action_type = inner_results[0].action_type
        sr.decision = comp_decision
        sr.reason = f"unwrapped: {comp_reason}"
        return sr

    # No composition trigger — the most restrictive stage decides, and every
    # inner stage stays reachable under it.
    worst = inner_results[0]
    for sr in inner_results[1:]:
        if taxonomy.STRICTNESS.get(sr.decision, 2) > taxonomy.STRICTNESS.get(worst.decision, 2):
            worst = sr
    return replace(worst, inner_stages=inner_results)
