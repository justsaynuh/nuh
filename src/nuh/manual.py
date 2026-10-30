"""Hand-written reference sections of the man page: NAME, DESCRIPTION, FILES, ENVIRONMENT, SEE ALSO.

The command reference comes from the parser (``cli_parser``) and its long
help (``cli_text``); nothing here repeats it. Markup as in ``cli_text``:
code in backticks.
"""

from __future__ import annotations

from dataclasses import dataclass

from nuh.cli_text import OVERVIEW


@dataclass(frozen=True)
class Entry:
    """A term and its explanation: one row of FILES, ENVIRONMENT or SEE ALSO."""

    term: str
    text: str


SUMMARY = "context-aware safety guard for Claude Code's tool calls"
"""The NAME line, after ``nuh -``."""

DESCRIPTION: tuple[str, ...] = (
    OVERVIEW[0],
    "A policy is `allow` (the call runs), `ask` (the agent asks you first), `block` (the call is refused) or "
    "`context`, which decides by what the call touches: a path inside the project or a trusted path, a known "
    "network host. `nuh types` lists the action types and their defaults. Some checks are floors no config "
    "lowers: commands that reconfigure or remove nuh (`nuh_admin`), reading the environment or a secret "
    "store (`env_read`), and the built-in sensitive paths.",
    "Configuration is layered: the built-in defaults, the global config, then the project's `.nuh.yaml`, "
    "which can only tighten. `nuh config show` prints the effective settings; `nuh allow`, `deny`, "
    "`classify`, `trust`, `allow-path` and `forget` edit the files for you.",
    "Every decision is appended to the decision log (`nuh log`, `nuh tail`). With `llm.trace_dir` set, each "
    "LLM review is also kept as a trace directory (`nuh trace`).",
)

FILES: tuple[Entry, ...] = (
    Entry("~/.config/nuh/config.yaml",
          "The global config: policies (`actions`), `classify` entries, trusted paths and hosts, sensitive "
          "paths, the LLM step (`llm`), logging (`log`) and the hook (`hook`). Some keys are global-only."),
    Entry("<project root>/.nuh.yaml",
          "The project config, merged over the global one; it can only tighten. The project root is the git "
          "top level of the working directory (with `project_root: git_or_launch_dir`, else the launch "
          "directory)."),
    Entry("~/.config/nuh/system1.yaml",
          "Your system 1 definition, used instead of the shipped one when it exists; `nuh system1 export` "
          "writes it. `llm.system1.definition` names another file."),
    Entry("~/.config/nuh/nuh.log",
          "The decision log, one JSON object per line. Past `log.max_size_bytes` (default 50 MB) it rotates to "
          "`nuh.log.1` ... `nuh.log.5` (`log.backups`)."),
    Entry("~/.config/nuh/hook-errors.log",
          "Errors the installed hook script hit around nuh; each such call was answered ask for Claude Code, "
          "deny otherwise. Started afresh past 1 MB."),
    Entry("llm.trace_dir",
          "No default: tracing is off until it is set. One directory per LLM-reviewed call; "
          "`.<name>.pruned` beside it records the last prune (`llm.trace_keep_days`, default 30)."),
    Entry("~/.local/state/nuh/",
          "The `llm.self_heal` workers' state: `llm-heal.jsonl` (one line per worker run; `nuh heal-log`) and "
          "`llm-heal.<provider>` (lock and cooldown)."),
    Entry("~/.claude/hooks/nuh_guard.py",
          "The hook script `nuh install` writes for Claude Code (`~/.codex/hooks/nuh_guard.py` for Codex), "
          "read-only. It runs nuh with the interpreter nuh was installed in."),
    Entry("~/.claude/settings.json",
          "Claude Code's settings, where `nuh install` adds the PreToolUse entries (`~/.codex/hooks.json` for "
          "Codex); the previous file is kept as `.json.bak`."),
    Entry("$ZSH_CACHE_DIR/completions/_nuh",
          "The zsh completion `nuh install` writes when oh-my-zsh's completions directory exists (also looked "
          "for as `$ZSH/cache/completions` and `~/.oh-my-zsh/cache/completions`); `nuh update` refreshes it, "
          "`nuh uninstall` deletes it. Only a file with nuh's generated header line is replaced or deleted."),
    Entry("~/.local/share/zsh/site-functions/_nuh",
          "The zsh completion without oh-my-zsh (under `$XDG_DATA_HOME` when set). The directory must be on "
          "`fpath` before `compinit` runs; `nuh install` says when it is not."),
)

ENVIRONMENT: tuple[Entry, ...] = (
    Entry("NO_COLOR", "When set, `nuh log`, `nuh tail` and `nuh trace` print no colour (`--color always` "
                      "included)."),
    Entry("TERM", "`dumb` turns colour off, as `NO_COLOR` does, unless `nuh trace --color always`."),
    Entry("PAGER", "What the `nuh trace` picker opens the full view in (default `less -R`; `LESS` defaults "
                   "to `-R`)."),
    Entry("SHELL", "zsh, with stderr a terminal: until `nuh install` writes the zsh completion, nuh says so on "
                   "stderr after `--help` and before most commands (`completion_hint: false` hides it)."),
    Entry("CLAUDE_PROJECT_DIR", "Set by Claude Code: the launch directory, the project root under "
                                "`project_root: git_or_launch_dir` when there is no git root."),
    Entry("NUH_AGENT", "`claude` or `codex`: which agent the hook answers for, instead of telling it from the "
                       "hook input."),
    Entry("OPENROUTER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, SNOWFLAKE_PAT",
          "API keys of the `openrouter`, `openai`, `anthropic` and `cortex` providers, unless the provider's "
          "`key_env` names another variable. `SNOWFLAKE_ACCOUNT` is cortex's account when none is configured."),
)

SEE_ALSO: tuple[Entry, ...] = (
    Entry("site/ in the source tree",
          "The configuration reference: action types, profiles, safety lists, sensitive paths, content "
          "inspection, the LLM layer."),
    Entry("https://github.com/justsaynuh/nuh", "The project's home."),
)
