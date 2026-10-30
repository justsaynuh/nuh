"""Shared by the hint battery (test_hint_battery*.py)."""

from nuh.bash import classify_command
from nuh.bash_hint import build as _build_bash_hint


def _hint(cmd: str) -> tuple[str, str | None]:
    """Return (final_decision, hint) for a command."""
    result = classify_command(cmd)
    return result.final_decision, _build_bash_hint(result)
