"""Gating: how much nuh and its LLM handle for each tool, and which asks no LLM may lower.

``tools:`` gives each tool one word on the ladder ``defer < approve < check < decide``:

- ``defer``: static asks and blocks as usual; an allow is left silent, so
  Claude Code's own permission mode decides. No LLM.
- ``approve``: an allow is sent, so Claude Code runs the call without its
  own prompt. No LLM.
- ``check``: an allow is sent unless the LLM raises it; the LLM sees allows only.
- ``decide``: the LLM also sees asks and may lower one to allow.

What a reviewing word sends to the LLM depends on the tool's kind
(``kind``), hard-coded:

- Bash: ``decide`` sends static asks and clean-script allows; ``check`` the
  clean-script allows.
- The write tools (Write, Edit, MultiEdit, NotebookEdit, apply_patch):
  ``decide`` sends asks and allows; ``check`` allows.
- Any other tool, through the generic review: ``decide`` sends asks and
  allows; ``check`` allows.

A tool name matches its exact key first; MultiEdit, NotebookEdit and
apply_patch then follow Edit; then the longest matching glob; else
``defer``.

``human_only:`` names the asks no LLM may lower: floor rule ids
(floor.RULES), action types, and the write content categories
``content_secret``, ``content_custom`` and ``content_too_large``. Every
floor rule and content key is true by default, every action type false. An
ask may go to the LLM only when every key behind it (``keys_of``) is false;
an ask nuh cannot attribute to any key is human-only (``UNATTRIBUTED``).
"""

from __future__ import annotations

import fnmatch
import sys

from nuh import floor, taxonomy

DEFER, APPROVE, CHECK, DECIDE = "defer", "approve", "check", "decide"
WORDS = (DEFER, APPROVE, CHECK, DECIDE)
REVIEWS = frozenset({CHECK, DECIDE})  # the words under which the LLM is called

DEFAULTS: dict[str, str] = {"Bash": DECIDE, "Write": DECIDE, "Edit": DECIDE,
                            "Read": APPROVE, "Glob": APPROVE, "Grep": APPROVE}
FOLLOWS_EDIT = ("MultiEdit", "NotebookEdit", "apply_patch")
BUILT_IN = (*DEFAULTS, *FOLLOWS_EDIT)  # the tools nuh has a handler for, shown by ``nuh config show``

BASH, WRITE, GENERIC = "bash", "write", "generic"
_WRITE_TOOLS = frozenset({"Write", "Edit", *FOLLOWS_EDIT})

CONTENT_SECRET, CONTENT_CUSTOM, CONTENT_TOO_LARGE = "content_secret", "content_custom", "content_too_large"
CONTENT_KEYS = (CONTENT_SECRET, CONTENT_CUSTOM, CONTENT_TOO_LARGE)
# The key of an ask nuh cannot attribute to any other: always human-only, not settable.
UNATTRIBUTED = "unattributed"

# Decision key: the human_only keys behind a static decision, besides its floor rule (``_floor``).
KEYS = "_keys"

# Sources ``resolve`` reports.
SRC_DEFAULT, SRC_CONFIG, SRC_FOLLOWS_EDIT, SRC_UNLISTED = "default", "config", "follows Edit", "not listed"


def kind(tool: str) -> str:
    """The tool's kind: what its reviewing words send to the LLM (module docstring)."""
    if tool == "Bash":
        return BASH
    return WRITE if tool in _WRITE_TOOLS else GENERIC


def resolve(tool: str, configured: dict[str, str]) -> tuple[str, str]:
    """(word, source) for *tool* under the *configured* ``tools`` entries; source says which entry decided.

    The exact name wins (configured over default); MultiEdit, NotebookEdit and
    apply_patch then follow Edit; then the longest configured glob; else ``defer``.
    """
    if tool in configured:
        return configured[tool], SRC_CONFIG
    if tool in DEFAULTS:
        return DEFAULTS[tool], SRC_DEFAULT
    if tool in FOLLOWS_EDIT:
        return resolve("Edit", configured)[0], SRC_FOLLOWS_EDIT
    best = match(tool, [key for key in configured if is_glob(key)])
    if best:
        return configured[best], f"{SRC_CONFIG}: {best}"
    return DEFER, SRC_UNLISTED


def is_glob(key: str) -> bool:
    return any(ch in key for ch in "*?[")


def match(tool: str, keys) -> str | None:
    """The key among *keys* (tool names and globs) that applies to *tool*, by ``resolve``'s rule; None for none."""
    if tool in keys:
        return tool
    if tool in FOLLOWS_EDIT and "Edit" in keys:
        return "Edit"
    globs = [key for key in keys if is_glob(key) and fnmatch.fnmatchcase(tool, key)]
    return max(globs, key=len) if globs else None


def _configured() -> dict[str, str]:
    from nuh.config import get_config  # lazy import to avoid circular
    return get_config().tools


def word(tool: str) -> str:
    """The ``tools`` word for *tool* (a canonical tool name).

    A config that cannot be read gives ``check``: the LLM may raise a
    decision but never lower one, and an allow is still sent, as it was
    before ``tools`` existed.
    """
    try:
        configured = _configured()
    except Exception as exc:
        sys.stderr.write(f"nuh: config: tools: {exc}; using check\n")
        return CHECK
    return resolve(tool, configured)[0]


def sends_allow(tool: str) -> bool:
    """True when nuh hands *tool*'s allow to the agent explicitly; False leaves it silent (``defer``)."""
    return word(tool) != DEFER


# -- human_only --


def known_keys() -> frozenset[str]:
    """Every key ``human_only`` accepts."""
    return frozenset(floor.RULES) | frozenset(taxonomy.POLICIES) | frozenset(CONTENT_KEYS)


def default_human_only(key: str) -> bool:
    """A key's default: true for floor rules, the content keys and UNATTRIBUTED; false for action types."""
    return key in floor.RULES or key in CONTENT_KEYS or key == UNATTRIBUTED


def is_human_only(key: str) -> bool:
    """True when no LLM may lower an ask *key* is behind; a config that cannot be read keeps every key human-only."""
    if not key:
        return False
    if key == UNATTRIBUTED:
        return True
    try:
        from nuh.config import get_config  # lazy import to avoid circular
        configured = get_config().human_only
    except Exception as exc:
        # A broken config must not hand an ask to the LLM: keep it human-only and say why.
        sys.stderr.write(f"nuh: config: human_only: {exc}\n")
        return True
    return configured.get(key, default_human_only(key))


def held_keys(keys: list[str]) -> list[str]:
    """The human-only keys among *keys*, in order."""
    return [key for key in dict.fromkeys(keys) if is_human_only(key)]


def keys_of(decision: dict) -> list[str]:
    """The human_only keys behind a static *decision*: its floor rule, then the keys its checks attached.

    An ask with neither is UNATTRIBUTED.
    """
    keys = [decision["_floor"]] if decision.get("_floor") else []
    keys += [key for key in decision.get(KEYS) or [] if key not in keys]
    if not keys and decision.get("decision") == taxonomy.ASK:
        return [UNATTRIBUTED]
    return keys


def held_by(decision: dict) -> list[str]:
    """The human-only keys behind an ask *decision*; [] for a decision that is no ask."""
    if decision.get("decision") != taxonomy.ASK:
        return []
    return held_keys(keys_of(decision))


def bash_keys(result) -> list[str]:
    """The keys behind a Bash ClassifyResult's ask: its floor rules, then the action types of its ask stages.

    A composition rule can ask over stages that each allow: then every
    stage's type is behind it. A command without stages is ``unknown``.
    """
    stages = result.all_stages()
    types = [sr.action_type for sr in stages if sr.decision == taxonomy.ASK] or [sr.action_type for sr in stages]
    return list(dict.fromkeys([*result.floor_rules(), *(types or [taxonomy.UNKNOWN])]))


def combine(parts: list[dict]) -> dict:
    """The strictest of a write's static decisions, with the keys behind every ask among them.

    Between equals the higher-ranked floor wins (floor.rank), so the log keeps
    its rule. An ask part without keys of its own adds UNATTRIBUTED. The
    parts' ``_meta`` (a content match, say) is kept.
    """
    best = max(parts, key=lambda d: (taxonomy.STRICTNESS.get(d.get("decision", ""), 3),
                                     floor.rank(d.get("_floor", ""))))
    combined = dict(best)
    keys = [key for part in parts if part.get("decision") == taxonomy.ASK for key in keys_of(part)]
    combined.pop(KEYS, None)
    if keys:
        combined[KEYS] = list(dict.fromkeys(keys))
    meta: dict = {}
    for part in parts:
        meta.update(part.get("_meta") or {})
    if meta:
        combined["_meta"] = meta
    return combined
