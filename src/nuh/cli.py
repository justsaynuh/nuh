"""CLI entry point: parse with ``cli_parser`` and hand the command to its handler module (cli_*).

The handler module is imported only for the command that runs, so ``--help``
and ``nuh completion`` never load the classifier, the config or the LLM layer.
A zsh user at a terminal is told on stderr when nuh's completion is missing
(``completion_hint``).
"""

import argparse
import importlib
import os
import sys

# Command -> (handler module, function). ``claude`` is dispatched before argparse (see main).
HANDLERS: dict[str, tuple[str, str]] = {
    "install": ("nuh.cli_install", "cmd_install"),
    "update": ("nuh.cli_install", "cmd_update"),
    "uninstall": ("nuh.cli_install", "cmd_uninstall"),
    "completion": ("nuh.cli_completion", "cmd_completion"),
    "test": ("nuh.cli_dryrun", "cmd_test"),
    "config": ("nuh.cli", "cmd_config"),
    "log": ("nuh.cli_log", "cmd_log"),
    "tail": ("nuh.cli_log", "cmd_tail"),
    "trace": ("nuh.cli_trace", "cmd_trace"),
    "system1": ("nuh.cli_system1", "cmd_system1"),
    "heal-log": ("nuh.cli_heal", "cmd_heal_log"),
    "types": ("nuh.cli_rules", "cmd_types"),
    "allow": ("nuh.cli_rules", "cmd_allow"),
    "deny": ("nuh.cli_rules", "cmd_deny"),
    "allow-path": ("nuh.cli_rules", "cmd_allow_path"),
    "classify": ("nuh.cli_rules", "cmd_classify"),
    "trust": ("nuh.cli_rules", "cmd_trust"),
    "status": ("nuh.cli_rules", "cmd_status"),
    "forget": ("nuh.cli_rules", "cmd_forget"),
}


def cmd_config(args: argparse.Namespace) -> None:
    """Config subcommands."""
    sub = getattr(args, "config_command", None)
    if sub == "show":
        from nuh.config import get_config
        from nuh.cli_config import lines
        print("\n".join(lines(get_config())))
    elif sub == "path":
        from nuh.config import get_global_config_path, get_project_config_path
        print(f"Global:  {get_global_config_path()}")
        proj = get_project_config_path()
        print(f"Project: {proj or '(no project root)'}")
    else:
        print("Usage: nuh config {show|path}")


def main(argv: list[str] | None = None) -> None:
    words = sys.argv[1:] if argv is None else argv
    # "nuh claude ..." bypasses argparse: every word after it is claude's,
    # and argparse.REMAINDER fails when the first one starts with "--".
    if words[:1] == ["claude"]:
        from nuh.cli_install import cmd_claude
        cmd_claude(words[1:])
        return
    from nuh.cli_parser import build_parser
    parser = build_parser()
    try:
        args = parser.parse_args(words)
    except SystemExit as exc:
        if exc.code == 0 and _asks_help(words):
            _completion_hint()
        raise
    handler = HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        _completion_hint()
        return
    if args.command not in _NO_HINT:
        _completion_hint()  # before the command: `nuh tail` never returns
    module, function = handler
    getattr(importlib.import_module(module), function)(args)


# Commands that install, remove or print the completion themselves.
_NO_HINT = frozenset({"completion", "install", "update", "uninstall"})


def _asks_help(words: list[str]) -> bool:
    """Whether argparse took ``-h``/``--help`` among *words* (a whole word before any ``--``)."""
    return any(word in ("-h", "--help") for word in (words[:words.index("--")] if "--" in words else words))


def _completion_hint() -> None:
    """The missing-completion hint (completion_hint), loaded only for a zsh user whose stderr is a terminal."""
    if sys.stderr.isatty() and os.path.basename(os.environ.get("SHELL", "")) == "zsh":
        from nuh.completion_hint import show
        show()


if __name__ == "__main__":
    main()
