"""One Bash stage classified: wrappers unwrapped, nested commands followed, paths and redirects checked.

Privilege wrappers, passthrough wrappers, xargs, find -exec, task-runner
recipes, package runners and shells (``bash -c``, heredocs, ``source``,
``eval``) each classify the command they run, recursing back into
``_classify_stage``.
"""

import os
from dataclasses import replace

from nuh import (bash_type_floor, catastrophic, context, deadline, exec_source, file_operands, find_args, floor,
                 floor_rules, found_files, hidden_chars, nested_floor, path_names, runners,
                 sensitive_paths, shell_env, shell_expand, shell_program_text, shell_state, shell_unresolved,
                 task_recipe, task_runners, taxonomy)
from nuh.bash_assignments import _classify_assignments
from nuh.bash_compose import _floor_rank
from nuh.bash_decompose import _wrapped_stage
from nuh.bash_paths import (_body_text_words, _check_awk_files, _check_extracted_paths, _inline_code_words,
                            _path_operands, _URL_RE)
from nuh.bash_policy import _apply_policy, _extract_primary_target, _stage_program
from nuh.bash_programs import _SOURCE_COMMANDS, _source_program, _stdin_readings
from nuh.bash_redirects import _apply_redirect_guard, _classify_redirect_write
from nuh.bash_shell import _unwrap_shell
from nuh.bash_types import (_escalate, _escalate_unknown, _MAX_UNWRAP_DEPTH, _obfuscated_result, _only_unknown_asks,
                            _runs_found_result, _shell_words, Stage, StageResult)
from nuh.content import format_content_message, scan_content


def _classify_stage(stage: Stage, depth: int = 0, **kw) -> StageResult:
    """Judge every GNU/POSIX option reading through the normal stage checks."""
    words = _shell_words(stage)
    tried = [_classify_stage_reading(replace(stage, tokens=argv + stage.tokens[len(words):]), depth, **kw)
             for argv in file_operands.option_readings(words)]
    sr = max(tried, key=lambda r: (taxonomy.STRICTNESS.get(r.decision, 2), _floor_rank(r)))
    sr.inner_stages += [other for other in tried if other is not sr]
    return sr


def _classify_stage_reading(stage: Stage, depth: int = 0, **kw) -> StageResult:
    """Classify a single pipeline stage, then apply the floor for the environment it sets.

    A stage naming a path through a variable nuh cannot know
    (``shell_unresolved.unresolved_uses``: ``for f in $(cat list); do cat "$f"``)
    is unknown, and so is one naming a relative path in a working directory
    nuh could not follow (``cd "$X" && rm -rf build``) or holding a word whose
    brace expansion nuh cannot read (``Stage.brace_unread``).
    """
    readings = _stdin_readings(stage) if stage.stdin_ambiguous else []
    if readings:
        tried = [_classify_stage_reading(reading, depth, **kw) for reading in readings]
        sr = max(tried, key=lambda r: (taxonomy.STRICTNESS.get(r.decision, 2), _floor_rank(r)))
        sr.inner_stages += [other for other in tried if other is not sr]
    else:
        with shell_env.running(stage.prefix_env):
            sr = _classify_stage_body(stage, depth, **kw)
    if stage.env_floor is not None:
        _escalate(sr, stage.env_floor)
    if stage.stdin_ambiguous:
        policy = taxonomy.get_policy(taxonomy.UNKNOWN, kw.get("user_actions"))
        _escalate(sr, (taxonomy.BLOCK if policy == taxonomy.BLOCK else taxonomy.ASK,
                       "multiple stdin redirects differ between bash and zsh"))
    words, literals = _path_operands(stage, sr)
    state = shell_state.current()
    unresolved = list(dict.fromkeys(name for w in words if not _URL_RE.match(w)
                                    for name in shell_unresolved.unresolved_uses(w, state)))
    if unresolved:
        sr = _escalate_unknown(sr, shell_unresolved.unresolved_reason(unresolved, state), kw.get("user_actions"))
    if stage.brace_unread:
        sr = _escalate_unknown(sr, f"brace expansion nuh cannot read: {stage.brace_unread}", kw.get("user_actions"))
    if shell_state.unknown_cwd():
        def relative(word: str) -> bool:
            return bool(word) and not word.startswith(("/", "~", "$HOME", "${HOME}", "-"))

        found = next((w for w in words + literals if relative(w)), None)
        if found is not None:
            sr = _escalate_unknown(sr, f"relative path after a cd nuh cannot follow: {found}",
                                   kw.get("user_actions"))
    return sr


def _classify_stage_body(
    stage: Stage,
    depth: int = 0,
    *,
    global_table: list | None = None,
    builtin_table: list | None = None,
    project_table: list | None = None,
    user_actions: dict[str, str] | None = None,
    profile: str = "full",
) -> StageResult:
    """Classify a single pipeline stage."""
    deadline.check("Bash classification")
    tokens = stage.tokens
    sr = StageResult(tokens=tokens)


    if stage.unparseable:
        sr.reason = "unparseable command (shlex error)"
        return sr
    if not tokens:
        if stage.redirects:  # a redirect with no command: the writes are all it does
            first, *rest = stage.redirects
            sr = _classify_redirect_write(stage, first, user_actions)
            sr.redirect_target = first.target
            return _apply_redirect_guard(replace(stage, redirects=rest), sr, user_actions=user_actions)
        sr.reason = "empty stage"
        return sr

    # Pre-set action type (e.g. env var with exec sink)
    if stage.action_hint:
        sr.action_type = stage.action_hint
        sr.default_policy = taxonomy.get_policy(sr.action_type, user_actions)
        _apply_policy(sr)
        sr.reason = stage.action_reason or f"env var exec sink: {sr.action_type} → {sr.decision}"
        return _apply_redirect_guard(stage, sr, user_actions=user_actions)

    assigned = shell_expand.assignments(tokens)
    if assigned is not None:
        return _apply_redirect_guard(stage, _classify_assignments(tokens, assigned, user_actions),
                                     user_actions=user_actions)

    # Privilege wrappers: classify what they run, then floor the result at ask
    kw = dict(global_table=global_table, builtin_table=builtin_table, project_table=project_table,
              user_actions=user_actions, profile=profile)
    privileged = _classify_privileged(stage, depth, kw)
    if privileged is not None:
        return _apply_redirect_guard(stage, privileged, user_actions=user_actions)

    # Shell unwrapping
    unwrapped = _unwrap_shell(stage, depth, global_table=global_table,
                              builtin_table=builtin_table, project_table=project_table,
                              user_actions=user_actions, profile=profile)
    if unwrapped is not None:
        return _apply_redirect_guard(stage, unwrapped, user_actions=user_actions)

    # Classify tokens
    sourced = _source_program(stage) if tokens[0] in _SOURCE_COMMANDS else None
    if sourced is not None:
        sr.action_type = floor_type = taxonomy.LANG_EXEC
    else:
        sr.action_type = taxonomy.classify_tokens(tokens, global_table, builtin_table, project_table,
                                                  profile=profile)
        # The floors keyed on a type hold whatever classify or the profile made of it (bash_type_floor).
        floor_type = bash_type_floor.builtin_type(
            tokens, sr.action_type, configured=bool(global_table or project_table) or profile != "full")
    sr.default_policy = taxonomy.get_policy(sr.action_type, user_actions)
    text = _body_text_words(stage)

    # Apply policy → decision
    if sourced is not None:
        program, unreadable = sourced
        _apply_policy(sr, program)
        if unreadable and sr.default_policy == taxonomy.CONTEXT:
            sr.reason = f"source: {unreadable}"
    else:
        program = _stage_program(stage) if taxonomy.LANG_EXEC in (sr.action_type, floor_type) else None
        _apply_policy(sr, program if sr.action_type == taxonomy.LANG_EXEC else None,
                      [tok for i, tok in enumerate(tokens) if i not in text])
    bash_type_floor.apply(sr, tokens, floor_type)

    # Path extraction + checking (regardless of policy)
    code, files = _inline_code_words(stage, sr), file_operands.files(_shell_words(stage))
    _escalate(sr, _check_extracted_paths(tokens, sr.action_type, code, files, text))
    if floor_type != sr.action_type:
        # Deletes and writes as nuh's tables see them: catastrophic targets and protected writes.
        _escalate(sr, _check_extracted_paths(tokens, floor_type, code, files, text))
    _escalate(sr, _check_awk_files(tokens, user_actions))
    bash_type_floor.script_hidden_chars(sr, tokens, program, floor_type, profile)
    # Floor rules on the command's words (regardless of policy)
    for verdict in floor_rules.token_floor(tokens):
        _escalate(sr, verdict)

    return _apply_redirect_guard(stage, sr, user_actions=user_actions)


def _classify_privileged(stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """Classify a privilege wrapper's command, floored at ask (rule ``privilege``); None otherwise.

    ``sudo rm -rf /`` keeps the inner block; ``sudo apt install x`` asks,
    and the LLM may not lower it.
    """
    found = floor_rules.privilege_wrapper(stage.tokens)
    if found is None:
        return None
    wrapper, inner = found
    if depth >= _MAX_UNWRAP_DEPTH:
        return _obfuscated_result(stage.tokens, "excessive wrapper nesting", kw.get("user_actions"))
    if inner:
        sr = _classify_stage(_wrapped_stage(stage, inner), depth + 1, **kw)
    else:
        sr = StageResult(tokens=stage.tokens, reason=f"{wrapper}: no command nuh can read")
    _escalate(sr, floor.Verdict(taxonomy.ASK, f"privilege wrapper: {wrapper} runs it as another user",
                                "privilege"))
    return sr


def _classify_runner(stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """The command a package runner runs, classified; None to classify the runner itself.

    A command nuh does not know (``npx tsc``) keeps the runner's own
    classification, as before runners were unwrapped; a known one (``uv run
    rm -rf ~``) is judged as itself. A user's global ``classify:`` entry for
    the runner command wins, as it does for any command, except over a floor
    (nested_floor). A package the runner fetches is judged by its name only
    when that does not allow (``npx rimraf ~``).
    """
    only_floors = nested_floor.floors_only(stage, kw)
    found = runners.unwrap(stage.tokens)
    inner = (found.inner or found.fetched) if found is not None else None
    if found is None or not inner:
        return None
    sr = _classify_stage(_wrapped_stage(stage, inner), depth + 1, **kw)
    if _only_unknown_asks(sr) or not nested_floor.kept(sr, only_floors):
        return None
    if not found.inner and sr.decision == taxonomy.ALLOW:
        # A fetched package may not be the program its name says, so the name
        # only tightens: `npx rimraf ~` deletes ~, `npx cat x` stays package_run.
        return None
    sr.reason = f"{found.name}: {sr.reason}"
    return sr


def _classify_find_exec(stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """``find … -exec CMD {} ;`` classified as find plus CMD on a file under each root; None without -exec.

    CMD gets full stage classification at depth+1 (as xargs's command does),
    with ``{}`` standing for a path under the root. An unknown CMD asks; it is
    never taken for a delete. A delete with no test in the expression wipes
    the root, which the catastrophic floor checks.
    """
    tokens = stage.tokens
    clauses = find_args.exec_clauses(tokens) if find_args.is_find(tokens) else []
    if not clauses:
        return None
    own = _classify_stage(Stage(tokens=find_args.without_exec(tokens), operator=stage.operator), depth, **kw)
    roots, filtered = find_args.roots(tokens), find_args.has_test(tokens)
    payloads: list[StageResult] = []
    for action, command in clauses:
        if not command:
            payloads.append(StageResult(tokens=tokens, reason=f"find {action} with no command"))
            continue
        if found_files.runs_found_file(command):
            payloads.append(_runs_found_result(tokens, f"find {action} runs the files it finds (unknown until run)",
                                               kw.get("user_actions")))
            continue
        for root in roots:
            sr = _classify_stage(Stage(tokens=find_args.payload_for_root(command, root)), depth + 1, **kw)
            if nested_floor.deletes(sr, command):
                verdict = catastrophic.check_delete_target(root, find_root=filtered)
                if verdict is not None:
                    _escalate(sr, verdict)
                _check_find_parents([root], sr)
            elif sr.action_type == taxonomy.FILESYSTEM_WRITE:
                # A write's context is its last operand (mv's destination), which is not
                # the found file: files found under a root outside the project ask.
                verdict = context.resolve_filesystem_context(root)
                if verdict[0] != taxonomy.ALLOW:
                    _escalate(sr, verdict)
            sr.reason = f"find {action}: {sr.reason}"
            payloads.append(sr)
    worst = own
    for sr in payloads:
        if taxonomy.STRICTNESS.get(sr.decision, 2) > taxonomy.STRICTNESS.get(worst.decision, 2) or (
                sr.floor and sr.decision == worst.decision != taxonomy.ALLOW and not worst.floor):
            worst = sr
    result = replace(worst, tokens=tokens, inner_stages=[own, *payloads])
    # A classify entry for find does not set the payloads aside: they are commands of their own.
    return result if nested_floor.kept(result, kw.get("profile") == "none") else None


def _check_find_parents(roots: list[str], sr: StageResult) -> None:
    """A delete of files find found under a root above nuh's code or agent wiring: the parent check ``-delete`` gets.

    Filtered or not, find descends into the protected directory and can remove what is in it.
    """
    for root in roots:
        verdict = sensitive_paths.check_protected_write(root, parents=True)
        if verdict is not None:
            _escalate(sr, verdict)


def _classify_task(stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """A task runner's recipe (``make test``, ``npm run build``) classified; None when not one.

    A user's global ``classify:`` entry for the command wins, as for any
    command, except over a floor (nested_floor).
    """
    sr = _task_result(stage, depth, kw)
    return sr if sr is not None and nested_floor.kept(sr, nested_floor.floors_only(stage, kw)) else None


def _task_result(stage: Stage, depth: int, kw: dict) -> StageResult | None:
    """The recipe's result, an ask when the recipe cannot be read, or None when *stage* runs no task."""
    unreadable = ""
    try:
        recipe = task_runners.resolve(stage.tokens)
    except task_recipe.Unreadable as exc:
        recipe, unreadable = None, str(exc)
    if (recipe is not None or unreadable) and shell_state.unknown_cwd():
        unreadable = "runs in a directory nuh cannot follow (after cd), so its recipe file is unknown"
    if unreadable:
        return StageResult(tokens=stage.tokens, action_type=taxonomy.LANG_EXEC,
                           default_policy=taxonomy.get_policy(taxonomy.LANG_EXEC, kw.get("user_actions")),
                           decision=taxonomy.ASK, reason=f"{os.path.basename(stage.tokens[0])}: {unreadable}")
    if recipe is None:
        return None
    return _classify_recipe(stage, recipe, depth, kw)


def _classify_recipe(stage: Stage, recipe: task_recipe.Recipe, depth: int, kw: dict) -> StageResult:
    """Classify a recipe like a script: content inspection plus each line as a command.

    A line that is only an unknown command is covered by the content scan, as
    in a script; any other line's ask or block is the recipe's. When unknown
    lines rest on the scan, the stage is a lang_exec "script clean" allow, so
    the LLM veto (when on) reviews the recipe text.
    """
    user_actions = kw.get("user_actions")
    text = "\n".join(recipe.lines)
    where = f"{recipe.runner} recipe ({path_names.friendly_path(recipe.source)})"
    lines = [text] if recipe.one_shell and text else recipe.lines
    # Each line runs in a new shell in the runner's directory: paths there are relative to it.
    with shell_state.entered(shell_state.State(cwd=recipe.cwd or shell_state.current().cwd)):
        results = [_classify_stage(Stage(tokens=["sh", "-c", shell_program_text.literal(line)]), depth, **kw)
                   for line in lines]
    sr = StageResult(tokens=stage.tokens, inner_stages=results, decision=taxonomy.ALLOW)
    judged = [(r, line) for r, line in zip(results, lines) if not _only_unknown_asks(r)]
    # The content scan covers the lines no classification judged; a judged
    # `rm -rf build` is a delete inside the project, not a destructive pattern.
    unjudged = "\n".join(line for r, line in zip(results, lines) if _only_unknown_asks(r))
    matches = scan_content(unjudged) if unjudged else []
    if matches:
        sr.action_type = taxonomy.LANG_EXEC
        sr.decision = taxonomy.BLOCK if any(m.policy == taxonomy.BLOCK for m in matches) else taxonomy.ASK
        sr.reason = format_content_message(where, matches)
    if hidden_chars.find(text) >= 0:
        _escalate(sr, floor.Verdict(taxonomy.ASK, hidden_chars.reason(text, where), hidden_chars.RULE))
    for result, line in judged:
        for inner in result.walk():
            if inner.decision == taxonomy.ALLOW or (inner.action_type == taxonomy.UNKNOWN and not inner.floor
                                                    and not inner.unreadable_program
                                                    and inner.decision == taxonomy.ASK):
                continue  # an unknown command rests on the content scan, as in a script
            stricter = taxonomy.STRICTNESS.get(inner.decision, 2) > taxonomy.STRICTNESS.get(sr.decision, 0)
            if stricter or (inner.floor and inner.decision == sr.decision and not sr.floor):
                sr.action_type, sr.decision, sr.floor = inner.action_type, inner.decision, inner.floor
                sr.reason = f"{recipe.runner}: {inner.reason} (line: {line[:100]})"
                # The path the hint suggests trusting is the line's, not make's argv.
                sr.redirect_target = (inner.redirect_target or inner.context_target
                                      or _extract_primary_target(inner.tokens))
    if recipe.opaque_reason:
        policy = taxonomy.get_policy(taxonomy.UNKNOWN, user_actions)
        sr.action_type = taxonomy.UNKNOWN
        _escalate(sr, (taxonomy.BLOCK if policy == taxonomy.BLOCK else taxonomy.ASK, recipe.opaque_reason))
    if sr.decision != taxonomy.ALLOW and sr.reason:
        return sr
    if len(judged) < len(results):
        sr.action_type = taxonomy.LANG_EXEC
        sr.inline_program = exec_source.InlineProgram(recipe.runner.split()[0], text, where)
        sr.default_policy = taxonomy.get_policy(taxonomy.LANG_EXEC, user_actions)
        sr.decision = sr.default_policy if sr.default_policy in (taxonomy.ASK, taxonomy.BLOCK) else taxonomy.ALLOW
        sr.reason = f"script clean: {where}"
    else:
        sr.action_type = taxonomy.PACKAGE_RUN
        sr.default_policy = taxonomy.get_policy(taxonomy.PACKAGE_RUN, user_actions)
        sr.decision = sr.default_policy if sr.default_policy != taxonomy.CONTEXT else taxonomy.ALLOW
        sr.reason = f"{recipe.runner}: every recipe line allowed" if lines else f"{recipe.runner}: nothing to run"
    return sr
