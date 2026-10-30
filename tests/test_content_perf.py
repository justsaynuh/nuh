"""Content patterns stay linear on input crafted to make regexes backtrack.

A hook that runs out of time does not block, so a slow pattern is a bypass.
Before the rewrite, one 256 KiB line of repeated ``base64 `` took 2.3 s in a
single pattern and ``rm -`` + a long flag run took minutes. Every built-in
content and credential pattern must now get through all of these inputs (each
1 MiB single line plus a 4 MiB file) in well under a second.
"""

import time

import pytest

from nuh import content

MIB = 1024 * 1024
# Per pattern, over all inputs below. Linear scans take ~0.1 s here; the old
# quadratic patterns took minutes, so the bound has room for slow CI machines.
BOUND_S = 1.0

# Each repeated to fill one line: anchors with no tail, and partial tails.
_TRIGGERS = [
    "curl ", "curl -", "base64 -d |", "urllib.request.urlopen ", "rm -", "BEGIN",
    "sk-", "api_key='", "-----BEGIN ",
]


def _line(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


@pytest.fixture(scope="module")
def adversarial() -> list[str]:
    inputs = [_line(t, MIB) for t in _TRIGGERS]
    inputs.append("rm -" + "rf" * (MIB // 2) + "_")   # every split of the flag run fails
    inputs.append("sk-" + "a" * MIB + "_")
    lines = [_line(t, 64 * 1024) for t in _TRIGGERS]
    file_text = "\n".join(lines[i % len(lines)] for i in range(64))  # 4 MiB, 64 lines
    assert len(file_text) >= content.MAX_SCAN_CHARS - 64
    inputs.append(file_text)
    return inputs


def _all_patterns() -> list[tuple[str, content.Searcher]]:
    found = [(desc, rx) for entries in content._CONTENT_PATTERNS_DEFAULTS.values() for rx, desc in entries]
    found += [(rx.pattern, rx) for rx in content._CREDENTIAL_SEARCH_DEFAULTS]
    return found


@pytest.mark.parametrize("desc, regex", _all_patterns(), ids=[d for d, _ in _all_patterns()])
def test_pattern_is_linear_on_adversarial_input(adversarial: list[str], desc: str,
                                                regex: content.Searcher) -> None:
    started = time.perf_counter()
    for text in adversarial:
        regex.search(text)
    elapsed = time.perf_counter() - started
    assert elapsed < BOUND_S, f"{desc}: {elapsed:.2f}s on adversarial input"


def test_scan_content_on_a_4_mib_adversarial_file(adversarial: list[str]) -> None:
    started = time.perf_counter()
    content.scan_content(adversarial[-1])
    assert time.perf_counter() - started < 3 * BOUND_S
