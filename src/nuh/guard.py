"""Standalone guard source, copied into installed hooks without package dependencies."""

import io
import json
import os
import sys
import time


def _detect_agent(data: object) -> str:
    """Mirror the adapter's caller contract without importing the package."""
    override = os.environ.get("NUH_AGENT")
    if override is not None:
        if override not in {"codex", "claude"}:
            raise ValueError("NUH_AGENT must be codex or claude")
        return override
    if not isinstance(data, dict):
        raise ValueError("hook input must be an object")
    return "codex" if "turn_id" in data else "claude"


def _validate_input(data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("hook input must be an object")
    if not isinstance(data.get("tool_name"), str) or not data["tool_name"]:
        raise ValueError("hook input requires tool_name")
    if not isinstance(data.get("tool_input", {}), dict):
        raise ValueError("hook input requires tool_input object")
    if data.get("hook_event_name", "PreToolUse") != "PreToolUse":
        raise ValueError("unsupported hook event")


def _validate_output(output: str, agent: str) -> None:
    data = json.loads(output)
    allowed = {"hookSpecificOutput"}
    if agent == "claude":
        allowed.update({"systemMessage", "suppressOutput", "continue", "stopReason"})
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("invalid hook response object")
    specific = data.get("hookSpecificOutput")
    fields = {"hookEventName", "permissionDecision", "permissionDecisionReason"}
    if not isinstance(specific, dict) or set(specific) - fields:
        raise ValueError("invalid hookSpecificOutput")
    if specific.get("hookEventName") != "PreToolUse":
        raise ValueError("invalid response event")
    decisions = {"allow", "deny", "ask"} if agent == "claude" else {"allow", "deny"}
    if specific.get("permissionDecision") not in decisions:
        raise ValueError("unsupported permission decision")
    for field in ("permissionDecisionReason",):
        if field in specific and not isinstance(specific[field], str):
            raise ValueError("invalid response reason")
    for field in ("systemMessage", "stopReason"):
        if field in data and not isinstance(data[field], str):
            raise ValueError("invalid response message")
    for field in ("suppressOutput", "continue"):
        if field in data and not isinstance(data[field], bool):
            raise ValueError("invalid response flag")


def _log_error(tool_name: str, error: BaseException) -> None:
    """Log best effort; logging failures cannot prevent a safety decision."""
    try:
        from datetime import datetime
        path = os.path.expanduser("~/.config/nuh/hook-errors.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "w" if os.path.exists(path) and os.path.getsize(path) > 1_000_000 else "a"
        with open(path, mode) as stream:
            stream.write(f"{datetime.now().isoformat(timespec='seconds')} "
                         f"{tool_name or 'unknown'} {type(error).__name__}: {str(error)[:200]}\n")
    except Exception as exc:
        # A read-only home or full disk must not suppress the fallback response.
        sys.stderr.write(f"nuh: error logging failed: {exc}\n")


def _pass_timing() -> None:
    """Tell nuh when the hook started and the timeout it was installed with (nuh.cascade_budget)."""
    os.environ["NUH_HOOK_STARTED"] = repr(time.monotonic())
    timeout = globals().get("_NUH_HOOK_TIMEOUT_S")  # written above this source by `nuh install`
    if timeout:
        os.environ["NUH_HOOK_TIMEOUT_S"] = str(timeout)


def run() -> None:
    """Read once, replay into nuh, and publish only a validated response."""
    _pass_timing()
    real_stdout, real_stdin = sys.stdout, sys.stdin
    agent, tool_name = "unknown", ""
    try:
        raw = real_stdin.read()
        data = json.loads(raw)
        detected = _detect_agent(data)
        _validate_input(data)
        if detected == "codex" and "turn_id" in data and (
            not isinstance(data["turn_id"], str) or not data["turn_id"]
        ):
            raise ValueError("invalid turn_id")
        agent = detected
        tool_name = data["tool_name"]
        buf = io.StringIO()
        sys.stdout, sys.stdin = buf, io.StringIO(raw)
        try:
            from nuh.hook import main
            main()
        except SystemExit as exc:
            if exc.code is not None and exc.code != 0:
                raise
        output = buf.getvalue()
        if output.strip():
            _validate_output(output, agent)
    except BaseException as exc:
        _log_error(tool_name, exc)
        decision = "ask" if agent == "claude" else "deny"
        reason = "nuh: error, requesting confirmation" if decision == "ask" else (
            "nuh: hook error; blocked for safety. Review required before retrying.")
        output = json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }}) + "\n"
    finally:
        sys.stdout, sys.stdin = real_stdout, real_stdin
    try:
        real_stdout.write(output)
        real_stdout.flush()
    except BrokenPipeError:
        # The caller closed its pipe; there is no receiver for a response.
        pass


if __name__ == "__main__":
    run()
    # Prevent interpreter shutdown from retrying a failed pipe flush.
    os._exit(0)
