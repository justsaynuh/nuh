"""The files curl reads through syntax inside an option's value, as curl(1) and its form parser read them.

``-d/--data/--data-binary/--json/-H/--header @FILE``, ``--data-urlencode
[name]@FILE``, ``-b FILE`` (a value with no ``=``), the certificate of ``-E
CERT[:PASSWORD]`` and the ``-F`` form files (``name=@FILE[;type=...]``,
``name=<FILE``, ``headers=@FILE``). The ``-F`` grammar follows curl's
``tool_formparse.c`` (``formparse``, ``get_param_part``, ``get_param_word``).
A path read out of a value keeps the shell quoting recorded for the value.
"""

from nuh import word_quoting

_BLANKS = " \t"
_TYPE_STOP = frozenset('()<>@,;:\\"[]?=\r\n ')  # what ends a type= value (no quoting)
_AT_FILE = frozenset({"d", "--data", "--data-binary", "H", "--header", "--json"})


def _piece(value: str, start: int, end: int | None = None) -> str:
    """``value[start:end]``, keeping the shell quoting recorded for *value*."""
    word = value[start:end]
    spelling = word_quoting.encoding(value)
    if word and word != value and spelling is not None:
        return word_quoting.spelled(word, spelling[start:end])
    return word


def _unescaped(value: str, start: int, end: int, escapes: str) -> str:
    r"""``value[start:end]`` with ``\`` before a character of *escapes* removed."""
    chars: list[str] = []
    i = start
    while i < end:
        escaped = value[i] == "\\" and i + 1 < end and value[i + 1] in escapes
        chars.append(value[i + 1] if escaped else value[i])
        i += 2 if escaped else 1
    word = "".join(chars)
    if word == value[start:end]:
        return _piece(value, start, end)
    word_quoting.unknown(word)  # unescaped text: no shell spelling carries over
    return word


def _form_word(value: str, start: int, ends: str) -> tuple[list[str], int]:
    """One ``-F`` word after its leading blanks: its readings and the index of the character that ended it.

    A double-quoted word is its content (``\\"``, ``\\\\`` escaped); text after
    the closing quote up to an end character is dropped. An unterminated quote
    reads as unquoted. An unquoted word ends at a character of *ends*, its
    trailing blanks trimmed; the untrimmed reading is kept too.
    """
    while start < len(value) and value[start] in _BLANKS:
        start += 1
    if value.startswith('"', start):
        close = start + 1
        while close < len(value) and value[close] != '"':
            close += 2 if value[close] == "\\" and value[close + 1:close + 2] in ('"', "\\") else 1
        if close < len(value):
            end = close + 1
            while end < len(value) and value[end] not in ends:
                end += 1
            return [_unescaped(value, start + 1, close, '"\\')], end
    end = start
    while end < len(value) and value[end] not in ends:
        end += 1
    trimmed = value[start:end].rstrip(_BLANKS)
    return list(dict.fromkeys((_piece(value, start, end), _piece(value, start, start + len(trimmed))))), end


def _form_part(value: str, start: int, endchar: str) -> tuple[list[str], list[str], int] | None:
    """A ``-F`` word and its ``;key=value`` parameters: (word readings, ``headers=@`` files, next index).

    None when a ``type=`` value stops at a character after which curl's reading is not followed.
    """
    ends = endchar + ";"
    words, i = _form_word(value, start, ends)
    headers: list[str] = []
    while i < len(value) and value[i] == ";":
        p = i + 1
        while p < len(value) and value[p] in _BLANKS:
            p += 1
        key = value[p:p + 9].lower()
        if key.startswith("type="):
            i = p + 5
            while i < len(value) and value[i] not in _TYPE_STOP:
                i += 1
            if i < len(value) and value[i] not in ends:
                return None
        elif key.startswith("headers=") and value[p + 8:p + 9] in ("@", "<"):
            header, i = _form_word(value, p + 9, ends)
            headers += header
        else:
            skip = 8 if key.startswith(("headers=", "encoder=")) else 9 if key.startswith("filename=") else 0
            i = _form_word(value, p + skip, ends)[1]  # a value, or an unknown parameter skipped whole
    return words, headers, i


def _form_files(value: str) -> list[str] | None:
    """Files a ``-F name=content`` value reads: ``@FILE[,FILE...]``, ``<FILE`` and ``headers=@FILE``."""
    name, eq, content = value.partition("=")
    if not eq:
        return []  # curl rejects a form part without "="
    start = len(name) + 1
    if content.startswith("@"):
        files: list[str] = []
        i = start
        while i < len(value) and value[i] in "@,":
            part = _form_part(value, i + 1, ",")
            if part is None:
                return None
            files += part[0] + part[1]
            i = part[2]
        return files
    from_file = content.startswith("<")  # tested before blanks are skipped: "f= <x" is literal text
    part = _form_part(value, start + 1 if from_file else start, "")
    if part is None:
        return None
    words, headers, _end = part
    return (words if from_file else []) + headers  # a literal value's own words are text, not files


def _cert_file(value: str) -> str:
    r"""The file of ``-E CERT[:PASSWORD]`` (``\:`` escapes a colon); "" for a PKCS#11 URI."""
    if value.startswith("pkcs11:"):
        return ""
    end = 0
    while end < len(value) and value[end] != ":":
        end += 2 if value[end] == "\\" and value[end + 1:end + 2] in (":", "\\") else 1
    return _unescaped(value, 0, min(end, len(value)), ":\\")


def reads(options: list[tuple[str, str]]) -> list[str] | None:
    """Files curl reads through the values in *options* (``file_operands.option_values``); ``-`` is stdin.

    None when a ``-F`` value cannot be read the way curl reads it.
    """
    found: list[str] = []
    for key, value in options:
        if key in _AT_FILE:
            found += [_piece(value, 1)] if value.startswith("@") else []
        elif key == "--data-urlencode":
            split = min((i for i in (value.find("@"), value.find("=")) if i >= 0), default=-1)
            found += [_piece(value, split + 1)] if split >= 0 and value[split] == "@" else []
        elif key in {"F", "--form"}:
            files = _form_files(value)
            if files is None:
                return None
            found += files
        elif key in {"b", "--cookie"}:
            found += [value] if "=" not in value else []  # NAME=VALUE is a literal cookie
        elif key in {"E", "--cert"}:
            found.append(_cert_file(value))
    return [word for word in found if word and word != "-"]
