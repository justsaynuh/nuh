"""The hook's other handlers: Read, Glob and Grep (path checks, credential search), and tools without a handler.

A tool with no handler of its own is classified through the classify table (FD-024).
"""

import os

from nuh import context, floor, gating, paths, steps, taxonomy
from nuh.content import is_credential_search
from nuh.hook_write import _checked


def handle_read(tool_input: dict) -> dict:
    return _checked("path", paths.check_path("Read", tool_input.get("file_path", "")))


def handle_glob(tool_input: dict) -> dict:
    raw_path = tool_input.get("path", "")
    if not raw_path:
        return {"decision": taxonomy.ALLOW}  # defaults to cwd
    return _checked("path", paths.check_path("Glob", raw_path))


def handle_grep(tool_input: dict) -> dict:
    raw_path = tool_input.get("path", "")
    # Path check (if path provided)
    if raw_path:
        path_check = paths.check_path("Grep", raw_path)
        if path_check:
            return _checked("path", path_check)

    # Credential search detection
    pattern = tool_input.get("pattern", "")
    if is_credential_search(pattern):
        # Check if searching outside project root
        project_root = paths.get_project_root()
        if project_root:
            resolved_path = paths.resolve_path(raw_path) if raw_path else ""
            real_root = paths.resolve_path(project_root)
            if resolved_path and not (resolved_path == real_root or resolved_path.startswith(real_root + os.sep)):
                return _checked("credential_search", {
                    "decision": taxonomy.ASK,
                    "reason": "Grep: credential search pattern outside project root",
                    "_hint": "(content varies per call — cannot be remembered)",
                })
        else:
            # No project root — any credential search is suspicious
            if raw_path:
                return _checked("credential_search", {
                    "decision": taxonomy.ASK,
                    "reason": "Grep: credential search pattern (no project root)",
                    "_hint": "(content varies per call — cannot be remembered)",
                })

    return {"decision": taxonomy.ALLOW}


def _classify_unknown_tool(canonical: str, tool_input: dict | None = None) -> dict:
    """Classify tools without a dedicated handler via the classify table.

    MCP tools (mcp__*) skip the project classify table — only global config
    can classify them. See FD-024 for rationale.
    """
    try:
        from nuh.config import get_config
        cfg = get_config()

        global_table = taxonomy.build_user_table(cfg.classify_global) if cfg.classify_global else None
        builtin_table = taxonomy.get_builtin_table(cfg.profile)

        # MCP tools: project config cannot classify (untrusted, no builtin coverage)
        is_mcp = canonical.startswith("mcp__")
        project_table = None
        if not is_mcp and cfg.classify_project:
            project_table = taxonomy.build_user_table(cfg.classify_project)

        user_actions = cfg.actions or None
    except Exception:
        return {"decision": taxonomy.ASK, "reason": f"unrecognized tool: {canonical}"}

    action_type = taxonomy.classify_tokens([canonical], global_table, builtin_table, project_table,
                                           profile=cfg.profile)

    policy = taxonomy.get_policy(action_type, user_actions)
    if policy == taxonomy.ALLOW:
        decision = {"decision": taxonomy.ALLOW}
    elif policy == taxonomy.CONTEXT:
        verdict = context.resolve_context(action_type, tool_input=tool_input)
        decision = {"decision": verdict[0], "reason": verdict[1]}
        if floor.rule_of(verdict):
            decision["_floor"] = floor.rule_of(verdict)
    else:
        reason = f"unrecognized tool: {canonical}" if action_type == taxonomy.UNKNOWN else f"{action_type} → {policy}"
        decision = {"decision": taxonomy.BLOCK if policy == taxonomy.BLOCK else taxonomy.ASK, "reason": reason}
    decision[gating.KEYS] = [action_type]  # the human_only key behind an ask (gating.keys_of)
    decision[steps.BREAKDOWN] = [{"check": "classify", "action_type": action_type, "policy": policy,
                                  "decision": decision["decision"], "reason": decision.get("reason", "")}]
    return decision
