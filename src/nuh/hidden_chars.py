"""Characters that make displayed text differ from the text that runs (floor rule ``hidden_chars``).

Adapted from manuelschipper/nah (MIT),
crates/nah-proto/src/labels/hidden_characters.rs. Hidden are:

- C0 controls other than tab and line feed, DEL, and C1 controls;
- a carriage return followed by anything but a line feed (it returns the
  cursor so the rest overwrites the line); CRLF and a final CR are ordinary;
- bidi embeddings, overrides and isolates (U+202A–U+202E, U+2066–U+2069);
- the invisible format characters U+200B, U+2060, U+FEFF, U+180E, the soft
  hyphen U+00AD and the Hangul fillers U+115F, U+1160, U+3164, U+FFA0;
- Unicode tag characters (U+E0000–U+E007F) outside the subdivision flags
  of England, Scotland and Wales.

Joiners (U+200C, U+200D), variation selectors and the directional marks
LRM, RLM and ALM are ordinary text. A person approving a prompt, and an LLM
reviewing it, must see the text that runs, so the reason shows the escaped
form.
"""

RULE = "hidden_chars"
MARK = " holds hidden characters: "  # in every reason(); the escaped excerpt follows it

_TAG_BASE = "\U0001F3F4"
_RGI_FLAG_TAGS = (
    "\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",  # gbeng
    "\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",  # gbsct
    "\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F",  # gbwls
)
_SINGLE = frozenset("\u000b\u000c​⁠﻿᠎­ᅟᅠㅤﾠ")
_EXCERPT = 60


def _hidden(char: str) -> bool:
    code = ord(char)
    return (code <= 0x08 or 0x0E <= code <= 0x1F or 0x7F <= code <= 0x9F
            or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069
            or 0xE0000 <= code <= 0xE007F or char in _SINGLE)


def _next_hidden(text: str, i: int) -> int:
    """Index of the first hidden character in *text* at or after *i*, or -1."""
    n = len(text)
    while i < n:
        char = text[i]
        if char == _TAG_BASE:
            tags = next((t for t in _RGI_FLAG_TAGS if text.startswith(t, i + 1)), None)
            if tags:
                i += 1 + len(tags)
                continue
        if _hidden(char) or (char == "\r" and i + 1 < n and text[i + 1] != "\n"):
            return i
        i += 1
    return -1


def find(text: str) -> int:
    """Index of the first hidden character in *text*, or -1."""
    return _next_hidden(text, 0)


def visible(text: str) -> str:
    """*text* with every hidden character escaped (``\\u200b``); line feeds, tabs and CRLF stay as they are."""
    parts = []
    start = 0
    i = _next_hidden(text, 0)
    while i >= 0:
        parts.append(text[start:i])
        parts.append(_escape(text[i]))
        start = i + 1
        i = _next_hidden(text, start)
    parts.append(text[start:])
    return "".join(parts)


def _escape(char: str) -> str:
    code = ord(char)
    if char == "\n":
        return "\\n"
    if char == "\t":
        return "\\t"
    if char == "\r" or _hidden(char):
        return f"\\x{code:02x}" if code < 0x100 else (f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}")
    return char


def escaped_excerpt(text: str, index: int) -> str:
    """Up to ~60 characters of *text* around *index*, every hidden character escaped."""
    start = max(0, index - _EXCERPT // 2)
    end = min(len(text), start + _EXCERPT)
    body = "".join(_escape(c) for c in text[start:end])
    return f"{'…' if start else ''}{body}{'…' if end < len(text) else ''}"


def reason(text: str, what: str) -> str:
    """A floor reason for *text*, which must hold a hidden character, naming *what* it is."""
    index = find(text)
    return f"{what}{MARK}{escaped_excerpt(text, index)}"
