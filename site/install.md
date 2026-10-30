# Installation

## Requirements

- Python 3.10+

## Quick start

```bash
pip install nuh
nuh claude              # try it — hooks active for this session only
```

`nuh claude` writes the hook script to `~/.claude/hooks/nuh_guard.py` and passes hooks inline via Claude Code's `--settings` flag, scoped to that process.

## Permanent install

```bash
nuh install
```

Registers nuh as a [PreToolUse hook](https://docs.anthropic.com/en/docs/claude-code/hooks) in Claude Code's `settings.json`. Every `claude` session runs through nuh.

### Dependencies

`pip install nuh` brings `pyyaml` (your YAML config, a YAML system 1 definition) and `cel-python` (system 1 gates). nuh imports each only when it is needed, so a hook call that reads no config file and runs no gate starts on Python's standard library alone.

## How permissions work

When active (via `nuh claude` or `nuh install`), nuh takes over permissions for Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch, and all MCP tools. Safe operations go through automatically, dangerous ones are blocked, ambiguous ones ask.

nuh has no built-in classification for WebFetch and WebSearch, so each call asks until a [`classify`](guides/custom-taxonomy.md) entry gives the tool an action type (for example `classify: {network_diagnostic: [WebFetch, WebSearch]}`). An install made before WebFetch and WebSearch were guarded does not send them to nuh: run `nuh update` to add their matchers.

**Don't use `--dangerously-skip-permissions`** — just run `claude` in default mode. In `--dangerously-skip-permissions` mode, hooks [fire asynchronously](https://github.com/anthropics/claude-code/issues/20946) and commands execute before nuh can block them.

### Sent and silent allows

When nuh classifies a tool call as safe, it can send an explicit `"allow"`, so Claude Code skips its own permission prompt, or stay silent, so Claude Code's own permission mode decides. The tool's word in [`tools:`](configuration/llm.md#gating-three-questions-per-tool) chooses: `approve`, `check` and `decide` send the allow, `defer` leaves it silent. By default Bash, Write, Edit, Read, Glob and Grep send it and every other tool defers.

Want nuh's protection on writes and edits but Claude Code's normal prompt before each one?

```yaml
# ~/.config/nuh/config.yaml
tools:
  Write: defer
  Edit: defer       # MultiEdit, NotebookEdit and apply_patch follow Edit
```

nuh still classifies every call whatever the word: it still blocks or asks for dangerous Write and Edit calls. `defer` also turns the LLM layer off for that tool. `active_allow`, the old key for this, is removed: a config that sets it prints a warning and the key is ignored.

## Update

After upgrading nuh via pip:

```bash
pip install --upgrade nuh
nuh update
```

`nuh update` unlocks the hook script, overwrites it with the new version, and re-locks it (chmod 444).

## Uninstall

```bash
nuh uninstall
pip uninstall nuh
```

`nuh uninstall` removes hook entries from `settings.json` and deletes the hook script.

## Verify installation

```bash
nuh --version              # check installed version
nuh test "git status"      # dry-run classification
nuh config path            # show config file locations
```

## See it in action

Clone the repo and run the security demo inside Claude Code:

```bash
git clone https://github.com/justsaynuh/nuh.git
cd nuh
# inside Claude Code:
/nuh-demo
```

25 live cases across 8 threat categories — remote code execution, data exfiltration, obfuscated commands, and more. Takes ~5 minutes.

---

<p align="center">
  <code>--dangerously-skip-permissions?</code><br><br>
  <img src="../assets/logo_hammock.png" alt="nuh" width="280" class="invertible">
</p>

