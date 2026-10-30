"""Agent support — tool name mapping, agent detection, output formatting.

Supports Claude Code and Codex. The hook script detects the calling agent from payload
fields and formats output accordingly.
"""

import os
from pathlib import Path

from nuh import notice as _notice

# ---------------------------------------------------------------------------
# Tool name → canonical handler name
# ---------------------------------------------------------------------------

TOOL_MAP: dict[str, str] = {
    # Claude Code (canonical — identity mapping)
    "Bash": "Bash",
    "Read": "Read",
    "Write": "Write",
    "Edit": "Edit",
    "MultiEdit": "MultiEdit",
    "NotebookEdit": "NotebookEdit",
    "Glob": "Glob",
    "Grep": "Grep",
}


def normalize_tool(tool_name: str) -> str:
    """Map agent-specific tool name to canonical handler name."""
    return TOOL_MAP.get(tool_name, tool_name)


# ---------------------------------------------------------------------------
# Agent detection
# ---------------------------------------------------------------------------

# Agent type constants
CLAUDE = "claude"
CODEX = "codex"


def detect_agent(data: object) -> str:
    """Detect which agent is calling.

    Accepts either a full payload dict or a bare tool name string.
    """
    override = os.environ.get("NUH_AGENT")
    if override is not None:
        if override not in (CLAUDE, CODEX):
            raise ValueError("NUH_AGENT must be codex or claude")
        return override
    # Presence identifies the interface even if the value needs validation.
    if isinstance(data, dict) and "turn_id" in data:
        return CODEX
    return CLAUDE


# ---------------------------------------------------------------------------
# Output formatting per agent
# ---------------------------------------------------------------------------

def format_block(reason: str, agent: str, notice: str = "") -> dict:
    """Format a block/deny response for the given agent.

    *notice* is the user-facing line (nuh.notice); without one, the reason
    alone is shown, made Markdown-safe the same way.
    """
    branded = f"nuh. {reason}" if reason else "nuh."
    result: dict = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}
    if branded:
        result["hookSpecificOutput"]["permissionDecisionReason"] = branded
    if agent == CLAUDE and reason:
        # The deny reason goes to the agent; systemMessage is what the user
        # sees, including in Claude clients that do not show hook reasons.
        result["systemMessage"] = notice or _fallback_notice(_notice.BLOCK, reason)
    return result


def format_ask(reason: str, agent: str, warning: str = "", notice: str = "") -> dict:
    """Format an ask/confirm response for the given agent.

    *notice* is the user-facing line (nuh.notice); without one, the reason
    is shown, made Markdown-safe the same way. Codex shows the LLM's
    *warning* below the reason.
    """
    if agent == CODEX:
        details = "\n".join(part for part in (reason, warning) if part)
        return format_block(
            "Review required: Codex PreToolUse cannot request approval; "
            "this attempt is blocked. Review the action and policy before retrying."
            + (f"\n{details}" if details else ""), agent,
        )
    branded = f"nuh? {reason}" if reason else "nuh?"
    result: dict = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask"}}
    if branded:
        result["hookSpecificOutput"]["permissionDecisionReason"] = branded
    # Top-level systemMessage is shown to the user only. Carry the ask reason
    # there too: some Claude clients (the app) do not show the hook's reason.
    if reason or notice:
        result["systemMessage"] = notice or _fallback_notice(_notice.ASK, reason)
    return result


def _fallback_notice(head: str, reason: str) -> str:
    """The notice when the caller built none: the reason's first line (later lines are hints, not shown)."""
    return _notice.render(head, reason.partition("\n")[0])


def format_allow(agent: str) -> dict:
    """Format an allow response for the given agent."""
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def format_error(error: str, agent: str) -> dict:
    """Format an error response (deny with error message)."""
    msg = (
        f"nuh: internal error — blocked for safety: {error}\n"
        "      To bypass: nuh uninstall | To debug: nuh log"
    )
    result: dict = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": msg,
    }}
    if agent == CLAUDE:
        result["systemMessage"] = _notice.error(error)
    return result


# ---------------------------------------------------------------------------
# Agent install configs
# ---------------------------------------------------------------------------

# Per-agent tool matchers for hook registration.
AGENT_TOOL_MATCHERS: dict[str, list[str]] = {
    CLAUDE: ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "mcp__.*",
             "WebFetch", "WebSearch"],
    CODEX: ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep",
            "mcp__.*", "WebFetch", "WebSearch", "apply_patch"],
}

# Settings/hooks file paths per agent.
AGENT_SETTINGS: dict[str, Path] = {
    CLAUDE: Path.home() / ".claude" / "settings.json",
    CODEX: Path.home() / ".codex" / "hooks.json",
}

# Agents whose config format we can auto-install into.
INSTALLABLE_AGENTS = {CLAUDE, CODEX}

AGENT_NAMES: dict[str, str] = {
    CLAUDE: "Claude Code",
    CODEX: "Codex",
}
