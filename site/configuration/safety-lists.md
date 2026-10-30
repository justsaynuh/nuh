# Safety Lists

nuh uses four configurable safety lists that feed into classification and composition rules. All lists have built-in defaults that you can extend or trim.

## known_registries

Trusted hosts for network context resolution. Outbound requests to known registries are auto-allowed; unknown hosts trigger `ask`.

**Built-in defaults (20 hosts):**

| Registry | Hosts |
|----------|-------|
| npm | `npmjs.org`, `www.npmjs.org`, `registry.npmjs.org`, `registry.yarnpkg.com`, `registry.npmmirror.com` |
| PyPI | `pypi.org`, `files.pythonhosted.org` |
| GitHub | `github.com`, `api.github.com`, `raw.githubusercontent.com` |
| Crates | `crates.io` |
| RubyGems | `rubygems.org` |
| Packagist | `packagist.org` |
| Go | `pkg.go.dev`, `proxy.golang.org` |
| Maven | `repo.maven.apache.org` |
| Google | `dl.google.com` |
| Docker | `hub.docker.com`, `registry.hub.docker.com`, `ghcr.io` |

Localhost addresses (`localhost`, `127.0.0.1`, `0.0.0.0`, `::1`) are always allowed regardless of this list.

!!! note
    `network_write` requests (POST/PUT/DELETE/PATCH) always ask, even to known hosts. Known registries only auto-allow reads.

**Config:**

```yaml
# Add hosts (list form)
known_registries:
  - internal-mirror.corp.com
  - artifacts.mycompany.io

# Add and remove (dict form)
known_registries:
  add:
    - internal-mirror.corp.com
  remove:
    - registry.npmmirror.com
```

!!! warning "Global config only"
    `known_registries` is only accepted in `~/.config/nuh/config.yaml`. Project `.nuh.yaml` cannot modify it (supply-chain safety).

**CLI:** `nuh trust api.example.com` / `nuh forget api.example.com`

## exec_sinks

Executables that trigger pipe composition rules. When a network or decode command pipes into an exec sink, nuh blocks it.

**Built-in defaults (15):**

`bash`, `sh`, `dash`, `zsh`, `eval`, `python`, `python3`, `node`, `ruby`, `perl`, `php`, `bun`, `deno`, `fish`, `pwsh`

**Config:**

```yaml
exec_sinks:
  add:
    - lua
    - elixir
  remove:
    - php
```

!!! warning
    Removing exec sinks weakens composition rules (nuh prints a stderr warning). The `network | exec` and `decode | exec` rules won't fire for removed sinks.

## sensitive_basenames

Filenames that trigger sensitive path detection regardless of directory.

**Built-in defaults (5):**

| Basename | Default policy |
|----------|:--------------:|
| `.env` | ask |
| `.env.local` | ask |
| `.env.production` | ask |
| `.npmrc` | ask |
| `.pypirc` | ask |

**Config:**

```yaml
sensitive_basenames:
  .env.staging: ask         # add new
  .npmrc: block             # tighten existing
```

The built-in basenames are credential floors: `allow` on one prints a warning and is ignored.

## decode_commands

Commands that trigger obfuscation detection in pipe composition. When a decode command pipes into an exec sink, nuh blocks the chain.

**Built-in defaults (4):**

| Command | Flag | Detects |
|---------|------|---------|
| `base64` | `-d` | `base64 -d \| bash` |
| `base64` | `--decode` | `base64 --decode \| bash` |
| `xxd` | `-r` | `xxd -r \| bash` |
| `uudecode` | *(any)* | `uudecode \| bash` |

**Config:**

```yaml
decode_commands:
  add:
    - "openssl enc -d"    # "command flag" format
    - "gunzip"            # no flag needed
  remove:
    - uudecode
```

!!! warning
    Removing decode commands weakens composition rules (nuh prints a stderr warning).

## profile: none

Setting `profile: none` clears the safety lists above to empty, except the sensitive basenames: those are floors and stay. You then build up exactly what you want:

```yaml
profile: none
known_registries:
  - pypi.org
  - github.com
exec_sinks:
  - bash
  - sh
```
