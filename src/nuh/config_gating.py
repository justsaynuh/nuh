"""The ``tools`` and ``human_only`` settings (gating.py), parsed from the global config only.

A repo's ``.nuh.yaml`` may only tighten, so it never sets ``tools``,
``human_only`` or ``llm``: each prints a warning and is ignored. An entry
that cannot be read (an unknown word or key, a value of the wrong type)
warns and is dropped, so a typo keeps the default and never hands the LLM
more say than it had.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from nuh import floor, gating

if TYPE_CHECKING:
    from nuh.config import NuhConfig

PROJECT_IGNORED = ("tools", "human_only", "llm")


def parse_tools(raw: object, *, warn: bool = True) -> dict[str, str]:
    """``tools``: tool name or glob -> word; entries that are not one of gating.WORDS are dropped."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        if warn:
            sys.stderr.write(f"nuh: config: tools must map tool names to {'|'.join(gating.WORDS)}; ignored\n")
        return {}
    table: dict[str, str] = {}
    for tool, value in raw.items():
        if not isinstance(tool, str) or not tool.strip():
            if warn:
                sys.stderr.write(f"nuh: config: tools: {tool!r} is not a tool name; ignored\n")
        elif value not in gating.WORDS:
            if warn:
                sys.stderr.write(f"nuh: config: tools.{tool} must be one of {', '.join(gating.WORDS)}, "
                                 f"got {value!r}; ignored\n")
        else:
            table[tool.strip()] = value
    return table


def parse_human_only(raw: object, *, warn: bool = True) -> dict[str, bool]:
    """``human_only``: key -> true/false; unknown keys and values that are not true/false are dropped."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        if warn:
            sys.stderr.write("nuh: config: human_only must map keys to true or false; ignored\n")
        return {}
    known = gating.known_keys()
    out: dict[str, bool] = {}
    for key, value in raw.items():
        if key not in known:
            if warn:
                sys.stderr.write(f"nuh: config: human_only: unknown key {key!r} ignored (keys: floor rule ids, "
                                 f"action types, {', '.join(gating.CONTENT_KEYS)}; see nuh types)\n")
        elif not isinstance(value, bool):
            if warn:
                sys.stderr.write(f"nuh: config: human_only.{key} must be true or false, got {value!r}; "
                                 f"keeping {str(gating.default_human_only(key)).lower()}\n")
        else:
            out[key] = value
    return out


def apply(config: "NuhConfig", global_cfg: dict, project_cfg: dict) -> None:
    """Set *config*'s ``tools`` and ``human_only`` from the global config; warn about a project that sets them."""
    config.tools = parse_tools(global_cfg.get("tools"))
    config.human_only = parse_human_only(global_cfg.get("human_only"))
    for key in PROJECT_IGNORED:
        if key in project_cfg:
            sys.stderr.write(f"nuh: config: {key} is global-only; ignored in .nuh.yaml (a project may only tighten)\n")


def apply_override(config: "NuhConfig", override: dict) -> None:
    """``nuh test --config``: merge ``tools`` and ``human_only`` entries onto *config*, in memory only."""
    if "tools" in override:
        config.tools = {**config.tools, **parse_tools(override["tools"])}
    if "human_only" in override:
        config.human_only = {**config.human_only, **parse_human_only(override["human_only"])}


# -- nuh config show --

_OTHER_TOOL, _OTHER_TYPE = "any other tool", "any other action type"


def tools_lines(configured: dict[str, str], pad: str) -> list[str]:
    """``tools``: each built-in tool and each configured name with its word and where the word comes from."""
    names = list(dict.fromkeys([*gating.BUILT_IN, *configured]))
    width = max(len(name) for name in [*names, _OTHER_TOOL])
    out = []
    for name in names:
        word, source = (configured[name], gating.SRC_CONFIG) if name in configured else gating.resolve(name, configured)
        out.append(f"{pad}{name:<{width}}  {word:<8} ({source})")
    out.append(f"{pad}{_OTHER_TOOL:<{width}}  {gating.DEFER:<8} ({gating.SRC_DEFAULT})")
    return out


def human_only_lines(configured: dict[str, bool], pad: str) -> list[str]:
    """``human_only``: every key that is true, each configured key, and the action types left false."""
    shown = [*floor.RULES, *gating.CONTENT_KEYS, *sorted(k for k in configured if k not in floor.RULES
                                                           and k not in gating.CONTENT_KEYS)]
    width = max(len(key) for key in [*shown, _OTHER_TYPE])
    out = []
    for key in shown:
        value = configured.get(key, gating.default_human_only(key))
        source = gating.SRC_CONFIG if key in configured else gating.SRC_DEFAULT
        out.append(f"{pad}{key:<{width}}  {str(value).lower():<5} ({source})")
    out.append(f"{pad}{_OTHER_TYPE:<{width}}  false ({gating.SRC_DEFAULT})")
    return out
