"""Decode ``$'…'`` ANSI-C quoted strings before ``shlex`` reads shell words.

``shlex`` does not know ``$'…'``: it reads ``$'\\x72m'`` as the word
``$\\x72m`` and fails outright on ``$'it\\'s'``. The shell decodes the escapes
first, so ``$'\\x72m' -rf ~`` runs ``rm``. :func:`requote` rewrites each
unquoted ``$'…'`` as an ordinary single-quoted word holding the decoded text,
and each bash ``$"…"`` locale string as the double-quoted string it reads.

Only escapes bash and zsh decode identically are accepted. One the two
shells read differently (``\\cX``, an unknown escape such as ``\\m``, ``\\x``
without digits, a NUL byte) raises ``ValueError``, which callers treat like
any other unparseable stage: ask.
"""

from __future__ import annotations

import shlex

from nuh.shell_heredoc import escaped_quote_close

_SIMPLE = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
    "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?",
}
_OCTAL = "01234567"
_HEX = "0123456789abcdefABCDEF"
# Escape letter -> (digit alphabet, max digits, emits a code point not a byte)
_NUMERIC = {"x": (_HEX, 2, False), "u": (_HEX, 4, True), "U": (_HEX, 8, True)}


def requote(text: str) -> str:
    """*text* with every unquoted ``$'…'`` replaced by an equivalent ``'…'`` word, and ``$"…"`` by ``"…"``.

    Text inside ``'…'`` and ``"…"`` is copied unchanged (``$'`` there is not
    ANSI-C quoting). Raises ``ValueError`` for an unterminated ``$'`` or an
    escape bash and zsh decode differently.

    ``$"…"`` becomes the stricter of its two readings. Bash looks the string
    up in the locale's message catalog and, finding no translation (nuh
    cannot know any), reads a double-quoted string: ``$"$HOME/.ssh"`` names
    ``~/.ssh``. Zsh reads a literal ``$`` before the same double-quoted
    string. That word is bash's with a ``$`` in front: it names no command,
    and it is relative (``$/home/…``) where bash's is absolute, so it never
    reaches anything bash's word does not.
    """
    if "$'" not in text and '$"' not in text:
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" or text.startswith("$$", i):
            # An escape, or the parameter $$: in `$$'x'` and `$$"x"` the quote
            # after it opens an ordinary string.
            out.append(text[i:i + 2])
            i += 2
        elif c == "$" and text.startswith('"', i + 1):
            i += 1  # bash's reading: the double-quoted string that follows
        elif c == "'":
            close = text.find("'", i + 1)
            end = n if close < 0 else close + 1
            out.append(text[i:end])
            i = end
        elif c == '"':
            close = escaped_quote_close(text, i + 1, n, '"')
            end = n if close < 0 else close + 1
            out.append(text[i:end])
            i = end
        elif c == "$" and text.startswith("'", i + 1):
            close = escaped_quote_close(text, i + 2, n, "'")
            if close < 0:
                raise ValueError("unterminated $'…' string")
            out.append(shlex.quote(decode(text[i + 2:close])))
            i = close + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def decode(body: str) -> str:
    """Decode the text between ``$'`` and ``'`` as bash and zsh both would."""
    data = bytearray()
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\":
            data += c.encode("utf-8", "surrogatepass")
            i += 1
            continue
        if i + 1 >= n:
            raise ValueError("trailing backslash in $'…'")
        i = _escape(body, i + 1, data)
    if 0 in data:
        raise ValueError("NUL byte in $'…' (bash truncates, zsh keeps it)")
    # Invalid UTF-8 from \xHH bytes becomes U+FFFD: such a word never names a
    # known command or a protected path, so it classifies no less strictly.
    return data.decode("utf-8", "replace")


def _escape(body: str, i: int, data: bytearray) -> int:
    """Append the escape starting at body[i] (just past the backslash); return the next index."""
    letter = body[i]
    if letter in _SIMPLE:
        data += _SIMPLE[letter].encode()
        return i + 1
    if letter in _OCTAL:
        digits = _digits(body, i, _OCTAL, 3)
        value = int(digits, 8)
        if value > 0xFF:
            raise ValueError(f"octal escape \\{digits} in $'…' is wider than a byte")
        data.append(value)
        return i + len(digits)
    if letter in _NUMERIC:
        alphabet, limit, code_point = _NUMERIC[letter]
        digits = _digits(body, i + 1, alphabet, limit)
        if not digits:
            raise ValueError(f"\\{letter} without digits in $'…'")
        value = int(digits, 16)
        if not code_point:
            data.append(value)
        elif value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            raise ValueError(f"invalid code point \\{letter}{digits} in $'…'")
        else:
            data += chr(value).encode("utf-8")
        return i + 1 + len(digits)
    raise ValueError(f"escape \\{letter} in $'…' decodes differently in bash and zsh")


def _digits(body: str, i: int, alphabet: str, limit: int) -> str:
    """Up to *limit* characters of *alphabet* starting at *i*."""
    j = i
    while j < len(body) and j - i < limit and body[j] in alphabet:
        j += 1
    return body[i:j]
