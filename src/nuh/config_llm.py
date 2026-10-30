"""The ``llm`` section's settings, parsed onto the merged config (global config only).

A repo's ``.nuh.yaml`` never sets any of them (config._merge_configs reads
``llm`` from the global file alone); it may only switch tracing off
(``trace: false``).
"""

import os
import sys
from typing import TYPE_CHECKING

from nuh import llm_cap, llm_fallback, llm_heal

if TYPE_CHECKING:
    from nuh.config import NuhConfig

TRACE_KEEP_DAYS = 30  # llm.trace_keep_days default


def apply(config: "NuhConfig", llm: dict, *, warn: bool) -> None:
    """Set *config*'s ``llm`` section and every setting parsed from it.

    *warn*: report invalid values on stderr (the config load does;
    ``nuh test``'s in-memory override reads a section already loaded).
    """
    config.llm = llm
    # llm.can_block: whether an LLM block blocks or is capped to ask
    config.llm_can_block = llm_cap.parse_can_block(llm, warn=warn)
    # llm.on_uncertain / llm.on_failure: what a review with no decision does
    config.llm_on_uncertain = llm_fallback.parse(llm, llm_fallback.ON_UNCERTAIN, warn=warn)
    config.llm_on_failure = llm_fallback.parse(llm, llm_fallback.ON_FAILURE, warn=warn)
    # llm.self_heal: a provider timeout starts a detached worker that loads a cold model (llm_heal.py)
    config.llm_self_heal = llm_heal.parse(llm, warn=warn)
    # llm.trace_dir: opt-in debugging trace of every LLM review; a project's
    # `trace: false` (config.trace_off) switches it off, trusted or not
    config.llm_trace_dir = "" if config.trace_off else parse_trace_dir(llm.get("trace_dir"))
    # llm.trace_keep_days: traces older than this many days are pruned, at most daily
    config.llm_trace_keep_days = parse_trace_keep_days(llm.get("trace_keep_days"), warn=warn)


def parse_project_trace(project_cfg: dict) -> bool:
    """Project ``trace``: False opts the project out of tracing; True or unset leaves it to the global config."""
    raw = project_cfg.get("trace", True)
    if not isinstance(raw, bool):
        sys.stderr.write(f"nuh: config: .nuh.yaml trace must be true or false, got {raw!r}; ignored\n")
        return False
    return raw is False


def parse_trace_keep_days(raw: object, *, warn: bool = True) -> int:
    """Parse llm.trace_keep_days: a whole number of days, 0 = keep forever; unset or invalid is the default."""
    if raw is None:
        return TRACE_KEEP_DAYS
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    if warn:
        sys.stderr.write(f"nuh: config: llm.trace_keep_days must be a whole number of days (0 = keep forever), "
                         f"got {raw!r}; using {TRACE_KEEP_DAYS}\n")
    return TRACE_KEEP_DAYS


def parse_trace_dir(raw: object) -> str:
    """Parse llm.trace_dir: unset or empty is off; ~ expands; must end up absolute."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        sys.stderr.write("nuh: config: llm.trace_dir must be a path string; tracing off\n")
        return ""
    path = os.path.expanduser(raw.strip())
    if path and not os.path.isabs(path):
        # A relative path would resolve against each hook call's cwd,
        # scattering traces across projects.
        sys.stderr.write(f"nuh: config: llm.trace_dir must be absolute or start with ~: {raw}; tracing off\n")
        return ""
    return path
