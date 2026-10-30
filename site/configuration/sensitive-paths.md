# Sensitive Paths

nuh protects sensitive filesystem locations from accidental access. Both directory paths and filename patterns are checked.

## Built-in sensitive directories

| Path | Default policy |
|------|:--------------:|
| `~/.ssh` | block |
| `~/.gnupg` | block |
| `~/.git-credentials` | block |
| `~/.netrc` | block |
| `~/.aws` | ask |
| `~/.config/gcloud` | ask |

These are checked on every tool call that touches a file path (Bash, Read, Write, Edit, Glob, Grep).

## Built-in sensitive basenames

| Basename | Default policy |
|----------|:--------------:|
| `.env` | ask |
| `.env.local` | ask |
| `.env.production` | ask |
| `.npmrc` | ask |
| `.pypirc` | ask |

Basename matching triggers regardless of directory -- a file named `.env` anywhere will be flagged. So do the other env files (`*.env` such as `cloudflare.env`, `.envrc`, and `.env.*` except templates like `.env.example`), key and certificate files (`*.pem`, `*.key`, `id_rsa*`, ...), and deploy CLIs' login stores (`~/.wrangler`, `~/.config/.wrangler`, `~/.fly`, `~/.netlify`, `~/.railway`, ...).

## Hook self-protection

`~/.claude/hooks/` is **always** protected. Write and Edit to this directory are blocked (not just asked). This is immutable -- no config can change it.

## Config options

### sensitive_paths

Override policies for existing paths or add new ones:

```yaml
# ~/.config/nuh/config.yaml
sensitive_paths:
  ~/.kube: ask              # add new sensitive directory
  ~/Documents/taxes: block  # add new blocked directory
  ~/.aws: ask               # already default, but explicit
```

Valid policies: `ask`, `block`. Project config can only tighten (e.g., escalate `ask` to `block`). You can move a built-in entry between `ask` and `block` (`~/.gnupg: ask`), but not remove it: `allow` on a built-in entry (or on `~/.ssh`) prints a warning and is ignored.

Every sensitive-path ask is a static floor decision (see "Static floor" in the README): built-in credential files carry rule `credential_paths`, everything else (your entries, shell init files, agent settings, ...) `sensitive_paths`. Neither the LLM layer nor config lowers them. To let the LLM decide their asks: `human_only: {sensitive_paths: false}` (or `credential_paths: false`) in the global config.

### sensitive_paths_default

Set the default policy for all sensitive paths:

```yaml
sensitive_paths_default: block   # default is "ask"
```

### allow_paths

Exempt specific paths from your own `sensitive_paths` and `sensitive_basenames` entries for a given project:

```yaml
# ~/.config/nuh/config.yaml (global only)
sensitive_paths:
  ~/Documents/taxes: block
allow_paths:
  ~/Documents/taxes/2025:
    - /Users/me/accounting-project
```

This allows `~/Documents/taxes/2025` access only from `/Users/me/accounting-project`. The exemption is scoped to the project root. It never exempts a built-in entry (`~/.aws`, `.env`, `~/.bashrc`, ...): those are floors, and the path still asks or blocks as before. `nuh allow-path` refuses such a path.

**CLI:** `nuh allow-path ~/Documents/taxes/2025`

### trusted_paths

Directories outside the project root where Write/Edit are allowed without asking:

```yaml
# ~/.config/nuh/config.yaml (global only)
trusted_paths:
  - ~/builds
  - /tmp/staging
```

Without this, Write/Edit to paths outside the git project root triggers an `ask` decision (project boundary check).

**CLI:** `nuh trust ~/builds`

### project_root

Where the project is when the working directory is not in a git repository:

```yaml
# ~/.config/nuh/config.yaml (global only)
project_root: git_or_launch_dir   # default: git
```

With `git_or_launch_dir` and no git root, the project root is the directory the agent was launched in: `$CLAUDE_PROJECT_DIR` for Claude Code, the hook payload's `cwd` for Codex. It must be an absolute, existing directory; `/`, your home directory and any directory above it are refused (each would make all of home "the project"). A git root still wins when there is one. Everything that uses the project root follows it: the boundary check, `allow_paths`, the project's `.nuh.yaml`.

### project_lifts_sensitive

Let the project skip your own `sensitive_paths` entries that contain it:

```yaml
# ~/.config/nuh/config.yaml (global only)
project_root: git_or_launch_dir
project_lifts_sensitive: true     # default: false
sensitive_paths:
  ~/Documents: ask
```

Launched in `~/Documents/Club`, reads, edits and Bash paths inside `Club` go through, while `~/Documents/Other` still asks. Only entries from the global config that are the project root or above it are lifted, `block` ones included. These still apply inside the project:

- built-in sensitive and credential paths (a `.ssh/id_rsa` or `.env` in the project, or a project at `~/.aws`), and your overrides of them (`~/.aws: block`)
- entries under `~/.ssh`, entries inside the project (`~/Documents/Club/secret: block`), and entries from a project `.nuh.yaml`
- hook and nuh config protection, protected writes (agent settings, `.nuh.yaml`, git and ssh config), nuh's own package directory, and content and secret checks

A path is lifted only when it is inside the project both as written and with symlinks resolved, so a symlink out of the project still asks. A Bash word the shell could still expand (`*.txt`, `$X/...`, `~-/...`) is never lifted.

!!! warning "Global config only"
    `allow_paths`, `trusted_paths`, `project_root` and `project_lifts_sensitive` are only accepted in global config. Project `.nuh.yaml` cannot modify them (the last two warn on stderr).

## profile: none

`profile: none` keeps every built-in sensitive directory and basename: each is a floor, which no config lowers. The hook self-protection (`~/.claude/hooks/`) remains active too.
