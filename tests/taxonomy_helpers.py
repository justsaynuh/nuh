"""Shared by the taxonomy tests (test_taxonomy*.py)."""

from nuh.taxonomy import classify_tokens, get_builtin_table


_FULL = get_builtin_table("full")


def _ct(tokens: list[str]) -> str:
    """Classify tokens against the full built-in table."""
    return classify_tokens(tokens, builtin_table=_FULL)
