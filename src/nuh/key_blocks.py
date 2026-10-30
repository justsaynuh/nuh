"""Private key blocks for redaction: a real key goes whole, anything else between its markers stays.

Redaction must fail in neither direction. A key block is read line by line
(real line breaks or the ``\\n`` escapes of a key kept on one line in JSON or
.env), and it is replaced by one placeholder only when every line is what a
key holds. Otherwise only its markers and the base64 lines right after them
go, and every other line stays visible: text planted between fake markers
(``sudo mv …``, a heredoc ``dd`` line) cannot hide there.

PEM and PGP armor (``-----BEGIN … PRIVATE KEY[ BLOCK]-----``): header lines
(``Name: value``, as ``Proc-Type``, ``DEK-Info``, ``Version``, ``Comment``) only
directly after BEGIN, then an optional blank line, then base64 lines of one
width (40 or more chars) with a shorter last line and an optional PGP
``=XXXX`` checksum, then END. The base64 adds up to a multiple of 4 chars.
A header value holds no shell syntax (``; & | ` $ < >``), so a header cannot
carry a command either. Without a valid body and END, the BEGIN marker and
the base64 lines (each on its own line) after the headers are replaced, and
the headers stay; a lone END marker is replaced too.

PuTTY .ppk: the ``PuTTY-User-Key-File-N: <type>`` line, then only the known
fields, with base64 lines accepted exactly as counted by ``Public-Lines: N``
and ``Private-Lines: N``, ending at ``Private-MAC: <hex>``. ``Comment:`` runs to
the end of its line (``Alice's laptop``, ``alice@host (work)``, ``key #2``) but
holds no shell syntax. Anything else (an unknown ``run:`` line, a bare
``reboot``) ends the walk: then the header line and each counted base64 run
are replaced, and every field and other line stays.

Linear: each regex runs in one pass from a marker, and a walk stops at the
first line that does not belong, so markers repeated many times cost a line each.
"""

from __future__ import annotations

import re

KEY_DESC = "private key"
PPK_DESC = "PuTTY private key"

_HSPACE = r"[ \t]*"
_EOL = r"(?:\r?\n|(?:\\r)?\\n)"  # one line break, real or \n-escaped
_NL = rf"{_HSPACE}{_EOL}{_HSPACE}"
_SAFE_VALUE = r"[^\r\n\\;&|`$<>]*"  # rest of the line, without shell syntax

# --- PEM / PGP ---
_LABEL = r"\s+(?:[A-Z0-9]+\s+)*PRIVATE\s+KEY(?:\s+BLOCK)?-----"
_HEADER = rf"[A-Za-z][A-Za-z0-9-]*: {_SAFE_VALUE}(?={_HSPACE}{_EOL})"
_BODY_SEP = rf"(?:{_NL}|[ \t]+)"  # key material may also be space-joined on one line
_PEM_RE = re.compile(
    rf"-----BEGIN{_LABEL}"
    rf"(?P<headers>(?:{_NL}{_HEADER})*(?:{_NL}(?={_HSPACE}{_EOL}))?)"
    rf"(?P<body>(?:{_BODY_SEP}[A-Za-z0-9+/=]+)*)"
    rf"(?P<end>{_BODY_SEP}?-----END{_LABEL})?"
)
_PEM_END_RE = re.compile(rf"-----END{_LABEL}")
_BODY_SEP_RE = re.compile(_BODY_SEP)
_B64_LINE_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_PGP_CHECKSUM_RE = re.compile(r"=[A-Za-z0-9+/]{4}")
_MIN_WIDTH = 40
# Base64 lines of 40+ chars, each on its own line, right after the marker or headers.
_PEM_RUN_RE = re.compile(rf"(?:{_NL}[A-Za-z0-9+/]{{{_MIN_WIDTH},}}={{0,2}}(?={_HSPACE}(?:{_EOL}|$)))+")
_NL_RE = re.compile(_NL)

# --- PuTTY ---
_PPK_HEADER_RE = re.compile(r"PuTTY-User-Key-File-[23]: *[a-z0-9-]+")
_LINE_RE = re.compile(rf"{_NL}(?P<line>(?:[^\r\n\\]|\\(?![rn]))*)")
_PPK_FIELD_RE = re.compile(
    r"(Encryption|Comment|Public-Lines|Private-Lines|Private-MAC|Private-Hash|Key-Derivation"
    r"|Argon2-Memory|Argon2-Passes|Argon2-Parallelism|Argon2-Salt): (.*)")
_PPK_PLAIN_VALUE_RE = re.compile(r"[A-Za-z0-9-]+")
_SAFE_VALUE_RE = re.compile(_SAFE_VALUE)
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_PPK_FULL_LINE_RE = re.compile(r"[A-Za-z0-9+/]{64}")
_PPK_LAST_LINE_RE = re.compile(r"[A-Za-z0-9+/]{1,64}={0,2}")
_PPK_MAX_LINES = 1024


def redact_key_blocks(text: str) -> str:
    """*text* with PEM, PGP and PuTTY private keys replaced, and the markers of anything that only looks like one."""
    placeholder = f"[redacted: {KEY_DESC}]"
    text = _PEM_RE.sub(_pem_block, text)
    text = _PEM_END_RE.sub(placeholder, text)
    return _redact_ppk(text)


def _pem_block(match: re.Match[str]) -> str:
    placeholder = f"[redacted: {KEY_DESC}]"
    if match["end"] and _is_key_material(match["body"]):
        return placeholder
    # Not a whole key: the marker goes, the headers stay, and so does everything after the
    # base64 lines (the END marker there is replaced by its own pass).
    text = match.string
    headers = match["headers"]
    body_start = match.start("body")
    run = _PEM_RUN_RE.match(text, body_start)  # its lookahead sees where the line really ends
    rest = text[run.end() if run else body_start:match.end()]
    if not run:
        return placeholder + headers + rest
    if not headers:
        return placeholder + rest
    sep = _NL_RE.match(text, run.start()).group()
    return placeholder + headers + sep + placeholder + rest


def _is_key_material(body: str) -> bool:
    """Whether *body* is the base64 of a key: one line width of 40+, a shorter last line, optional checksum."""
    tokens = [token for token in _BODY_SEP_RE.split(body) if token]  # split, not findall: "\\n" holds an n
    if len(tokens) > 1 and _PGP_CHECKSUM_RE.fullmatch(tokens[-1]):
        tokens.pop()
    if not tokens or not all(_B64_LINE_RE.fullmatch(t) for t in tokens):
        return False
    if any("=" in t for t in tokens[:-1]):
        return False  # padding only ends the last line
    width = len(tokens[0])
    if width < _MIN_WIDTH or any(len(t) != width for t in tokens[1:-1]) or len(tokens[-1]) > width:
        return False
    return sum(len(t) for t in tokens) % 4 == 0


def _redact_ppk(text: str) -> str:
    placeholder = f"[redacted: {PPK_DESC}]"
    spans: list[tuple[int, int]] = []
    for header in _PPK_HEADER_RE.finditer(text):
        if spans and header.start() < spans[-1][1]:
            continue  # inside the block just replaced
        spans.extend(_ppk_spans(text, header))
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(placeholder)
        last = end
    out.append(text[last:])
    return "".join(out)


def _ppk_spans(text: str, header: re.Match[str]) -> list[tuple[int, int]]:
    """The spans to replace for the .ppk starting at *header*: the whole file, or its header and counted runs."""
    runs: list[tuple[int, int]] = []
    safe = True  # every field fits, so a complete file goes whole
    pos = header.end()
    while line := _LINE_RE.match(text, pos):
        field = _PPK_FIELD_RE.fullmatch(line["line"].rstrip(" \t"))
        if not field:
            break
        name, value = field.groups()
        pos = line.end()
        if name.endswith("-Lines"):
            pos, run, complete = _counted_run(text, pos, value)
            if run:
                runs.append(run)
            if not complete:
                break
        elif name in ("Private-MAC", "Private-Hash"):
            mac = _HEX_RE.match(value)
            if mac:
                # The file ends with the hex: anything after it on the line stays (a pipe, a quote).
                end = line.start("line") + field.start(2) + mac.end()
                return [(header.start(), end)] if safe else [header.span(), *runs]
            break
        elif name == "Comment":
            safe = safe and bool(_SAFE_VALUE_RE.fullmatch(value))
        elif not _PPK_PLAIN_VALUE_RE.fullmatch(value):
            break
    return [header.span(), *runs]


def _counted_run(text: str, pos: int, value: str) -> tuple[int, tuple[int, int] | None, bool]:
    """Read the base64 lines a ``*-Lines: N`` field counts: (position after them, their span, all N read)."""
    if not value.isascii() or not value.isdigit() or int(value) > _PPK_MAX_LINES:
        return pos, None, False
    count = int(value)
    start = end = -1
    for index in range(count):
        line = _LINE_RE.match(text, pos)
        shape = _PPK_FULL_LINE_RE if index < count - 1 else _PPK_LAST_LINE_RE
        if not line or not shape.fullmatch(line["line"].rstrip(" \t")):
            return pos, (start, end) if start >= 0 else None, False
        if start < 0:
            start = line.start("line")
        end = pos = line.end()
    return pos, (start, end) if start >= 0 else None, True
