"""``nuh test``: dry-run classification of a command or tool input, printed with its gating and steps."""

import argparse
import json
import shlex
import sys
from typing import TYPE_CHECKING

from nuh import cli_gating

if TYPE_CHECKING:
    from nuh import cli_llm

_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def cmd_test(args: argparse.Namespace) -> None:
    """Dry-run classification for a command or tool input."""
    from nuh import cli_llm, cli_llm_args
    try:
        plan = cli_llm_args.from_args(args)
        with plan.session():
            _run_test(args, plan)
    except cli_llm.UsageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _run_test(args: argparse.Namespace, plan: "cli_llm.Plan") -> None:
    """``cmd_test`` inside the session the LLM flags set up (``cli_llm.Plan``)."""
    if getattr(args, "config", None):
        try:
            override = json.loads(args.config)
        except json.JSONDecodeError as e:
            print(f"Error: invalid --config JSON: {e}", file=sys.stderr)
            raise SystemExit(1)
        from nuh.config import apply_override
        apply_override(override)
    plan.apply_config()

    tool = getattr(args, "tool", None) or "Bash"
    if tool == "Bash":
        _test_bash(args, plan)
    elif tool in _WRITE_TOOLS:
        _test_write(args, plan, tool)
    else:
        _test_other(args, plan, tool)


def _test_bash(args: argparse.Namespace, plan: "cli_llm.Plan") -> None:
    from nuh import bash_review, cli_llm, gating
    from nuh.bash import classify_command
    if not args.args:
        print("Error: nuh test requires a command string", file=sys.stderr)
        raise SystemExit(1)
    command = args.args[0] if len(args.args) == 1 else shlex.join(args.args)
    result = classify_command(command)

    print(f"Command:  {result.command}")
    if result.stages:
        print("Stages:")
        for i, sr in enumerate(result.stages, 1):
            tokens_str = " ".join(sr.tokens)
            print(f"  [{i}] {tokens_str} → {sr.action_type} → {sr.default_policy} → {sr.decision} ({sr.reason})")
    if result.composition_rule:
        print(f"Composition: {result.composition_rule} → {result.final_decision.upper()}")
    print(f"Decision:    {result.final_decision.upper()}")
    print(f"Reason:      {result.reason}")
    _print_floor(result.floor_rules(), "       ")
    route = bash_review.route(result)  # what the hook does with this call's LLM step
    cli_gating.print_lines("Bash", {"decision": result.final_decision}, 13,
                           keys=gating.bash_keys(result), route_why_not=route.why_not)
    cli_llm.review_bash(plan, result, route)


def _test_write(args: argparse.Namespace, plan: "cli_llm.Plan", tool: str) -> None:
    """Write-like tools: path, boundary and content checks, then the hook's LLM veto gate."""
    from nuh import cli_llm
    from nuh.hook import HANDLERS
    from nuh.hook_write import static_decision
    file_path = getattr(args, "path", None) or " ".join(args.args)
    content = getattr(args, "content", None) or ""
    if tool == "Write":
        ti = {"file_path": file_path, "content": content}
    elif tool == "Edit":
        ti = {"file_path": file_path, "new_string": content}
    elif tool == "MultiEdit":
        ti = {"file_path": file_path, "edits": [{"old_string": "", "new_string": content}] if content else []}
    else:
        ti = {"notebook_path": file_path, "action": "replace", "new_source": content}
    static = static_decision(tool, ti)  # what the gating lines explain; the handler computes it again
    decision = cli_llm.review_write(plan, tool, ti, HANDLERS[tool])
    print(f"Tool:     {tool}")
    print(f"Path:     {file_path}")
    if content:
        print(f"Content:  {content[:100]}")
    _print_decision(tool, decision, static)


def _test_other(args: argparse.Namespace, plan: "cli_llm.Plan", tool: str) -> None:
    """Read, Glob, Grep and any other tool: its handler or the classify table, then the generic review."""
    from nuh import cli_llm
    from nuh.hook import HANDLERS
    from nuh.hook_tools import _classify_unknown_tool
    raw = getattr(args, "path", None) or " ".join(args.args)
    ti = _tool_input(tool, raw, getattr(args, "pattern", None) or "")
    handler = HANDLERS.get(tool)
    static = handler(ti) if handler is not None else _classify_unknown_tool(tool, ti)
    shown = dict(static)
    decision = cli_llm.review_generic(plan, tool, ti, static)
    print(f"Tool:     {tool}")
    print(f"Input:    {json.dumps(ti, ensure_ascii=False)}")
    _print_decision(tool, decision, shown)


def _tool_input(tool: str, raw: str, pattern: str) -> dict:
    """The tool input ``nuh test --tool`` builds from its arguments.

    Read takes a file path, Glob and Grep a path (Grep a --pattern too),
    WebFetch a URL, WebSearch a query; any other tool a JSON object, else
    nothing.
    """
    if tool == "Read":
        return {"file_path": raw}
    if tool in ("Glob", "Grep"):
        return {"path": raw, **({"pattern": pattern} if pattern else {})}
    if tool == "WebFetch":
        return {"url": raw}
    if tool == "WebSearch":
        return {"query": raw}
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _print_decision(tool: str, decision: dict, static: dict) -> None:
    """The final decision, its reason, the gating lines for the *static* decision, and the steps."""
    print(f"Decision: {decision['decision'].upper()}")
    reason = decision.get("reason", "")
    if reason:
        print(f"Reason:   {reason}")
    _print_floor([decision.get("_floor", "")], "    ")
    cli_gating.print_lines(tool, static, 12)
    _print_steps(decision)


def _print_steps(decision: dict) -> None:
    """The decision's steps as the hook logs them (``nuh log -v`` shows them the same way)."""
    from nuh import steps
    from nuh.log_view import LogView
    trail = steps.ensure(dict(decision))[steps.KEY]
    entry = {"decision": decision.get("decision", ""), "steps": steps.for_log(trail)}
    print("Steps:")
    for row in LogView(color=False, width=120).step_rows(entry)[1:]:
        print(row)


def _print_floor(rules: list[str], pad: str) -> None:
    """Print the floor rules behind a decision (each is a human_only key; the Human-only line says which hold)."""
    rules = [r for r in rules if r]
    if rules:
        print(f"Floor:{pad}{', '.join(rules)}")
