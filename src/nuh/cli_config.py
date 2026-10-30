"""``nuh config show``: every effective setting, one per line.

It walks the merged config's fields, so a new setting shows up here without
anyone remembering to add it. Each line is labelled with the key you write in
YAML (``llm.on_failure``, ``hook.deadline_s``), or the field name where the
setting is a merge of several keys (``classify_global``). ``tools`` and
``human_only`` print as tables, each entry with where its value comes from.
"""

import dataclasses

from nuh import config_gating
from nuh.config import NuhConfig

_LABEL_WIDTH = 30
# Field name → the YAML key it comes from, where the two differ.
_KEYS = {
    "content_patterns_add": "content_patterns.add",
    "content_patterns_suppress": "content_patterns.suppress",
    "content_policies": "content_patterns.policies",
    "credential_patterns_add": "credential_patterns.add",
    "credential_patterns_suppress": "credential_patterns.suppress",
    "llm_can_block": "llm.can_block",
    "llm_trace_dir": "llm.trace_dir",
    "llm_trace_keep_days": "llm.trace_keep_days",
    "llm_on_uncertain": "llm.on_uncertain",
    "llm_on_failure": "llm.on_failure",
    "llm_self_heal": "llm.self_heal",
    "trace_off": "trace off (project)",
    "hook_deadline_s": "hook.deadline_s",
    "hook_on_error": "hook.on_error",
    "sensitive_paths_global": "sensitive_paths (global)",
    "unknown": "unknown keys (ignored)",
}


def _shown(cfg: NuhConfig, name: str, value: object) -> str:
    """*value* of field *name* as ``config show`` prints it."""
    if name == "llm_trace_dir" and not value:
        return "(off: project trace: false)" if cfg.trace_off else "(off)"
    if name == "llm_trace_keep_days" and not value:
        return "0 (keep forever)"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (dict, list, frozenset)) and not value:
        return "{}" if isinstance(value, dict) else "[]"
    return str(value)


def lines(cfg: NuhConfig) -> list[str]:
    """``config show``'s output for *cfg*."""
    out = ["Effective config (merged):"]
    for f in dataclasses.fields(cfg):
        label = _KEYS.get(f.name, f.name) + ":"
        if f.name in _TABLES:
            out.append(f"  {label}")
            out += _TABLES[f.name](getattr(cfg, f.name), "    ")
            continue
        out.append(f"  {label:<{_LABEL_WIDTH}} {_shown(cfg, f.name, getattr(cfg, f.name))}")
    return out


# Settings printed as tables, one entry per line: (configured value, indent) -> lines.
_TABLES = {"tools": config_gating.tools_lines, "human_only": config_gating.human_only_lines}
