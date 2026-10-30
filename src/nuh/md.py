"""Markdown-safe text for nuh's user-facing notices (the hook's ``systemMessage``).

Claude clients render a hook's systemMessage as inline Markdown: emphasis,
code, strikethrough, ``[text](url)`` links and bare ``https://`` URLs all
take effect on the Claude mobile app. A notice carries text nuh did not
write (commands, paths, hosts, script names, the LLM reviewer's reasoning),
so each such value goes through one of the two functions here and nothing
else reaches the message:

- ``code_span(text)``: the value as one inline code span. Nothing inside a
  code span is parsed, in CommonMark, GFM or linkify-style renderers: no
  emphasis, link, autolink or HTML forms there.
- ``escape(text)``: untrusted prose. The ASCII punctuation that can form
  Markdown inside a line is backslash-escaped: ``\\ ` * _ ~ [ ] < > &``.
  ``( ) | # !`` are not: ``(`` opens a link destination only right after
  ``]`` and ``!`` an image only right before ``[``, both always escaped; a
  ``|`` table needs a second line, and the result is one line; ``#`` (like
  a list or quote marker) starts a block only at the start of a line, where
  the text's first character is escaped. Auto-links are defanged by setting every
  link-shaped word (one holding ``://``, ``@``, or a dotted name such as
  ``www.evil.example``) in a code span of its own, which reads naturally
  and holds on every renderer; a backslash before a colon or a dot does
  not, since linkify-style renderers look for links after escapes are
  resolved. Any other word holding code-like punctuation (a path, a flag,
  ``snake_case``) is set in a code span too, and backtick spans the text
  already holds stay code spans.

Both make hidden characters visible (``hidden_chars.visible``) and keep the
result on one line: Claude Code shows each line of a systemMessage as its
own notice.
"""

import re

from nuh import hidden_chars

NEWLINE = "⏎"       # stands for a line break inside a code span
EMPTY = "(empty)"   # code_span of empty text

# \v, \f, \x1c-\x1e and \x85 also break lines, but they are hidden characters: visible() escapes them first.
_LINE_BREAK = re.compile(r"\r\n|[\n\r  ]")
_SPACE = re.compile(r"\s+")
_BACKTICKS = re.compile(r"`+")
_ESCAPED = frozenset("\\`*_[]<>~&")  # & too: a numeric character reference can spell any character
_CODELIKE = _ESCAPED | frozenset("/$={}@:()|")
_DOTTED = re.compile(r"[^\W_][.。．｡][^\W\d_]")  # a dot between a letter or digit and a letter: evil.example
_LEAD = "([{<\"'"
_TRAIL = ".,;:!?)]}>\"'"
_BLOCK_START = re.compile(r"[-+=#]|\d+[.)]")


def code_span(text: str) -> str:
    """*text* as one CommonMark inline code span, on one line.

    The fence is one backtick longer than the longest backtick run in the
    text, and a space pads each side when the text starts or ends with a
    backtick or a space (CommonMark strips one space from each side when
    both are there). A line break shows as ⏎; empty text as ``(empty)``.
    """
    body = _LINE_BREAK.sub(NEWLINE, hidden_chars.visible(text))
    if not body:
        return EMPTY
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(body)), default=0) + 1)
    pad = " " if (body[0] in "` " or body[-1] in "` ") and body.strip(" ") else ""
    return f"{fence}{pad}{body}{pad}{fence}"


def escape(text: str) -> str:
    """Untrusted prose *text* as inline Markdown that renders as the text, on one line (see the module doc)."""
    flat = _SPACE.sub(" ", _LINE_BREAK.sub(" ", hidden_chars.visible(text))).strip()
    pieces: list[tuple[bool, str]] = []  # (is code, raw text)
    for is_code, part in _code_parts(flat):
        if is_code:
            pieces.append((True, part))
        else:
            pieces.extend(_words(part))
    rendered = "".join(code_span(raw) if is_code else _escape_chars(raw) for is_code, raw in _merged(pieces))
    start = _BLOCK_START.match(rendered)
    if start:
        # Escape what would start a list, heading, heading underline or ordered list at a line start.
        cut = start.end() - 1
        rendered = f"{rendered[:cut]}\\{rendered[cut:]}"
    return rendered


def _code_parts(text: str) -> list[tuple[bool, str]]:
    """*text* split into prose and the content of its CommonMark code spans: [(is code, text)]."""
    parts: list[tuple[bool, str]] = []
    pos = 0
    prose_from = 0
    while True:
        opener = _BACKTICKS.search(text, pos)
        if opener is None:
            break
        closer = next((m for m in _BACKTICKS.finditer(text, opener.end())
                       if len(m.group()) == len(opener.group())), None)
        if closer is None:
            # An unmatched backtick run is literal text; later runs may still pair up.
            pos = opener.end()
            continue
        parts.append((False, text[prose_from:opener.start()]))
        content = text[opener.end():closer.start()]
        if content.startswith(" ") and content.endswith(" ") and content.strip(" "):
            content = content[1:-1]
        parts.append((True, content))
        pos = prose_from = closer.end()
    parts.append((False, text[prose_from:]))
    return [(is_code, part) for is_code, part in parts if part]


def _words(prose: str) -> list[tuple[bool, str]]:
    """*prose* as pieces: each code-like or link-shaped word's core is code, everything else prose."""
    pieces: list[tuple[bool, str]] = []
    for i, word in enumerate(prose.split(" ")):
        if i:
            pieces.append((False, " "))
        core = word.lstrip(_LEAD)
        lead = word[:len(word) - len(core)]
        stripped = core.rstrip(_TRAIL)
        trail = core[len(stripped):]
        if _code_like(stripped):
            pieces.extend([(False, lead), (True, stripped), (False, trail)])
        else:
            pieces.append((False, word))
    return [(is_code, raw) for is_code, raw in pieces if raw]


def _code_like(word: str) -> bool:
    """True when *word* reads as code or could auto-link, so it belongs in a code span."""
    if not any(c.isalnum() for c in word):
        return False
    return word.startswith("-") or any(c in _CODELIKE for c in word) or bool(_DOTTED.search(word))


def _merged(pieces: list[tuple[bool, str]]) -> list[tuple[bool, str]]:
    """*pieces* with adjacent code pieces joined: two spans side by side would fuse into one wrong span."""
    merged: list[tuple[bool, str]] = []
    for is_code, raw in pieces:
        if merged and is_code and merged[-1][0]:
            merged[-1] = (True, merged[-1][1] + raw)
        else:
            merged.append((is_code, raw))
    return merged


def _escape_chars(text: str) -> str:
    return "".join(f"\\{c}" if c in _ESCAPED else c for c in text)
