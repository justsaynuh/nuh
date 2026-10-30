# Taxonomy Profiles

Profiles control how much built-in classification nuh starts with. Set in global config:

```yaml
# ~/.config/nuh/config.yaml
profile: full      # full | minimal | none
```

## full (default)

Comprehensive coverage across all tool categories.

- **17 classification files** covering shell builtins, coreutils, git, package managers, containers, databases, network tools, and more
- **9 flag classifiers** for commands that need flag-dependent classification (find, sed, awk, tar, git, curl, wget, httpie, global_install)
- **All safety lists** populated with defaults (known registries, exec sinks, sensitive basenames, decode commands)
- **All sensitive paths** active

Best for: most users. Start here and tune as needed.

## minimal

Curated essentials only — the commands most likely to be dangerous.

- **10 classification files** with fewer prefix entries
- **Same 9 flag classifiers** as full
- **All safety lists** populated with defaults
- **All sensitive paths** active

Covers the high-risk commands (rm, git push --force, curl, kill, docker rm, etc.) while leaving common development tools unclassified (defaulting to `unknown` → `ask`).

Best for: users who want a lighter touch and are comfortable with more `ask` prompts.

## none

Blank slate. Clears everything:

- **Empty classify tables** — no commands are recognized
- **Flag classifiers disabled** — no flag-dependent classification
- **Safety lists cleared** — no known registries, exec sinks or decode commands
- **Content patterns cleared** — no built-in content inspection
- **Project boundary check disabled**

The static floor stays whole: built-in sensitive directories and basenames, `nuh_admin` and `env_read` commands, catastrophic deletes, protected writes and hidden characters in scripts all still ask or block. Floors key on what nuh's own tables make of a command, so an empty table does not lower one. That holds for the commands a command runs too: a `find -exec` payload (`find / -exec sh -c 'rm -rf "$1"' _ {} \;`), a package runner's command (`uv run rm -rf ~`) and a `make` or `npm run` recipe are still read for their floors, and only a floor there counts; otherwise the command stays `unknown`.

Everything falls to `unknown` → `ask` unless you explicitly classify it.

Best for: users who want full control and will build their own taxonomy.

```yaml
profile: none

# Build up from scratch
classify:
  filesystem_delete:
    - "rm -rf"
    - "rm -r"
  git_history_rewrite:
    - "git push --force"

actions:
  filesystem_delete: ask
  git_history_rewrite: block

known_registries:
  - pypi.org
  - github.com
```

## How profiles interact with user rules

Your `classify:` entries in global config are **always Phase 1** (checked first), regardless of profile. They override both built-in tables and flag classifiers.

The profile controls what's available in **Phase 2** (flag classifiers) and **Phase 3** (built-in tables):

| Phase | Source | `full` | `minimal` | `none` |
|:-----:|--------|:------:|:---------:|:------:|
| 1 | Global config `classify:` | active | active | active |
| 2 | Flag classifiers | active | active | **skipped** |
| 3 | Built-in tables | full set | minimal set | **empty** |
| 3 | Project config `classify:` | active | active | active |

This means even with `profile: none`, your global and project classify entries still work.
