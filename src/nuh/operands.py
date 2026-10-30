"""Which words of a sed, awk, grep or rg command are a script or pattern rather than a path.

``sed 's/^ *\\([0-9]*\\)/x/' notes.txt`` names one file: the first word is
the sed script, and a slash in it is a delimiter, not a directory. The same
holds for awk's program and the pattern of grep and rg. Each tool takes its
script or pattern from the first operand unless an option supplied it
(``-e``/``--expression``, ``-e``/``--regexp``, gawk ``-e``/``--source``), and a
script file (``-f FILE``) is a path like any other.

Only words this module can place are reported. An option it does not know
leaves the words after it as they were (path candidates), so a misread can
only check a script as a path, never skip a path. The files a sed script
itself reads and writes (``r``/``R``/``w``/``W`` and the ``s///w`` flag) are
reported separately by ``sed_script_files``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Spec:
    """One tool's option grammar, as far as finding its script or pattern needs."""

    script_flags: frozenset[str]          # take the script/pattern as their value
    script_long: tuple[str, ...]          # long forms of the same, as --name
    file_flags: frozenset[str]            # take a script/pattern file as their value
    file_long: tuple[str, ...]
    value_letters: str                    # other short options that take a value
    value_long: frozenset[str]            # other long options that take a separate value
    script_letters: str                   # short letters of script_flags (for clusters)
    file_letters: str
    optional_letters: str = ""            # take the rest of their cluster as an optional value (sed -i.bak)


_SED = _Spec(
    script_flags=frozenset({"-e"}), script_long=("--expression",),
    file_flags=frozenset({"-f"}), file_long=("--file",),
    value_letters="l", value_long=frozenset({"--line-length"}),
    script_letters="e", file_letters="f", optional_letters="i",
)
_AWK = _Spec(
    # gawk -e/--source adds program text; -f/--file and -i/--include, -E/--exec name files.
    script_flags=frozenset({"-e"}), script_long=("--source",),
    file_flags=frozenset({"-f", "-E", "-i"}), file_long=("--file", "--exec", "--include"),
    value_letters="Fvl", value_long=frozenset({"--field-separator", "--assign", "--load"}),
    script_letters="e", file_letters="fEi", optional_letters="dDLop",
)
_GREP = _Spec(
    script_flags=frozenset({"-e"}), script_long=("--regexp",),
    file_flags=frozenset({"-f"}), file_long=("--file",),
    value_letters="ABCDdm",
    value_long=frozenset({"--after-context", "--before-context", "--context", "--max-count", "--devices",
                          "--directories", "--binary-files", "--label", "--include", "--exclude",
                          "--exclude-dir", "--exclude-from", "--group-separator"}),
    script_letters="e", file_letters="f",
)
_RG = _Spec(
    script_flags=frozenset({"-e"}), script_long=("--regexp",),
    file_flags=frozenset({"-f"}), file_long=("--file",),
    value_letters="ABCEMTdgjmrt",
    value_long=frozenset({"--after-context", "--before-context", "--context", "--encoding", "--max-columns",
                          "--type-not", "--max-depth", "--glob", "--iglob", "--threads", "--max-count",
                          "--replace", "--type", "--type-add", "--type-clear", "--color", "--colors",
                          "--context-separator", "--field-context-separator", "--field-match-separator",
                          "--path-separator", "--pre", "--pre-glob", "--ignore-file", "--max-filesize",
                          "--dfa-size-limit", "--regex-size-limit", "--engine", "--sort", "--sortr",
                          "--hostname-bin", "--hyperlink-format"}),
    script_letters="e", file_letters="f",
)
_SPECS = {
    "sed": _SED, "gsed": _SED,
    "awk": _AWK, "gawk": _AWK, "mawk": _AWK, "nawk": _AWK,
    "grep": _GREP, "egrep": _GREP, "fgrep": _GREP, "zgrep": _GREP,
    "rg": _RG,
}


def grammar(command: str) -> _Spec:
    """The option grammar of sed, awk, grep or rg (by command name; KeyError for others)."""
    return _SPECS[command]


def _option_value(tok: str, spec: _Spec) -> tuple[str, bool] | None:
    """How option *tok* takes a value: ``("script"|"file"|"other", value is the next word)``; None: no value."""
    if tok.startswith("--"):
        name, eq, _value = tok.partition("=")
        for kind, longs in (("script", spec.script_long), ("file", spec.file_long)):
            if name in longs:
                return ("include" if spec is _AWK and name == "--include" else kind), not eq
        if name in spec.value_long:
            return "other", not eq
        return None
    letters = tok[1:]
    for i, letter in enumerate(letters):
        if letter in spec.optional_letters:
            return None
        kind = ("script" if letter in spec.script_letters else "file" if letter in spec.file_letters
                else "other" if letter in spec.value_letters else "")
        if kind:
            if spec is _AWK and letter == "i":
                kind = "include"  # an extension does not supply the main program
            return kind, i == len(letters) - 1  # the rest of the cluster is the value when attached
    return None


def script_operands(tokens: list[str]) -> set[int]:
    """Indices of *tokens* that are a sed script, awk program or grep/rg pattern (never a path).

    Empty for any other command. An option this grammar does not know is
    read as taking no value, so the word after it becomes the script or
    pattern; when that word was really the option's value, the real script
    is then checked as a path. A misread checks too much, never too little
    (every option listed as taking a value does take one).
    """
    if not tokens:
        return set()
    spec = _SPECS.get(os.path.basename(tokens[0]))
    if spec is None:
        return set()
    found: set[int] = set()
    supplied = False  # the script or pattern came from an option
    first_operand: int | None = None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            if first_operand is None and i + 1 < len(tokens):
                first_operand = i + 1
            break
        if not tok.startswith("-") or tok == "-":
            if first_operand is None:
                first_operand = i
            if spec is _AWK:
                break  # unlike GNU sed, awk ends option processing at its program/files
            i += 1
            continue
        taken = _option_value(tok, spec)
        if taken is not None:
            kind, separate = taken
            supplied = supplied or kind in ("script", "file")
            if separate and i + 1 < len(tokens):
                if kind == "script":
                    found.add(i + 1)
                i += 1
        i += 1
    if not supplied and first_operand is not None:
        found.add(first_operand)
    return found


def script_texts(tokens: list[str]) -> list[str]:
    """The sed scripts, awk programs or patterns in *tokens*, as written (attached option values included)."""
    texts = [tokens[i] for i in sorted(script_operands(tokens))]
    spec = _SPECS.get(os.path.basename(tokens[0])) if tokens else None
    if spec is None:
        return texts
    for tok in tokens[1:]:
        if tok == "--":
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if eq and name in spec.script_long:
                texts.append(value)
        elif tok.startswith("-") and len(tok) > 2 and tok[1] in spec.script_letters:
            texts.append(tok[2:])
    return texts


# A sed command that reads or writes the file named by the rest of its line:
# r, R, w, W after an address, ``!``, ``;``, a brace or a space, and the
# s///w flag (after the closing delimiter and other flags). Text in a regex
# that happens to match only adds a path to check.
_SED_FILE_RE = re.compile(r"(?:^|(?<=[\s;{}!$0-9,/|#:@%+gpiImMe]))([rRwW])[ \t]+(.+)")
_SED_SINKS = frozenset({"/dev/stdout", "/dev/stderr", "/dev/stdin"})


def sed_script_files(script: str) -> list[tuple[str, bool]]:
    """``(file, is a write)`` for each file a sed *script* names with r, R, w, W or ``s///w``."""
    found: list[tuple[str, bool]] = []
    for line in script.split("\n"):
        match = _SED_FILE_RE.search(line)
        name = match.group(2).strip() if match else ""
        if name and name not in _SED_SINKS:
            found.append((name, match.group(1) in "wW"))
    return found


def sed_files(tokens: list[str]) -> list[tuple[str, bool]]:
    """``(file, is a write)`` for the files the scripts of a sed command read or write; empty for other commands."""
    if not tokens or _SPECS.get(os.path.basename(tokens[0])) is not _SED:
        return []
    return [found for text in script_texts(tokens) for found in sed_script_files(text)]
