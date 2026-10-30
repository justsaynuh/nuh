"""The tool call under review, as the agent made it: what system 1 is shown."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """The call under review as the agent made it (the hook payload's fields).

    *replies* keeps a reviewer's answer per request for the rest of the hook
    invocation: a Codex ``apply_patch`` is reviewed once per file operation,
    but a request built from the whole call is the same each time, so the
    reviewer is asked once and a slow one cannot stack timeouts past the
    hook's own. *facts* keeps the context items system 1 computed about the
    call (system1_facts), so each is computed at most once per invocation.
    """

    tool_name: str
    tool_input: dict | str
    cwd: str = ""
    agent: str = "claude"
    replies: dict = field(default_factory=dict, compare=False, repr=False)
    facts: dict = field(default_factory=dict, compare=False, repr=False)
