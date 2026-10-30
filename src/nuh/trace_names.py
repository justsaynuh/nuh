"""LLM trace directory names: ``<UTC timestamp>_<tool_use_id>``, sortable by time."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

_UNSAFE_CALL_ID = re.compile(r"[^A-Za-z0-9_-]")
_CALL_ID_MAX = 64
_STAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"
# <stamp>_<call id, or a uuid4>, with an 8-hex suffix after a name collision (dir_name).
_NAME = re.compile(r"(\d{8}T\d{6}\.\d{6}Z)_([A-Za-z0-9_-]{1,%d})" % (_CALL_ID_MAX + 9))
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_COLLISION = re.compile(r"(.+)-[0-9a-f]{8}")


@dataclass(frozen=True)
class Name:
    """A parsed trace directory name."""

    stamp: datetime  # when the trace was created (aware, UTC)
    call: str        # the tool call ID ("" when the payload had none), without a collision suffix


def call_id(payload: dict) -> str:
    """The payload's tool_use_id made safe for a directory name ("" when absent or unusable)."""
    value = payload.get("tool_use_id")
    if not isinstance(value, str):
        return ""
    return _UNSAFE_CALL_ID.sub("", value)[:_CALL_ID_MAX]


def dir_name(call_id: str = "", attempt: int = 0) -> str:
    """Sortable trace directory name: ``<UTC ts>_<tool_use_id>``, or ``<UTC ts>_<uuid4>``.

    The tool call ID joins a trace to its log line and transcript entry. A
    retry after a name collision keeps the ID and adds a short random suffix.
    """
    stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)
    if not call_id:
        return f"{stamp}_{uuid.uuid4()}"
    if attempt == 0:
        return f"{stamp}_{call_id}"
    return f"{stamp}_{call_id}-{uuid.uuid4().hex[:8]}"


def looks_like(name: str) -> bool:
    """*name* has a trace name's shape (no date check: cheap enough for every entry of a large root)."""
    return _NAME.fullmatch(name) is not None


def parse(name: str) -> Name | None:
    """*name* as a trace directory name, or None when nuh would not have written it."""
    match = _NAME.fullmatch(name)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match[1], _STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return Name(stamp, _call(match[2]))


def call_of(name: str) -> str:
    """The tool call ID in trace name *name* ("" for none, or not a trace name), without parsing its date."""
    match = _NAME.fullmatch(name)
    return _call(match[2]) if match else ""


def _call(rest: str) -> str:
    if _UUID.fullmatch(rest):
        return ""
    collision = _COLLISION.fullmatch(rest)
    return collision[1] if collision else rest
