"""GNU make functions for ``task_make``: text functions computed, ``$(shell)`` handed to the classifier.

``$(eval)``, ``$(file)``, ``$(guile)`` and ``$(let)`` are ``Unreadable``.
"""

import fnmatch
import glob
import os
from typing import TYPE_CHECKING

from nuh.task_recipe import Unreadable

if TYPE_CHECKING:
    from nuh.task_make import Makefile


def split_args(args: str) -> list[str]:
    """A function's comma-separated arguments; commas inside nested references do not split."""
    parts, depth, current = [], 0, []
    for ch in args:
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def pattern_stem(pattern: str, word: str) -> str | None:
    prefix, _pct, suffix = pattern.partition("%")
    if word.startswith(prefix) and word.endswith(suffix) and len(word) >= len(prefix) + len(suffix):
        return word[len(prefix):len(word) - len(suffix)]
    return None


def patsubst(pattern: str, replacement: str, word: str) -> str:
    stem = pattern_stem(pattern, word)
    if stem is None:
        return word
    return replacement.replace("%", stem, 1) if "%" in replacement else replacement


def builtin_var(name: str, mk: "Makefile") -> str:
    builtins = {"MAKE": "make", "CC": "cc", "CXX": "g++", "CPP": "cc -E", "RM": "rm -f", "AR": "ar",
                "SHELL": "/bin/sh", "CURDIR": mk.directory, "MAKEFILE_LIST": " ".join(mk.files),
                "MAKECMDGOALS": " ".join(mk.goals), "MAKELEVEL": "0", "MAKEFLAGS": "", ".SHELLFLAGS": "-c"}
    return builtins.get(name, "")


def _fn_shell(mk: "Makefile", args: list[str], depth: int) -> str:
    command = mk.expand(",".join(args), depth)
    # Make splices stdout into the recipe before its shell parses the line;
    # shell substitution would instead treat metacharacters as ordinary data.
    return mk.produced_text(command)


def _fn_text(fn):
    def wrapped(mk: "Makefile", args: list[str], depth: int) -> str:
        return fn(*[mk.expand(a, depth) for a in args])
    return wrapped


def _fn_call(mk: "Makefile", args: list[str], depth: int) -> str:
    values = [mk.expand(a, depth) for a in args]
    saved = dict(mk.auto)
    mk.auto.update({str(n): v for n, v in enumerate(values)})
    try:
        return mk.var(values[0].strip())
    finally:
        mk.auto = saved


def _fn_foreach(mk: "Makefile", args: list[str], depth: int) -> str:
    if len(args) < 3:
        return ""
    name, words = mk.expand(args[0], depth).strip(), mk.expand(args[1], depth).split()
    saved = dict(mk.auto)
    try:
        out = []
        for word in words:
            mk.auto[name] = word
            out.append(mk.expand(",".join(args[2:]), depth))
        return " ".join(out)
    finally:
        mk.auto = saved


def _fn_eval(_mk: "Makefile", _args: list[str], _depth: int) -> str:
    raise Unreadable("$(eval …) defines rules nuh cannot read")


def _fn_unsupported(name: str):
    def fn(_mk: "Makefile", _args: list[str], _depth: int) -> str:
        raise Unreadable(f"$({name} …) is not something nuh can read")
    return fn


def _fn_wildcard(mk: "Makefile", args: list[str], depth: int) -> str:
    found = []
    for pattern in mk.expand(",".join(args), depth).split():
        matches = sorted(glob.glob(os.path.join(mk.directory, pattern)))
        found += [os.path.relpath(m, mk.directory) if not os.path.isabs(pattern) else m for m in matches]
    return " ".join(found)


def _filter(patterns: str, text: str, keep: bool) -> str:
    pats = patterns.split()
    return " ".join(w for w in text.split()
                    if any(fnmatch.fnmatchcase(w, p.replace("%", "*")) for p in pats) == keep)


def _word(n: str, text: str) -> str:
    words = text.split()
    index = int(n) if n.strip().isdigit() else 0
    return words[index - 1] if 0 < index <= len(words) else ""


FUNCTIONS = {
    "shell": _fn_shell,
    "call": _fn_call,
    "foreach": _fn_foreach,
    "eval": _fn_eval,
    "file": _fn_unsupported("file"),
    "guile": _fn_unsupported("guile"),
    "let": _fn_unsupported("let"),
    "wildcard": _fn_wildcard,
    "if": _fn_text(lambda _c="", a="", b="", *_r: f"{a} {b}".strip()),
    "or": _fn_text(lambda *a: " ".join(a)),
    "and": _fn_text(lambda *a: " ".join(a)),
    "info": _fn_text(lambda *_a: ""),
    "warning": _fn_text(lambda *_a: ""),
    "error": _fn_text(lambda *_a: ""),
    "value": lambda mk, args, _d: mk.vars.get(args[0].strip(), ("", False))[0],
    "origin": _fn_text(lambda *_a: "file"),
    "flavor": _fn_text(lambda *_a: "recursive"),
    "subst": _fn_text(lambda a="", b="", t="", *_r: t.replace(a, b) if a else t),
    "patsubst": _fn_text(lambda p="", r="", t="", *_x: " ".join(patsubst(p, r, w) for w in t.split())),
    "strip": _fn_text(lambda t="", *_r: " ".join(t.split())),
    "findstring": _fn_text(lambda f="", t="", *_r: f if f in t else ""),
    "filter": _fn_text(lambda p="", t="", *_r: _filter(p, t, True)),
    "filter-out": _fn_text(lambda p="", t="", *_r: _filter(p, t, False)),
    "sort": _fn_text(lambda t="", *_r: " ".join(sorted(set(t.split())))),
    "word": _fn_text(lambda n="", t="", *_r: _word(n, t)),
    "words": _fn_text(lambda t="", *_r: str(len(t.split()))),
    "wordlist": _fn_text(lambda s="", e="", t="", *_r: " ".join(t.split()[max(int(s or 1) - 1, 0):int(e or 0)])
                         if s.strip().isdigit() and e.strip().isdigit() else ""),
    "firstword": _fn_text(lambda t="", *_r: (t.split() or [""])[0]),
    "lastword": _fn_text(lambda t="", *_r: (t.split() or [""])[-1]),
    "dir": _fn_text(lambda t="", *_r: " ".join((os.path.dirname(w) or ".") + "/" for w in t.split())),
    "notdir": _fn_text(lambda t="", *_r: " ".join(os.path.basename(w) for w in t.split())),
    "suffix": _fn_text(lambda t="", *_r: " ".join(os.path.splitext(w)[1] for w in t.split())),
    "basename": _fn_text(lambda t="", *_r: " ".join(os.path.splitext(w)[0] for w in t.split())),
    "addsuffix": _fn_text(lambda s="", t="", *_r: " ".join(w + s for w in t.split())),
    "addprefix": _fn_text(lambda p="", t="", *_r: " ".join(p + w for w in t.split())),
    "join": _fn_text(lambda a="", b="", *_r: " ".join(x + y for x, y in zip(a.split(), b.split()))),
    "abspath": _fn_text(lambda t="", *_r: " ".join(os.path.abspath(w) for w in t.split())),
    "realpath": _fn_text(lambda t="", *_r: " ".join(os.path.realpath(w) for w in t.split())),
}
