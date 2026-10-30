"""The CLI's long help: the overview, and each command's description and examples.

One source for every rendering: ``--help`` (``cli_help.describe_tree``), the
man page (``manpage``) and shell completion (summaries only). Each command's
one-line summary and each option's help sit beside their definitions in
``cli_parser``. Text marks code with backticks; the terminal shows them as
written, the man page sets them in bold.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Example:
    """One example invocation: what it is for, and the command line (always starting ``nuh``)."""

    comment: str
    command: str


@dataclass(frozen=True)
class CommandText:
    """A command's long help: description paragraphs and 1-3 examples."""

    description: tuple[str, ...]
    examples: tuple[Example, ...]


OVERVIEW: tuple[str, ...] = (
    "nuh is a PreToolUse hook for Claude Code (and Codex). Before a tool call runs (Bash, Read, Write, Edit, "
    "Glob, Grep, WebFetch, MCP tools and the rest) the agent hands it to nuh, which answers allow, ask or block. "
    "The static rules are deterministic: a shell command is split into stages, each stage gets an action type "
    "(`git_history_rewrite`, `network_outbound`, ...) and each type a policy; paths, file content and network "
    "hosts are checked as well. An optional LLM step can review what the static rules leave undecided.",
    "With no command, nuh prints this help. `nuh install` installs the hook; `nuh test` shows what the hook "
    "would decide for a command without running it. `man nuh` has the full reference.",
)

_PROJECT = ("With `--project` the rule goes into the project's `.nuh.yaml` (in the git root) instead of the "
            "global config; a project config can only tighten, so a project rule that loosens the effective "
            "policy is refused.")
_ADMIN = "When an agent runs it, it asks under nuh's own `nuh_admin` floor."
_WRITES = ("Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks "
           "first, and refuses when stdin is not a terminal. " + _ADMIN)
_UNKNOWN_TYPE = ("A name that is not a built-in action type is refused with suggestions when close to one; "
                 "otherwise nuh offers to create it as a custom type.")

COMMANDS: dict[str, CommandText] = {
    "install": CommandText((
        "Write the hook script (`~/.claude/hooks/nuh_guard.py` for Claude Code, `~/.codex/hooks/nuh_guard.py` "
        "for Codex; read-only, mode 444) and add a PreToolUse entry per guarded tool to the agent's settings "
        "(`~/.claude/settings.json`, `~/.codex/hooks.json`), keeping the previous file as `.json.bak`. The hook "
        "runs with the Python interpreter nuh is installed in.",
        "The hook's timeout is `hook.deadline_s` plus every LLM provider's timeout plus 10 seconds, at least 60. "
        "Claude Code blocks a call when the hook fails or times out; Codex has no such setting, and its hooks "
        "must be enabled and trusted in Codex.",
        "It also writes the zsh completion (`nuh completion zsh`) as `_nuh`, once per user whichever agents "
        "are named: into oh-my-zsh's completions directory (`$ZSH_CACHE_DIR/completions`, asked of an "
        "interactive zsh) when there is one, else into `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions`, "
        "saying what to add to `~/.zshrc` when that directory is not on `fpath`. A `_nuh` that nuh did not "
        "write is left alone; without zsh the step is skipped. `--no-completion` skips it, "
        "`--completion-only` does only it (what `make install` in a checkout runs).",
    ), (
        Example("install for Claude Code", "nuh install"),
        Example("every agent whose settings directory exists", "nuh install --agent all"),
        Example("only the zsh completion, no hook", "nuh install --completion-only"),
    )),
    "update": CommandText((
        "Rewrite the hook script after upgrading nuh or changing the hook's timeout settings: unlock it, "
        "overwrite it with this version, re-lock it, and rewrite the agent's hook entries (interpreter path, "
        "command, timeout). Without an installed script it says to run `nuh install` and changes nothing.",
        "It also refreshes every zsh completion file nuh wrote, or writes one as `nuh install` does when there "
        "is none (`--no-completion` skips this).",
    ), (
        Example("after upgrading nuh, or changing hook.deadline_s or a provider timeout", "nuh update"),
    )),
    "uninstall": CommandText((
        "Remove nuh's entries from the agent's settings, leaving other hooks alone, then delete the hook "
        "script. The Claude Code script is kept while another agent's settings still point at it.",
        "Once no agent keeps nuh's hooks, it also deletes the zsh completion files nuh wrote (each has nuh's "
        "generated header line); a `_nuh` without it is left alone.",
    ), (
        Example("remove the Claude Code hook", "nuh uninstall"),
        Example("remove the Codex hook only", "nuh uninstall --agent codex"),
    )),
    "claude": CommandText((
        "Start Claude Code with nuh guarding this session only. When `nuh install` already added the hook to "
        "`~/.claude/settings.json`, this simply runs `claude`; otherwise it writes the hook script and passes "
        "the hook entries with `claude --settings`.",
        "Every argument after `claude` goes to claude unchanged, `--help` included. `--settings` is refused: "
        "nuh manages it.",
    ), (
        Example("start a guarded session", "nuh claude"),
        Example("resume one, flags passed through", "nuh claude --resume"),
    )),
    "completion": CommandText((
        "Print the completion script for SHELL. It is generated from this version's commands and options; "
        "`nuh install` writes it for zsh and `nuh update` refreshes it after an upgrade. It completes "
        "commands, nested commands, options with their help, choices, file paths, action types, tool names, "
        "providers and agents. Reads no config, log or trace, and writes nothing.",
        "To place it yourself, save it as `_nuh` in a directory on zsh's `fpath` that is listed before "
        "`compinit` runs, then start a new shell. To try it in the current shell only: "
        "`source <(nuh completion zsh)`.",
    ), (
        Example("print the zsh completion function", "nuh completion zsh"),
    )),
    "test": CommandText((
        "Show what the hook would decide for a command or tool input, without running it: the stages with "
        "their action types and policies, the decision and its reason, any floor rule behind it, then how the "
        "hook would route it (`Word:`, `Human-only:`, `LLM call:`, `Delivery:`). When the hook would call the "
        "LLM, `nuh test` makes the same live call and shows the review and the decision after it.",
        "The default tool is Bash, taking the command as one argument (several are joined as a shell line). "
        "`--tool Read` takes a file path, `Glob` and `Grep` a path (`Grep` also `--pattern`), `Write`, `Edit`, "
        "`MultiEdit` and `NotebookEdit` a path plus `--content`, `WebFetch` a URL, `WebSearch` a query, and any "
        "other tool (`mcp__*`) its input as a JSON object.",
        "The LLM flags change the LLM config in memory for this run only; picking a provider turns the LLM "
        "step on, as `--llm` does. `--provider-url` or `--model` without `--provider`, and `--no-llm` with any "
        "LLM flag, are errors. `--user` and `--first-prompt` build a throwaway transcript, so providers pick "
        "the session context as they would from a real one.",
    ), (
        Example("a shell command", "nuh test \"rm -rf /\""),
        Example("a Read of a key file", "nuh test --tool Read ~/.ssh/id_rsa"),
        Example("a Write whose content holds a secret",
                "nuh test --tool Write --path ./config.py --content \"api_key='sk-secret123'\""),
    )),
    "config": CommandText((
        "Show the effective config (`config show`) or where its files are (`config path`). Without a "
        "subcommand it prints its usage.",
    ), (
        Example("every effective setting", "nuh config show"),
    )),
    "config show": CommandText((
        "Print every setting with its value after merging the global config and the project's `.nuh.yaml`, one "
        "per line, labelled with the key you write in YAML (`llm.on_failure`, `hook.deadline_s`); a setting "
        "merged from several keys keeps its own name (`classify_global`). `tools` and `human_only` print as "
        "tables, each entry with where its value comes from.",
    ), (
        Example("is the LLM step on, and what happens when it fails?", "nuh config show"),
    )),
    "config path": CommandText((
        "Print the global config path (`~/.config/nuh/config.yaml`) and the project config path (`.nuh.yaml` "
        "in the project root), or `(no project root)` outside a project.",
    ), (
        Example("which files nuh reads here", "nuh config path"),
    )),
    "log": CommandText((
        "Show hook decisions from the decision log, oldest first, one compact line each: local time, session, "
        "call reference, decision, tool, input, action type and reason, the LLM verdict and total time. Input "
        "and reason are cut to the terminal width; `-v` shows every field, the decision's steps in order "
        "among them. Filters combine.",
        "Session and tool call ID together identify a call: `--call` takes the tool call ID (whole, its start "
        "or its end), `SESSION:ID` also the session's start. Colour only on a terminal, never with `NO_COLOR` "
        "set or `TERM=dumb`.",
    ), (
        Example("asks in the last two hours", "nuh log -s 2h -d ask"),
        Example("one call, every field", "nuh log --call 01AbCdEfGh -v"),
        Example("input matching a regex, only where the LLM was consulted", "nuh log -g 'git push' --llm"),
    )),
    "tail": CommandText((
        "`nuh log --follow` with a shorter backlog: the last 10 matching entries, then every new one as it is "
        "logged, until Ctrl-C. It survives log rotation. Takes the same filters as `nuh log`.",
    ), (
        Example("follow everything", "nuh tail"),
        Example("follow asks, every field", "nuh tail -d ask -v"),
    )),
    "trace": CommandText((
        "Browse and render the LLM traces under `llm.trace_dir`: what each question asked, what system 1 "
        "answered and how sure it was, and how the answers became the decision. Read-only, except `--prune`.",
        "With no argument on a terminal with fzf on `PATH`, an interactive picker: one line per trace, newest "
        "first, the highlighted one rendered beside it; alt-a, alt-p, alt-d and alt-r switch the preview "
        "between the views, Enter opens all of them in `$PAGER` (default `less -R`). Without fzf or a terminal "
        "it prints the list. A call reference (`#9c3f1a`, as the notice shows it) or a trace directory "
        "renders one trace.",
        "Views: `answers` (each system 1 question, its answer and what it triggered), `prompt` (each request "
        "as sent), `path` (the decision's steps, as `nuh log -v` words them), `raw` (every file, "
        "pretty-printed) and `all`. Traces older than `llm.trace_keep_days` (default 30) are pruned at most "
        "once a day on their own; `--prune` does it now.",
    ), (
        Example("pick a trace interactively", "nuh trace"),
        Example("one trace's request as sent", "nuh trace '#9c3f1a' --view prompt"),
        Example("what pruning would delete now", "nuh trace --prune --dry-run"),
    )),
    "system1": CommandText((
        "Write out the system 1 definition nuh ships, to edit (`system1 export`), or validate one "
        "(`system1 check`). Neither calls a model. Without a subcommand it prints its usage.",
    ), (
        Example("validate the definition nuh uses now", "nuh system1 check"),
    )),
    "system1 export": CommandText((
        "Write the shipped system 1 definition as YAML to edit by hand: block style, the preamble and each "
        "question's instructions as literal blocks, and `based_on` set to this nuh version. The default path "
        "is `llm.system1.definition` when set, else `system1.yaml` beside the global config "
        "(`~/.config/nuh/system1.yaml`). A YAML file only: a `.json` path is refused.",
        "The file replaces the shipped definition entirely (only its `tools:` blocks merge, per tool) and nuh "
        "does not update it on upgrade. An existing file is never overwritten without `--force`. Writing asks "
        "under nuh's `nuh_admin` floor when an agent runs it; `--stdout` only reads.",
    ), (
        Example("write the file nuh reads, then edit it", "nuh system1 export"),
        Example("print it, to compare with your copy", "nuh system1 export --stdout"),
    )),
    "system1 check": CommandText((
        "Load a definition with the code the system 1 provider uses (the base and every `tools:` block) and "
        "compile its gates, without calling a model. Prints the file, the preamble, context items, questions "
        "and how each decides, gates, each tool block, any warnings, and `OK`. A definition nuh cannot use "
        "prints why and exits 1.",
        "With no FILE it checks the one nuh uses now: `llm.system1.definition`, else "
        "`~/.config/nuh/system1.yaml`, else the shipped one.",
    ), (
        Example("the definition nuh uses now", "nuh system1 check"),
        Example("a copy you are editing", "nuh system1 check ~/defs/s1.yaml"),
    )),
    "heal-log": CommandText((
        "What the `llm.self_heal` workers did, oldest first: one line per worker run from "
        "`~/.local/state/nuh/llm-heal.jsonl` with its time, provider, model, host, outcome, model load time "
        "and error. A worker starts after a provider times out, and replays the request only when the model "
        "is on the server but not loaded. Read only.",
    ), (
        Example("the last 20 runs", "nuh heal-log"),
        Example("every run, as JSON lines", "nuh heal-log -n 0 --json"),
    )),
    "types": CommandText((
        "List every built-in action type with its default policy and description. A global `classify` entry "
        "that overrides a built-in rule or a flag classifier is noted under its type, with the `nuh forget` "
        "that restores the built-in.",
    ), (
        Example("the action types and their defaults", "nuh types"),
    )),
    "allow": CommandText((
        "Set an action type's policy to `allow` in the global config (`actions:`). Floor types (`nuh_admin`, "
        "`env_read`) cannot be lowered; the error says which `human_only` key lets the LLM decide them "
        "instead. " + _UNKNOWN_TYPE,
        _PROJECT + " " + _WRITES,
    ), (
        Example("stop asking before deleting files", "nuh allow filesystem_delete"),
    )),
    "deny": CommandText((
        "Set an action type's policy to `block` in the global config (`actions:`). " + _UNKNOWN_TYPE,
        _PROJECT + " " + _WRITES,
    ), (
        Example("block outbound network requests", "nuh deny network_outbound"),
        Example("block history rewrites in this project only", "nuh deny git_history_rewrite --project"),
    )),
    "allow-path": CommandText((
        "Exempt a path from your own `sensitive_paths` and `sensitive_basenames` entries, for the current "
        "project root only (`allow_paths:` in the global config). A path a built-in entry covers "
        "(`~/.aws/config`, `.env`, ...) is a floor: the command refuses it. Needs a project root.",
        _WRITES,
    ), (
        Example("let this project read a path you marked sensitive", "nuh allow-path ~/Documents/taxes/2025"),
    )),
    "classify": CommandText((
        "Classify every command that starts with PREFIX as TYPE (`classify:` in the config). Global entries "
        "are looked up before the built-in table, so they can override it (`nuh types` and `nuh status` say "
        "when one does); project entries are looked up after it and only add commands. " + _UNKNOWN_TYPE,
        _WRITES,
    ), (
        Example("treat docker rm as destructive", "nuh classify \"docker rm\" container_destructive"),
        Example("a project's own tool", "nuh classify \"./deploy.sh\" network_write --project"),
    )),
    "trust": CommandText((
        "Trust a path or a network host in the global config. A TARGET starting with `/`, `~` or `.` is a path "
        "(`trusted_paths:`; `.example.com` is a host suffix unless it exists on disk): Write and Edit there "
        "are allowed without asking although it is outside the project. Sensitive paths stay protected, `/` "
        "is refused, and trusting your home directory asks first.",
        "Anything else is a host (`known_registries:`): a name, `*.domain` for its subdomains, an IP address "
        "or a CIDR range. Outbound requests to a known host are allowed; an unknown host asks. " + _WRITES,
    ), (
        Example("trust a build directory outside the project", "nuh trust ~/builds"),
        Example("trust a package host", "nuh trust api.example.com"),
    )),
    "status": CommandText((
        "List your custom rules, global config first, then the project's: action policies, allowed paths, "
        "classify entries (a global entry that shadows built-in rules is noted), trusted hosts and paths, exec "
        "sinks, decode commands and sensitive basenames.",
    ), (
        Example("everything you have changed from the defaults", "nuh status"),
    )),
    "forget": CommandText((
        "Remove the rule RULE names: an action policy, an allowed path, a classify entry, a trusted host or a "
        "trusted path. Both configs are searched unless `--project` or `--global` narrows it.",
        "Removing a rule rewrites the YAML file and drops its comments, without asking. " + _ADMIN,
    ), (
        Example("undo nuh allow filesystem_delete", "nuh forget filesystem_delete"),
        Example("remove a classify entry", "nuh forget \"docker rm\""),
        Example("search only the project config", "nuh forget --project lang_exec"),
    )),
}
"""Long help per command path (``"config show"`` for a nested command)."""
