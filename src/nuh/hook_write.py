"""The write path of the hook: Write, Edit, MultiEdit, NotebookEdit (and apply_patch's operations, patch.py).

Static checks (path, project boundary, content), then the LLM veto gate: the
review of the write and how its verdict merges with the static decision.
"""

import sys

from nuh import (deadline, exec_target, floor, gating, hidden_chars, llm_failure, llm_fallback, llm_merge, llm_skip,
                 paths, steps, taxonomy, trace)
from nuh.content import (OVERSIZE_CATEGORY, RUNNABLE_CATEGORIES, TIMEOUT_CATEGORY, format_content_message,
                         scan_content, without_runnable)
from nuh.llm_meta import build as build_llm_meta


def _check_write_content(tool_name: str, tool_input: dict, content_field: str) -> dict:
    """Shared handler for Write/Edit: path check + boundary check + content inspection."""
    # Write sends the whole new file; Edit's new_string lands somewhere inside it.
    return _write_decision(tool_name, tool_input.get("file_path", ""), tool_input.get(content_field, ""),
                           partial=content_field != "content")


def _write_decision(tool_name: str, file_path: str, content: str, *, partial: bool) -> dict:
    """The static decision for a write: path check, boundary check, content inspection.

    Every check runs and the strictest wins, whatever the ``tools`` word says:
    an outside-project ask never hides a content block, and an LLM allow
    that lowers the ask cannot skip a secret in the content (gating.combine
    keeps the keys behind every ask).
    """
    path_check = paths.check_path(tool_name, file_path)
    boundary_check = paths.check_project_boundary(tool_name, file_path)
    scanned = _scan_and_decide(tool_name, content, file_path, partial=partial)
    combined = gating.combine([d for d in (path_check, boundary_check, scanned) if d])
    combined[steps.BREAKDOWN] = [*(steps.check(name, d) for name, d in (("path", path_check),
                                                                         ("boundary", boundary_check)) if d),
                                 *scanned.get(steps.BREAKDOWN, [])]
    return combined


def _checked(name: str, decision: dict | None) -> dict:
    """*decision* from check *name* with its static breakdown; an allow (and no breakdown) for None."""
    if not decision:
        return {"decision": taxonomy.ALLOW}
    return {**decision, steps.BREAKDOWN: [steps.check(name, decision)]}


def _should_llm_inspect_write(tool_input: dict) -> bool:
    """Check if LLM should inspect this write (FD-080).

    A config that cannot be read counts as LLM on (as in llm_skip): the
    review then fails on the same error and asks, instead of the write
    going through unreviewed.
    """
    try:
        from nuh.config import get_config
        cfg = get_config()
        if not cfg.llm or not cfg.llm.get("enabled", False):
            return False
    except Exception as exc:
        sys.stderr.write(f"nuh: config: llm: {exc}\n")
        return True
    # LLM inspects all writes when enabled — the value is catching
    # what deterministic misses, so we can't filter by decision.
    return True


def _try_llm_write(tool_name: str, tool_input: dict, decision: dict) -> tuple[dict | None, dict]:
    """LLM veto gate for Write/Edit. Returns (decision, llm_meta).

    LLM off → (None, {}) → structural decision stands.
    The LLM's ask asks; an uncertain answer follows ``llm.on_uncertain``.
    Any exception, or no provider answering → ask (llm_failure).
    """
    try:
        from nuh.config import get_config
        cfg = get_config()
        if not cfg.llm or not cfg.llm.get("enabled", False):
            return None, {}
        from nuh import hook
        from nuh.llm import try_llm_write
        deadline.check(f"{tool_name} inspection")
        tr = trace.start_review("write", lambda: _write_trace_classification(tool_name, tool_input, decision))
        with deadline.suspended():
            llm_call = try_llm_write(tool_name, tool_input, decision, cfg.llm, hook._transcript(), trace=tr,
                                     call=hook._tool_call())
        result, meta = _write_llm_outcome(tool_name, llm_call, cfg)
        trace.record_call(llm_call, result)
        return result, meta
    except Exception as exc:
        # A crashed review is no review: the write the LLM was to inspect asks.
        trace.record_error(exc)
        return llm_failure.crashed(tool_name, exc, llm_fallback.configured(llm_fallback.ON_FAILURE))


def _write_llm_outcome(tool_name: str, llm_call, cfg) -> tuple[dict | None, dict]:
    """Turn a write review's cascade result into (decision, llm_meta); (None, {}) when no provider ran."""
    if llm_call.decision is not None:
        return llm_call.decision, build_llm_meta(llm_call, cfg)
    sys.stderr.write(f"nuh: LLM write: no providers responded for {tool_name}\n")
    return None, {}


def _write_trace_classification(tool_name: str, tool_input: dict, decision: dict) -> dict:
    """Everything the deterministic write checks decided, for the LLM trace."""
    record = {
        "tool": tool_name,
        "target": tool_input.get("file_path", "") or tool_input.get("notebook_path", ""),
        "deterministic_decision": decision,
        "content_match": (decision.get("_meta") or {}).get("content_match", ""),
    }
    if "patch" in tool_input:
        record["patch_operation"] = tool_input["patch"]
    return record


def _scan_and_decide(tool_name: str, content: str, file_path: str, *, partial: bool = False,
                     source: str = "") -> dict:
    """Scan *content* written to *file_path* and return the deterministic decision dict.

    Runnable-text patterns (destructive, exfiltration, ...) apply only when
    the target can run (exec_target.py); secrets and hidden characters apply
    everywhere. *partial*: *content* is an edit or hunk, not the whole file.
    *source*: the file a move takes the content from; either one running counts.
    """
    if not content:
        return {"decision": taxonomy.ALLOW}
    matches = scan_content(content)
    runs = ""
    if any(m.category in RUNNABLE_CATEGORIES for m in matches):
        runs = exec_target.runnable_reason(file_path, content, partial=partial) or (
            source and exec_target.runnable_reason(source, content, partial=partial))
        if not runs:
            matches = without_runnable(matches)
    decision = max((m.policy for m in matches), key=lambda p: taxonomy.STRICTNESS.get(p, 2), default="")
    if decision != taxonomy.BLOCK and hidden_chars.find(content) >= 0:
        # What the person approves in the diff is not what lands on disk.
        return _checked("hidden_chars", {"decision": taxonomy.ASK, "_floor": hidden_chars.RULE,
                                          "reason": hidden_chars.reason(content, f"{tool_name} content")})
    if matches:
        result = {
            "decision": decision,
            "reason": format_content_message(tool_name, matches, runs),
            "_meta": {"content_match": ", ".join(m.pattern_desc for m in matches)},
            "_hint": "(content varies per call — cannot be remembered)",
        }
        result[gating.KEYS] = content_keys(matches)
        return _checked("content", result)
    return {"decision": taxonomy.ALLOW}


def content_keys(matches: list) -> list[str]:
    """The human_only keys behind a content ask: runnable text is ``lang_exec``; secrets, config-added
    categories and content too large (or too slow) to scan have their own keys, human-only by default."""
    keys = []
    for match in matches:
        if match.category in RUNNABLE_CATEGORIES:
            keys.append(taxonomy.LANG_EXEC)
        elif match.category == "secret":
            keys.append(gating.CONTENT_SECRET)
        elif match.category in (OVERSIZE_CATEGORY, TIMEOUT_CATEGORY):
            keys.append(gating.CONTENT_TOO_LARGE)
        else:
            keys.append(gating.CONTENT_CUSTOM)
    return list(dict.fromkeys(keys))


def _llm_veto_gate(tool_name: str, tool_input: dict, det_result: dict) -> dict:
    """LLM gate for write-like tools: the review under ``check`` or ``decide`` and its merge (llm_merge.py).

    ``check`` reviews allows; ``decide`` also an ask no ``human_only`` key
    is behind, which a confident LLM allow lowers. Returns det_result
    (possibly augmented), the LLM-escalated decision or the lowered allow,
    with the steps that made it (steps.KEY).
    """
    word = gating.word(tool_name)
    skip = llm_skip.write_ask(tool_name, det_result, word)
    if skip:
        return llm_skip.note(det_result, skip)
    if word not in gating.REVIEWS or not _should_llm_inspect_write(tool_input):
        return det_result
    trail = [steps.static(det_result)]
    llm_decision, llm_meta = _try_llm_write(tool_name, tool_input, det_result)

    # Always attach LLM metadata when LLM was called (even if it agrees)
    if llm_meta:
        det_result.setdefault("_meta", {}).update(llm_meta)
    trail += steps.review(llm_meta, llm_decision)
    final, merged = llm_merge.merge(tool_name, word, det_result, llm_decision, llm_meta)
    final[steps.KEY] = trail + merged
    return final


def _floored(decision: dict) -> bool:
    """True when *decision* comes from a floor rule the LLM may not review."""
    return floor.is_enforced(decision.get("_floor", ""))


def _final_without_llm(decision: dict) -> bool:
    """A write decision the LLM veto gate must not see: a block, or a floored ask."""
    return decision.get("decision") == taxonomy.BLOCK or _floored(decision)


def static_decision(tool_name: str, tool_input: dict) -> dict:
    """The static decision for a Write, Edit, MultiEdit or NotebookEdit call: path, boundary and content checks."""
    if tool_name == "Write":
        return _check_write_content(tool_name, tool_input, "content")
    if tool_name == "Edit":
        return _check_write_content(tool_name, tool_input, "new_string")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        combined = "\n".join(str(e.get("new_string") or "") for e in edits if isinstance(e, dict))
        return _write_decision(tool_name, tool_input.get("file_path", ""), combined, partial=True)
    content = "" if tool_input.get("action", "") == "delete" else str(tool_input.get("new_source") or "")
    return _write_decision(tool_name, tool_input.get("notebook_path", ""), content, partial=True)


def _handle_write_with_llm(tool_name: str, tool_input: dict) -> dict:
    """The write handlers: static checks (``static_decision``), then the LLM veto gate (FD-080)."""
    det_result = static_decision(tool_name, tool_input)
    if _final_without_llm(det_result):
        return llm_skip.note_write(tool_name, det_result)
    return _llm_veto_gate(tool_name, tool_input, det_result)


def handle_write(tool_input: dict) -> dict:
    return _handle_write_with_llm("Write", tool_input)


def handle_edit(tool_input: dict) -> dict:
    return _handle_write_with_llm("Edit", tool_input)


def handle_multiedit(tool_input: dict) -> dict:
    """Guard MultiEdit: path + boundary + content check on every edit's new text + LLM veto."""
    return _handle_write_with_llm("MultiEdit", tool_input)


def handle_notebookedit(tool_input: dict) -> dict:
    """Guard NotebookEdit: path + boundary + content check on the cell source + LLM veto."""
    return _handle_write_with_llm("NotebookEdit", tool_input)
