"""Decision logging — JSONL log with a size cap and rotation.

Tool input is stored raw, never secret-redacted: by the time a tool call exists its
secret is already in the agent's transcript and on the model's server, so masking the
log protects nothing, and replaying a logged decision needs the input as it was.
"""

import json
import os
import sys
from datetime import datetime, timezone


_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "nuh")
LOG_PATH = os.path.join(_CONFIG_DIR, "nuh.log")

_DEFAULT_VERBOSITY = "all"
_DEFAULT_MAX_SIZE = 50_000_000  # 50 MB
_DEFAULT_BACKUPS = 5  # nuh.log.1 … nuh.log.5
DEFAULT_MAX_INPUT_CHARS = 20_000


def log_decision(entry: dict, log_config: dict | None = None) -> None:
    """Write a JSONL log entry. Never raises."""
    try:
        cfg = log_config or {}
        verbosity = cfg.get("verbosity", _DEFAULT_VERBOSITY)
        decision = entry.get("decision", "allow")

        if verbosity == "blocks_only" and decision != "block":
            return
        if verbosity == "decisions" and decision == "allow":
            return

        if "ts" not in entry:
            entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        if "tool_input" in entry:
            max_chars = _int_setting(cfg, "max_input_chars", DEFAULT_MAX_INPUT_CHARS, minimum=1)
            entry["tool_input"], truncated = cap_tool_input(entry["tool_input"], max_chars)
            if truncated:
                entry["tool_input_truncated"] = True

        line = json.dumps(entry, separators=(",", ":")) + "\n"

        os.makedirs(_CONFIG_DIR, exist_ok=True)

        max_size = _int_setting(cfg, "max_size_bytes", _DEFAULT_MAX_SIZE, minimum=1)
        try:
            if os.path.getsize(LOG_PATH) > max_size:
                _rotate(_int_setting(cfg, "backups", _DEFAULT_BACKUPS, minimum=0))
        except OSError:
            # No log file yet (first write) or it vanished mid-check: nothing
            # to rotate, and the append below creates or surfaces real errors.
            pass

        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception as exc:
        try:
            sys.stderr.write(f"nuh: log: {exc}\n")
        except Exception:
            pass


def cap_tool_input(value: object, max_chars: int = DEFAULT_MAX_INPUT_CHARS) -> tuple[object, bool]:
    """Return (copy of *value* with each string cut to *max_chars* and marked, whether any was cut).

    One huge Write cannot bloat the log; everything else is kept as it was.
    """
    if isinstance(value, str):
        if len(value) > max_chars:
            return f"{value[:max_chars]}…[truncated {len(value) - max_chars} chars]", True
        return value, False
    if isinstance(value, dict):
        out: dict = {}
        truncated = False
        for key, item in value.items():
            out[key], cut = cap_tool_input(item, max_chars)
            truncated = truncated or cut
        return out, truncated
    if isinstance(value, (list, tuple)):
        items = [cap_tool_input(item, max_chars) for item in value]
        return [item for item, _cut in items], any(cut for _item, cut in items)
    return value, False


def _int_setting(cfg: dict, key: str, default: int, *, minimum: int) -> int:
    """Integer log setting; a malformed value warns and falls back to *default*."""
    value = cfg.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        sys.stderr.write(f"nuh: config: log.{key}: expected an integer >= {minimum}, "
                         f"got {value!r}; using {default}\n")
        return default
    return value


def _backup_path(index: int) -> str:
    return f"{LOG_PATH}.{index}"


def _rotate(backups: int = _DEFAULT_BACKUPS) -> None:
    """Rotate log: .N-1 -> .N, …, current -> .1, start fresh. Oldest is dropped."""
    try:
        if not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) == 0:
            return
        if backups == 0:
            os.unlink(LOG_PATH)
            return
        if os.path.exists(_backup_path(backups)):
            os.unlink(_backup_path(backups))
        for index in range(backups - 1, 0, -1):
            if os.path.exists(_backup_path(index)):
                os.rename(_backup_path(index), _backup_path(index + 1))
        os.rename(LOG_PATH, _backup_path(1))
    except OSError as exc:
        sys.stderr.write(f"nuh: log: rotation: {exc}\n")
        try:
            with open(LOG_PATH, "w") as f:
                f.write("")
        except OSError as exc2:
            sys.stderr.write(f"nuh: log: rotation reset: {exc2}\n")


def build_entry(
    tool: str, input_summary: str, decision: str, reason: str,
    agent: str, hook_version: str, total_ms: int,
    meta: dict, transcript_path: str = "",
    tool_input: dict | None = None,
    subagent: dict | None = None,
    steps: list | None = None,
) -> dict:
    """Build a structured log entry with core + detail fields.

    *tool_input* is stored whole as ``tool_input``; log_decision caps each of
    its strings at ``log.max_input_chars`` and redacts nothing. *subagent*
    carries the payload's agent_id / agent_type / tool_use_id; non-empty ones are logged so a
    decision can be traced to the subagent transcript that made the call.
    *steps* are the decision's steps (steps.py), logged as ``steps``, bounded
    by steps.for_log: the static verdict and its breakdown (Bash's stages),
    the LLM's own verdict and reasoning, and each merge.
    """
    from nuh import steps as decision_steps  # lazy import, like get_project_root
    from nuh.paths import get_project_root  # lazy import to avoid circular

    entry: dict = {
        "id": os.urandom(8).hex(),
        "user": os.environ.get("USER", ""),
        "agent": agent,
        "hook_version": hook_version,
        "tool": tool,
        "input": input_summary,
        "project": get_project_root() or "",
        "session": os.path.basename(transcript_path) if transcript_path else "",
        "decision": decision,
        "reason": reason,
        "action_type": _extract_action_type(meta),
        "ms": total_ms,
    }
    if tool_input is not None:
        entry["tool_input"] = tool_input
    for key in ("agent_id", "agent_type", "tool_use_id"):
        val = (subagent or {}).get(key)
        if val:
            entry[key] = val

    if steps:
        entry["steps"] = decision_steps.for_log(steps)

    # Detail: llm — how the LLM step ran, whenever it was attempted (provider
    # set or cascade exists) or skipped: too large to send whole, or no verdict
    # could change the ask (no_effect, with skip_reason; llm_skip.py). Its
    # verdict, reasoning and cap are the llm and cap steps.
    llm_provider = meta.get("llm_provider", "")
    llm_cascade = meta.get("llm_cascade")
    llm_skipped = meta.get("llm_skipped", "")
    if llm_provider or llm_cascade or llm_skipped:
        llm: dict = {
            "provider": llm_provider or "(none)",
            "model": meta.get("llm_model", ""),
            "ms": meta.get("llm_latency_ms", 0),
        }
        if llm_cascade:
            llm["cascade"] = llm_cascade
        if llm_skipped:
            llm["skipped"] = llm_skipped
        if meta.get("llm_skip_reason"):
            llm["skip_reason"] = meta["llm_skip_reason"]
        if meta.get("llm_error"):
            llm["error"] = meta["llm_error"]  # why the review got no verdict (llm_failure)
        if meta.get("llm_action"):
            llm["action"] = meta["llm_action"]
        prompt = meta.get("llm_prompt")
        if prompt:
            llm["prompt"] = prompt
        entry["llm"] = llm

    # Detail: hint, content_match
    hint = meta.get("hint")
    if hint:
        entry["hint"] = hint
    content = meta.get("content_match")
    if content:
        entry["content_match"] = content

    return entry


def _extract_action_type(meta: dict) -> str:
    """Extract primary action_type: first ask/block stage, else first stage."""
    stages = meta.get("stages", [])
    for s in stages:
        if s.get("decision") in ("ask", "block"):
            return s.get("action_type", "")
    return stages[0].get("action_type", "") if stages else ""


def input_summary(tool: str, tool_input: dict) -> str:
    """The entry's one-line input summary (raw, cut short)."""
    if tool == "Bash":
        return tool_input.get("command", "")[:200]

    if tool in ("Read", "Glob"):
        return tool_input.get("file_path", "") or tool_input.get("path", "") or tool_input.get("pattern", "")

    if tool == "Grep":
        path = tool_input.get("path", "")
        pattern = tool_input.get("pattern", "")
        return f"pattern={pattern} path={path}" if path else f"pattern={pattern}"

    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return tool_input.get("file_path", "") or tool_input.get("notebook_path", "")

    if tool.startswith("mcp__"):
        for key, val in tool_input.items():
            return f"{key}={str(val)[:100]}"
        return ""

    return ""


def log_paths() -> list[str]:
    """The live log path (whether or not it exists), then each rotated backup, newest first."""
    paths = [LOG_PATH]
    index = 1
    while os.path.isfile(_backup_path(index)):
        paths.append(_backup_path(index))
        index += 1
    return paths

