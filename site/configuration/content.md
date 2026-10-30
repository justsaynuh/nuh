# Content Inspection

nuh scans the content of Write, Edit, and Grep operations for dangerous patterns. This catches threats that path-based checks alone can't detect.

## What gets scanned

| Tool | Field scanned |
|------|---------------|
| **Write** | `content` (the full file content being written) |
| **Edit** | `new_string` (the replacement text) |
| **Grep** | `pattern` (the search query -- checked for credential searches) |

MultiEdit, NotebookEdit and Codex `apply_patch` are scanned the same way.

## Only where the text can run

The `destructive`, `exfiltration`, `credential_access` and `obfuscation` patterns describe text that does harm only when something runs it. On a write they apply only when the target file can run:

- a script or source extension (`.sh`, `.py`, `.js`, `.ts`, `.rb`, `.ps1`, `.ipynb`, `.pth`, ...);
- a file whose content tools execute: `Makefile` / `*.mk`, `justfile`, `Taskfile`, `package.json`, `pyproject.toml`, `setup.cfg`, `Dockerfile` / `Containerfile`, any `.yml` / `.yaml` / `.toml` / `.ini` / `.cfg` / `.conf` (CI, compose, Ansible, tox ...), systemd units, `crontab` and `cron.d/`, `.git/hooks/`, `.husky/`, `.githooks/`, git config, shell rc and profile files, `.envrc`, `.env`, `.vscode/`, `.devcontainer/`, `bin/`;
- new content that starts with a shebang (for an edit or patch hunk, a shebang on any line);
- an existing file with its executable bit set or a shebang on its first line, or one nuh cannot examine.

Anything else (`.md`, `.txt`, `.rst`, `.json` data, `.html`, ...) is data: a README that mentions `rm -rf` writes without an ask. Running such a file later is still inspected at run time: `bash notes.md`, `. notes.md`, `./notes.md`, `python notes.md` and a Makefile `include notes.md` all scan its content.

These apply to every file whatever its kind: the `secret` patterns, categories added with `content_patterns.add` under a new name, the size and time limits, and the hidden-characters floor. When a runnable pattern asks, the reason says why the target runs (`(target runs: .sh file)`).

## Built-in content patterns

Patterns are organized by category. Each match triggers the category's policy (default: `ask`).

### destructive

| Pattern | Matches |
|---------|---------|
| `rm -rf` | `rm` with recursive + force flags |
| `shutil.rmtree` | Python recursive delete |
| `os.remove` | Python file delete |
| `os.unlink` | Python file unlink |

### exfiltration

| Pattern | Matches |
|---------|---------|
| `curl -X POST` | curl with POST method |
| `curl --data` | curl with data flag |
| `curl -d` | curl with short data flag |
| `requests.post` | Python requests POST |
| `urllib POST` | Python urllib with data= |

### credential_access

| Pattern | Matches |
|---------|---------|
| `~/.ssh/` access | References to SSH directory |
| `~/.aws/` access | References to AWS directory |
| `~/.gnupg/` access | References to GPG directory |

### obfuscation

| Pattern | Matches |
|---------|---------|
| `base64 -d \| bash` | Decode-pipe-execute |
| `eval(base64.b64decode` | Python base64 eval |
| `exec(compile` | Python dynamic compilation |

### secret

The secret table lives in `src/nuh/secret_patterns.py` (48 patterns). The descriptions, which `suppress` names:

| Matches | Description |
|---------|-------------|
| `-----BEGIN … PRIVATE KEY-----` of any kind (RSA, PKCS#8, OpenSSH, EC, DSA, encrypted) and `PGP PRIVATE KEY BLOCK` | `private key` |
| `PuTTY-User-Key-File-2:` / `-3:` | `PuTTY private key` |
| `AGE-SECRET-KEY-1…` | `age secret key` |
| `AKIA…`, `ASIA…`, `ABIA…`, `ACCA…`, `A3T…` | `AWS access key ID` |
| `aws_secret_access_key`, `AWS_SECRET_KEY`, `--aws-secret-access-key`, JSON `SecretAccessKey` + 40-char value | `AWS secret access key` |
| `aws_session_token`, `--aws-session-token`, JSON `SessionToken` + value | `AWS session token` |
| `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` / `github_pat_` | `GitHub token` / `GitHub fine-grained token` |
| `glpat-` and other GitLab prefixes | `GitLab token` |
| `gto_`, `gta_` / `GITEA…TOKEN=` + 40 chars | `Gitea OAuth secret` / `Gitea token assignment` |
| `sk-ant-…` / `sk-proj-`, `sk-svcacct-`, `sk-admin-`, `sk-None-` / `sk-or-v1-` / other `sk-` | `Anthropic key` / `OpenAI key` / `OpenRouter key` / `secret key token (sk-)` |
| `gsk_`, `xai-`, `hf_` and `api_org_`, `r8_` | `Groq key`, `xAI key`, `Hugging Face token`, `Replicate token` |
| `npm_`, `pypi-AgE…`, `dckr_pat_` / `dckr_oat_`, `ptr_` | `npm token`, `PyPI token`, `Docker Hub token`, `Portainer API key` |
| `"auth": "…"` (Docker config), `/api/webhooks/<uuid>` | `Docker registry auth`, `Portainer webhook URL` |
| `AIza…`, `GOCSPX-…`, `ya29.…` | `Google API key`, `Google OAuth client secret`, `Google OAuth access token` |
| `…8Q~…`, `AccountKey=…` | `Azure Entra client secret`, `Azure storage account key` |
| `dop_v1_…`, `hvs.…`, `tskey-…` | `DigitalOcean token`, `Vault token`, `Tailscale key` |
| `sk_live_` / `rk_live_`, `whsec_`, `xoxb-` / `xapp-`, `hooks.slack.com/…`, `SG.…` | `Stripe secret key`, `Stripe webhook secret`, `Slack token`, `Slack webhook URL`, `SendGrid key` |
| `eyJ….eyJ….…` | `JSON web token` (redacted only, never asks) |
| `scheme://user:password@host` | `credentials in URL` (redacted only, never asks) |
| `Authorization: Bearer|token|Basic …`, also as a JSON / dict entry | `authorization header` |
| `X-API-Key: …` or `api-key: …`, also as a JSON / dict entry | `API key header` |
| `api_key / apikey / api_secret = '...'` (lowercase; a value of 8+ key characters, no spaces or shell syntax) | `hardcoded API key` |

The same table drives [`redact_secrets`](llm.md#redact_secrets) for LLM providers. There each match is replaced in place, and for a keyword pattern only its value (`aws_secret_access_key = [redacted: AWS secret access key]`). No match ever runs into whitespace or shell syntax. A JSON web token or URL password is redacted for a provider but never makes a write ask: both are everywhere in ordinary development config.

## Credential search patterns (Grep)

These patterns flag Grep queries that look like credential searches:

`password`, `secret`, `token`, `api_key`, `private_key`, `AWS_SECRET`, `BEGIN.*PRIVATE`

## Config options

### Suppress built-in patterns

Suppress by description string (the "Matches" column above):

```yaml
content_patterns:
  suppress:
    - "rm -rf"              # too many false positives in your workflow
    - "requests.post"       # you POST frequently in this project
```

Unmatched suppress entries print a stderr warning. Suppressing a `secret` description stops only its ask: `redact_secrets` still masks it for providers.

### Add custom patterns

```yaml
content_patterns:
  add:
    - category: secret
      pattern: "PRIVATE_TOKEN_[A-Z0-9]{32}"
      description: "internal service token"
    - category: exfiltration
      pattern: "\\bwebhook\\.site\\b"
      description: "webhook.site exfil endpoint"
```

Each entry needs `category`, `pattern` (regex), and `description`. Invalid regexes are rejected with a stderr warning.

### Per-category policies

Override the default `ask` policy for specific categories:

```yaml
content_patterns:
  policies:
    secret: block              # block all secret pattern matches
    obfuscation: block         # block obfuscation patterns
```

Valid values: `ask`, `block`. Project config can only tighten.

### Suppress credential search patterns

```yaml
credential_patterns:
  suppress:
    - "\\btoken\\b"         # suppress the token pattern (by regex string)
  add:
    - "\\bINTERNAL_SECRET\\b"  # add a custom credential pattern
```

## profile: none

Setting `profile: none` clears all built-in content patterns and credential search patterns. Add back only what you need. `redact_secrets` keeps the whole secret table either way.
