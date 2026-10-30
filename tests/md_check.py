"""A CommonMark-subset reader for nuh's one-line notices: what a Markdown renderer would make of them.

It reads one line of inline Markdown the way CommonMark does for the parts
that matter here: a backslash before ASCII punctuation makes it literal, a
backtick run opens a code span that only an equal run closes (nothing
inside is parsed), and everything else is text. What a renderer could turn
into formatting is collected as "active" runs: emphasis and strike
delimiters, link and image brackets, raw HTML and autolink angle brackets,
and character references. Some punctuation is active only where CommonMark
and GFM give it meaning on one line: ``(`` right after an unescaped ``]``
(a link destination), ``!`` right before an unescaped ``[`` (an image). A
``|`` forms a table only with a delimiter row on the next line, and ``#``,
``>``, list and ordered-list markers only at a line start; ``problems``
flags any line break and any block start. Text that an autolinker (GFM's
extended autolinks, linkify) would turn into a link is caught by
``LINK_SHAPED`` on the text outside code spans.
"""

import re
import string
from dataclasses import dataclass, field

from nuh import hidden_chars

_PUNCT = frozenset(string.punctuation)
_ACTIVE = frozenset("*_~[]<>&")
LINK_SHAPED = re.compile(r"://|www[.。．｡]|@|[^\W_][.。．｡][^\W\d_]", re.IGNORECASE)
_SPAN = "\0"  # stands for a code span in Inline.text: an autolink cannot run through one


@dataclass
class Inline:
    text: str = ""                                 # outside code spans, escapes resolved
    spans: list[str] = field(default_factory=list)  # each code span's content
    active: list[str] = field(default_factory=list)  # unescaped Markdown-active runs outside code spans


def read(line: str) -> Inline:
    """What a CommonMark renderer makes of *line* (see the module doc)."""
    out = Inline()
    text: list[str] = []
    i, n = 0, len(line)
    after_close = False  # the previous token was an unescaped "]"
    while i < n:
        char = line[i]
        closes, after_close = after_close, False
        if char == "\\" and i + 1 < n and line[i + 1] in _PUNCT:
            text.append(line[i + 1])
            i += 2
            continue
        run = _run(line, i)
        if (char == "(" and closes) or (char == "!" and line[i + 1:i + 2] == "["):
            out.active.append(char)  # a link destination after "]", an image before "["
            text.append(char)
            i += 1
            continue
        if char == "`":
            close = _closing(line, i + len(run), len(run))
            if close < 0:
                out.active.append(run)  # an unmatched backtick run: literal, but nuh never emits one
                text.append(run)
            else:
                out.spans.append(_span_content(line[i + len(run):close]))
                text.append(_SPAN)
                i = close
            i += len(run)
            continue
        if char in _ACTIVE:
            out.active.append(run)
            text.append(run)
            i += len(run)
            after_close = char == "]"
            continue
        text.append(char)
        i += 1
    out.text = "".join(text)
    return out


def _run(line: str, i: int) -> str:
    j = i
    while j < len(line) and line[j] == line[i]:
        j += 1
    return line[i:j]


def _closing(line: str, start: int, length: int) -> int:
    """Index of the backtick run of exactly *length* that closes a code span opened before *start*, or -1."""
    i = start
    while i < len(line):
        if line[i] == "`":
            run = _run(line, i)
            if len(run) == length:
                return i
            i += len(run)
        else:
            i += 1
    return -1


def _span_content(raw: str) -> str:
    if raw.startswith(" ") and raw.endswith(" ") and raw.strip(" "):
        return raw[1:-1]
    return raw


def problems(message: str, decoration: list[str]) -> list[str]:
    """Everything in *message* that renders as more than its text, beyond nuh's own *decoration* runs."""
    found = []
    if any(c in message for c in "\n\r  \x85\v\f"):
        found.append("line break")
    if hidden_chars.find(message) >= 0:
        found.append(f"hidden character at {hidden_chars.find(message)}")
    if message[:1] in ("-", "+", "=", "#", ">", " ") or re.match(r"\d+[.)]", message):
        found.append("block start")
    inline = read(message)
    if inline.active != decoration:
        found.append(f"markup {inline.active!r}, expected {decoration!r}")
    link = LINK_SHAPED.search(inline.text)
    if link:
        found.append(f"link-shaped text {inline.text[max(0, link.start() - 10):link.end() + 10]!r}")
    return found
