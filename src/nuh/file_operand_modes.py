"""Bounded write selectors for commands whose mode or output naming is not positional.

These rules place argv outputs only. Indirect filenames, execution hooks and
unmodeled destination rewriting ask; they never trigger program/archive analysis.
"""

import os
import re

from nuh import curl_files, word_quoting

_COMPRESSORS = {"gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"}
_READ, _WRITE, _UNKNOWN = "filesystem_read", "filesystem_write", "unknown"
_UNSAFE = {
    "sort": {"--files0-from"},
    "tar": {"T", "--files-from", "g", "--listed-incremental", "--index-file", "--volno-file",
            "--transform", "--xform", "--strip-components", "--one-top-level", "P", "--absolute-names",
            "--remove-files", "--to-command", "--checkpoint-action", "F", "--info-script",
            "--new-volume-script", "I", "--use-compress-program"},
    "patch": {"B", "Y", "z", "--prefix", "--basename-prefix", "--suffix"},
    "split": {"--filter"}, "tree": {"R"},
    "zip": {"m", "--move", "T", "--test", "--test-test", "l", "s", "--split-size"},
    "unzip": {":", "T"},
    "xz": {"F", "--files", "--files0", "--format"},
    "awk": {"d", "D", "L", "o", "p", "l", "--load", "f", "E", "--file", "--exec"},
    "curl": {"K", "--config", "O", "--remote-name", "--remote-name-all", "J", "--remote-header-name",
             "--next", "--trace", "--trace-ascii", "D", "--dump-header", "w", "n", "--netrc"},
    "wget": {"i", "--input-file", "--config", "--execute", "e"},
    "rsync": {"--files-from", "--read-batch", "--rsync-path", "f", "F", "--filter"},
    "scp": {"F", "o", "S", "D"},
}
_REMOTE = re.compile(r"^(?:[^/]+:|[A-Za-z][A-Za-z0-9+.-]*://)")
_DD_KEYS = frozenset("if of ibs obs bs cbs skip iseek seek oseek count conv iflag oflag status".split())
_LONG_FLAGS = {
    "sed": "--in-place --quiet --silent --debug --follow-symlinks --posix --regexp-extended --sandbox "
           "--separate --unbuffered --null-data",
    "sort": "--reverse --stable --unique --numeric-sort --general-numeric-sort --human-numeric-sort "
            "--month-sort --version-sort --random-sort --merge --check --ignore-case --ignore-leading-blanks "
            "--dictionary-order --ignore-nonprinting --zero-terminated --debug",
    "diff": "--recursive --brief --report-identical-files --text --ignore-case --ignore-all-space "
            "--ignore-space-change --ignore-blank-lines --unified --context --new-file",
    "uniq": "--count --repeated --unique --ignore-case --zero-terminated",
    "tree": "--dirsfirst --noreport --inodes --device --prune --du --si --gitignore",
    "tar": "--create --append --update --extract --get --list --diff --compare --concatenate --delete "
           "--gzip --bzip2 --xz --verbose --preserve-permissions --keep-old-files --overwrite --to-stdout",
    "patch": "--batch --force --forward --reverse --dry-run --silent --quiet --backup --binary --unified",
    "split": "--numeric-suffixes --elide-empty-files --verbose --unbuffered",
    "csplit": "--quiet --silent --keep-files --suppress-matched --elide-empty-files",
    "zip": "--recurse-paths --quiet --verbose --junk-paths --update --freshen",
    "unzip": "",
    "curl": "--silent --show-error --location --fail --fail-with-body --head --insecure --compressed "
            "--no-progress-meter --data-raw --data-binary --data-urlencode --form-string --json",
    "wget": "--quiet --verbose --continue --no-clobber --recursive --no-check-certificate "
            "--spider --post-data --post-file --method --body-data --body-file",
    "rsync": "--archive --recursive --verbose --compress --delete --delete-before --delete-after "
             "--progress --dry-run --remove-source-files --relative --links --perms --times --numeric-ids",
    "scp": "",
    "awk": "--posix --traditional --sandbox --lint --bignum",
}
_COMPRESS_FLAGS = "--stdout --to-stdout --decompress --uncompress --compress --keep --force --test --list " \
                  "--quiet --verbose --fast --best --no-name --name --recursive --small"
_SHORT_FLAGS = {
    "sed": "nErszu", "awk": "bcCnNOfE",
    "sort": "bdfghinMrsuVzcmR", "uniq": "cdiDu",
    "tar": "ctxruAtdvzjJhpkmwaOW", "zip": "qrvjufXoy", "unzip": "acCfjlLmMnopqtuvUVXZz",
    "patch": "bBcDeEfnNpRstTuvVxYzZ", "split": "deu", "csplit": "ksqz",
    "mkfifo": "", "mknod": "",
}


def normalize(tokens: list[str]) -> list[str]:
    """Normalize tar's traditional bare option word without guessing at other operands."""
    if os.path.basename(tokens[0]) == "tar" and len(tokens) > 1:
        first = tokens[1]
        if first.isalpha() and set(first) <= set("ctxruAfCvbzjJhpPOkSmW") and set(first) & set("ctxruA"):
            normalized = [tokens[0]]
            i = 2
            for letter in first:
                normalized.append("-" + letter)
                if letter in "fC" and i < len(tokens):
                    normalized.append(tokens[i])
                    i += 1
            return normalized + tokens[i:]
    return tokens


def local_path(word: str) -> bool:
    """A transfer operand is local unless its first component denotes a remote host or URL."""
    return not _REMOTE.match(word)


def _under(directory: str, word: str) -> str:
    child = word_quoting.encoding(word)
    dynamic = word.startswith(("~", "$")) and (child is None or child[:1] in {"~", "$"})
    if word.startswith("/") or dynamic:
        return word
    result = os.path.join(directory, word)
    parent = word_quoting.encoding(directory)
    if parent is not None and child is not None:
        return word_quoting.spelled(result, os.path.join(parent, child))
    return result


def tar_files(found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Tar's positional directory options rebase subsequent file operands, not its archive option."""
    directory = "."
    rebased: list[tuple[str, str]] = []
    for kind, word in found:
        if kind in {"C", "--directory"}:
            directory = _under(directory, word)
            word = directory
        elif kind == "operand" and directory != ".":
            word = _under(directory, word)
        rebased.append((kind, word))
    return rebased


def select(cmd: str, which: str, tokens: list[str], found: list[tuple[str, str]],
           options: list[tuple[str, str]]) -> list[str]:
    """Select explicit destinations, retaining positional stdin markers until positions are known."""
    words = [word for kind, word in found if kind == "operand"]
    values = dict(options)
    if which == "first":
        output = [word for key, word in found if key in {"O", "--out", "--output-file"}]
        archives = output or words[:1]
        generated = [word + ".zip" for word in archives if cmd == "zip" and word != "-"
                     and not os.path.splitext(word)[1]]
        return archives + generated + [word for key, word in found if key in {"b", "--temp-path"}]
    if which == "second":
        return words[1:2]
    if which == "prefix":
        return words[1:2] or ["x"]
    if which == "csplit":
        return [values.get("f", values.get("--prefix", "xx"))]
    if which == "dd":
        return [word for key, word in found if key == "of"]
    if which == "patch":
        directory = values.get("d", values.get("--directory", "."))
        outputs = [word for key, word in found if key in {"o", "--output"}] or words[:1]
        if not outputs:
            return [""]  # header-derived original names are unknown even with a reject file
        rejects = [word for key, word in found if key in {"r", "--reject-file"}]
        return [directory] + [_under(directory, word) for word in outputs + rejects]
    if which == "tar":
        return _tar_targets(found, values)
    if which == "unzip":
        directory = values.get("d", ".")
        return [directory] + [_under(directory, word) for word in words[1:]]
    if which == "compress":
        return words + [_compressed_output(cmd, word, values) for word in words if word != "-"]
    if which == "transfer":
        selected = words if "--remove-source-files" in values else words[-1:]
        return [word for word in selected if local_path(word)] + [word for key, word in found
                if key in {"T", "--temp-dir", "--backup-dir", "--partial-dir", "--log-file",
                           "--write-batch", "--only-write-batch"}]
    raise ValueError(f"unknown write selector: {which}")


def _tar_targets(found: list[tuple[str, str]], values: dict[str, str]) -> list[str]:
    if not set(values).intersection({"x", "--extract", "--get"}):
        return [word for key, word in found if key in {"f", "--file"}] or [""]
    directory = "."
    targets: list[str] = []
    for kind, word in found:
        if kind in {"C", "--directory"}:
            directory = _under(directory, word)
            targets.append(directory)
        elif kind == "operand":
            targets.append(_under(directory, word))
    return targets or [directory]


def _compressed_output(cmd: str, word: str, values: dict[str, str]) -> str:
    suffix = values.get("S", values.get("--suffix", ""))
    decompress = cmd in {"gunzip", "unxz", "bunzip2"} or bool(set(values) & {"d", "--decompress", "--uncompress"})
    if not decompress:
        return word + (suffix or {"xz": ".xz", "bzip2": ".bz2"}.get(cmd, ".gz"))
    suffixes = ([suffix] if suffix else []) + {
        "gunzip": [".gz", "-gz", ".z", "-z", "_z"], "gzip": [".gz", "-gz", ".z", "-z", "_z"],
        "xz": [".xz", ".lzma", ".lz"], "unxz": [".xz", ".lzma", ".lz"],
        "bzip2": [".bz2", ".bz"], "bunzip2": [".bz2", ".bz"],
    }[cmd]
    for ending in suffixes:
        if word.lower().endswith(ending.lower()):
            return word[:-len(ending)]
    for ending in (".tgz", ".taz", ".txz", ".tlz", ".tbz2", ".tbz"):
        if word.lower().endswith(ending):
            return word[:-len(ending)] + ".tar"
    return word + ".out"


def write_mode(cmd: str, tokens: list[str], options: list[tuple[str, str]], targets: list[str],
               value_longs: frozenset[str], value_letters: str) -> str | None:
    """Classify bounded modes and ask on argv features that can introduce unknown writes."""
    values = dict(options)
    flags = set(values)
    family = {"gsed": "sed", "gawk": "awk", "unxz": "xz"}.get(cmd, cmd)
    short = "0123456789acdefhklnNqrstvz" if cmd in _COMPRESSORS else _SHORT_FLAGS.get(family)
    if short is not None and any(len(flag) == 1 and flag not in short + value_letters for flag in flags):
        return _UNKNOWN
    known = _COMPRESS_FLAGS if cmd in _COMPRESSORS else _LONG_FLAGS.get(family)
    if known is not None:
        supported = set(known.split()) | value_longs | {"--help", "--version"}
        if any(flag.startswith("--") and flag not in supported for flag in flags):
            return _UNKNOWN  # includes abbreviated options: never silently lose an output
    unsafe = flags & _UNSAFE.get(family, set())
    if family == "awk" and not any(key in {"i", "--include"} for key in flags):
        unsafe &= {"l", "--load"}  # script files keep the ordinary policy; native libraries execute
    if unsafe or (cmd == "sort" and "--compress-program" in flags):
        return _UNKNOWN
    if cmd == "curl" and curl_files.reads(options) is None:
        return _UNKNOWN
    if cmd in {"sed", "gsed"}:
        inplace = flags & {"i", "I", "--in-place"}
        if any("/" in values[key] or "*" in values[key] for key in inplace):
            return _UNKNOWN
        return _WRITE if inplace else _READ
    if cmd in {"awk", "gawk"}:
        includes = [value for key, value in options if key in {"i", "--include"}]
        if includes:
            return _WRITE if all(value == "inplace" for value in includes) else _UNKNOWN
        return None
    if cmd in {"sort", "diff", "tree", "uniq"}:
        return _WRITE if targets else _READ
    if cmd == "tar":
        if flags & {"O", "--to-stdout"}:
            return _UNKNOWN  # combining read/write modes must not hide archive writes
        if flags & {"x", "--extract", "--get"}:
            return _WRITE
        if flags & {"c", "r", "u", "A", "--create", "--append", "--update", "--concatenate", "--delete"}:
            return _WRITE if targets and all(local_path(word) for word in targets) else _UNKNOWN
        return _READ if flags & {"t", "--list", "d", "--diff", "--compare"} else _WRITE
    if cmd == "patch":
        return _WRITE if len(tokens) == 1 or len(targets) > 1 else _UNKNOWN
    if cmd in {"split", "csplit"}:
        suffix = values.get("--additional-suffix", values.get("b", values.get("--suffix-format", "")))
        explicit = cmd == "csplit" and bool(flags & {"f", "--prefix"})
        return _UNKNOWN if explicit or "/" in suffix else _WRITE
    if cmd == "unzip":
        return _READ if flags & {"c", "p", "t", "l", "z", "v", "Z"} else _UNKNOWN
    if cmd in _COMPRESSORS:
        if flags & {"r", "--recursive"}:
            return _UNKNOWN
        if flags & {"c", "--stdout", "--to-stdout", "t", "--test", "l", "--list"}:
            return _READ
        suffix = values.get("S", values.get("--suffix", ""))
        if flags & {"z", "--compress"} and (cmd in {"gunzip", "unxz", "bunzip2"}
                                            or flags & {"d", "--decompress", "--uncompress"}):
            return _UNKNOWN
        return _UNKNOWN if flags & {"N", "--name"} or "/" in suffix else _WRITE
    if cmd == "dd":
        if any("=" not in word or word.partition("=")[0] not in _DD_KEYS for word in tokens[1:]):
            return _UNKNOWN
    return None
