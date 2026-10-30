r"""Linear-time search for ``ANCHOR … TAIL`` regexes.

A regex such as ``\bcurl\s+.*-d\s`` backtracks quadratically on one long line
that repeats its anchor: after a failed attempt at one ``curl``, the search
starts again at the next ``curl`` and rescans the rest of the line. A crafted
1 MiB line takes tens of seconds, enough to time the hook out.

``GapPattern`` answers the same question in one forward pass. It walks the
anchors in order, keeps the leftmost tail found so far, and only searches for a
new tail once the anchors have moved past it. When an anchor has a tail inside
its reach, the original regex is matched from that anchor, so callers get the
exact ``re.Match`` the plain ``search`` would have returned.
"""

from __future__ import annotations

import re


class GapPattern:
    r"""``ANCHOR`` + gap + ``TAIL`` where the gap is ``\s+.*`` or ``.*`` (no DOTALL).

    *pattern* is the equivalent single regex. Its source string is
    ``self.pattern`` (config suppression compares against it) and its match is
    what ``search`` returns. *anchor* must define group 1: the whitespace run
    that opens the gap (``(\s+)``), or an empty group (``()``) when the gap is a
    bare ``.*``. *tail* is the regex that follows the gap.

    Why one pass is exact: ``.*`` cannot cross a newline, so a tail is in reach
    of an anchor exactly when it starts after the gap's first character and no
    later than the end of the line the gap's whitespace run ends on.
    """

    def __init__(self, pattern: str, anchor: str, tail: str, flags: int = 0) -> None:
        self._full = re.compile(pattern, flags)
        self._anchor = re.compile(anchor, flags)
        self._tail = re.compile(tail, flags)
        if self._anchor.groups < 1:
            raise ValueError(f"GapPattern anchor needs a gap group: {anchor!r}")
        self.pattern = pattern
        self.flags = self._full.flags

    def __repr__(self) -> str:
        return f"GapPattern({self.pattern!r})"

    def search(self, string: str) -> re.Match[str] | None:
        """The match ``re.search(self.pattern, string)`` returns, in linear time."""
        size = len(string)
        tail_from, tail_at = 0, -1  # leftmost tail start >= tail_from
        eol_from, eol = 0, -1       # first newline (or size) at or after eol_from
        pos = 0
        while (anchor := self._anchor.search(string, pos)) is not None:
            gap_end = anchor.end(1)
            reach_lo = min(anchor.start(1) + 1, gap_end)
            if not eol_from <= gap_end <= eol:
                eol_from = gap_end
                eol = string.find("\n", gap_end)
                eol = size if eol < 0 else eol
            if not tail_from <= reach_lo <= tail_at:
                tail = self._tail.search(string, reach_lo)
                if tail is None:
                    return None
                tail_from, tail_at = reach_lo, tail.start()
            if tail_at <= eol:
                found = self._full.match(string, anchor.start())
                if found is not None:
                    return found
            pos = anchor.start() + 1
        return None
