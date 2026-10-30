"""nuh's argparse tree: every command, its one-line summary and each option with its help.

``build_parser`` is the single source the CLI parses with and every CLI doc
renders from: ``--help``, the man page (``manpage``), shell completion
(``completion_zsh``) and the tests that keep the help complete (each command's
description and examples come from ``cli_help``). It imports no handler
module, so ``--help``, ``nuh completion zsh`` and the man page build stay
cheap and need none of nuh's runtime dependencies; ``cli.main`` loads the one
handler a command needs after parsing.

Metavars name what a value is, and completion reads them (``completion``):
``FILE``/``PATH`` complete paths, ``DIR`` directories, ``TYPE`` action types,
``TOOL`` tool names, ``PROVIDER`` LLM providers and ``AGENT`` agents.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime

from nuh import __version__, cli_help
from nuh.cli_completion import SHELLS
from nuh.trace_views import ALL, VIEWS

PROG = "nuh"
DECISIONS = ("allow", "ask", "block")
TRACE_LIMIT = 200
_AGENT_HELP = "Agent to target: claude (default), codex, or all (every agent whose settings directory exists)"


def regex(raw: str) -> re.Pattern[str]:
    """``--grep`` as argparse reads it: a case-insensitive regex."""
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error as exc:
        raise argparse.ArgumentTypeError(f"bad regex {raw!r}: {exc}") from exc


def since(raw: str) -> datetime:
    """``--since`` as argparse reads it (``nuh log``, ``nuh trace``)."""
    from nuh.log_query import parse_since  # lazy: only a given --since pays for it
    try:
        return parse_since(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def seconds(raw: str) -> float:
    """``nuh test --timeout``: a positive, finite number of seconds."""
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds: {raw!r}")
    return value


def _setup(sub: argparse._SubParsersAction) -> None:
    parsers = {name: sub.add_parser(name, help=summary) for name, summary in (
        ("install", "Install the nuh hook into coding agents, and nuh's zsh completion"),
        ("update", "Rewrite the installed hook script and settings (unlock, overwrite, re-lock)"),
        ("uninstall", "Remove the nuh hook from coding agents"))}
    for p in parsers.values():
        p.add_argument("--agent", metavar="AGENT", default=None, help=_AGENT_HELP)
    install = parsers["install"].add_mutually_exclusive_group()
    install.add_argument("--no-completion", action="store_true", help="Do not write the zsh completion")
    install.add_argument("--completion-only", action="store_true",
                         help="Only write the zsh completion: no hook (--agent is ignored)")
    parsers["update"].add_argument("--no-completion", action="store_true",
                                   help="Do not refresh or write the zsh completion")
    claude = sub.add_parser("claude", help="Launch Claude Code with nuh hooks active")
    claude.add_argument("claude_args", nargs=argparse.REMAINDER, metavar="ARGS",
                        help="Arguments passed to claude unchanged (nuh claude --help is claude's help)")
    shells = sub.add_parser("completion", help="Print a shell completion script for nuh")
    names = sorted(SHELLS)
    shells.add_argument("shell", choices=names, metavar="SHELL",
                        help=f"The shell to print the script for: {', '.join(names)}")


def _test(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("test", help="Dry-run classification for a command or tool input")
    p.add_argument("--tool", metavar="TOOL", default=None, help="Tool name (default: Bash)")
    p.add_argument("--path", default=None, help="File or directory path for the tool input")
    p.add_argument("--content", default=None, help="Content for Write/Edit content inspection")
    p.add_argument("--pattern", default=None, help="Search pattern for Grep")
    p.add_argument("--config", metavar="JSON", default=None,
                   help="Inline JSON override of the non-LLM config (classify, actions, profile, ...)")
    p.add_argument("args", nargs="*", help="The command string, or the tool input")
    g = p.add_argument_group("LLM step (overrides live in memory for this run; no config file is written)")
    g.add_argument("--provider", metavar="PROVIDER", help="Restrict the cascade to this provider")
    g.add_argument("--provider-url", metavar="URL", help="URL for --provider")
    g.add_argument("--model", metavar="NAME", help="Model for --provider")
    g.add_argument("--timeout", type=seconds, metavar="SECONDS", help="Each provider's timeout")
    g.add_argument("--can-block", action=argparse.BooleanOptionalAction, default=None,
                   help="Let an LLM block block (--no-can-block: cap it to ask)")
    on_off = g.add_mutually_exclusive_group()
    on_off.add_argument("--llm", dest="llm_mode", action="store_const", const="on",
                        help="Review even when llm.enabled is off or the result is not an LLM-eligible ask")
    on_off.add_argument("--no-llm", dest="llm_mode", action="store_const", const="off", help="Skip the LLM step")
    g.add_argument("--set", dest="set_values", action="append", metavar="llm.KEY=VALUE",
                   help="Set any llm.* key; the value is JSON when it parses, else a string (repeatable)")
    g.add_argument("--show-request", action="store_true", help="Print each request sent and the raw response")
    c = p.add_argument_group("session context for the LLM step")
    c.add_argument("--user", dest="user_messages", action="append", metavar="MESSAGE",
                   help="A recent user message (repeatable, oldest first)")
    c.add_argument("--first-prompt", metavar="TEXT", help="The session's first user message")
    c.add_argument("--transcript", metavar="FILE", help="A real transcript (Claude Code JSONL or Codex rollout)")
    c.add_argument("--cwd", metavar="DIR", help="Run the whole test from DIR")


def _log_flags(p: argparse.ArgumentParser, *, default_limit: int, follow_flag: bool) -> None:
    """The filter and view flags shared by ``nuh log`` and ``nuh tail``."""
    p.add_argument("-n", "--limit", type=int, default=default_limit,
                   help=f"Entries to show (default: {default_limit}; 0 = no limit)")
    which = p.add_mutually_exclusive_group()
    which.add_argument("-d", "--decision", choices=DECISIONS, help="Only this decision")
    which.add_argument("--blocks", dest="decision", action="store_const", const="block",
                       help="Only blocked decisions (= -d block)")
    which.add_argument("--asks", dest="decision", action="store_const", const="ask", help="Only asks (= -d ask)")
    p.add_argument("-t", "--tool", metavar="TOOL", default=None, help="Tool name, any case (Bash, Read, Write, ...)")
    p.add_argument("--action-type", metavar="TYPE", default=None,
                   help="Action type (git_write, filesystem_read, ...)")
    p.add_argument("-p", "--project", default=None, help="Project path substring")
    p.add_argument("-g", "--grep", type=regex, default=None, help="Regex on the input (case-insensitive)")
    p.add_argument("-s", "--since", type=since, default=None, help="Only the last 30s, 10m, 2h, 1d, or today")
    p.add_argument("--call", default=None,
                   help="Tool call ID: whole, its start or its end, with or without toolu_ (or the entry id);"
                        " SESSION:ID also matches the session ID's start")
    p.add_argument("--session", default=None, help="Session ID prefix")
    p.add_argument("--llm", action="store_true", help="Only entries where the LLM was consulted")
    p.add_argument("--errors", action="store_true", help="Only entries whose LLM step errored or timed out")
    p.add_argument("-a", "--all", action="store_true", dest="all_logs",
                   help="Read every rotated log (default: only as far back as -n needs)")
    p.add_argument("-r", "--reverse", action="store_true", help="Newest first (default: oldest first)")
    p.add_argument("-v", "--details", action="store_true", help="Multi-line view of every field")
    p.add_argument("-I", "--full-input", action="store_true",
                   help="Whole input, never truncated (with -v, the logged tool_input)")
    p.add_argument("--json", action="store_true", help="Raw JSON lines (pipe to jq)")
    if follow_flag:
        p.add_argument("-f", "--follow", action="store_true",
                       help="Keep printing new entries as they are logged (= nuh tail)")


def _trace(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("trace", help="Browse and render LLM traces (llm.trace_dir)", allow_abbrev=False)
    p.add_argument("target", nargs="?", default=None,
                   help="A call reference (#abc123, an ID's start or end) or a trace directory")
    p.add_argument("--render", metavar="DIR", default=None, help="Render this trace directory (or name)")
    p.add_argument("--view", choices=(*VIEWS, ALL), default="answers", help="What to render (default: answers)")
    p.add_argument("--json", action="store_true", help="Machine-readable summary (JSON lines for the list)")
    p.add_argument("--list", action="store_true", help="Print the list even on a terminal with fzf")
    p.add_argument("--dir", metavar="DIR", default=None, help="Trace directory to read (default: llm.trace_dir)")
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                   help="Colour: auto (a terminal), always (still off with NO_COLOR), never")
    p.add_argument("-n", "--limit", type=int, default=TRACE_LIMIT,
                   help=f"Traces to list (default: {TRACE_LIMIT}; 0 = all)")
    which = p.add_mutually_exclusive_group()
    which.add_argument("-d", "--decision", choices=DECISIONS, help="Only this final decision")
    which.add_argument("--blocks", dest="decision", action="store_const", const="block", help="Only blocks")
    which.add_argument("--asks", dest="decision", action="store_const", const="ask", help="Only asks")
    p.add_argument("-t", "--tool", metavar="TOOL", default=None, help="Tool name, any case (Bash, Read, Write, ...)")
    p.add_argument("--provider", metavar="PROVIDER", default=None,
                   help="Only traces whose LLM answer came from this provider")
    p.add_argument("-s", "--since", type=since, default=None, help="Only the last 30s, 10m, 2h, 1d, or today")
    p.add_argument("--prune", action="store_true",
                   help="Delete traces older than llm.trace_keep_days now (nuh does it daily on its own)")
    p.add_argument("--dry-run", action="store_true", help="With --prune: only say what would be deleted")


def _inspect(sub: argparse._SubParsersAction) -> None:
    config = sub.add_parser("config", help="Show the effective config or its file paths")
    config_sub = config.add_subparsers(dest="config_command", metavar="COMMAND")
    config_sub.add_parser("show", help="Display the effective merged config")
    config_sub.add_parser("path", help="Show the config file paths")
    _log_flags(sub.add_parser("log", help="Show recent hook decisions"), default_limit=50, follow_flag=True)
    _log_flags(sub.add_parser("tail", help="Follow hook decisions as they are logged (nuh log -f)"),
               default_limit=10, follow_flag=False)
    _trace(sub)
    system1 = sub.add_parser("system1", help="Write out or check the system 1 definition (no model call)")
    system1_sub = system1.add_subparsers(dest="system1_command", metavar="COMMAND")
    export = system1_sub.add_parser("export", allow_abbrev=False,
                                    help="Write the shipped system 1 definition as YAML to edit")
    export.add_argument("path", nargs="?", metavar="FILE", default=None,
                        help="Where to write (default: llm.system1.definition, else system1.yaml beside the"
                             " global config)")
    export.add_argument("--force", action="store_true", help="Overwrite an existing file")
    export.add_argument("--stdout", action="store_true", help="Print the definition instead of writing a file")
    check = system1_sub.add_parser("check", allow_abbrev=False,
                                   help="Validate a definition as the system 1 provider loads it (no model call)")
    check.add_argument("path", nargs="?", metavar="FILE", default=None,
                       help="The definition to check (default: the one nuh uses for llm.system1)")
    heal = sub.add_parser("heal-log", help="Show what the llm.self_heal workers did (read only)")
    heal.add_argument("-n", "--limit", type=int, default=20, help="Entries to show (default: 20; 0 = all)")
    heal.add_argument("--json", action="store_true", help="Raw JSON lines (pipe to jq)")
    sub.add_parser("types", help="List all action types with descriptions and default policies")


def _rules(sub: argparse._SubParsersAction) -> None:
    project = "Write to the project's .nuh.yaml instead of the global config"
    for name, verb in (("allow", "allow"), ("deny", "block")):
        p = sub.add_parser(name, help=f"Set an action type's policy to {verb}")
        p.add_argument("action_type", metavar="TYPE", help=f"The action type to {name}")
        p.add_argument("--project", action="store_true", help=project)
    p = sub.add_parser("allow-path", help="Allow a sensitive path for the current project")
    p.add_argument("path", metavar="PATH", help="The path to allow")
    p = sub.add_parser("classify", help="Classify a command prefix as an action type")
    p.add_argument("command_prefix", metavar="PREFIX", help="The command prefix to classify (quote it: \"docker rm\")")
    p.add_argument("type", metavar="TYPE", help="The action type to assign")
    p.add_argument("--project", action="store_true", help=project)
    p = sub.add_parser("trust", help="Trust a path or network host (global config)")
    p.add_argument("target", metavar="TARGET",
                   help="A path (starts with /, ~ or .), or a network host: name, *.domain (subdomains), IP, or CIDR")
    p.add_argument("--project", action="store_true",
                   help="Rejected for a path; for a host it changes nothing (host trust is always global)")
    sub.add_parser("status", help="Show all custom rules")
    p = sub.add_parser("forget", help="Remove a rule")
    p.add_argument("arg", metavar="RULE", help="The rule to remove: an action type, path, command prefix, or host")
    p.add_argument("--project", action="store_true", help="Search only the project config")
    p.add_argument("--global", dest="global_flag", action="store_true", help="Search only the global config")


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI, every parser carrying its description and examples (``cli_help.describe_tree``)."""
    parser = argparse.ArgumentParser(prog=PROG)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}",
                        help="Print nuh's version and exit")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    _setup(sub)
    _test(sub)
    _inspect(sub)
    _rules(sub)
    cli_help.describe_tree(parser)
    return parser
