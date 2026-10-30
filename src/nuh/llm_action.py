"""The action under LLM review: sent whole, or not reviewed at all.

The reviewer never sees a cut-down action. A head/tail or prefix cut lets an
agent pad a command, script or file with harmless text and put the dangerous
part where the cut falls, so the command, program, script body, written
content and edit strings go into the prompt whole and raw: spotting
credential exfiltration is part of the review, and masking key-shaped
text would let a payload hide inside a fake key. A provider can opt in to
redaction (``llm.<provider>.redact_secrets``; see ``llm._dispatch_provider``).
An action over ``llm.max_action_chars`` is not reviewed: the decision is ask,
on the veto path too, because the review it depends on cannot happen.

The rest of the prompt fits around the action: conversation context gets
whatever ``llm.max_prompt_chars`` leaves (never more than
``llm.context_chars``) and gives way first (see ``transcript_render``).

The model must also read the whole prompt. Past its context size Ollama
silently drops the start of the prompt (the instructions), so every provider
has a token limit: ``llm.ollama.num_ctx``, sent with each Ollama request, and
``llm.<provider>.max_prompt_tokens`` for hosted APIs (not sent; they size
their own context). Prompt tokens are estimated at ``CHARS_PER_TOKEN``, with
``OUTPUT_RESERVE_TOKENS`` kept for the answer and ``TEMPLATE_TOKENS`` for
the chat template. The cascade's smallest limit lowers ``max_prompt_chars``.

Defaults:

- ``max_action_chars`` 48000 (~12-16K tokens): four times the 12000-char
  context default, 96x the old 500-char command cut and ~6x the old
  8192-char script/write cut. Larger actions are rare and get a human.
- ``max_prompt_chars`` 60000: system prompt (~1.4K), labels (~1.5K), a
  maximal action and ~9K of context; a typical action of a few K leaves the
  full 12K context. Context starts giving way for actions over ~45K.
- ``num_ctx`` 24576: a 60000-char prompt at 3 chars/token is 20000 tokens;
  plus 1024 for the answer and 256 for the template is 21280, under 24576
  with ~3.3K tokens of headroom. Real traces run 3.2-4.0 chars/token, so a
  maximal prompt actually uses 15-19K. Ollama reloads the model when a
  request's num_ctx differs from the loaded one (a one-time delay on the
  first call), and the KV cache grows with num_ctx (more VRAM).
- ``max_prompt_tokens`` 32768 for hosted providers: every current hosted
  model takes far more, and it leaves the 60000-char budget binding.

This module also builds the Bash action: the command, every inline program a
lang_exec stage carries (``StageResult.inline_program``), or a script file's
content, labeled, with the review question for programs.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Protocol

from nuh import config_keys, taxonomy
from nuh.content import scan_content
from nuh.exec_source import InlineProgram
from nuh.transcript import _int_setting

DEFAULT_MAX_ACTION_CHARS = 48_000
DEFAULT_MAX_PROMPT_CHARS = 60_000
DEFAULT_OLLAMA_NUM_CTX = 24_576
DEFAULT_MAX_PROMPT_TOKENS = 32_768
CHARS_PER_TOKEN = 3          # conservative: real prompts run 3.2-4.0
OUTPUT_RESERVE_TOKENS = 1024  # the JSON answer, and any thinking a model does first
TEMPLATE_TOKENS = 256        # chat template and role markers around system + user
# A prompt the model read whole costs at least one token per this many chars
# of the user part (the system prompt alone may come from Ollama's cache).
_MAX_CHARS_PER_TOKEN = 6
# Fewer prompt tokens than this is a cache hit, not a cut: a cut keeps
# about num_ctx tokens.
_MIN_COUNT_CHECKED = 64
_ELIDE_MIN_CHARS = 200

REVIEW_QUESTION = """\
Review the code above as a program that is about to run. The deterministic \
content scan only matches patterns: it misses list-form subprocess calls, \
pathlib methods (unlink, write_text, rename), sockets and indirect calls. \
Look specifically for code that:
- deletes, moves or overwrites files outside the task \
(os.remove, Path.unlink, shutil.rmtree/move, open(..., "w"), write_text)
- reads or changes credentials, keys, tokens or dotfiles \
(~/.ssh, ~/.aws, ~/.config, .env, shell rc files)
- sends data over the network (sockets, HTTP clients, urllib, fetch)
- spawns shells or other commands (subprocess in any form, os.system, \
child_process, exec)
Block if it does any of these beyond what the task needs. Allow if it only \
reads, computes, or writes inside the project as the task requires. \
Otherwise say uncertain.
"""


@dataclass(frozen=True)
class Limits:
    """Prompt size limits from the ``llm`` config section."""

    max_action_chars: int = DEFAULT_MAX_ACTION_CHARS
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS


def limits_from_config(llm_config: dict) -> Limits:
    """Read ``llm.max_action_chars`` and ``llm.max_prompt_chars`` (negative counts as 0)."""
    return Limits(
        max(0, _int_setting(llm_config.get("max_action_chars"), "llm.max_action_chars",
                            DEFAULT_MAX_ACTION_CHARS)),
        max(0, _int_setting(llm_config.get("max_prompt_chars"), "llm.max_prompt_chars",
                            DEFAULT_MAX_PROMPT_CHARS)),
    )


def provider_token_limit(name: str, provider_config: dict) -> int:
    """Tokens provider *name* can take: Ollama's ``num_ctx``, else ``max_prompt_tokens``."""
    key = config_keys.token_limit_key(name)
    default = DEFAULT_OLLAMA_NUM_CTX if name == "ollama" else DEFAULT_MAX_PROMPT_TOKENS
    value = _int_setting(provider_config.get(key), f"llm.{name}.{key}", default)
    if value <= 0:
        sys.stderr.write(f"nuh: config: llm.{name}.{key} must be positive; using {default}\n")
        return default
    return value


def prompt_chars_fitting(tokens: int) -> int:
    """Prompt chars (system + user) that fit *tokens* with the answer and template reserved."""
    return max(0, (tokens - OUTPUT_RESERVE_TOKENS - TEMPLATE_TOKENS) * CHARS_PER_TOKEN)


def prompt_cut_reason(count: object, num_ctx: int, user_chars: int) -> str:
    """Why Ollama's ``prompt_eval_count`` suggests the prompt was cut, or "".

    At or near num_ctx, the server filled the context and dropped the
    start; far below the user part's size, it ran with a smaller context
    than asked. A missing count (fully cached prompts omit it) is no evidence.
    """
    if isinstance(count, bool) or not isinstance(count, int):
        return ""
    if count >= num_ctx - OUTPUT_RESERVE_TOKENS:
        return (f"the model read {count} prompt tokens, at its num_ctx limit of {num_ctx}:"
                " the prompt may have been cut")
    if count >= _MIN_COUNT_CHECKED and count < user_chars / _MAX_CHARS_PER_TOKEN:
        return (f"the model read only {count} prompt tokens for {user_chars} chars:"
                " the prompt may have been cut")
    return ""


def too_large_reason(label: str, chars: int, limit: int) -> str:
    """The ask reason for an action the LLM was not shown."""
    return f"{label}: too large for LLM review ({chars} chars > {limit})"


def log_summary(action: dict) -> dict:
    """The decision-log view of a review's action record: sizes, not content."""
    summary = {k: action[k] for k in ("chars", "limit", "too_large", "script_chars") if k in action}
    if action.get("input_cut"):
        summary["input_cut"] = True  # a generic review was shown the input cut at the limit
    programs = action.get("programs") or []
    if programs:
        summary["inline_programs"] = len(programs)
        summary["inline_program_chars"] = sum(p["chars"] for p in programs)
    return summary


def write_action_chars(tool_name: str, tool_input: dict) -> int:
    """Size of what a Write/Edit/MultiEdit/NotebookEdit/apply_patch review sends."""
    if tool_name == "apply_patch":
        return len(str(tool_input.get("patch") or ""))
    if tool_name == "Edit":
        return len(str(tool_input.get("old_string") or "")) + len(str(tool_input.get("new_string") or ""))
    if tool_name == "MultiEdit":
        return sum(len(str(e.get("old_string") or "")) + len(str(e.get("new_string") or ""))
                   for e in tool_input.get("edits", []) if isinstance(e, dict))
    if tool_name == "NotebookEdit":
        return 0 if tool_input.get("action") == "delete" else len(str(tool_input.get("new_source") or ""))
    return len(str(tool_input.get("content") or ""))


# -- Bash --


class _Stage(Protocol):
    tokens: list[str]
    action_type: str
    decision: str
    inline_program: InlineProgram | None


@dataclass
class BashAction:
    """The parts of a Bash review prompt that show the action."""

    command: str     # long inline programs replaced by a pointer to their section
    details: str     # program sections or script content, with the review question; may be ""
    chars: int       # action size: command + programs + script
    stats: dict = field(default_factory=dict)  # for the trace and log


def driving_stage(stages: list[_Stage]) -> _Stage | None:
    """The stage the review is about.

    The first stage that asks; otherwise (the lang_exec veto) the first
    lang_exec stage with an inline program, then any lang_exec stage; then
    the first stage.
    """
    picks: tuple[Callable[[_Stage], bool], ...] = (
        lambda s: s.decision == taxonomy.ASK,
        lambda s: s.action_type == taxonomy.LANG_EXEC and s.inline_program is not None,
        lambda s: s.action_type == taxonomy.LANG_EXEC,
    )
    for pick in picks:
        for stage in stages:
            if pick(stage):
                return stage
    return stages[0] if stages else None


def bash_action(
    command: str,
    stages: list[_Stage],
    read_script: Callable[[list[str]], str | None],
) -> BashAction:
    """The command and the program it runs, whole.

    Every distinct inline program of *stages* gets a labeled section. Without
    one, a lang_exec driving stage's script file is read with *read_script*
    (FD-079).
    """
    programs = list(dict.fromkeys(s.inline_program for s in stages if s.inline_program is not None))
    elided = _elide_programs(command, programs)
    stats: dict = {}
    script = None
    if programs:
        blocks = [_program_block(n, len(programs), p) for n, p in enumerate(programs, 1)]
        details = "".join(blocks) + "\n" + REVIEW_QUESTION
        stats["programs"] = [{"interpreter": p.interpreter, "source": p.source, "chars": len(p.code)}
                             for p in programs]
    else:
        stage = driving_stage(stages)
        if stage is not None and stage.action_type == taxonomy.LANG_EXEC:
            script = read_script(stage.tokens)
        details = _script_block(script) if script else ""
        if script:
            stats["script_chars"] = len(script)
    chars = len(elided) + sum(len(p.code) for p in programs) + len(script or "")
    return BashAction(elided, details, chars, stats)


def _elide_programs(command: str, programs: list[InlineProgram]) -> str:
    """*command* with each long program that appears in it verbatim, once, replaced by a pointer.

    The program is shown whole in its own section, so a heredoc body is not
    sent twice. One that cannot be found exactly once stays in place.
    """
    for number, program in enumerate(programs, 1):
        code = program.code
        if len(code) >= _ELIDE_MIN_CHARS and command.count(code) == 1:
            command = command.replace(code, f"[program {number}: {len(code)} characters, shown below]")
    return command


def _program_block(number: int, total: int, program: InlineProgram) -> str:
    label = f"Program {number} of {total}" if total > 1 else f"Program {number}"
    return (
        f"\n{label} about to execute ({program.interpreter}, from {program.source},"
        f" {len(program.code)} characters):\n"
        f"```\n{program.code}\n```\n"
        f"Content inspection: {_inspection(program.code)}\n"
    )


def _script_block(script: str) -> str:
    return (
        f"\nScript about to execute:\n```\n{script}\n```\n"
        f"Content inspection: {_inspection(script)}\n"
        f"\n{REVIEW_QUESTION}"
    )


def _inspection(code: str) -> str:
    matches = scan_content(code)
    return ", ".join(m.pattern_desc for m in matches) if matches else "no flags"
