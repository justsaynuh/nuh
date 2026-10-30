# Codex integration

nuh shares policy evaluation between Claude Code and Codex. The adapter handles
their differences at the input and output boundaries. Claude remains the default
for existing CLI commands and public adapter calls.

## Setup

Run `nuh install --agent codex` to install the guard and synchronous
`PreToolUse` registrations in `~/.codex/hooks.json`. Use
`nuh update --agent codex` after package updates, or
`nuh uninstall --agent codex` to remove this integration. `--agent all` targets
supported agent configuration directories that exist.

Each registration carries a `timeout` (seconds) sized like Claude's: nuh's
inspection budget (`hook.deadline_s`) plus the LLM providers' timeouts plus a
margin, at least 60. Codex's `hooks.json` has no failure setting like Claude
Code's `"onFailure": "block"`, so a Codex hook that crashes or times out lets
the call through; nuh's own deadline answers (deny, for Codex) before that.

Review and enable/trust the hook commands in Codex. nuh does not edit Codex's
hook trust or enabled state. Recheck that state after changing a command or hook
configuration. Claude's registration and guard remain independently managed.

## Decisions and detection

The documented `turn_id` payload field identifies Codex. Set `NUH_AGENT=codex`
or `NUH_AGENT=claude` explicitly for an ambiguous invocation. The override wins;
unrecognized override values are errors. Generic inherited agent environment
variables and the guard's installation directory do not identify the caller.
Payloads without a Codex marker retain the legacy Claude default.

Codex shell tools arrive as `Bash`, with `tool_input.command`. File edits arrive
as `apply_patch`, also with `tool_input.command`; `Edit` and `Write` matcher
aliases do not change that payload.

For Codex, internal `ask` decisions become `deny`, with a review-required reason.
This blocks the attempt; it does not open an approval dialog. Claude `ask`
responses are unchanged. An `allow` response does not override Codex's normal
permissions. `PermissionRequest` hooks run only when approval is already needed
and are not used as a substitute for `PreToolUse` enforcement.

## Patch inspection and failures

The dedicated patch handler inspects add, update, delete, and move operations,
including every source and destination in a multi-file patch. It combines path,
project-boundary, and content checks using the strictest decision. Malformed or
unsupported patches are rejected before policy evaluation can allow them. The
guard inspects patches without applying them.

Inspection covers added text and retained hunk context, not a reconstructed copy
of the entire resulting file. Patches over 1 MiB, overlapping targets, wrapper
syntax, and moves without update hunks are conservatively blocked. Use ordinary
patch framing with distinct file targets.

The optional LLM reviewer receives each original patch operation with its hunk
markers, removed/added lines, and move destination. Its prompt explains that
updates preserve omitted content; patch fragments are not presented as whole-file
writes. The patch is sent whole and unredacted; one over `llm.max_action_chars`
is not reviewed and gets ask (see the README's LLM configuration).

The standalone wrapper reads stdin once, replays it to the package, and validates
the response structure before forwarding captured stdout. Malformed input is
denied. Import/runtime failures and invalid output request confirmation for a
recognized Claude invocation and deny Codex invocations. Successful empty output
continues to support `active_allow: false`. Successful zero exits retain captured
decisions; unsuccessful exits use the interface-aware failure response.

Hook coverage is limited to the tool paths Codex exposes to hooks. Consult the
[official Codex hook contract](https://learn.chatgpt.com/docs/hooks) for current
coverage and limitations.

## Local verification (2026-09-19)

The isolated `.venv` run, using a clean HOME and a socket blocker and excluding
`tests/test_llm_live.py`, completed with 3,349 passed, 34 failed, 6 skipped, and
15 expected failures. All 34 failing test identifiers also fail on an untouched
archive of the pre-integration HEAD under the same environment; there are no new
full-suite failures. The baseline had 3,231 passing tests.

Eight additional offline checks passed against the installed Codex wrapper and
its pipx interpreter: editable package location, exact rendered wrapper, read-only
mode, synchronous registration, Claude ASK, Codex DENY, and patch decisions.
Registration preserves the original eleven shell, file, read/search, MCP, and
web matchers and adds explicit apply_patch coverage. Updates also preserve custom
nuh matchers instead of reducing them to built-in defaults. Existing disabled hook
state was left intact; enable/trust the hooks in Codex before relying on live
enforcement.
