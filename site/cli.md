# CLI Reference

<!-- Each command's section between BEGIN GENERATED and END GENERATED is rendered from nuh's argparse parser:
edit summaries and options in src/nuh/cli_parser.py, descriptions and examples in src/nuh/cli_text.py, then run
`make docs`. tests/test_gen_cli_docs.py fails while a block differs from the parser. The rest of the page is
hand-written: notes on reading the output, sample output and the demo. -->

Every nuh command. Each command's section is the text `nuh COMMAND --help` and `man nuh` show, followed by notes on
reading its output where it needs them. Every command also takes `-h`, `--help`; `nuh --version` prints the installed
version.

## Core

<!-- BEGIN GENERATED: nuh install -->

### nuh install

Install the nuh hook into coding agents, and nuh's zsh completion

```text
nuh install [--agent AGENT] [--no-completion | --completion-only]
```

Write the hook script (`~/.claude/hooks/nuh_guard.py` for Claude Code, `~/.codex/hooks/nuh_guard.py` for Codex; read-only, mode 444) and add a PreToolUse entry per guarded tool to the agent's settings (`~/.claude/settings.json`, `~/.codex/hooks.json`), keeping the previous file as `.json.bak`. The hook runs with the Python interpreter nuh is installed in.

The hook's timeout is `hook.deadline_s` plus every LLM provider's timeout plus 10 seconds, at least 60. Claude Code blocks a call when the hook fails or times out; Codex has no such setting, and its hooks must be enabled and trusted in Codex.

It also writes the zsh completion (`nuh completion zsh`) as `_nuh`, once per user whichever agents are named: into oh-my-zsh's completions directory (`$ZSH_CACHE_DIR/completions`, asked of an interactive zsh) when there is one, else into `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions`, saying what to add to `~/.zshrc` when that directory is not on `fpath`. A `_nuh` that nuh did not write is left alone; without zsh the step is skipped. `--no-completion` skips it, `--completion-only` does only it (what `make install` in a checkout runs).

| Option | Description |
| --- | --- |
| `--agent AGENT` | Agent to target: claude (default), codex, or all (every agent whose settings directory exists) |
| `--no-completion` | Do not write the zsh completion |
| `--completion-only` | Only write the zsh completion: no hook (--agent is ignored) |

Examples:

```bash
# install for Claude Code
nuh install

# every agent whose settings directory exists
nuh install --agent all

# only the zsh completion, no hook
nuh install --completion-only
```

<!-- END GENERATED: nuh install -->

<!-- BEGIN GENERATED: nuh update -->

### nuh update

Rewrite the installed hook script and settings (unlock, overwrite, re-lock)

```text
nuh update [--agent AGENT] [--no-completion]
```

Rewrite the hook script after upgrading nuh or changing the hook's timeout settings: unlock it, overwrite it with this version, re-lock it, and rewrite the agent's hook entries (interpreter path, command, timeout). Without an installed script it says to run `nuh install` and changes nothing.

It also refreshes every zsh completion file nuh wrote, or writes one as `nuh install` does when there is none (`--no-completion` skips this).

| Option | Description |
| --- | --- |
| `--agent AGENT` | Agent to target: claude (default), codex, or all (every agent whose settings directory exists) |
| `--no-completion` | Do not refresh or write the zsh completion |

Examples:

```bash
# after upgrading nuh, or changing hook.deadline_s or a provider timeout
nuh update
```

<!-- END GENERATED: nuh update -->

<!-- BEGIN GENERATED: nuh uninstall -->

### nuh uninstall

Remove the nuh hook from coding agents

```text
nuh uninstall [--agent AGENT]
```

Remove nuh's entries from the agent's settings, leaving other hooks alone, then delete the hook script. The Claude Code script is kept while another agent's settings still point at it.

Once no agent keeps nuh's hooks, it also deletes the zsh completion files nuh wrote (each has nuh's generated header line); a `_nuh` without it is left alone.

| Option | Description |
| --- | --- |
| `--agent AGENT` | Agent to target: claude (default), codex, or all (every agent whose settings directory exists) |

Examples:

```bash
# remove the Claude Code hook
nuh uninstall

# remove the Codex hook only
nuh uninstall --agent codex
```

<!-- END GENERATED: nuh uninstall -->

<!-- BEGIN GENERATED: nuh claude -->

### nuh claude

Launch Claude Code with nuh hooks active

```text
nuh claude ...
```

Start Claude Code with nuh guarding this session only. When `nuh install` already added the hook to `~/.claude/settings.json`, this simply runs `claude`; otherwise it writes the hook script and passes the hook entries with `claude --settings`.

Every argument after `claude` goes to claude unchanged, `--help` included. `--settings` is refused: nuh manages it.

| Option | Description |
| --- | --- |
| `ARGS` | Arguments passed to claude unchanged (nuh claude --help is claude's help) |

Examples:

```bash
# start a guarded session
nuh claude

# resume one, flags passed through
nuh claude --resume
```

<!-- END GENERATED: nuh claude -->

<!-- BEGIN GENERATED: nuh completion -->

### nuh completion

Print a shell completion script for nuh

```text
nuh completion SHELL
```

Print the completion script for SHELL. It is generated from this version's commands and options; `nuh install` writes it for zsh and `nuh update` refreshes it after an upgrade. It completes commands, nested commands, options with their help, choices, file paths, action types, tool names, providers and agents. Reads no config, log or trace, and writes nothing.

To place it yourself, save it as `_nuh` in a directory on zsh's `fpath` that is listed before `compinit` runs, then start a new shell. To try it in the current shell only: `source <(nuh completion zsh)`.

| Option | Description |
| --- | --- |
| `SHELL` | The shell to print the script for: zsh |

Examples:

```bash
# print the zsh completion function
nuh completion zsh
```

<!-- END GENERATED: nuh completion -->

<!-- BEGIN GENERATED: nuh config -->

### nuh config

Show the effective config or its file paths

```text
nuh config COMMAND ...
```

Show the effective config (`config show`) or where its files are (`config path`). Without a subcommand it prints its usage.

Examples:

```bash
# every effective setting
nuh config show
```

<!-- END GENERATED: nuh config -->

<!-- BEGIN GENERATED: nuh config show -->

### nuh config show

Display the effective merged config

```text
nuh config show
```

Print every setting with its value after merging the global config and the project's `.nuh.yaml`, one per line, labelled with the key you write in YAML (`llm.on_failure`, `hook.deadline_s`); a setting merged from several keys keeps its own name (`classify_global`). `tools` and `human_only` print as tables, each entry with where its value comes from.

Examples:

```bash
# is the LLM step on, and what happens when it fails?
nuh config show
```

<!-- END GENERATED: nuh config show -->

<!-- BEGIN GENERATED: nuh config path -->

### nuh config path

Show the config file paths

```text
nuh config path
```

Print the global config path (`~/.config/nuh/config.yaml`) and the project config path (`.nuh.yaml` in the project root), or `(no project root)` outside a project.

Examples:

```bash
# which files nuh reads here
nuh config path
```

<!-- END GENERATED: nuh config path -->

## Test & Inspect

<!-- BEGIN GENERATED: nuh test -->

### nuh test

Dry-run classification for a command or tool input

```text
nuh test [--tool TOOL] [--path PATH] [--content CONTENT] [--pattern PATTERN] [--config JSON] [--provider PROVIDER] [--provider-url URL] [--model NAME] [--timeout SECONDS] [--can-block | --no-can-block] [--llm | --no-llm] [--set llm.KEY=VALUE] [--show-request] [--user MESSAGE] [--first-prompt TEXT] [--transcript FILE] [--cwd DIR] [args ...]
```

Show what the hook would decide for a command or tool input, without running it: the stages with their action types and policies, the decision and its reason, any floor rule behind it, then how the hook would route it (`Word:`, `Human-only:`, `LLM call:`, `Delivery:`). When the hook would call the LLM, `nuh test` makes the same live call and shows the review and the decision after it.

The default tool is Bash, taking the command as one argument (several are joined as a shell line). `--tool Read` takes a file path, `Glob` and `Grep` a path (`Grep` also `--pattern`), `Write`, `Edit`, `MultiEdit` and `NotebookEdit` a path plus `--content`, `WebFetch` a URL, `WebSearch` a query, and any other tool (`mcp__*`) its input as a JSON object.

The LLM flags change the LLM config in memory for this run only; picking a provider turns the LLM step on, as `--llm` does. `--provider-url` or `--model` without `--provider`, and `--no-llm` with any LLM flag, are errors. `--user` and `--first-prompt` build a throwaway transcript, so providers pick the session context as they would from a real one.

| Option | Description |
| --- | --- |
| `args` | The command string, or the tool input |
| `--tool TOOL` | Tool name (default: Bash) |
| `--path PATH` | File or directory path for the tool input |
| `--content CONTENT` | Content for Write/Edit content inspection |
| `--pattern PATTERN` | Search pattern for Grep |
| `--config JSON` | Inline JSON override of the non-LLM config (classify, actions, profile, ...) |

**LLM step (overrides live in memory for this run; no config file is written)**

| Option | Description |
| --- | --- |
| `--provider PROVIDER` | Restrict the cascade to this provider |
| `--provider-url URL` | URL for --provider |
| `--model NAME` | Model for --provider |
| `--timeout SECONDS` | Each provider's timeout |
| `--can-block, --no-can-block` | Let an LLM block block (--no-can-block: cap it to ask) |
| `--llm` | Review even when llm.enabled is off or the result is not an LLM-eligible ask |
| `--no-llm` | Skip the LLM step |
| `--set llm.KEY=VALUE` | Set any llm.\* key; the value is JSON when it parses, else a string (repeatable) |
| `--show-request` | Print each request sent and the raw response |

**session context for the LLM step**

| Option | Description |
| --- | --- |
| `--user MESSAGE` | A recent user message (repeatable, oldest first) |
| `--first-prompt TEXT` | The session's first user message |
| `--transcript FILE` | A real transcript (Claude Code JSONL or Codex rollout) |
| `--cwd DIR` | Run the whole test from DIR |

Examples:

```bash
# a shell command
nuh test "rm -rf /"

# a Read of a key file
nuh test --tool Read ~/.ssh/id_rsa

# a Write whose content holds a secret
nuh test --tool Write --path ./config.py --content "api_key='sk-secret123'"
```

<!-- END GENERATED: nuh test -->

#### Reading the output

The stages, action types, policies, composition rules and decision come first. Then the
[gating](configuration/llm.md#gating-three-questions-per-tool) lines:

- `Word:` the tool's word in `tools:` and where it comes from (`config`, `default`, `follows Edit`, `config: <glob>` for the glob that matched, or `not listed`).
- `Human-only:` for an ask, the `human_only` keys behind it that are true, then the others; an ask with a true key never goes to the LLM.
- `LLM call:` whether the hook would call the LLM, and if not, why (the word, a human-only key, a static block, `llm.enabled` off).
- `Delivery:` how the hook hands the decision to Claude Code. `explicit allow` means nuh answers allow and Claude Code runs the call without asking. `silent` means the tool's word is `defer`: nuh says nothing and Claude Code's own permission settings decide, so you may still get a prompt. An ask or block is always sent.

When the hook would call the LLM, the review follows, then `After review:`, the decision the hook makes of it. Before
the LLM result, `nuh test` prints the effective settings: the providers, each one's model, URL and timeout, and
`can_block`. An API key shows only whether its environment variable is set (`key=$OPENROUTER_API_KEY (set)`), never
its value.

#### Pointing the LLM step anywhere

The LLM flags apply to every tool the LLM reviews: Bash, the write tools, and any other tool through the generic
review. `--provider` takes any of the [providers](configuration/llm.md#providers).

```bash
# system 1 alone, on a local endpoint
nuh test "rm -rf build/" --provider system1 --provider-url http://127.0.0.1:11434

# a different Ollama URL or model, alone in the cascade
nuh test "python3 build.py" --provider ollama --provider-url http://gpu-box:11434/api/chat --model qwen3:14b

# simulate a session, review even a static allow, and see what was sent
nuh test "ls ~/.aws" --llm --first-prompt "set up the deploy" --user "check my credentials" --show-request
```

`--llm` also reviews a call the hook would not send (an ask a `human_only` key is behind, a static block, an allow or
ask the tool's word does not send) and says why the hook would not send it. A `--set` value is parsed as JSON when it
parses, else kept as a string: `--set llm.context.user_turns=2`, `--set llm.ollama='{"model": "m"}'`.

The throwaway transcript `--user` and `--first-prompt` build lives in a temporary directory and is removed when the
test ends. Providers pick from it as from a real session: the last `llm.context.user_turns` messages (default 6), and
the first prompt separately only when it is not among them.

<!-- BEGIN GENERATED: nuh types -->

### nuh types

List all action types with descriptions and default policies

```text
nuh types
```

List every built-in action type with its default policy and description. A global `classify` entry that overrides a built-in rule or a flag classifier is noted under its type, with the `nuh forget` that restores the built-in.

Examples:

```bash
# the action types and their defaults
nuh types
```

<!-- END GENERATED: nuh types -->

<!-- BEGIN GENERATED: nuh log -->

### nuh log

Show recent hook decisions

```text
nuh log [-n LIMIT] [-d {allow,ask,block} | --blocks | --asks] [-t TOOL] [--action-type TYPE] [-p PROJECT] [-g GREP] [-s SINCE] [--call CALL] [--session SESSION] [--llm] [--errors] [-a] [-r] [-v] [-I] [--json] [-f]
```

Show hook decisions from the decision log, oldest first, one compact line each: local time, session, call reference, decision, tool, input, action type and reason, the LLM verdict and total time. Input and reason are cut to the terminal width; `-v` shows every field, the decision's steps in order among them. Filters combine.

Session and tool call ID together identify a call: `--call` takes the tool call ID (whole, its start or its end), `SESSION:ID` also the session's start. Colour only on a terminal, never with `NO_COLOR` set or `TERM=dumb`.

| Option | Description |
| --- | --- |
| `-n, --limit LIMIT` | Entries to show (default: 50; 0 = no limit) |
| `-d, --decision {allow,ask,block}` | Only this decision |
| `--blocks` | Only blocked decisions (= -d block) |
| `--asks` | Only asks (= -d ask) |
| `-t, --tool TOOL` | Tool name, any case (Bash, Read, Write, ...) |
| `--action-type TYPE` | Action type (git\_write, filesystem\_read, ...) |
| `-p, --project PROJECT` | Project path substring |
| `-g, --grep GREP` | Regex on the input (case-insensitive) |
| `-s, --since SINCE` | Only the last 30s, 10m, 2h, 1d, or today |
| `--call CALL` | Tool call ID: whole, its start or its end, with or without toolu\_ (or the entry id); SESSION:ID also matches the session ID's start |
| `--session SESSION` | Session ID prefix |
| `--llm` | Only entries where the LLM was consulted |
| `--errors` | Only entries whose LLM step errored or timed out |
| `-a, --all` | Read every rotated log (default: only as far back as -n needs) |
| `-r, --reverse` | Newest first (default: oldest first) |
| `-v, --details` | Multi-line view of every field |
| `-I, --full-input` | Whole input, never truncated (with -v, the logged tool\_input) |
| `--json` | Raw JSON lines (pipe to jq) |
| `-f, --follow` | Keep printing new entries as they are logged (= nuh tail) |

Examples:

```bash
# asks in the last two hours
nuh log -s 2h -d ask

# one call, every field
nuh log --call 01AbCdEfGh -v

# input matching a regex, only where the LLM was consulted
nuh log -g 'git push' --llm
```

<!-- END GENERATED: nuh log -->

#### Reading a log line

A compact line reads: local time, session (8 chars of the session ID), call reference, decision, tool,
`sub:<agent type>` for a subagent's call, the input, `[action type] — reason`, the LLM verdict
(`<provider>→<decision>`, `<provider>:block→ask` when `llm.can_block` capped the LLM's block, `<provider>:error` when
it failed), total ms, and `T` when the entry names an LLM trace directory (its `trace_dir` field; traces are opt-in
and `nuh log` never reads them). A dim `── date ──` line marks each new day.

The tool call ID column is `tool_use_id` without its `toolu_` prefix, cut to 10 characters; entries without one show
their log entry `id`. When tracing is on, the same ID names the call's trace directory. `-v` shows both IDs whole,
with the subagent ID, the decision's steps in the order they ran, one line each, then whose reason explains the
decision and what the LLM wanted when it differs:

```
  steps
    1. static       ask   Bash: unknown → ask
         unknown              → ask   (unknown → ask)
    2. llm          block exfiltrates keys
    3. cap          block → ask  (llm.can_block: false)
  outcome   reason from static · LLM wanted block
```

Then the trace dir and the LLM step's provider, model and cascade. Entries the removed decide backend wrote show the
same way, with `decide` as the provider.

#### Steps

Every value that feeds the final decision is a step in the entry's `steps` list, in order; top-level `decision` and
`reason` stay the final ones.

| Step | Fields | What it records |
|------|--------|-----------------|
| `static` | `decision`, `reason`, `breakdown` | The static verdict for the whole call, before any LLM step, for every tool. `breakdown` is Bash's per-stage results (`action_type`, `decision`, `policy`, `reason`; `composition` names a pipe rule), or for other tools each check that objected: `path`, `boundary`, `content`, `hidden_chars`, `credential_search`, `classify`, `deadline` (with `rule` for a floor rule) |
| `llm` | `decision`, `reason` | The LLM's own verdict before any cap (`allow`, `ask`, `block`, or `uncertain`) and its reasoning. A review with no verdict has `decision: ""` and `error` (`error` or `too_large`) |
| `question` | `provider`, `name`, `type`, `answer`, `triggered`, `rule` | One system 1 question: its answer (`p`, the top option's probability, for a choice; `label` for a score) and, when it triggered a decision, the threshold that fired. `-v` shows `destructive  noul 0.81 > 0.7 → block` |
| `gate` | `provider`, `name`, `then`, `fired` | One system 1 gate and whether it fired: `net_and_risky → ask (not fired)` |
| `provider` | `provider`, `model`, `outcome`, `decision`, `ms`, `error` | The system 1 attempt: `system1 nimble:9b 412ms ok`, `uncertain`, or `failed: <error>` |
| `cap` | `from`, `to`, `rule` | `llm.can_block: false` turned the LLM's block into an ask |
| `escalate` | `from`, `to`, `rule` | The LLM's verdict, after the cap, was stricter and replaced the decision |
| `lower` | `from`, `to`, `rule` | Under `decide`, the LLM's allow lowered an ask no `human_only` key was behind |
| `adopt` | `from`, `to`, `rule` | A Bash LLM ask took the static ask's place; its reason explains the ask |
| `human_only` | `from`, `to`, `rule` | The LLM wanted looser; a `human_only` key behind the ask held it (`rule` names the key) |
| `tool` | `from`, `to`, `rule` | The LLM wanted looser; the tool's word does not let it lower (`rule`: `tools.Write: check`) |
| `fail_closed` | `from`, `to`, `rule` | The review gave no verdict, so the call asks |
| `uncertain` | `from`, `to`, `rule` | The review was uncertain, so the call asks (`llm.on_uncertain: ask`) |
| `keep` | `from`, `to`, `rule` | The review reached no decision and `llm.on_uncertain` or `llm.on_failure` is `static`: the static decision stands |
| `strictest` | `from`, `to`, `rule`, `target` | apply_patch: the strictest operation's outcome won; review steps carry their operation's `target` |

A step kind added later is one more element, and readers skip kinds they do not know. The reason that explains the
decision is the static step's until a `escalate`, `lower`, `adopt` or `fail_closed` step takes the LLM's. Steps are
bounded in the log: a step's reason at 500 characters, a breakdown at 32 items of 300 characters each
(`breakdown_omitted` counts the rest); content written by a tool never goes into a step. An LLM trace keeps them
whole.

`llm` holds how the LLM step ran: `provider`, `model`, `ms`, `cascade`, `skipped` and `skip_reason`, `error`,
`action`, and `prompt` with `log.llm_prompt`. Entries logged before `steps` existed have `classify.stages`,
`llm.decision`, `llm.reasoning`, `llm.capped` and `warning` instead; `nuh log` still reads them.

#### More examples

```bash
nuh log --blocks                    # only blocked decisions
nuh log -t Edit -v                  # Edit calls, every field
nuh log --errors -s today           # LLM errors and timeouts today
nuh log --call 3f2a9c1e:01AbCdEfGh  # the call ID: session + tool call ID
nuh log -f                          # keep printing new decisions
nuh log --json | jq .               # raw JSON lines
```

<!-- BEGIN GENERATED: nuh tail -->

### nuh tail

Follow hook decisions as they are logged (nuh log -f)

```text
nuh tail [-n LIMIT] [-d {allow,ask,block} | --blocks | --asks] [-t TOOL] [--action-type TYPE] [-p PROJECT] [-g GREP] [-s SINCE] [--call CALL] [--session SESSION] [--llm] [--errors] [-a] [-r] [-v] [-I] [--json]
```

`nuh log --follow` with a shorter backlog: the last 10 matching entries, then every new one as it is logged, until Ctrl-C. It survives log rotation. Takes the same filters as `nuh log`.

| Option | Description |
| --- | --- |
| `-n, --limit LIMIT` | Entries to show (default: 10; 0 = no limit) |
| `-d, --decision {allow,ask,block}` | Only this decision |
| `--blocks` | Only blocked decisions (= -d block) |
| `--asks` | Only asks (= -d ask) |
| `-t, --tool TOOL` | Tool name, any case (Bash, Read, Write, ...) |
| `--action-type TYPE` | Action type (git\_write, filesystem\_read, ...) |
| `-p, --project PROJECT` | Project path substring |
| `-g, --grep GREP` | Regex on the input (case-insensitive) |
| `-s, --since SINCE` | Only the last 30s, 10m, 2h, 1d, or today |
| `--call CALL` | Tool call ID: whole, its start or its end, with or without toolu\_ (or the entry id); SESSION:ID also matches the session ID's start |
| `--session SESSION` | Session ID prefix |
| `--llm` | Only entries where the LLM was consulted |
| `--errors` | Only entries whose LLM step errored or timed out |
| `-a, --all` | Read every rotated log (default: only as far back as -n needs) |
| `-r, --reverse` | Newest first (default: oldest first) |
| `-v, --details` | Multi-line view of every field |
| `-I, --full-input` | Whole input, never truncated (with -v, the logged tool\_input) |
| `--json` | Raw JSON lines (pipe to jq) |

Examples:

```bash
# follow everything
nuh tail

# follow asks, every field
nuh tail -d ask -v
```

<!-- END GENERATED: nuh tail -->

<!-- BEGIN GENERATED: nuh trace -->

### nuh trace

Browse and render LLM traces (llm.trace\_dir)

```text
nuh trace [--render DIR] [--view {answers,prompt,path,raw,all}] [--json] [--list] [--dir DIR] [--color {auto,always,never}] [-n LIMIT] [-d {allow,ask,block} | --blocks | --asks] [-t TOOL] [--provider PROVIDER] [-s SINCE] [--prune] [--dry-run] [target]
```

Browse and render the LLM traces under `llm.trace_dir`: what each question asked, what system 1 answered and how sure it was, and how the answers became the decision. Read-only, except `--prune`.

With no argument on a terminal with fzf on `PATH`, an interactive picker: one line per trace, newest first, the highlighted one rendered beside it; alt-a, alt-p, alt-d and alt-r switch the preview between the views, Enter opens all of them in `$PAGER` (default `less -R`). Without fzf or a terminal it prints the list. A call reference (`#9c3f1a`, as the notice shows it) or a trace directory renders one trace.

Views: `answers` (each system 1 question, its answer and what it triggered), `prompt` (each request as sent), `path` (the decision's steps, as `nuh log -v` words them), `raw` (every file, pretty-printed) and `all`. Traces older than `llm.trace_keep_days` (default 30) are pruned at most once a day on their own; `--prune` does it now.

| Option | Description |
| --- | --- |
| `target` | A call reference (\#abc123, an ID's start or end) or a trace directory |
| `--render DIR` | Render this trace directory (or name) |
| `--view {answers,prompt,path,raw,all}` | What to render (default: answers) |
| `--json` | Machine-readable summary (JSON lines for the list) |
| `--list` | Print the list even on a terminal with fzf |
| `--dir DIR` | Trace directory to read (default: llm.trace\_dir) |
| `--color {auto,always,never}` | Colour: auto (a terminal), always (still off with NO\_COLOR), never |
| `-n, --limit LIMIT` | Traces to list (default: 200; 0 = all) |
| `-d, --decision {allow,ask,block}` | Only this final decision |
| `--blocks` | Only blocks |
| `--asks` | Only asks |
| `-t, --tool TOOL` | Tool name, any case (Bash, Read, Write, ...) |
| `--provider PROVIDER` | Only traces whose LLM answer came from this provider |
| `-s, --since SINCE` | Only the last 30s, 10m, 2h, 1d, or today |
| `--prune` | Delete traces older than llm.trace\_keep\_days now (nuh does it daily on its own) |
| `--dry-run` | With --prune: only say what would be deleted |

Examples:

```bash
# pick a trace interactively
nuh trace

# one trace's request as sent
nuh trace '#9c3f1a' --view prompt

# what pruning would delete now
nuh trace --prune --dry-run
```

<!-- END GENERATED: nuh trace -->

#### The list and the views

A list line reads: local time, call reference (`#` and the last 6 characters of the tool call ID, as in the notice
and `nuh log --call`), tool, the input, the final decision, the provider whose answer was used, system 1's verdict
with its probability, and the risk score. A trace that cannot be read whole (cut short, not JSON, still being
written) is listed with `(partial)` or `(unreadable: …)`, never skipped and never fatal. The list sorts directory
names, which start with a UTC timestamp, and opens only the traces it shows. Traces live under
[`llm.trace_dir`](configuration/llm.md#trace_dir); `--prune` keeps
[`llm.trace_keep_days`](configuration/llm.md#trace_keep_days).

| View | Shows |
|------|-------|
| `answers` | One row per system 1 question: type, answer, its probability (or score) with a bar, the reply's confidence, and what it triggered with the threshold that fired (`block (> 0.7)`). Below: each choice's probabilities, the risk distribution, the gates, each review's provider, verdict, confidence and reasoning, then the final decision, its reason and any cap or escalation |
| `prompt` | Each request as sent: system 1's state (preamble, tool call, session context) and questions, or a prompted provider's messages, wrapped to the terminal |
| `path` | The decision's steps from `result.json`, worded as `nuh log -v` words them |
| `raw` | Every file of the trace directory, pretty-printed |
| `all` | All four |

#### More examples

```bash
nuh trace --list -s 2h --asks    # plain list: asks in the last two hours
nuh trace <dir> --view all       # every view of one trace directory
nuh trace --json -n 20 | jq .    # machine-readable summaries
```

<!-- BEGIN GENERATED: nuh system1 -->

### nuh system1

Write out or check the system 1 definition (no model call)

```text
nuh system1 COMMAND ...
```

Write out the system 1 definition nuh ships, to edit (`system1 export`), or validate one (`system1 check`). Neither calls a model. Without a subcommand it prints its usage.

Examples:

```bash
# validate the definition nuh uses now
nuh system1 check
```

<!-- END GENERATED: nuh system1 -->

<!-- BEGIN GENERATED: nuh system1 export -->

### nuh system1 export

Write the shipped system 1 definition as YAML to edit

```text
nuh system1 export [--force] [--stdout] [FILE]
```

Write the shipped system 1 definition as YAML to edit by hand: block style, the preamble and each question's instructions as literal blocks, and `based_on` set to this nuh version. The default path is `llm.system1.definition` when set, else `system1.yaml` beside the global config (`~/.config/nuh/system1.yaml`). A YAML file only: a `.json` path is refused.

The file replaces the shipped definition entirely (only its `tools:` blocks merge, per tool) and nuh does not update it on upgrade. An existing file is never overwritten without `--force`. Writing asks under nuh's `nuh_admin` floor when an agent runs it; `--stdout` only reads.

| Option | Description |
| --- | --- |
| `FILE` | Where to write (default: llm.system1.definition, else system1.yaml beside the global config) |
| `--force` | Overwrite an existing file |
| `--stdout` | Print the definition instead of writing a file |

Examples:

```bash
# write the file nuh reads, then edit it
nuh system1 export

# print it, to compare with your copy
nuh system1 export --stdout
```

<!-- END GENERATED: nuh system1 export -->

The exported file is what [the system 1 definition](configuration/llm.md#the-definition) section describes: block
style, the preamble and each question's instructions as literal blocks (`|`), and a header comment. To keep a copy
somewhere else, export to that path and point `llm.system1.definition` at it. To see what changed upstream since your
copy:

```bash
nuh system1 export --stdout | diff ~/.config/nuh/system1.yaml -
```

<!-- BEGIN GENERATED: nuh system1 check -->

### nuh system1 check

Validate a definition as the system 1 provider loads it (no model call)

```text
nuh system1 check [FILE]
```

Load a definition with the code the system 1 provider uses (the base and every `tools:` block) and compile its gates, without calling a model. Prints the file, the preamble, context items, questions and how each decides, gates, each tool block, any warnings, and `OK`. A definition nuh cannot use prints why and exits 1.

With no FILE it checks the one nuh uses now: `llm.system1.definition`, else `~/.config/nuh/system1.yaml`, else the shipped one.

| Option | Description |
| --- | --- |
| `FILE` | The definition to check (default: the one nuh uses for llm.system1) |

Examples:

```bash
# the definition nuh uses now
nuh system1 check

# a copy you are editing
nuh system1 check ~/defs/s1.yaml
```

<!-- END GENERATED: nuh system1 check -->

Sample output:

```text
Checked /home/me/.config/nuh/system1.yaml
preamble: An AI coding agent is about to run the tool call below on Sam's workst... (259 chars)
context: action_tokens 1500, session_tokens 800, file_lines 30; sends user
questions (5):
  destructive   noul    logged only
  ...
  verdict       choice  the top choice decides
gates: none
OK
```

A gate reading a name that is not a question (in the base, or in a tool block that removed it) is a warning: the gate
errors whenever it needs that name, which fails the review.

<!-- BEGIN GENERATED: nuh heal-log -->

### nuh heal-log

Show what the llm.self\_heal workers did (read only)

```text
nuh heal-log [-n LIMIT] [--json]
```

What the `llm.self_heal` workers did, oldest first: one line per worker run from `~/.local/state/nuh/llm-heal.jsonl` with its time, provider, model, host, outcome, how long the replay took to answer (and the model load within it, when Ollama reports one) and error. A worker starts after a provider times out, and replays the request only when the model is on the server but not loaded. Read only.

| Option | Description |
| --- | --- |
| `-n, --limit LIMIT` | Entries to show (default: 20; 0 = all) |
| `--json` | Raw JSON lines (pipe to jq) |

Examples:

```bash
# the last 20 runs
nuh heal-log

# every run, as JSON lines
nuh heal-log -n 0 --json
```

<!-- END GENERATED: nuh heal-log -->

Sample output (see [`llm.self_heal`](configuration/llm.md#self_heal)):

```text
2026-10-23T08:00:00+00:00  system1  nimble:9b  https://classification.example  healed: loaded the model  ready after 41.2 s (no load timing from /v1/systemone)
2026-10-23T08:20:00+00:00  ollama  qwen3:8b  http://ollama.example:11434  healed: loaded the model  ready after 157.5 s (model load 4.2 s)
```

`ready after` is the replay's wall time: queueing on the server, freeing VRAM, the load, prompt evaluation and the
answer. The model load inside it comes from Ollama's own `load_duration`, which only `/api/chat` and `/api/generate`
report.

## Security Demo

### /nuh-demo

Live security demo that runs inside Claude Code. Clone the [nuh repo](https://github.com/justsaynuh/nuh) and
run `/nuh-demo` from within it; the slash command is defined in `.claude/commands/nuh-demo.md`. Without an argument it
asks which mode to run.

```
/nuh-demo                              # pick a mode: demo (25 curated cases across 8 stories), full, or one story
/nuh-demo full                         # all 90 base cases + 21 config variants
/nuh-demo story:remote_code_execution  # deep-dive into a single story
```

**Stories:**

| Story | What it covers |
|-------|---------------|
| `safe_operations` | Operations that should pass through |
| `remote_code_execution` | Remote code execution (curl \| bash, wget \| sh) |
| `data_exfiltration` | Data exfiltration (piping secrets to network) |
| `obfuscated_execution` | Obfuscated execution (base64, eval, nested shells) |
| `path_boundary_protection` | Path & boundary protection (sensitive dirs, project scope) |
| `destructive_operations` | Destructive operations (rm, force push, DROP TABLE) |
| `credential_secret_detection` | Credential & secret detection in file content |
| `network_context` | Network context (trusted vs unknown hosts) |

## Manage Rules

Adjust policies from the command line, no need to edit YAML.

<!-- BEGIN GENERATED: nuh allow -->

### nuh allow

Set an action type's policy to allow

```text
nuh allow [--project] TYPE
```

Set an action type's policy to `allow` in the global config (`actions:`). Floor types (`nuh_admin`, `env_read`) cannot be lowered; the error says which `human_only` key lets the LLM decide them instead. A name that is not a built-in action type is refused with suggestions when close to one; otherwise nuh offers to create it as a custom type.

With `--project` the rule goes into the project's `.nuh.yaml` (in the git root) instead of the global config; a project config can only tighten, so a project rule that loosens the effective policy is refused. Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks first, and refuses when stdin is not a terminal. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `TYPE` | The action type to allow |
| `--project` | Write to the project's .nuh.yaml instead of the global config |

Examples:

```bash
# stop asking before deleting files
nuh allow filesystem_delete
```

<!-- END GENERATED: nuh allow -->

<!-- BEGIN GENERATED: nuh deny -->

### nuh deny

Set an action type's policy to block

```text
nuh deny [--project] TYPE
```

Set an action type's policy to `block` in the global config (`actions:`). A name that is not a built-in action type is refused with suggestions when close to one; otherwise nuh offers to create it as a custom type.

With `--project` the rule goes into the project's `.nuh.yaml` (in the git root) instead of the global config; a project config can only tighten, so a project rule that loosens the effective policy is refused. Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks first, and refuses when stdin is not a terminal. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `TYPE` | The action type to deny |
| `--project` | Write to the project's .nuh.yaml instead of the global config |

Examples:

```bash
# block outbound network requests
nuh deny network_outbound

# block history rewrites in this project only
nuh deny git_history_rewrite --project
```

<!-- END GENERATED: nuh deny -->

<!-- BEGIN GENERATED: nuh classify -->

### nuh classify

Classify a command prefix as an action type

```text
nuh classify [--project] PREFIX TYPE
```

Classify every command that starts with PREFIX as TYPE (`classify:` in the config). Global entries are looked up before the built-in table, so they can override it (`nuh types` and `nuh status` say when one does); project entries are looked up after it and only add commands. A name that is not a built-in action type is refused with suggestions when close to one; otherwise nuh offers to create it as a custom type.

Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks first, and refuses when stdin is not a terminal. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `PREFIX` | The command prefix to classify (quote it: "docker rm") |
| `TYPE` | The action type to assign |
| `--project` | Write to the project's .nuh.yaml instead of the global config |

Examples:

```bash
# treat docker rm as destructive
nuh classify "docker rm" container_destructive

# a project's own tool
nuh classify "./deploy.sh" network_write --project
```

<!-- END GENERATED: nuh classify -->

<!-- BEGIN GENERATED: nuh trust -->

### nuh trust

Trust a path or network host (global config)

```text
nuh trust [--project] TARGET
```

Trust a path or a network host in the global config. A TARGET starting with `/`, `~` or `.` is a path (`trusted_paths:`; `.example.com` is a host suffix unless it exists on disk): Write and Edit there are allowed without asking although it is outside the project. Sensitive paths stay protected, `/` is refused, and trusting your home directory asks first.

Anything else is a host (`known_registries:`): a name, `*.domain` for its subdomains, an IP address or a CIDR range. Outbound requests to a known host are allowed; an unknown host asks. Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks first, and refuses when stdin is not a terminal. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `TARGET` | A path (starts with /, ~ or .), or a network host: name, \*.domain (subdomains), IP, or CIDR |
| `--project` | Rejected for a path; for a host it changes nothing (host trust is always global) |

Examples:

```bash
# trust a build directory outside the project
nuh trust ~/builds

# trust a package host
nuh trust api.example.com
```

<!-- END GENERATED: nuh trust -->

<!-- BEGIN GENERATED: nuh allow-path -->

### nuh allow-path

Allow a sensitive path for the current project

```text
nuh allow-path PATH
```

Exempt a path from your own `sensitive_paths` and `sensitive_basenames` entries, for the current project root only (`allow_paths:` in the global config). A path a built-in entry covers (`~/.aws/config`, `.env`, ...) is a floor: the command refuses it. Needs a project root.

Writing a rule rewrites the YAML file and drops its comments: when the file has comments nuh asks first, and refuses when stdin is not a terminal. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `PATH` | The path to allow |

Examples:

```bash
# let this project read a path you marked sensitive
nuh allow-path ~/Documents/taxes/2025
```

<!-- END GENERATED: nuh allow-path -->

<!-- BEGIN GENERATED: nuh status -->

### nuh status

Show all custom rules

```text
nuh status
```

List your custom rules, global config first, then the project's: action policies, allowed paths, classify entries (a global entry that shadows built-in rules is noted), trusted hosts and paths, exec sinks, decode commands and sensitive basenames.

Examples:

```bash
# everything you have changed from the defaults
nuh status
```

<!-- END GENERATED: nuh status -->

<!-- BEGIN GENERATED: nuh forget -->

### nuh forget

Remove a rule

```text
nuh forget [--project] [--global] RULE
```

Remove the rule RULE names: an action policy, an allowed path, a classify entry, a trusted host or a trusted path. Both configs are searched unless `--project` or `--global` narrows it.

Removing a rule rewrites the YAML file and drops its comments, without asking. When an agent runs it, it asks under nuh's own `nuh_admin` floor.

| Option | Description |
| --- | --- |
| `RULE` | The rule to remove: an action type, path, command prefix, or host |
| `--project` | Search only the project config |
| `--global` | Search only the global config |

Examples:

```bash
# undo nuh allow filesystem_delete
nuh forget filesystem_delete

# remove a classify entry
nuh forget "docker rm"

# search only the project config
nuh forget --project lang_exec
```

<!-- END GENERATED: nuh forget -->
