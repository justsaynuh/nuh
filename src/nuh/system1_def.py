"""The system 1 definition: what system 1 is asked and how its answers decide.

Found, in order: ``llm.system1.definition`` (absolute or ``~``), the user's
``system1.yaml`` beside the global config (``~/.config/nuh/``, under nuh's
own self-protection), else the default nuh ships (``data/system1.json``,
JSON so the hook reads it with stdlib alone). A repo never supplies one:
``llm`` is global-only.

A definition that cannot be read or is malformed is a DefinitionError, which
fails the system 1 attempt; nuh never falls back to the shipped default
behind the user's back.

    based_on: 0.5.5              # the nuh version a written-out copy came from (informational)
    preamble: <text>             # put before the tool call
    context:
      action_tokens: 1500        # the tool call, middle-clipped
      session_tokens: 800        # the user's messages, oldest cut first
      user: true                 # the user's messages
      file_lines: 30             # Edit/Write file context (0 = off)
      project: false             # the project root (system1_facts)
      cwd_rel: false             # the working directory, relative to the project root
      git: {branch: false, dirty: false}  # the current branch; how many paths git status lists
    questions: {name: {type, instructions, criteria?, explain?, allow?, ask?, block?}}
    gates: [{name?, if: <CEL>, then: allow|ask|block|uncertain}]
    tools:                       # per tool: a block merged onto the rest for that tool's calls
      Bash: {questions: {network: null}}   # null removes a key (here, a question)
      mcp__forge__*: {context: {git: {branch: true}}}

``tools`` keys match a tool as ``tools:`` in the config does
(gating.match): the exact name, then Edit's block for MultiEdit,
NotebookEdit and apply_patch, then the longest glob. A block merges onto the
base definition: maps merge, lists replace, and ``null`` removes a key.
Every block is checked when the definition loads, so a broken one fails
every review, not only its tool's.

The context keys ``agent``, ``tool_calls``, ``prior_decisions`` and
``prior_decisions_max`` are not supported yet: set, they are ignored with a
warning.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from nuh import gating, system1_gates
from nuh.system1_rules import DefinitionError, Question, question

SHIPPED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "system1.json")
USER_FILE = "system1.yaml"
_TOP_KEYS = frozenset({"based_on", "preamble", "context", "questions", "gates", "tools"})
_BLOCK_KEYS = frozenset({"preamble", "context", "questions", "gates"})  # what a tools block may set
_UNSUPPORTED_CONTEXT = frozenset({"agent", "tool_calls", "prior_decisions", "prior_decisions_max"})
_CONTEXT_INTS = {"action_tokens": 1500, "session_tokens": 800, "file_lines": 30}
_CONTEXT_BOOLS = frozenset({"user", "project", "cwd_rel"})
_GIT_ITEMS = {"branch": "git_branch", "dirty": "git_dirty"}


@dataclass(frozen=True)
class Context:
    """What the model is shown beside the questions."""

    action_tokens: int = 1500
    session_tokens: int = 800
    user: bool = True
    file_lines: int = 30
    project: bool = False
    cwd_rel: bool = False
    git_branch: bool = False
    git_dirty: bool = False


@dataclass(frozen=True)
class Definition:
    source: str
    preamble: str = ""
    context: Context = field(default_factory=Context)
    questions: dict[str, Question] = field(default_factory=dict)
    gates: list = field(default_factory=list)  # system1_gates.Gate


def path(section: dict) -> str:
    """Where the definition comes from for provider section *section* (``llm.system1``)."""
    raw = section.get("definition")
    if raw is not None:
        if not isinstance(raw, str) or not raw.strip():
            raise DefinitionError("llm.system1.definition must be a path")
        expanded = os.path.expanduser(raw.strip())
        if not os.path.isabs(expanded):
            raise DefinitionError(f"llm.system1.definition must be absolute or start with ~: {raw}")
        return expanded
    user = user_path()
    return user if os.path.isfile(user) else SHIPPED


def user_path() -> str:
    """The user's own definition file: ``system1.yaml`` beside the global config."""
    from nuh.config import get_global_config_path  # lazy import to avoid circular
    return os.path.join(os.path.dirname(get_global_config_path()), USER_FILE)


def load(section: dict, tool: str = "") -> Definition:
    """The definition for provider section *section* and calls of *tool*, read and checked.

    DefinitionError when it cannot be used.
    """
    where = path(section)
    return parse(read(where), where, tool)


def read(where: str) -> object:
    """The definition file *where*, parsed (JSON by its ``.json`` name, else YAML) but not yet checked."""
    try:
        with open(where, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise DefinitionError(f"cannot read {where}: {type(exc).__name__}: {exc}") from exc
    if where.endswith(".json"):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise DefinitionError(f"{where}: not JSON: {exc}") from exc
    import yaml  # lazy: the shipped definition is JSON, so a hook call using it never pays for it
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DefinitionError(f"{where}: not YAML: {exc}") from exc


def parse(data: object, source: str, tool: str = "") -> Definition:
    """The definition for calls of *tool* from its parsed file *data*; DefinitionError naming *source* when malformed.

    Every ``tools`` block is checked, merged onto the base, whichever *tool* is.
    """
    try:
        if not isinstance(data, dict):
            raise DefinitionError("a definition is a mapping")
        blocks = _blocks(data.get("tools"))
        base = {key: value for key, value in data.items() if key != "tools"}
        for name, block in blocks.items():
            try:
                _parse(merge(base, block), source, warn=False)
            except DefinitionError as exc:
                raise DefinitionError(f"tools.{name}: {exc}") from exc
        chosen = gating.match(tool, blocks) if tool else None
        return _parse(merge(base, blocks[chosen]) if chosen else base, source)
    except DefinitionError as exc:
        raise DefinitionError(f"{source}: {exc}") from exc


def _blocks(raw: object) -> dict[str, dict]:
    """The ``tools`` map: tool name or glob -> a block of the keys in _BLOCK_KEYS."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DefinitionError("tools must map tool names to definition blocks")
    for name, block in raw.items():
        if not isinstance(name, str) or not isinstance(block, dict):
            raise DefinitionError(f"tools.{name}: a block is a mapping")
        extra = sorted(set(block) - _BLOCK_KEYS)
        if extra:
            raise DefinitionError(f"tools.{name}: {', '.join(map(str, extra))} cannot be set per tool "
                                  f"(a block sets {', '.join(sorted(_BLOCK_KEYS))})")
    return raw


def merge(base: object, block: object) -> object:
    """*block* merged onto *base*: maps merge key by key, anything else replaces, and None removes the key."""
    if not isinstance(base, dict) or not isinstance(block, dict):
        return block
    out = dict(base)
    for key, value in block.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge(out[key], value) if key in out else value
    return out


def _parse(data: dict, source: str, *, warn: bool = True) -> Definition:
    for key in sorted(set(data) - _TOP_KEYS) if warn else ():
        sys.stderr.write(f"nuh: system1: {source}: unknown key {key!r} ignored\n")
    preamble = data.get("preamble", "")
    if not isinstance(preamble, str):
        raise DefinitionError("preamble must be text")
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, dict) or not raw_questions:
        raise DefinitionError("questions must name at least one question")
    questions = {str(name): question(str(name), raw) for name, raw in raw_questions.items()}
    gates = system1_gates.parse(data.get("gates"))
    return Definition(source, preamble.strip(), _context(data.get("context"), source, warn), questions, gates)


def _context(raw: object, source: str, warn: bool = True) -> Context:
    if raw is None:
        return Context()
    if not isinstance(raw, dict):
        raise DefinitionError("context must be a mapping")
    values: dict = {}
    for key, value in raw.items():
        if key in _CONTEXT_INTS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DefinitionError(f"context.{key} must be a whole number, 0 or more")
            values[key] = value
        elif key in _CONTEXT_BOOLS:
            values[key] = _bool(f"context.{key}", value)
        elif key == "git":
            values.update(_git(value))
        elif not warn:
            continue
        elif key in _UNSUPPORTED_CONTEXT:
            if value not in (False, None, "none", 0):
                sys.stderr.write(f"nuh: system1: {source}: context.{key} is not supported yet; ignored\n")
        else:
            sys.stderr.write(f"nuh: system1: {source}: unknown key context.{key!r} ignored\n")
    return Context(**values)


def _bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise DefinitionError(f"{name} must be true or false")
    return value


def _git(raw: object) -> dict[str, bool]:
    """``context.git``: which git items to send (branch, dirty)."""
    if not isinstance(raw, dict):
        raise DefinitionError("context.git must be a mapping of branch / dirty to true or false")
    unknown = sorted(set(raw) - set(_GIT_ITEMS))
    if unknown:
        raise DefinitionError(f"context.git: unknown item {unknown[0]!r} (items: {', '.join(_GIT_ITEMS)})")
    return {_GIT_ITEMS[item]: _bool(f"context.git.{item}", value) for item, value in raw.items()}
