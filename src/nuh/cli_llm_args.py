"""Validation of ``nuh test``'s LLM and session-context flags (defined in ``cli_parser``) into a ``cli_llm.Plan``.

A bad flag or combination is a ``cli_llm.UsageError`` naming the flag: a
provider flag without ``--provider``, a ``--set`` key outside ``llm.*``,
``--no-llm`` with any flag that only feeds the LLM step.
"""

from __future__ import annotations

import argparse
import json
import os

from nuh.cli_llm import Plan, UsageError

_PROVIDER_FLAGS = (("provider", "--provider"), ("provider_url", "--provider-url"), ("model", "--model"))
_OTHER_FLAGS = (("timeout", "--timeout"), ("can_block", "--[no-]can-block"), ("set_values", "--set"),
                ("show_request", "--show-request"), ("user_messages", "--user"),
                ("first_prompt", "--first-prompt"), ("transcript", "--transcript"))


def _given(attr: str, value: object) -> bool:
    if attr == "can_block":
        return value is not None  # --no-can-block is a choice too
    return value is not None and value is not False and value != []


def from_args(args: argparse.Namespace) -> Plan:
    """The validated plan for *args*, or UsageError naming the bad flag.

    Reads every flag with ``getattr``: callers may pass a Namespace built
    without them (the flags are all optional).
    """
    given = [flag for attr, flag in _PROVIDER_FLAGS + _OTHER_FLAGS
             if _given(attr, getattr(args, attr, None))]
    mode = getattr(args, "llm_mode", None) or ""
    if mode == "off" and given:
        raise UsageError(f"--no-llm turns the LLM step off; it does not go with {given[0]}")
    plan = Plan(mode=mode, show_request=bool(getattr(args, "show_request", False)))
    plan.provider, plan.provider_section = _provider(args)
    plan.timeout = getattr(args, "timeout", None)
    plan.can_block = getattr(args, "can_block", None)
    plan.sets = [parse_set(raw) for raw in getattr(args, "set_values", None) or []]
    _context(args, plan)
    return plan


def parse_set(raw: str) -> tuple[list[str], object]:
    """``llm.a.b=VALUE`` -> (["a", "b"], VALUE parsed as JSON, or the string when it is not JSON)."""
    key, sep, value = raw.partition("=")
    parts = key.strip().split(".")
    if not sep:
        raise UsageError(f"--set {raw!r}: expected llm.KEY=VALUE")
    if parts[0] != "llm" or len(parts) < 2 or not all(parts):
        raise UsageError(f"--set {raw!r}: only llm.* keys can be set (use --config for the rest)")
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    return parts[1:], parsed


def _provider(args: argparse.Namespace) -> tuple[str, dict]:
    from nuh import llm_providers
    name = getattr(args, "provider", None) or ""
    section = {k: v for k, v in (("url", getattr(args, "provider_url", None)),
                                 ("model", getattr(args, "model", None))) if v is not None}
    if section and not name:
        raise UsageError("--provider-url and --model need --provider NAME")
    if name and name not in llm_providers.NAMES:
        raise UsageError(f"--provider {name!r}: not a provider ({', '.join(sorted(llm_providers.NAMES))})")
    return name, section


def _context(args: argparse.Namespace, plan: Plan) -> None:
    plan.users = list(getattr(args, "user_messages", None) or [])
    plan.first_prompt = getattr(args, "first_prompt", None) or ""
    transcript = getattr(args, "transcript", None)
    if transcript and (plan.users or plan.first_prompt):
        raise UsageError("--transcript reads a real session; --user and --first-prompt build one: use one or the other")
    if transcript and not os.path.isfile(transcript):
        raise UsageError(f"--transcript {transcript}: no such file")
    plan.transcript = os.path.abspath(transcript) if transcript else ""
    cwd = getattr(args, "cwd", None)
    if cwd and not os.path.isdir(cwd):
        raise UsageError(f"--cwd {cwd}: not a directory")
    plan.cwd = os.path.abspath(cwd) if cwd else ""
