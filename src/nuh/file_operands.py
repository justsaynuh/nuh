"""The operands of common file commands that name files, bare names (``cat notes.txt``) included.

Path extraction has always checked words that look like paths (a ``/``, a
leading ``.`` or ``~``, a sensitive name). A bare name is a path too when it
sits where the command takes a file: ``cat notes.txt`` run in a sensitive
directory reads a sensitive file. ``bare_files`` lists such words for the
commands below, from their option grammar; other commands, and words in
other positions (``echo hello``, the ``5`` of ``head -n 5``), are left alone.

Options listed as taking a value all do. Unrecognized long options of the
mode-dependent writers ask: a destination option must never disappear
merely because the grammar could not place its value.

``modified_files`` lists the words a write or delete command changes, from
``_MODIFIES``: the project boundary judges each of them, not only the last.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from nuh import curl_files, file_operand_modes, input_redirects, operands, word_quoting


@dataclass(frozen=True)
class _Grammar:
    value_letters: str = ""                     # short options that take a value
    value_long: frozenset[str] = frozenset()    # long options that take a separate value
    file_letters: str = ""                      # of those, the ones whose value is a file
    file_long: frozenset[str] = frozenset()
    leading: int = 0                            # operands before the files (chmod MODE, chown OWNER)
    mode_letters: str = ""                      # a leading operand may look like an option (chmod -w)
    optional_letters: str = ""                  # optional attached values, never the next word
    stop_at_operand: bool = False              # awk's options end at its first positional word
    file_keys: frozenset[str] = frozenset()     # key=FILE operands
    read_keys: frozenset[str] = frozenset()     # explicit reads, even if the same path is also written


def _g(letters: str = "", longs: str = "", files: str = "", file_longs: str = "", leading: int = 0,
       mode_letters: str = "", optional: str = "") -> _Grammar:
    return _Grammar(letters, frozenset(longs.split()), files, frozenset(file_longs.split()), leading,
                    mode_letters, optional)


_PLAIN = _g()
_LESS = _g("bhjkoOpPtTxyz#", "--pattern --tag --log-file --LOG-FILE --tag-file", "oO", "--log-file --LOG-FILE")
_COPY = _g("tS", "--target-directory --suffix", "t", "--target-directory")
_OWNER = _g("", "", "", "", leading=1)
_GRAMMARS: dict[str, _Grammar] = {
    **dict.fromkeys(("cat", "tac", "rev", "comm", "zcat", "md5sum", "sha1sum", "sha256sum", "sha512sum",
                     "realpath", "readlink", "rm", "rmdir", "unlink", "tee"), _PLAIN),
    "head": _g("nc", "--lines --bytes"),
    "tail": _g("ncs", "--lines --bytes --sleep-interval --pid --max-unchanged-stats"),
    "less": _LESS, "more": _LESS, "zless": _LESS, "zmore": _LESS,
    "nl": _g("bdfhilnsvw", "--body-numbering --section-delimiter --footer-numbering --header-numbering "
                           "--line-increment --join-blank-lines --number-format --number-separator "
                           "--starting-line-number --number-width"),
    "wc": _PLAIN,
    "file": _g("mfFeP", "--magic-file --files-from --separator --exclude --parameter", "mf",
               "--magic-file --files-from"),
    "stat": _g("c", "--format --printf"),
    "du": _g("dBtX", "--max-depth --block-size --threshold --exclude --exclude-from --time-style --files0-from",
             "X", "--exclude-from --files0-from"),
    "ls": _g("ITw", "--hide --ignore --block-size --format --sort --time --time-style --quoting-style "
                    "--indicator-style --width --tabsize"),
    "tree": _g("LPIoH", "--filelimit --timefmt --sort --charset", "o"),
    "bat": _g("lrHm", "--language --line-range --highlight-line --map-syntax --style --theme --tabs "
                      "--wrap --terminal-width --pager --decorations --color --italic-text --file-name"),
    "diff": _g("UCIFDLSXxW", "--label --ignore-matching-lines --show-function-line --ifdef --starting-file "
                             "--exclude-from --exclude --width --tabsize --horizon-lines --line-format "
                             "--old-line-format --new-line-format --unchanged-line-format --output "
                             "--from-file --to-file", "X", "--exclude-from --output --from-file --to-file"),
    "cmp": _g("in", "--ignore-initial --bytes"),
    "sort": _g("ktoST", "--key --field-separator --output --buffer-size --temporary-directory --batch-size "
                        "--compress-program --parallel --files0-from --random-source", "oT",
               "--output --temporary-directory --files0-from --random-source"),
    "cut": _g("bcdf", "--bytes --characters --delimiter --fields --output-delimiter"),
    "uniq": _g("fsw", "--skip-fields --skip-chars --check-chars"),
    "paste": _g("d", "--delimiters"),
    "join": _g("12jteo", ""),
    "fold": _g("w", "--width"),
    "fmt": _g("wpgd", "--width --prefix --goal"),
    "column": _g("scoNlRHWOEd", "--separator --output-separator --table-columns --table-columns-limit "
                                "--table-right --table-hide --table-wrap --table-order --table-name "
                                "--table-noextreme --table-noheadings --output-width"),
    "expand": _g("t", "--tabs"), "unexpand": _g("t", "--tabs"),
    "cksum": _g("al", "--algorithm --length"), "b2sum": _g("l", "--length"),
    "od": _g("AjNSt", "--address-radix --skip-bytes --read-bytes --format --strings"),
    "hexdump": _g("nsef", "", "f"),
    "strings": _g("netT", "--bytes --radix --encoding --target"),
    "cp": _COPY, "mv": _COPY, "ln": _COPY,
    "install": _g("mogtS", "--mode --owner --group --target-directory --suffix", "t", "--target-directory"),
    "shred": _g("ns", "--iterations --size --random-source", "", "--random-source"),
    "touch": _g("dtr", "--date --reference", "r", "--reference"),
    "mkdir": _g("m", "--mode"),
    "truncate": _g("sr", "--size --reference", "r", "--reference"),
    "chmod": _g(leading=1, mode_letters="rwxXst"), "chown": _OWNER, "chgrp": _OWNER,
    # chattr's leading operand is the attribute change (+i, =ai, or -i, option-shaped).
    "chattr": _g("vp", "", leading=1, mode_letters="aAcCdDeEFiIjmNPsStTux"),
    # setfacl's ACL spec is an option's value (-m SPEC); -M/-X/--set-file read a spec file.
    "setfacl": _g("mMxX", "--modify --modify-file --remove --remove-file --set --set-file --restore", "MX",
                  "--modify-file --remove-file --set-file --restore"),
    "mkfifo": _g("m", "--mode"), "mknod": _g("m", "--mode"),
    "patch": _g("dopirgFDV",
                "--directory --output --strip --input --reject-file --get --fuzz --ifdef --version-control", "doir",
                "--directory --output --input --reject-file"),
    "split": _g("abClnt", "--suffix-length --bytes --line-bytes --lines --number --separator "
                "--additional-suffix --filter"),
    "csplit": _g("bfn", "--suffix-format --prefix --digits", "f", "--prefix"),
    "dd": _Grammar(file_keys=frozenset({"of"}), read_keys=frozenset({"if"})),
    "tar": _g("fCbTXgFHIKLNV", "--file --directory --label --blocking-factor --files-from --exclude-from "
              "--listed-incremental --info-script --new-volume-script --use-compress-program --tape-length "
              "--newer --newer-mtime --exclude --transform --xform --strip-components --index-file --volno-file",
              "fCTXgN", "--file --directory --files-from --exclude-from --listed-incremental --newer --newer-mtime"),
    "zip": _g("bOnP", "--temp-path --out --output-file --suffixes --password", "bO",
              "--temp-path --out --output-file"),
    "unzip": _g("dxPOI", "", "d"),
    **dict.fromkeys(("gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"),
                    _g("SbMTF", "--suffix --format --block-size --memlimit --threads")),
    "curl": _g("ocwXdHFAeumUTKbE", "--output --output-dir --cookie-jar --url --request --data --header --form "
               "--user-agent --referer --user --max-time --upload-file --config --cert --key --cacert --cookie "
               "--data-binary --data-urlencode --json --data-raw --form-string",
               "ocTKE", "--output --output-dir --cookie-jar --upload-file --config --cert --key --cacert"),
    "wget": _g("OPoaUitT", "--output-document --directory-prefix --output-file --append-output --user-agent "
               "--input-file --tries --timeout --post-file --body-file", "OPoai", "--output-document "
               "--directory-prefix --output-file --append-output --input-file --post-file --body-file"),
    "rsync": _g("eTfB", "--rsh --temp-dir --filter --block-size --exclude --include --exclude-from "
                "--include-from --files-from --backup-dir --partial-dir --log-file --write-batch --only-write-batch",
                "T", "--temp-dir --backup-dir --partial-dir --log-file --write-batch --only-write-batch "
                "--files-from --exclude-from --include-from"),
    "scp": _g("cDFiJloPSX", files="Fi"),
}
_ALL, _LAST = "all", "last"
# The operands a write or delete command modifies: every one (mv removes its sources), or the last
# (cp, ln and install only read their sources). A target directory option (_TARGET_OPTIONS) names a
# destination too, and the only one of a "last" command. A command not listed here is judged by its
# last path operand (bash_policy._extract_primary_target). Script tools share operands' grammar.
_MODIFIES: dict[str, str] = {
    **dict.fromkeys(("rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp", "chattr", "setfacl", "touch",
                     "truncate", "mkdir", "tee", "mv", "mkfifo", "sed", "gsed", "awk", "gawk"), _ALL),
    **dict.fromkeys(("cp", "ln", "install"), _LAST),
    **dict.fromkeys(("sort", "diff", "tree", "curl", "wget"), "options"),
    "uniq": "second", "mknod": "first", "zip": "first", "patch": "patch",
    "split": "prefix", "csplit": "csplit", "dd": "dd", "tar": "tar", "unzip": "unzip",
    **dict.fromkeys(("gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"), "compress"),
    "rsync": "transfer", "scp": "transfer",
}
_WRITE_OPTIONS = {
    "sort": {"o", "--output", "T", "--temporary-directory"}, "diff": {"--output"}, "tree": {"o"},
    "curl": {"o", "c", "--output", "--output-dir", "--cookie-jar"},
    "wget": {"O", "P", "o", "a", "--output-document", "--directory-prefix", "--output-file", "--append-output"},
}
# An option that makes a "last" command modify every operand: install -d creates each one as a directory.
_ALL_WITH: dict[str, tuple[str, str]] = {"install": ("d", "--directory")}
_TARGET_OPTIONS = frozenset({"t", "--target-directory"})   # a file option of cp, mv, ln, install (touch -t: a time)
_OPERAND, _TARGET, _OTHER = "operand", "target", "other"   # kinds of file word (_scan)
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SCRIPT_TOOLS = {"sed": "sed", "gsed": "sed", "awk": "awk", "gawk": "awk", "mawk": "awk", "nawk": "awk",
                 "grep": "grep", "egrep": "grep", "fgrep": "grep", "zgrep": "grep", "rg": "rg"}
# GNU file tools may permute options; POSIX processing makes late option
# values into filenames. Custom parsers (notably tar) retain their own rules.
_POSIX_TOOLS = frozenset({"cp", "mv", "ln", "install", "chmod", "chown", "chgrp", "touch", "truncate",
                          "mkdir", "tee", "mkfifo", "mknod", "sort", "diff", "uniq", "split", "csplit",
                          "sed", "gsed", "grep", "egrep", "fgrep", "zgrep",
                          "gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"})


def _option(tok: str, grammar: _Grammar) -> tuple[str, str | None]:
    """``(name, attached value)`` of option *tok* that takes a value, ``("", None)`` for one that takes none.

    The name is the long option or the short option's letter. The attached value
    is None when the value is the next word (``-t DIR``, ``--suffix X``).
    """
    if tok.startswith("--"):
        name, eq, value = tok.partition("=")
        if name == "--in-place" and "i" in grammar.optional_letters:
            return name, value if eq else ""
        if "--target-directory" in grammar.value_long and name.startswith("--target-"):
            if "--target-directory".startswith(name):
                name = "--target-directory"
        return (name, value if eq else None) if name in grammar.value_long else ("", None)
    letters = tok[1:]
    for i, letter in enumerate(letters):
        if letter in grammar.optional_letters:
            return letter, letters[i + 1:]
        if letter in grammar.value_letters:
            value = letters[i + 1:]
            if grammar is _GRAMMARS["zip"] and value.startswith("="):
                value = value[1:]  # zip permits -O=archive as well as -Oarchive
            return letter, value or None
    return "", None


def _is_mode(tok: str, grammar: _Grammar) -> bool:
    """True when *tok* is an option-shaped mode operand (chmod's ``-w``), not an option."""
    return bool(grammar.mode_letters) and all(letter in grammar.mode_letters for letter in tok[1:])


def _file_value(token: str, value: str) -> str:
    """Keep an attached file value's quoting when removing its option or key prefix."""
    spelling = word_quoting.encoding(token)
    if value and spelling is not None and token.endswith(value):
        return word_quoting.spelled(value, spelling[-len(value):])
    return value


def _scan(tokens: list[str], grammar: _Grammar, skip: set[int]) -> list[tuple[str, str]]:
    """``(kind, word)`` for each file word of *tokens* under *grammar*, in order; operands in *skip* are not files.

    The kind is ``_OPERAND``, ``_TARGET`` (a target directory option's value),
    an option name (its file value), or ``_OTHER`` (the file of an input redirect,
    ``input_redirects``). A here-string or heredoc operator and its word name no file.
    """
    found: list[tuple[str, str]] = []
    operand_count = 0
    leading = grammar.leading if not any(t.startswith("--reference") for t in tokens) else 0
    options = True
    redirects = input_redirects.find(tokens)
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        redirect = redirects.get(i)
        if redirect is not None:
            if redirect.file:
                found.append((_OTHER, redirect.file))
            i += redirect.width
            continue
        if options and tok == "--":
            options = False
        elif options and tok.startswith("-") and tok != "-" and not (
                operand_count < leading and _is_mode(tok, grammar)):
            name, value = _option(tok, grammar)
            if name and value is None and i + 1 < len(tokens):
                i += 1
                value = tokens[i]
            if value is not None and (name in grammar.file_letters or name in grammar.file_long):
                found.append((_TARGET if name in _TARGET_OPTIONS else name, _file_value(tok, value)))
        elif tok.partition("=")[0] in grammar.file_keys | grammar.read_keys and "=" in tok:
            key, _, value = tok.partition("=")
            found.append((key, _file_value(tok, value)))
        else:
            if grammar.stop_at_operand:
                options = False
            if i not in skip:
                operand_count += 1
                if operand_count > leading:
                    found.append((_OPERAND, tok))
        i += 1
    return found


def _script_tool_grammar(tool: str) -> _Grammar:
    spec = operands.grammar(tool)
    extra_files = {"grep": {"--exclude-from"}, "rg": {"--ignore-file"}, "awk": {"--load"}}
    return _Grammar(spec.value_letters + spec.script_letters + spec.file_letters,
                    spec.value_long | frozenset(spec.script_long) | frozenset(spec.file_long),
                    spec.file_letters + ("l" if tool == "awk" else ""),
                    frozenset(spec.file_long) | extra_files.get(tool, set()),
                    optional_letters=spec.optional_letters + ("I" if tool == "sed" else ""),
                    stop_at_operand=tool == "awk")


def files(tokens: list[str]) -> list[str]:
    """Every file operand or option value the grammar identifies, with its shell quoting.

    The file of an input redirect (``< FILE``, ``3<FILE``, ``<>FILE``) is one
    whatever the command: the shell opens it (``input_redirects``). The sed
    script, awk program and grep/rg pattern are not among them; awk's
    ``NAME=value`` operands neither.
    """
    if not tokens:
        return []
    tokens = file_operand_modes.normalize(tokens)
    cmd = os.path.basename(tokens[0])
    tool = _SCRIPT_TOOLS.get(cmd)
    if tool is not None:
        skip = operands.script_operands(tokens)
        if tool == "awk":
            skip |= {i for i, tok in enumerate(tokens) if _ASSIGNMENT_RE.match(tok)}
        found = _scan(tokens, _script_tool_grammar(tool), skip)
    elif cmd in _GRAMMARS:
        found = _scan(tokens, _GRAMMARS[cmd], set())
    else:
        return input_redirects.files(tokens)
    if cmd == "tar":
        found = file_operand_modes.tar_files(found)
    return [word for _kind, word in found if word and word != "-"] + _value_reads(tokens)


def _value_reads(tokens: list[str]) -> list[str]:
    """Files read through syntax inside an option's value (``curl -F f=@FILE``, ``curl -b FILE``)."""
    if os.path.basename(tokens[0]) != "curl":
        return []
    return curl_files.reads(option_values(tokens, _GRAMMARS["curl"])) or []


def bare_files(tokens: list[str]) -> list[str]:
    """The grammar's files with no path syntax; retained for callers interested only in bare names."""
    return [word for word in files(tokens) if "/" not in word and not word.startswith((".", "~"))]


def input_files(tokens: list[str]) -> list[str]:
    """Known inputs, excluding output-only paths; an explicit read still counts if also written."""
    if not tokens:
        return []
    grammar = _GRAMMARS.get(os.path.basename(tokens[0]), _PLAIN)
    reads = ({word for key, word in _scan(tokens, grammar, set()) if key in grammar.read_keys}
             if grammar.read_keys else set()) | set(_value_reads(tokens))
    outputs = {os.path.normpath(word) for word in modified_files(tokens) or []}
    return [word for word in files(tokens) if word in reads or os.path.normpath(word) not in outputs]


def modified_files(tokens: list[str]) -> list[str] | None:
    """The words of *tokens* the command modifies (``_MODIFIES``), or None for a command not listed there.

    Empty when the command names none (a bare ``rm``).
    """
    if not tokens:
        return None
    cmd = os.path.basename(tokens[0])
    which = _MODIFIES.get(cmd)
    if which is None:
        return None
    tokens = file_operand_modes.normalize(tokens)
    grammar = _script_tool_grammar(_SCRIPT_TOOLS[cmd]) if cmd in _SCRIPT_TOOLS else _GRAMMARS[cmd]
    skip = operands.script_operands(tokens)
    if cmd in ("awk", "gawk"):
        skip |= {i for i, tok in enumerate(tokens) if _ASSIGNMENT_RE.match(tok)}
    found = _scan(tokens, grammar, skip)
    targets = [word for kind, word in found if kind == _TARGET]
    operand_words = [word for kind, word in found if kind == _OPERAND]
    if which == _ALL or (cmd in _ALL_WITH and _has_option(tokens, grammar, *_ALL_WITH[cmd])):
        selected = targets + operand_words
    elif which == _LAST:
        selected = targets or operand_words[-1:]
    elif which == "options":
        selected = [word for kind, word in found if kind in _WRITE_OPTIONS[cmd]]
        if cmd == "wget":
            values = dict(option_values(tokens, grammar))
            if not set(values) & {"O", "--output-document", "P", "--directory-prefix", "--spider"}:
                selected.append(".")
    else:
        selected = file_operand_modes.select(cmd, which, tokens, found, option_values(tokens, grammar))
    dash_streams = {"sed", "gsed", "awk", "gawk", "sort", "uniq", "tar", "zip", "curl", "wget",
                    "gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"}
    return [word for word in selected if word != "-" or cmd not in dash_streams]


def option_values(tokens: list[str], grammar: _Grammar) -> list[tuple[str, str]]:
    """Options and their values, excluding words consumed as values and everything after ``--``."""
    found: list[tuple[str, str]] = []
    i = 1
    while i < len(tokens) and tokens[i] != "--":
        tok = tokens[i]
        if grammar.stop_at_operand and (not tok.startswith("-") or tok == "-"):
            break
        if tok.startswith("-") and tok != "-":
            name, value = _option(tok, grammar)
            if value:
                _file_value(tok, value)  # an attached value keeps its quoting for the paths read out of it
            if tok.startswith("--"):
                found.append((name or tok.partition("=")[0], value or ""))
            else:
                end = tok.index(name, 1) + 1 if name else len(tok)
                found.extend((letter, value or "" if letter == name else "") for letter in tok[1:end])
            if name and value is None and i + 1 < len(tokens):
                i += 1
                found[-1] = (name, tokens[i])
        i += 1
    return found


def option_readings(tokens: list[str]) -> list[list[str]]:
    """GNU argv and, when different, POSIX argv with options ended at the first operand.

    Inserting ``--`` lets the existing grammars place scripts, reads and
    writes independently in each reading, retaining every word's quoting.
    The caller must judge both readings and keep the strictest decision.
    """
    if not tokens or os.path.basename(tokens[0]) not in _POSIX_TOOLS:
        return [tokens]
    cmd = os.path.basename(tokens[0])
    grammar = _script_tool_grammar(_SCRIPT_TOOLS[cmd]) if cmd in _SCRIPT_TOOLS else _GRAMMARS[cmd]
    first: int | None = None
    redirects = input_redirects.find(tokens)
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if i in redirects:
            i += redirects[i].width
            continue
        if first is not None and tok.startswith("-") and tok != "-":
            return [tokens, tokens[:first] + ["--"] + tokens[first:]]
        if tok == "--":
            break  # options already end before the first operand in both readings
        if tok.startswith("-") and tok != "-" and not (first is None and _is_mode(tok, grammar)):
            name, value = _option(tok, grammar)
            if name and value is None:
                i += 1
        elif first is None:
            first = i
        i += 1
    return [tokens]


def write_mode(tokens: list[str]) -> str | None:
    """Flag-dependent file action, or ``unknown`` for write syntax we cannot safely place."""
    if not tokens:
        return None
    cmd = os.path.basename(tokens[0])
    if cmd not in _MODIFIES:
        return None
    tokens = file_operand_modes.normalize(tokens)
    grammar = _script_tool_grammar(_SCRIPT_TOOLS[cmd]) if cmd in _SCRIPT_TOOLS else _GRAMMARS[cmd]
    options = option_values(tokens, grammar)
    if cmd == "split" and sum(kind == _OPERAND for kind, _word in _scan(tokens, grammar, set())) > 1:
        return "unknown"  # an explicit prefix names a family of outputs, not a checked file
    return file_operand_modes.write_mode(cmd, tokens, options, modified_files(tokens) or [], grammar.value_long,
                                         grammar.value_letters + grammar.optional_letters)


def local_transfer(tokens: list[str]) -> bool:
    """True for scp/rsync with explicit operands that are all local paths."""
    if not tokens or os.path.basename(tokens[0]) not in {"scp", "rsync"}:
        return False
    found = _scan(tokens, _GRAMMARS[os.path.basename(tokens[0])], set())
    words = [word for kind, word in found if kind == _OPERAND]
    return len(words) >= 2 and all(file_operand_modes.local_path(word) for word in words)


def _has_option(tokens: list[str], grammar: _Grammar, letter: str, long: str) -> bool:
    """True when *tokens* set the flag *letter* / *long* anywhere before ``--`` (GNU options may follow operands).

    A long option counts by any prefix of at least ``--`` and two letters, as getopt
    abbreviates; a misread counts the flag set, which judges every operand.
    """
    i = 1
    while i < len(tokens) and tokens[i] != "--":
        tok = tokens[i]
        if tok.startswith("--"):
            name, eq, _value = tok.partition("=")
            if len(name) >= 4 and long.startswith(name):
                return True
            i += 1 if eq or name not in grammar.value_long else 2
            continue
        if tok.startswith("-") and tok != "-":
            name, value = _option(tok, grammar)
            if letter in tok[1:tok.index(name) if name else len(tok)]:
                return True
            i += 2 if name and value is None else 1
            continue
        i += 1
    return False
