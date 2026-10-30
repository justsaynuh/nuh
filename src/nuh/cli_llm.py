"""``nuh test``'s LLM step: point it at any provider or model for one run.

The flags (``cli_llm_args``) change the merged config in memory
(``config.apply_llm_override``) and never write a config file. The safety
rules still hold under them: a review that gets no verdict still asks.

``--user`` and ``--first-prompt`` are written as a throwaway Claude Code
transcript in a temporary directory, removed when the test ends, so the
providers pick and budget the messages by the same rules as a real session's.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from nuh import agents, bash_review
from nuh import cli_llm_report as report

if TYPE_CHECKING:
    from nuh.trace import MemoryTrace

class UsageError(Exception):
    """A bad flag or flag combination. ``nuh test`` prints it and exits 1."""


@dataclass
class Plan:
    """What the flags ask of this run's LLM step, validated (``cli_llm_args.from_args``)."""

    mode: str = ""                # "on", "off", or "" (llm.enabled decides)
    provider: str = ""
    provider_section: dict = field(default_factory=dict)  # url / model for --provider
    timeout: float | None = None
    can_block: bool | None = None  # None: llm.can_block decides
    sets: list[tuple[list[str], object]] = field(default_factory=list)
    users: list[str] = field(default_factory=list)
    first_prompt: str = ""
    transcript: str = ""
    cwd: str = ""
    show_request: bool = False
    transcript_path: str = ""     # what the review reads: --transcript, or the one session() writes

    @property
    def changes_llm(self) -> bool:
        return bool(self.mode or self.provider or self.sets
                    or self.timeout is not None or self.can_block is not None)

    def apply(self, llm_config: dict) -> dict:
        """A copy of *llm_config* with the flags applied; ``--set`` goes last.

        Choosing a provider (``--provider``) turns the LLM step on, as
        ``--llm`` does; ``--no-llm`` turns it off.
        """
        from nuh import llm_providers
        out = copy.deepcopy(llm_config)
        if self.mode or self.provider:
            out["enabled"] = self.mode != "off"
        if self.provider:
            out["providers"] = [self.provider]
            section = _section(out, self.provider)
            section.update(self.provider_section)
            if not section:
                # An empty section reads as unconfigured (llm_providers._configured_providers).
                section["model"] = llm_providers._DEFAULT_MODELS[self.provider]
        if self.timeout is not None:
            for _, section in llm_providers._configured_providers(out):
                if isinstance(section, dict):
                    section["timeout"] = self.timeout
        if self.can_block is not None:
            out["can_block"] = self.can_block
        for path, value in self.sets:
            _set_path(out, path, value)
        return out

    def apply_config(self) -> None:
        """Apply the flags to the merged config, in memory only."""
        if self.changes_llm:
            from nuh import config
            config.apply_llm_override(self.apply(config.get_config().llm))

    def context_lines(self) -> list[str]:
        lines = [f"transcript {self.transcript}"] if self.transcript else []
        lines += ["first prompt"] if self.first_prompt else []
        lines += [f"{len(self.users)} user message(s)"] if self.users else []
        return lines

    @contextlib.contextmanager
    def session(self) -> Iterator[None]:
        """Run the test from --cwd, with --user / --first-prompt written as a transcript."""
        with contextlib.ExitStack() as stack:
            self.transcript_path = self.transcript
            if self.users or self.first_prompt:
                directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="nuh-test-"))
                self.transcript_path = _write_transcript(directory, self.first_prompt, self.users)
            if self.cwd:
                stack.callback(_chdir, os.getcwd())
                _chdir(self.cwd)
            yield

    @contextlib.contextmanager
    def invocation(self, tool_name: str, tool_input: dict) -> Iterator[MemoryTrace | None]:
        """The hook's per-call state as ``hook.main`` sets it, so its LLM path sees this call.

        Yields the in-memory trace of the reviews with --show-request, else None.
        """
        from nuh import hook, trace
        saved = (hook._payload, hook._transcript_path, hook._agent, hook._call)
        hook._payload = {"tool_name": tool_name, "tool_input": tool_input, "cwd": os.getcwd()}
        hook._transcript_path, hook._agent, hook._call = self.transcript_path, agents.CLAUDE, None
        try:
            if self.show_request:
                with trace.capture() as memory:
                    yield memory
            else:
                yield None
        finally:
            hook._payload, hook._transcript_path, hook._agent, hook._call = saved


def _chdir(path: str) -> None:
    from nuh import paths
    os.chdir(path)
    paths.reset_project_root()


def _write_transcript(directory: str, first: str, users: list[str]) -> str:
    """A Claude Code transcript holding *first*, then *users*, as typed user messages."""
    path = os.path.join(directory, "session.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for text in ([first] if first else []) + users:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
    return path


def _section(llm_config: dict, name: str) -> dict:
    """``llm.<name>``, created when missing."""
    section = llm_config.setdefault(name, {})
    if not isinstance(section, dict):
        raise UsageError(f"llm.{name} in the config is not a mapping: {section!r}")
    return section


def _set_path(llm_config: dict, path: list[str], value: object) -> None:
    node = llm_config
    for depth, key in enumerate(path[:-1], 1):
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise UsageError(f"--set llm.{'.'.join(path)}: llm.{'.'.join(path[:depth])} is not a mapping")
    node[path[-1]] = value


# -- Running the review --


def review_bash(plan: Plan, result: object, route: bash_review.Route) -> None:
    """The LLM step for a Bash command: as the hook runs it (*route*), or forced by --llm."""
    from nuh import hook, llm_skip, trace
    from nuh.config import get_config
    from nuh.llm import try_llm
    if plan.mode == "off":
        print("LLM step:     off (--no-llm)")
        return
    if plan.mode != "on" and llm_skip.meta(route.skip):
        print(f"LLM step:     skipped, {route.skip} (--llm reviews it anyway)")
        return
    if not route.kind and plan.mode != "on":
        if plan.show_request:
            print(f"Request:      none sent ({route.why_not}; --llm reviews it anyway)")
        return
    cfg = get_config()
    if not cfg.llm:
        print("LLM config:   not configured")
        return
    if not cfg.llm.get("enabled", False):
        print("LLM config:   disabled (set enabled: true to activate, or pass --llm)")
        return
    if not route.kind:
        print(f"LLM review:   forced by --llm (the hook would not send it: {route.why_not})")
    elif route.kind == bash_review.SCRIPT:
        print("LLM review:   script file (the static allow runs a script nuh scanned clean; the hook sends it,"
              " and an LLM ask or block raises the allow)")
    report.print_settings(cfg.llm, plan.context_lines())
    command = result.command
    with plan.invocation("Bash", {"command": command}) as memory:
        review = trace.start_review("bash", lambda: {"tool": "Bash", "command": command})
        llm_call = try_llm(result, cfg.llm, hook._transcript(), trace=review, call=hook._tool_call())
    report.print_bash_result(llm_call)
    if route.kind == bash_review.SCRIPT:
        _print_script_outcome(llm_call, cfg)
    if memory is not None:
        report.print_exchanges(memory)


def _print_script_outcome(llm_call: object, cfg: object) -> None:
    """What the hook makes of the static allow after this script review (bash_review.raised_allow)."""
    from nuh import llm_meta
    raised = bash_review.raised_allow(copy.deepcopy(llm_call.decision), llm_meta.build(llm_call, cfg))
    if raised:
        print(f"After review: {raised['decision'].upper()}")
    else:
        print("After review: ALLOW (the static allow stands)")


def review_write(plan: Plan, tool: str, tool_input: dict, handler: Callable[[dict], dict]) -> dict:
    """Run a write-like *handler* (static checks, then the hook's LLM veto) and report its LLM step."""
    from nuh.config import get_config
    cfg = get_config()
    enabled = bool(cfg.llm) and bool(cfg.llm.get("enabled", False))
    if plan.mode == "off":
        print("LLM step:     off (--no-llm)")
    elif enabled:
        report.print_settings(cfg.llm, plan.context_lines())
    with plan.invocation(tool, tool_input) as memory:
        decision = handler(tool_input)
    if enabled:
        report.print_write_result(decision)
    if memory is not None:
        report.print_exchanges(memory)
    return decision


def review_generic(plan: Plan, tool: str, tool_input: dict, static: dict) -> dict:
    """Run the generic review (hook_generic.py) on a tool's *static* decision and report its LLM step."""
    from nuh import hook_generic
    from nuh.config import get_config
    cfg = get_config()
    enabled = bool(cfg.llm) and bool(cfg.llm.get("enabled", False))
    if plan.mode == "off":
        print("LLM step:     off (--no-llm)")
        return static
    if enabled:
        report.print_settings(cfg.llm, plan.context_lines())
    with plan.invocation(tool, tool_input) as memory:
        decision = hook_generic.review(tool, tool_input, static)
    if enabled and decision.get("_meta"):
        report.print_write_result(decision)
    if memory is not None:
        report.print_exchanges(memory)
    return decision
