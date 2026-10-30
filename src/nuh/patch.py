"""Inspect Codex apply_patch operations without modifying the filesystem."""

from dataclasses import dataclass
import os

from nuh import floor, gating, llm_skip, paths, steps, taxonomy

MAX_PATCH_CHARS = 1_048_576
_HEADERS = {"*** Add File: ": "add", "*** Update File: ": "update",
            "*** Delete File: ": "delete"}


@dataclass(frozen=True)
class PatchOperation:
    kind: str
    path: str
    destination: str | None = None
    content: str = ""
    patch: str = ""


def _path(value: str) -> str:
    if not value or not value.strip() or "\x00" in value:
        raise ValueError("empty or invalid patch path")
    return value


def _update_body(lines: list[str]) -> str:
    """Validate hunks and collect post-edit context and additions for scanning."""
    content: list[str] = []
    hunk_lines = 0
    for index, line in enumerate(lines):
        if line == "@@" or line.startswith("@@ "):
            if index and not hunk_lines:
                raise ValueError("empty update hunk")
            hunk_lines = 0
            content.append("\n")
        elif line == "*** End of File":
            if not hunk_lines or index != len(lines) - 1:
                raise ValueError("misplaced end-of-file marker")
        elif line and line[0] in " +-":
            hunk_lines += 1
            if line[0] != "-":
                content.append(line[1:])
        else:
            raise ValueError("unsupported update line")
    if not hunk_lines:
        raise ValueError("empty update")
    return "\n".join(content)


def _operation(header: str, body: list[str]) -> PatchOperation:
    patch = "\n".join([header, *body])
    for prefix, kind in _HEADERS.items():
        if header.startswith(prefix):
            source = _path(header[len(prefix):])
            break
    else:
        raise ValueError("unsupported patch operation")
    destination = None
    if kind == "update" and body and body[0].startswith("*** Move to: "):
        destination = _path(body.pop(0)[len("*** Move to: "):])
    if kind == "delete":
        if body:
            raise ValueError("delete operation has unexpected content")
        content = ""
    elif kind == "add":
        if any(not line.startswith("+") for line in body):
            raise ValueError("invalid add-file content")
        content = "\n".join(line[1:] for line in body)
    else:
        content = _update_body(body)
    return PatchOperation(kind, source, destination, content, patch)


def parse_patch(command: str) -> list[PatchOperation]:
    """Parse the complete supported patch grammar; reject unchecked structure."""
    if not isinstance(command, str) or len(command) > MAX_PATCH_CHARS:
        raise ValueError("patch must be text within the inspection size limit")
    if "\x00" in command:
        raise ValueError("NUL in patch")
    lines = command.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("invalid patch framing")
    operations: list[PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not any(lines[index].startswith(p) for p in _HEADERS):
            body.append(lines[index])
            index += 1
        operations.append(_operation(header, body))
    return operations


def _action_policy(operation: PatchOperation) -> dict:
    from nuh.config import get_config

    actions = [taxonomy.FILESYSTEM_DELETE if operation.kind == "delete"
               else taxonomy.FILESYSTEM_WRITE]
    if operation.destination:
        actions.append(taxonomy.FILESYSTEM_DELETE)
    policies = [(action, taxonomy.get_policy(action, get_config().actions or None))
                for action in actions]
    action, policy = max(policies, key=lambda pair: taxonomy.STRICTNESS.get(pair[1], 3))
    # CONTEXT is evaluated by the literal path and boundary checks below.
    if policy == taxonomy.CONTEXT:
        policy = taxonomy.ALLOW
    return {"decision": policy, "reason": f"apply_patch: {action} → {policy}"}


def handle_apply_patch(tool_input: dict) -> dict:
    """Inspect every affected path and hunk, returning the strictest decision."""
    from nuh.hook_write import _checked, _floored, _llm_veto_gate, _scan_and_decide

    try:
        operations = parse_patch(tool_input.get("command"))
    except (ValueError, TypeError, AttributeError) as exc:
        return _checked("parse", {"decision": taxonomy.BLOCK, "reason": f"apply_patch: cannot inspect patch: {exc}"})
    # Under tools.apply_patch: decide an operation's path and boundary asks join
    # its content decision, so the review that may lower them sees them all.
    word = gating.word("apply_patch")
    review = word == gating.DECIDE
    results: list[dict] = []
    reviews: list[tuple[PatchOperation, dict]] = []  # (operation, the decision its review sees)
    breakdown: list[dict] = []  # every part that objected, for the static step
    seen: set[str] = set()
    for operation in operations:
        results.append(_action_policy(operation))
        breakdown += _objections("action", results[-1], operation.path)
        checks: list[dict] = []
        for raw in (operation.path, operation.destination):
            if raw is None:
                continue
            target = os.path.realpath(raw)
            if target in seen:
                return _checked("overlap", {"decision": taxonomy.BLOCK,
                                            "reason": "apply_patch: overlapping patch targets require review"})
            seen.add(target)
            for name, check in (("path", paths.check_path), ("boundary", paths.check_project_boundary)):
                result = check("apply_patch", raw, literal=True)
                if result:
                    checks.append(result)
                    breakdown += _objections(name, result, raw)
        if operation.kind == "delete" or not review:
            results.extend(checks)
            checks = []
        if operation.kind != "delete":
            # A move lands the content at the destination, from the source file; an
            # update's hunks sit anywhere in the file, so a shebang on any line counts.
            content = _scan_and_decide(
                "apply_patch", operation.content, operation.destination or operation.path,
                partial=operation.kind == "update", source=operation.path if operation.destination else "")
            breakdown += [{**item, "target": _written(operation)} for item in content.pop(steps.BREAKDOWN, [])]
            reviews.append((operation, gating.combine([*checks, content]) if review else content))
    trail = [steps.static(dict(_strictest([*results, *(content for _op, content in reviews)])), breakdown)]
    # A deterministic denial or a floored ask anywhere in the patch needs no
    # external inspection, matching Claude writes and avoiding disclosure of
    # already-blocked content.
    final_without_llm = any(r["decision"] == taxonomy.BLOCK or _floored(r)
                            for r in (*results, *(content for _op, content in reviews)))
    # An ask no review can move holds the whole patch at ask: then no review can change it.
    pinned = "" if final_without_llm else llm_skip.patch_pinned(
        results, [content for _op, content in reviews], word)
    reviewed: list[tuple[str, dict]] = []  # (target, outcome) of each operation the LLM reviewed
    for operation, result in reviews:
        if not (final_without_llm or pinned):
            adapted = {"file_path": _written(operation), "patch": operation.patch}
            result = _llm_veto_gate("apply_patch", adapted, result)
            # The operation's own static step is part of the patch's; its review and merges follow.
            own = (result.pop(steps.KEY, None) or [])[1:]
            if own:
                trail += steps.tagged(own, _written(operation))
                reviewed.append((_written(operation), result))
        results.append(result)
    final = _strictest(results)
    if reviewed:
        winner = next((target for target, result in reviewed if result is final), "")
        trail.append({**steps.merge(steps.STRICTEST, trail[0]["decision"], final["decision"], "strictest"),
                      "target": winner})
    final[steps.KEY] = trail
    if pinned:
        return llm_skip.note(final, pinned)
    return llm_skip.note_write("apply_patch", final) if final_without_llm else final


def _strictest(results: list[dict]) -> dict:
    """Strictest wins; between equals the higher-ranked floor, so the log keeps its rule."""
    return max(results, key=lambda result: (taxonomy.STRICTNESS.get(result["decision"], 3),
                                            floor.rank(result.get("_floor", ""))))


def _written(operation: PatchOperation) -> str:
    """The path *operation* writes: a move's destination, else its path."""
    return operation.destination or operation.path


def _objections(name: str, decision: dict, target: str) -> list[dict]:
    """The breakdown item for check *name* on *target* when *decision* objects; [] for an allow."""
    if decision.get("decision") == taxonomy.ALLOW:
        return []
    return [{**steps.check(name, decision), "target": target}]
