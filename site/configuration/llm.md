# LLM Layer

nuh can optionally consult an LLM to resolve ambiguous `ask` decisions that the deterministic classifier can't handle.

```
Tool call → nuh (deterministic) → LLM (optional) → Claude Code permissions → execute
```

The deterministic layer always runs first. Which of its decisions the LLM sees, and what it may do with them,
is set per tool ([Gating](#gating-three-questions-per-tool)). If no LLM is configured or available, the decision
stays as the deterministic layer made it, and an ask prompts.

## Gating: three questions per tool

Three settings answer everything about how a call is handled. nuh's static rules (classification, paths,
content, floors) always run first, and an ask or block always reaches Claude Code.

1. **Is nuh's allow sent, or left to Claude Code?** The tool's word in `tools:`.
2. **Does the LLM look, and may it lower an ask?** The same word.
3. **Which asks may no LLM ever lower?** `human_only:`.

```yaml
# ~/.config/nuh/config.yaml (global only)
tools:                  # defer < approve < check < decide
  Bash: decide
  Write: decide
  Edit: decide          # MultiEdit, NotebookEdit and apply_patch follow Edit
  Read: approve
  Glob: approve
  Grep: approve
  # any other tool: defer

human_only:             # asks the LLM may never lower; every floor rule is already true
  git_remote_write: true
```

The block above is the default, except the `human_only` line, which is an example.

| Word | nuh's allow | LLM |
|---|---|---|
| `defer` | silent: Claude Code's own permission mode decides, so you may still get a prompt | not called |
| `approve` | sent: Claude Code runs the call without its own prompt | not called |
| `check` | sent, unless the LLM raises it | sees allows; may raise one to ask (to block with [`can_block`](#can_block)) |
| `decide` | sent | also sees asks; a confident allow lowers an ask no `human_only` key is behind |

Each word includes everything the one before it does.

### What a reviewing word sends

Fixed per kind of tool:

- **Bash:** `decide` sends static asks and allows that run a script file nuh scanned clean; `check` sends those
  clean-script allows only.
- **Write tools** (Write, Edit, MultiEdit, NotebookEdit, apply_patch): `decide` sends asks and allows; `check`
  sends allows.
- **Any other tool** (Read, Glob, Grep, `mcp__*`, WebFetch, ...): the generic review. `decide` sends asks and
  allows; `check` sends allows. The model is shown the tool name, its input as JSON and the static reason; input
  over `llm.max_action_chars` (48000 characters by default) is cut, with a marker saying how much was shown
  (`…[nuh: input cut here, 200 of 5016 characters shown; the rest was not reviewed]`). System 1 is shown the
  call as it is shown any other.

The LLM is only called when its answer could change something: never for a static block, an ask a `human_only`
key is behind, an ask under `check`, or when `llm.enabled` is off. A skipped ask is logged as
`llm.skipped: no_effect` with a `skip_reason` (shown by `nuh log -v` and `nuh test`); `nuh log --llm` leaves
those entries out.

### Tool names

A key is a tool name or a glob (`mcp__forge__*`). The exact name wins; MultiEdit, NotebookEdit and apply_patch
then follow Edit unless they are listed themselves; then the longest matching glob; a tool nothing matches is
`defer`. Restating a default overrides it: `Edit: approve` turns the LLM off for edits.

### human_only

Keys, each true (no LLM may lower an ask it is behind) or false:

| Key | Default | Behind |
|---|---|---|
| floor rule ids (`credential_paths`, `sensitive_paths`, `nuh_admin`, `hidden_chars`, ...; `nuh config show` lists them) | true | the asks that rule makes |
| action types (`unknown`, `lang_exec`, `filesystem_write`, `git_remote_write`, ... see `nuh types`) | false | a Bash stage's ask; a tool the classify table types; a write outside the project (`filesystem_write`) or with runnable text in it (`lang_exec`) |
| `content_secret` | true | a secret pattern in written content |
| `content_custom` | true | your own `content_patterns.add` categories |
| `content_too_large` | true | content too large (or too slow) to scan |

An ask may go to the LLM only when every key behind it is false, so a secret beside an outside-project write
holds the whole write. An ask nuh cannot attribute to any key (a Grep credential search, say) is human-only.
Floors stay floors: setting a floor rule to false only lets the LLM decide its asks, never lowers a verdict by
itself, and blocks stay blocks. An unknown key warns and is ignored.

A lowered ask keeps its static reason in the log (`Write outside project: ~/x.txt — lowered to allow by LLM
review`), and `nuh log -v` shows the LLM's verdict and reasoning on the `llm` row.

## Providers

nuh supports 6 LLM providers. Configure one or more in cascade order: the first that answers decides (see [How the cascade works](#how-the-cascade-works)).

| Provider | API | Default model | Auth env var |
|----------|-----|---------------|-------------|
| `system1` | System 1 (`/v1/systemone`), see [System 1](#system-1) | `nimble:9b` | *(none, or `key_env`)* |
| `ollama` | Chat API (`/api/chat`) | `qwen3.5:9b` | *(none -- local)* |
| `openrouter` | OpenAI-compatible | `google/gemini-3.1-flash-lite-preview` | `OPENROUTER_API_KEY` |
| `openai` | Responses API (`/v1/responses`) | `gpt-5.3-codex` | `OPENAI_API_KEY` |
| `anthropic` | Messages API (`/v1/messages`) | `claude-haiku-4-5` | `ANTHROPIC_API_KEY` |
| `cortex` | Snowflake Cortex REST | `claude-haiku-4-5` | `SNOWFLAKE_PAT` |

All providers use `urllib.request` (stdlib) -- no external HTTP dependencies.

## Configuration

```yaml
# ~/.config/nuh/config.yaml
llm:
  enabled: true
  providers: [ollama, openrouter]   # cascade order
  ollama:
    url: http://localhost:11434/api/chat
    model: qwen3.5:9b
    timeout: 10
  openrouter:
    url: https://openrouter.ai/api/v1/chat/completions
    key_env: OPENROUTER_API_KEY
    model: google/gemini-3.1-flash-lite-preview
    timeout: 10
```

### Provider examples

=== "Ollama (local)"

    ```yaml
    llm:
      enabled: true
      providers: [ollama]
      ollama:
        url: http://localhost:11434/api/chat
        model: qwen3.5:9b
        timeout: 10
    ```

=== "OpenRouter"

    ```yaml
    llm:
      enabled: true
      providers: [openrouter]
      openrouter:
        url: https://openrouter.ai/api/v1/chat/completions
        key_env: OPENROUTER_API_KEY
        model: google/gemini-3.1-flash-lite-preview
    ```

=== "OpenAI"

    ```yaml
    llm:
      enabled: true
      providers: [openai]
      openai:
        url: https://api.openai.com/v1/responses
        key_env: OPENAI_API_KEY
        model: gpt-5.3-codex
    ```

=== "Anthropic"

    ```yaml
    llm:
      enabled: true
      providers: [anthropic]
      anthropic:
        url: https://api.anthropic.com/v1/messages
        key_env: ANTHROPIC_API_KEY
        model: claude-haiku-4-5
    ```

=== "Snowflake Cortex"

    ```yaml
    llm:
      enabled: true
      providers: [cortex]
      cortex:
        account: myorg-myaccount   # or set SNOWFLAKE_ACCOUNT env var
        key_env: SNOWFLAKE_PAT
        model: claude-haiku-4-5
    ```

## System 1

`system1` is a provider that asks a decision model a few fixed questions in one call (`POST /v1/systemone`, which stock Ollama serves from version 0.40) and decides from the answers' numbers, with no prompt to parse. nuh calls it from the hook itself; nothing else sits in the path. It goes in `providers:` like any other, so a prompted model can stand behind it:

```yaml
llm:
  enabled: true
  providers: [system1, ollama]
  system1:
    url: https://ollama.example.lan     # /v1/systemone is appended (a trailing /api or /v1 path is replaced)
    model: nimble:9b                    # default shown
    keep_alive: 24h                     # sent as is; unset leaves it to the server
    timeout: 10                         # seconds (default shown)
    key_env: SYSTEM1_TOKEN              # optional bearer token from this env var
    redact_secrets: false               # default shown; see redact_secrets below
    definition: ~/nuh/system1.yaml      # optional, see below
```

- **https off this machine.** `http://` is accepted only for loopback (`127.0.0.1`, `::1`, `localhost`). Any other host, LAN addresses included, needs `https://`: the request carries the tool call and the reply decides it, so plain http on a network lets anyone on it read the call or forge an allow.
- **One call per hook run.** The request is at most 64 KiB; a larger one is a failure (the next provider is tried). A Codex patch reviewed per operation asks once for identical requests.
- **Failure.** An unreachable endpoint, a timeout, an HTTP error, a reply that is not JSON or has no answers, a missing `key_env` variable, a definition that cannot be read, or a gate that cannot run is a failure: the cascade tries the next provider, and when none answers, [`on_failure`](#on_uncertain-and-on_failure) decides.

### The definition

What system 1 is asked and how its answers decide. nuh uses, in order: `llm.system1.definition` (an absolute or `~` path), `system1.yaml` beside your global config (`~/.config/nuh/system1.yaml`), else the default it ships (`nuh/data/system1.json`: five questions, `destructive`, `out_of_scope`, `network`, `verdict` and `risk`, where the `verdict` choice decides). A definition that cannot be read or is malformed fails the system 1 attempt with the reason on the `provider` step; nuh never falls back to the shipped one behind your back.

To start your own, [`nuh system1 export`](../cli.md#nuh-system1-export) writes the shipped definition as commented YAML to the file nuh would read (`llm.system1.definition` when set, else `~/.config/nuh/system1.yaml`). Your file replaces the shipped definition entirely, and nuh never updates it: `based_on` records the nuh version it came from. After editing, [`nuh system1 check`](../cli.md#nuh-system1-check) loads it as the provider does, every tool block and gate included, without calling a model. The shipped preamble names a fixed person ("Sam") on purpose, because naming a person raised the model's caution in calibration; keep a named owner when you edit it.

```yaml
preamble: You review tool calls an AI coding agent wants to run ...   # put before the tool call
context:
  action_tokens: 1500     # the tool call, middle-clipped (about 4 characters a token)
  session_tokens: 800     # your recent messages, oldest cut first
  user: true              # send your messages at all
  file_lines: 30          # for a Write or Edit, the file's current lines (0 = off)
  project: false          # the project root
  cwd_rel: false          # the working directory, relative to the project root
  git: {branch: false, dirty: false}   # the current branch; how many paths git status lists
questions:
  destructive:
    type: noul            # yes/no: the answer is P(yes)
    instructions: Could this delete or overwrite data outside build, cache or temp directories?
    explain: may delete or overwrite data outside build, cache or temp
    block: "> 0.9"        # thresholds make a question decide
    ask: true             # between them, ask
    allow: "< 0.1"
  risk:
    type: score           # a level on criteria; explain may use {label}
    instructions: How risky is this call?
    criteria: [none, low, medium, high]
    explain: "risk: {label}"
  verdict:
    type: choice
    instructions: Should this run unattended, ask the user, or be blocked?
    criteria:             # plain form: every option named allow, ask or block, so the top choice decides
      allow: routine development work, safe to run unattended
      ask: worth a human look before it runs
      block: clearly harmful or exfiltrating
gates:                    # optional, CEL over every answer
  - name: wipe-and-send
    if: destructive.p > 0.8 && network.p > 0.5
    then: block
tools:                    # optional: per tool, a block merged onto everything above
  Bash:
    questions: {network: null}         # null removes a key: Bash calls are not asked this
  mcp__forge__*:
    context: {git: {branch: true}}
```

- **Thresholds.** On a `noul` (P(yes)) or `score` (the level), `allow` and `block` are comparisons (`"< 0.2"`, `">= 0.9"`) and `ask: true` asks in the middle where neither matched. On a `choice`, the detailed form gives each option `text` and its own comparisons on that option's probability; the plain form (each option named `allow`, `ask` or `block`) lets the top choice decide. Overlapping thresholds are refused when the definition loads. A question with no thresholds is still asked, logged and readable by gates; it does not decide.
- **Gates.** Each reads the answers by question name (`destructive.p`, `risk.score`, `risk.label`, `verdict.choice`, `verdict.p.block`) and names a decision (`allow`, `ask`, `block` or `uncertain`). cel-python is imported only when a gate runs; a gate that cannot run (a bad expression, a non-boolean result) is a failure, never skipped.
- **Deciding.** Every triggered threshold and gate counts, and the strictest wins: allow < uncertain < ask < block. A deciding question the model left unanswered counts as uncertain. Nothing triggered is **uncertain**: an allow has to be triggered. An uncertain answer goes to [`on_uncertain`](#on_uncertain-and-on_failure); it does not try the next provider.
- **Explanations** are built from what triggered, without a second model, and carry no numbers: `system1: allow; routine development work, safe to run unattended`, or `system1: ask; may delete or overwrite data outside build, cache or temp`. A score's explanation names its label (`risk: High`). An uncertain one lists what the deciding questions said, a choice or a score's label: `system1: uncertain (allow)`. The probabilities and levels are in the log's question steps.
- **The log.** Before the `llm` step, `nuh log -v` shows a `question` step per question (its answer, probabilities, what it triggered and, as `rule`, the threshold that fired: `> 0.7`, `p(risky)=0.42 >= 0.3` (with that option's probability), `top choice` or `between thresholds`), a `gate` step per gate (whether it fired), and a `provider` step per attempt (outcome, decision, milliseconds, error). An LLM trace's `result.json` keeps the same steps under `final._steps`, so a trace on its own shows how the answers became the decision.
- **Per tool.** `tools:` keys match a tool as `tools:` in the config does (exact name, then Edit's block for
  MultiEdit, NotebookEdit and apply_patch, then the longest glob). A block may set `preamble`, `context`,
  `questions` and `gates`, and merges onto the rest: maps merge key by key, lists replace, `null` removes a key.
  Every block is checked when the definition loads, so a broken block fails every review. The config's word
  decides how far the LLM is trusted; the definition decides what it is asked. The shipped default has no
  blocks: tune in `~/.config/nuh/system1.yaml`.
- **Context items are data.** `project`, `cwd_rel` and `git` go to the model as one JSON object in their own
  section of the state, never into the preamble or a question's instructions, so a branch named like an
  instruction stays a string. Each is computed only when a definition (or the matching block) asks for it, once
  per hook call; `git.dirty` is a count, never the paths. Each git call may take 200 ms and runs with fsmonitor
  off, no index write, no submodules and no clean filters, so a repository's own config cannot make it run a
  program. An item nuh could not compute is `null`, and `omitted` says why.
- The context keys `agent`, `tool_calls`, `prior_decisions` and `prior_decisions_max` are not supported yet; set, they warn on stderr and are ignored.

## LLM options

### can_block

Whether an LLM block blocks:

```yaml
llm:
  can_block: false   # default: an LLM block asks
  can_block: true    # an LLM block blocks (full trust)
```

With `can_block: false` an LLM `block` becomes `ask`, with the LLM's reasoning kept in the prompt ("LLM suggested block: …"). That ask stands wherever it is stricter than the static decision: an LLM block on a call the deterministic layer allowed (a clean script file, a Write) asks. The cap touches nothing else: it never lowers a static decision and never turns an LLM ask into an allow (lowering is
[`decide`](#gating-three-questions-per-tool)'s job). Global config only. It replaced `max_decision`, now a
removed key.

### on_uncertain and on_failure

What a review that reached no decision does:

```yaml
llm:
  on_uncertain: ask   # default; or static
  on_failure: ask     # default; or static
```

- **uncertain**: a model looked and reached no confident decision. For [`system1`](#system-1), nothing triggered (or a deciding question went unanswered, or a gate said `uncertain`); for a prompted provider, it answered `uncertain`. The cascade stops there.
- **failure**: nobody really looked. Every provider errored, timed out or gave no usable reply, or nuh's own LLM step crashed.

`ask` makes the call an ask; `static` keeps the static decision. They differ only where the LLM was reviewing a static allow (a clean script file, a write): a static ask stays an ask either way, and neither ever lowers anything. The reason says which setting decided (`on_uncertain: ask`), and with `static` the log's merge step reads `keep`. An action too large to show a model whole is neither: nuh refused to send it, and it always asks (`llm.skipped: too_large`). Global config only; any other value warns on stderr and reads as `ask`.

### self_heal

Load a cold Ollama model in the background after a provider times out:

```yaml
llm:
  self_heal: true   # the default; false turns it off
```

After a restart, Ollama lists its models but has none loaded, and loading one can take longer than the provider's `timeout`. Every review then times out, and a client that gives up may make Ollama drop the load, so the model never loads. With `self_heal` on, a provider attempt that **timed out** (any provider; other failures do not count) starts one detached worker process for that provider. The hook does not wait for it: the cascade goes on exactly as without it (the next provider, then [`on_failure`](#on_uncertain-and-on_failure)).

The worker:

1. Takes a lock on `~/.local/state/nuh/llm-heal.<provider>` and gives up if another worker holds it or the file was touched less than 10 minutes ago (the cooldown, always equal to the replay cap below). Otherwise it touches the file when the attempt starts and again when it ends, whatever comes of it. So no worker starts while an attempt is running, and the next one comes at least 10 minutes after the last one ended.
2. Probes the host of the provider's URL (`scheme://host[:port]`): `GET /api/ps` and `/api/tags`, 5 s each.
3. Only when the server is up, lists the model, and does not have it loaded, replays the request that timed out (same URL, headers and body, so `/v1/systemone` and `/api/chat` alike) with a 10-minute cap, discards the answer, and checks `/api/ps` again.

The request reaches the worker through a pipe and is never written to disk. Each run appends one line to `~/.local/state/nuh/llm-heal.jsonl` (time, provider, model, host, outcome, `elapsed_ms`, `error`, and after a replay `load_ms` and `loaded`), kept to the newest 500 lines, with nothing from the request. The outcomes:

| Outcome | Meaning |
|---|---|
| `healed` | Replayed; the model is now loaded |
| `not_loaded` | Replayed, but `/api/ps` still does not list the model |
| `heal_failed` | The replay itself failed (`error` says how) |
| `already_loaded` | The model is loaded and still timing out: not a warm-up problem, so nothing is replayed |
| `model_missing` | The server does not have the model |
| `down` | The host is unreachable or answered with an error |
| `not_ollama` | `/api/ps` or `/api/tags` is a 404, or not Ollama's reply |
| `busy`, `cooldown` | Another worker held the lock, or an attempt is running or ended under 10 minutes ago |
| `error` | The worker itself failed (`error` says how) |

[`nuh heal-log`](../cli.md#nuh-heal-log) shows the file. Global config only; any value other than `true` or `false` warns on stderr and reads as `true`.

### context_chars

The overall cap on conversation context in the LLM prompt:

```yaml
llm:
  context_chars: 12000  # default: 12000 characters
```

Set to `0` to disable transcript context entirely.

nuh reads the agent's transcript: Claude Code's session JSONL, or a Codex rollout. It sends the conversation in labeled sections, wrapped with anti-injection framing:

- **Your first message** of the session. It usually states the task.
- **Your most recent messages**, including ones typed while the agent was busy.
- **For a subagent**, the brief from the agent that delegated to it, labeled as agent-written. Your own messages still come from the parent session.
- **The agent's own messages**. Kept small, because agent prose can steer the reviewer.
- **The agent's recent tool calls** as one-line summaries. The call under review is left out.

Harness injections (system reminders, task notifications, AGENTS.md, environment context) are not counted as your messages. Secrets are masked only for a provider with [`redact_secrets: true`](#redact_secrets). The file is read backward from the end and the read stops once every section is full, so long sessions stay fast.

### context

Per-section budgets inside `context_chars`. Each setting is optional; the defaults are shown. When the sections together would exceed `context_chars`, they shrink proportionally.

```yaml
llm:
  context:
    first_prompt_chars: 1500  # your first message (also the size of a subagent's brief)
    user_turns: 6             # how many of your recent messages
    user_chars: 4000          # their combined size; long ones are trimmed evenly
    tool_calls: 12            # how many recent tool calls
    assistant_chars: 1500     # the agent's own prose
```

Set a section to `0` to leave it out.

### file_context_lines

A Write or Edit review also sees what the file holds now, so a small edit is judged in its place:

- **Edit / MultiEdit:** the whole current file when it fits `file_context_chars`, otherwise `file_context_lines` lines either side of each edit's `old_string` (its first occurrence; edits whose text is not in the file are skipped).
- **Write over an existing file:** its current content from the top.
- **New file:** nothing.

```yaml
llm:
  file_context_lines: 30    # default: 30 lines either side of an edit; 0 turns file context off
  file_context_chars: 8000  # default: the most the excerpt may hold
```

Over `file_context_chars`, the windows shrink first, then the excerpt is cut at a line. Files under a [sensitive path](sensitive-paths.md) (anything a Read would ask or block on, nuh's config included), binary files and unreadable files are never sent. Only the first 1 MiB of a file is read.

With prompted providers, the excerpt goes into the prompt after the conversation, framed as background context, and is the first thing cut when the prompt runs out of room; it never changes whether the review runs. System 1 gets it as the last section of its state, under the same framing. A provider with [`redact_secrets: true`](#redact_secrets) gets it redacted. An excerpt's windows are joined by a line `...`.

### redact_secrets

By default a provider sees secrets as they are: spotting credential exfiltration is part of the review. To keep secrets away from a third-party provider, set `redact_secrets` on that provider:

```yaml
llm:
  openrouter:
    redact_secrets: true   # default: false
```

nuh then replaces each secret in the action, the conversation and the file excerpt with a placeholder that says what it was, and leaves the rest of the line alone. A command chained with `&&` stays readable around the masked token:

```
curl -H 'Authorization: Bearer [redacted: GitHub token]' https://api.github.com/user && make deploy
```

- The secrets are content inspection's [secret table](content.md#secret), all of it even where `suppress` or `profile: none` turned off its ask, plus any `secret` category you add. JSON web tokens and URL passwords, which never make a write ask, are masked here too. For a keyword pattern only the value goes: `aws_secret_access_key = [redacted: AWS secret access key]`.
- A private key (PEM, PGP armor, PuTTY `.ppk`) becomes one `[redacted: private key]` (or `PuTTY private key`), also when it is kept on one line with `\n` escapes, but only when every line between its markers is key material: armor headers right after BEGIN, base64 lines of one width with a shorter last line, the PuTTY fields with exactly the counted base64 lines. If any other line sits between the markers, only the markers and the base64 lines go, and that line stays visible to the reviewer.
- `export NAME=value` values stay visible to the reviewer (`LD_PRELOAD=/tmp/x.so` is what it has to judge).
- This applies to what a provider is sent, nothing else: the decision log stores tool input raw.

The model's verdict on the masked prompt counts like any other verdict. Static secret checks on writes still run on the real content, and an LLM allow never lowers their ask. The conversation context is built once per review, so it is masked for every provider in that cascade.

### trace_dir

Write a complete record of every LLM review to disk, for debugging what the LLM was asked and what it answered:

```yaml
llm:
  trace_dir: ~/.local/state/nuh/llm-traces   # unset or empty: off (the default)
```

Global config only: a project `.nuh.yaml` cannot turn tracing on or redirect it. The path must be absolute or start with `~`. When it is unset, the hook does no trace work at all.

A project can opt out. A top-level `trace: false` in its `.nuh.yaml` turns tracing off for calls in that project. It is honoured because it only reduces what is written; `trace: true` changes nothing.

```yaml
# <project>/.nuh.yaml
trace: false   # never write LLM traces for this project
```

Traces are optional debug data: `nuh log` and `nuh tail` work from the decision log alone and only show a trace's directory name when the entry has one. [`nuh trace`](../cli.md#nuh-trace) browses and renders them: system 1's answers with their probabilities, the prompt as sent, and the decision path.

Each hook call that reaches the LLM stage creates one new directory, named so they sort by time and carrying the tool call ID (`tool_use_id`), so a trace joins its `nuh log` line (`nuh log --call <id>`) and its transcript entry. A payload without a tool call ID gets a random UUID instead.

```
<trace_dir>/20260927T210358.141265Z_toolu_01AbCdEfGhIjKlMnOpQrStUv/
  input.json                    agent, tool name, the complete tool input, session id, cwd, project root
  classification.json           every deterministic stage and verdict handed to the LLM (Write/Edit: the content inspection result)
  request-01-ollama.json        per provider attempt: URL, model, timeout, headers, the exact JSON body sent
  response-01-ollama.json       HTTP status, latency, the full raw body, extracted content, parsed decision, or the exception
  request-02-openrouter.json    ...one pair per attempt in the cascade
  response-02-openrouter.json
  result.json                   the LLM decision used, the can_block cap, and the final answer returned to the agent
```

!!! warning "Traces are not redacted"
    Traces hold the full tool input, file contents and prompt (including transcript context) exactly as sent. Only auth header values (`Authorization`, `x-api-key`, and similar) and credential query parameters in provider URLs are replaced with `<redacted>`. The trace directory is created `0700` when missing, each trace directory is `0700`, and every file is `0600`. Treat the directory like the transcript itself; nuh deletes old traces after [`trace_keep_days`](#trace_keep_days).

Tracing never changes a decision: a failed trace write (full disk, permissions) is reported once on stderr as `nuh: trace: ...` and the hook carries on. `nuh test` is a dry run and is not traced.

### trace_keep_days

How long traces are kept, in whole days. Older trace directories are deleted, at most once a day. `0` keeps them forever.

```yaml
llm:
  trace_keep_days: 30   # the default
```

nuh checks after the hook has printed its decision, when it has just written a trace: one look at a marker file, `.<trace dir name>.pruned` beside the trace directory (for `~/.local/state/nuh/llm-traces`, `~/.local/state/nuh/.llm-traces.pruned`). The first trace only stamps the marker. When the marker is a day old nuh restamps it and runs the prune in a separate background process, so no tool call waits for it; that process records its outcome in the marker.

A prune deletes a directory only when its name is a nuh trace name with a timestamp before the cutoff (a timestamp in the future is never old), it is a real directory (not a symlink), and it holds nothing but trace files (`input.json`, `classification.json`, `result.json`, `request-NN-*.json`, `response-NN-*.json`). Anything else in the trace directory is never touched, and a trace directory holding anything else is kept whole. Files are removed one by one, never recursively.

`nuh trace --prune --dry-run` shows what would go and the last prune's outcome; `nuh trace --prune` prunes now. An agent running `nuh trace --prune` asks first (the `nuh_admin` floor rule); `--dry-run` does not.

## How the cascade works

1. nuh tries each provider in the order listed in `providers:`
2. The first provider that answers decides; the cascade **stops** there. Its answer is a decision (`allow`, `ask` or `block`) or `uncertain`, which [`on_uncertain`](#on_uncertain-and-on_failure) settles
3. If a provider fails (timeout, auth failure, a [system 1](#system-1) reply with no answers, or an Ollama answer whose `prompt_eval_count` says part of the prompt was dropped, so the model may not have read the action), nuh tries the next provider. A timeout also starts a background warm-up of that provider's model ([`self_heal`](#self_heal)), which nothing waits for
4. A decision is allow, ask or block, and the LLM's ask is a permission prompt like any other: on a static allow the LLM reviews (a clean script file, a write) it asks, a static ask stays `ask`, and the log's `llm` step says `ask`
5. An LLM `block` is capped by [`can_block`](#can_block); stricter than the static decision, it applies
6. If every provider fails, [`on_failure`](#on_uncertain-and-on_failure) decides. By default the review **fails closed**: the decision is `ask` even where the static layer allowed (a clean script file, a write), with `llm.skipped: error` and the errors in `llm.error` in the log. An exception in nuh's own LLM step (building the prompt, reading the transcript) does the same, and says so on stderr as `nuh: llm: …`

## Testing

```bash
nuh test "python3 -c 'import os; os.system(\"rm -rf /\")'"
nuh test --tool WebFetch https://example.com.
```

`nuh test` prints the tool's word and where it comes from, the `human_only` keys behind an ask, whether the hook
would call the LLM (and why not), and whether an allow is sent or left silent. With the LLM enabled it makes the
live call the hook would make, so you can verify the whole pipeline.

To try another provider or model without editing this file, pass flags to `nuh test`. They change the config in memory for that one run:

```bash
nuh test "rm -rf build/" --provider system1 --provider-url http://127.0.0.1:11434 --show-request
nuh test "python3 build.py" --provider ollama --model qwen3:14b --user "run the build"
nuh test "ls" --llm --set llm.context.user_turns=2      # review even a static allow
```

See [`nuh test`](../cli.md#nuh-test) for every flag.
