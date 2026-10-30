# Configuration Overview

nuh works out of the box with zero config. When you want to tune it, configuration lives in two places.

## File locations

| Scope | Path | Purpose |
|-------|------|---------|
| **Global** | `~/.config/nuh/config.yaml` | Your personal preferences, trusted paths, LLM setup |
| **Project** | `.nuh.yaml` (in git root) | Per-project tightening, custom classifications |

```bash
nuh config path    # show both paths
nuh config show    # display effective merged config
```

## Global vs project scope

**Global config** can do everything -- override policies, add trusted paths, configure LLM, modify safety lists.

**Project config** can only **tighten** security. It can:

- Add classify entries (commands → action types)
- Escalate action policies (e.g., `git_write: ask`)
- Tighten content pattern policies (ask → block)

It **cannot**:

- Relax any policy (lowering strictness is rejected)
- Modify safety lists (`known_registries`, `exec_sinks`, etc.)
- Set `trusted_paths`, `allow_paths`, `db_targets`, `project_root` or `project_lifts_sensitive`
- Configure the LLM layer
- Change the taxonomy profile

This is the **supply-chain safety** model: a malicious repo's `.nuh.yaml` can't weaken your protections.

## Merge rules

When both configs exist, nuh merges them with these rules:

| Field | Merge behavior |
|-------|---------------|
| `profile` | Global only |
| `actions` | Tighten-only (project can only escalate strictness) |
| `classify` | Kept separate (global = Phase 1, project = Phase 3 lookup) |
| `sensitive_paths` | Tighten-only |
| `sensitive_basenames` | Global only |
| `content_patterns` | Project can tighten policies only (add/suppress global-only) |
| `credential_patterns` | Global only |
| `known_registries` | Global only |
| `exec_sinks` | Global only |
| `decode_commands` | Global only |
| `trusted_paths` | Global only |
| `allow_paths` | Global only |
| `project_root` | Global only (warns if set in `.nuh.yaml`) |
| `project_lifts_sensitive` | Global only (warns if set in `.nuh.yaml`) |
| `completion_hint` | Global only (warns if set in `.nuh.yaml`) |
| `db_targets` | Global only |
| `llm` | Global only |
| `log` | Global only |
| `tools` | Global only (warns if set in `.nuh.yaml`) |
| `human_only` | Global only (warns if set in `.nuh.yaml`) |

## Quick reference — all config keys

| Key | Type | Scope | Docs |
|-----|------|-------|------|
| `profile` | `full` / `minimal` / `none` | global | [Profiles](profiles.md) |
| `classify` | dict of type → prefix list | both* | [Custom taxonomy](../guides/custom-taxonomy.md) |
| `actions` | dict of type → policy | both | [Action types](actions.md) |
| `sensitive_paths_default` | `ask` / `block` | both* | [Sensitive paths](sensitive-paths.md) |
| `sensitive_paths` | dict of path → policy | both | [Sensitive paths](sensitive-paths.md) |
| `allow_paths` | dict of path → project list | global | [Sensitive paths](sensitive-paths.md) |
| `trusted_paths` | list of paths | global | [Sensitive paths](sensitive-paths.md) |
| `project_root` | `git` / `git_or_launch_dir` | global | [Sensitive paths](sensitive-paths.md#project_root) |
| `project_lifts_sensitive` | `true` / `false` | global | [Sensitive paths](sensitive-paths.md#project_lifts_sensitive) |
| `known_registries` | list or dict (add/remove) | global | [Safety lists](safety-lists.md) |
| `exec_sinks` | list or dict (add/remove) | global | [Safety lists](safety-lists.md) |
| `sensitive_basenames` | dict of name → policy | global | [Safety lists](safety-lists.md) |
| `decode_commands` | list or dict (add/remove) | global | [Safety lists](safety-lists.md) |
| `content_patterns` | dict (add/suppress) | both | [Content inspection](content.md) |
| `credential_patterns` | dict (add/suppress) | global | [Content inspection](content.md) |
| `tools` | dict of tool name or glob → `defer` / `approve` / `check` / `decide` | global | [Gating](llm.md#gating-three-questions-per-tool) |
| `human_only` | dict of floor rule, action type or content key → `true` / `false` | global | [human_only](llm.md#human_only) |
| `llm` | dict (providers, `can_block`, `context_chars`, `trace_dir`, `trace_keep_days`) | global | [LLM layer](llm.md) |
| `trace` | `false` turns `llm.trace_dir` off for the project | project | [LLM layer](llm.md#trace_dir) |
| `db_targets` | list of database/schema dicts | global | [Database targets](database.md) |
| `log` | dict (verbosity, etc.) | global | [CLI reference](../cli.md#nuh-log) |
| `hook` | dict: `deadline_s` (seconds of static inspection, default 20), `on_error` (`ask` / `block` / `defer`, default `ask`: what nuh answers when it fails inside) | global (warns if set in `.nuh.yaml`) | [hook.on_error](#hookon_error) |
| `completion_hint` | `true` / `false` (default `true`): `false` hides the CLI's "zsh completion isn't installed" line | global (warns if set in `.nuh.yaml`) | [completion_hint](#completion_hint) |

A key nuh does not read, at the top level, under `llm:` or in a provider's section (`llm.ollama.port`), prints
one line on stderr (`nuh: config: unknown key llm.ollama.port; ignored`), is listed by `nuh config show` as
`unknown keys (ignored)`, and is ignored. `llm.tools` and `llm.human_only` say they are top-level keys.

*\* `classify` entries in global config are Phase 1 (checked first, can override built-in). Project entries are Phase 3 (checked after built-in, can only add new commands). `sensitive_paths_default` in project config can only tighten (ask → block).*

## hook.on_error

What nuh answers when it fails inside: a bug, a malformed hook payload, a config that will not load.

```yaml
# ~/.config/nuh/config.yaml (global only)
hook:
  on_error: ask     # ask | block | defer
```

| Value | Claude Code | Codex | Logged as |
|-------|-------------|-------|-----------|
| `ask` (default) | asks; the reason names the error | blocked for review, as any ask | `ask` |
| `block` | denied: "internal error — blocked for safety" | denied | `block` |
| `defer` | no decision: Claude Code's own permission mode decides | no decision | `allow`, reason "internal error, deferred (… no decision sent)" |

A crash means nuh could not classify the call, so by default it asks, as for any call it cannot settle. nuh never sends an allow for a call it failed on: `defer` leaves the call to the agent, like `defer` in `tools:`. Every row carries `internal_error` (the error and the `on_error` value used). An invalid value warns and asks; a `hook:` key in `.nuh.yaml` warns and is ignored; when the config itself fails to load, `ask` applies. The installed hook's `"onFailure": "block"` is a different case: a hook that dies or times out before it answers at all.

## completion_hint

`nuh install` writes nuh's zsh completion (see `nuh install` in the [CLI reference](../cli.md#nuh-install)). Until
it exists, a zsh user (`$SHELL` is zsh) whose stderr is a terminal gets one line on stderr, after any `--help`
output and before every command except `completion`, `install`, `update` and `uninstall`:

```
nuh: zsh completion isn't installed; run 'nuh install' (or 'nuh completion zsh > DIR_ON_FPATH/_nuh'; completion_hint: false in the global config hides this)
```

It never goes to stdout, so piped output and the generated docs stay clean, and it writes nothing. The check
only stats the places `nuh install` writes to (`$ZSH_CACHE_DIR/completions`, `$ZSH/cache/completions`,
`~/.oh-my-zsh/cache/completions`, `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions`), looking for a `_nuh`
with nuh's generated header; the config is read only when the line would show. To silence it:

```yaml
# ~/.config/nuh/config.yaml (global only)
completion_hint: false
```

## YAML format

Both config files use standard YAML. If nuh detects comments in a file before a CLI write operation (`nuh allow`, `nuh classify`, etc.), it warns you that comments will be removed and asks for confirmation.
