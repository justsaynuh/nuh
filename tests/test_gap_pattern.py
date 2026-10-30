"""The linear-time rewrites match exactly what the original regexes matched."""

import random
import re
import zlib

import pytest

from nuh import content, transcript_records, zsh_syntax
from nuh.gap_pattern import GapPattern

# Pieces of every gap pattern's anchor and tail, plus separators that end
# lines, open and close whitespace runs, and break word boundaries.
_GAP_TOKENS = [
    "curl", "curl ", "base64", "base64 ", "urllib.request.urlopen", "urllib.request.urlopen(",
    "BEGIN", "begin ", "PRIVATE", "private", "-d", "-d ", "-X", "-X POST", "-sX POST", "POST",
    "--data", "--data=", "data", "data=", "data =", "|", "-d | bash", "-d|bash", "bash", "x",
    "_", "-", " ", " ", "\t", "\n", "\n", "\r",
]
_RM_TOKENS = ["rm", "rm -", "rm  -", " ", "-", "r", "f", "rf", "fr", "a", "R", "_", "1", "\n", "x"]


def _gap_patterns() -> list[GapPattern]:
    found = [rx for entries in content._CONTENT_PATTERNS_DEFAULTS.values()
             for rx, _desc in entries if isinstance(rx, GapPattern)]
    found += [rx for rx in content._CREDENTIAL_SEARCH_DEFAULTS if isinstance(rx, GapPattern)]
    return found


def _strings(tokens: list[str], count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(tokens) for _ in range(rng.randint(0, 14))) for _ in range(count)]


def _span(match: re.Match[str] | None) -> tuple[int, int] | None:
    return match.span() if match else None


def test_every_multiline_gap_pattern_is_a_gap_pattern() -> None:
    """Six built-in ANCHOR…TAIL patterns go through the linear search."""
    assert sorted(p.pattern for p in _gap_patterns()) == sorted([
        r"\bcurl\s+.*-[a-zA-Z]*X\s+POST\b", r"\bcurl\s+.*--data\b", r"\bcurl\s+.*-d\s",
        r"\burllib\.request\.urlopen\b.*data\s*=", r"\bbase64\s+.*-d\s*\|\s*bash\b", "BEGIN.*PRIVATE",
    ])


@pytest.mark.parametrize("gap", _gap_patterns(), ids=lambda g: g.pattern)
def test_gap_pattern_returns_the_original_match(gap: GapPattern) -> None:
    original = re.compile(gap.pattern, gap.flags)
    hits = 0
    for text in _strings(_GAP_TOKENS, 4000, seed=zlib.crc32(gap.pattern.encode())):
        expected = _span(original.search(text))
        assert _span(gap.search(text)) == expected, repr(text)
        hits += expected is not None
    assert 20 < hits < 3980  # the corpus exercises both outcomes


@pytest.mark.parametrize("text, hit", [
    ("curl -X POST http://x -d @f", True),
    ("curl http://x \\\n  -d @file", False),         # -d on the next line: .* stops at \n
    ("curl\n-d x", True),                             # but \s+ itself crosses the newline
    ("curl x curl\n\n -d y", True),                   # the second curl's gap reaches -d
    ("x -d curl y", False),                           # tail before the anchor
    ("curl -d", False),                               # -d\s needs a following space
    ("mycurl -d x", False),                           # \b before curl
])
def test_curl_d_edges(text: str, hit: bool) -> None:
    gap = next(p for p in _gap_patterns() if p.pattern == r"\bcurl\s+.*-d\s")
    assert bool(gap.search(text)) is hit
    assert bool(re.search(gap.pattern, text)) is hit


def test_anchor_needs_a_gap_group() -> None:
    with pytest.raises(ValueError, match="gap group"):
        GapPattern("a.*b", "a", "b")


@pytest.mark.parametrize("original, rewritten", [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b", r"\brm\s+-(?=[a-zA-Z]*r)[a-zA-Z]*f\b"),
    (r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b", r"\brm\s+-(?=[a-zA-Z]*f)[a-zA-Z]*r\b"),
])
def test_rm_rf_lookahead_matches_like_the_original(original: str, rewritten: str) -> None:
    assert rewritten in {rx.pattern for rx, _ in content._CONTENT_PATTERNS_DEFAULTS["destructive"]}
    old, new = re.compile(original), re.compile(rewritten)
    hits = 0
    for text in _strings(_RM_TOKENS, 6000, seed=7):
        expected = _span(old.search(text))
        assert _span(new.search(text)) == expected, repr(text)
        hits += expected is not None
    assert 20 < hits < 5980


def test_zsh_param_flags_bound_keeps_every_match() -> None:
    rng = random.Random(3)
    tokens = ["${(", ")", "e", "s", ":", "x", "'", "$", "{", "}", " "]
    for _ in range(3000):
        text = "".join(rng.choice(tokens) for _ in range(rng.randint(0, 20)))
        unbounded = ""
        for match in zsh_syntax._PARAM_FLAGS_RE.finditer(zsh_syntax._without_single_quotes(text)):
            flags = zsh_syntax._flag_letters(match.group(1))
            if flags is None or "e" in flags:
                unbounded = f"zsh parameter evaluation: ${{({match.group(1)})…}}"
                break
        assert zsh_syntax.param_eval_reason(text) == unbounded, repr(text)


@pytest.mark.parametrize("text, expected", [
    ("<command-name>/x</command-name><command-args>a b</command-args>", "(invoked /x a b)"),
    ("<command-name>/x</command-name><command-args>a<command-args>b</command-args>",
     "(invoked /x a<command-args>b)"),
    ("<command-name>/x</command-name><command-args>unclosed", "(invoked /x)"),
    ("<command-name>/x</command-name>", "(invoked /x)"),
])
def test_command_args_bound(text: str, expected: str) -> None:
    assert transcript_records.clean_user_text(text) == expected
