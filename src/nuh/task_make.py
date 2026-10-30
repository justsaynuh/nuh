"""What ``make`` would run: the recipe lines for the goals, read from the Makefile.

Static and over-approximate: every conditional branch counts, ``$(if)`` gives
both branches, and prerequisites are followed. Make's own up-to-date rule is
applied (a file target newer than its prerequisites runs nothing), so a
venv-building prerequisite only counts when make would rebuild it. Commands
that run while make reads the file (``$(shell …)`` in ``:=`` and ``!=``
assignments) count for every goal. Anything nuh cannot follow — ``$(eval)``,
a missing include, a goal with no rule, an unusual ``SHELL`` — is
``Unreadable`` and the caller asks.

0.x 952b5663 / c8b81b2e (manuelschipper/nah, MIT) treat make as code
execution unless read-only; reading the recipe instead is ours.
"""

import os
import re
from dataclasses import dataclass, field

from nuh.task_make_args import parse_args
from nuh.task_make_fns import FUNCTIONS, builtin_var, patsubst, pattern_stem, split_args
from nuh.task_recipe import MAX_FILE_BYTES, Recipe, Unreadable

_MAX_DEPTH = 30          # nested variable references and function calls
_MAX_TEXT = 200_000      # characters one expansion may produce
_MAX_LINES = 500         # recipe lines collected for one invocation
_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
_PRODUCED_TEXT = "__nuh_make_produced_output__"


@dataclass
class _Rule:
    prereqs: list[str] = field(default_factory=list)
    order_only: list[str] = field(default_factory=list)
    recipe: list[str] = field(default_factory=list)


_ASSIGN_RE = re.compile(r"^((?:(?:export|override|private)\s+)*)([^:#=\s]+)\s*(:::=|::=|:=|\?=|\+=|!=|=)(.*)$")
_DIRECTIVES = ("ifeq", "ifneq", "ifdef", "ifndef", "else", "endif", "vpath")


class Makefile:
    """One make invocation's parsed files, variables and rules."""

    def __init__(self, directory: str, overrides: dict[str, str], goals: list[str]):
        self.directory = directory
        self.overrides = overrides
        self.goals = goals
        self.vars: dict[str, tuple[str, bool]] = {}  # name -> (value, expand on use)
        self.rules: dict[str, _Rule] = {}
        self.patterns: list[tuple[str, _Rule]] = []
        self.phony: set[str] = set()
        self.default_goal = ""
        self.parse_shell: list[str] = []   # producer commands whose stdout becomes unknown make text
        self.files: list[str] = []
        self.auto: dict[str, str] = {}
        self.produced_reads: int = 0
        self.opaque_reason: str = ""

    # -- reading --

    def read(self, path: str, depth: int = 0) -> None:
        if depth > 5:
            raise Unreadable(f"includes nested too deeply at {path}")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise Unreadable(f"cannot read {path}: {exc.strerror}") from exc
        if len(text) > MAX_FILE_BYTES:
            raise Unreadable(f"{path} is too large to read")
        self.files.append(path)
        self._parse(text.replace("\r\n", "\n").split("\n"), path, depth)

    def _parse(self, raw_lines: list[str], path: str, depth: int) -> None:
        rules: list[_Rule] = []  # the rules the following recipe lines belong to
        i = 0
        while i < len(raw_lines):
            line, i = _logical_line(raw_lines, i)
            if line.startswith("\t"):
                if rules:
                    for rule in rules:
                        rule.recipe.append(line[1:])
                    continue
                if not line.strip():
                    continue
            stripped = _strip_comment(line).strip()
            if not stripped:
                continue
            first = stripped.split(None, 1)[0]
            if first == "define":
                i = self._define(stripped, raw_lines, i)
                rules = []
                continue
            if first in _DIRECTIVES or (first in ("export", "unexport") and "=" not in stripped):
                continue  # every conditional branch counts; exporting a name changes nothing nuh reads
            if first in ("include", "-include", "sinclude"):
                self._include(first, stripped, path, depth)
                rules = []
                continue
            if self._assign(stripped):
                rules = []
                continue
            rules = self._rule(stripped)

    def _define(self, header: str, raw_lines: list[str], i: int) -> int:
        parts = header.split(None, 2)
        name = parts[1] if len(parts) > 1 else ""
        immediate = len(parts) > 2 and parts[2].strip() in (":=", "::=", ":::=")
        body: list[str] = []
        while i < len(raw_lines) and raw_lines[i].strip() != "endef":
            body.append(raw_lines[i])
            i += 1
        value = "\n".join(body)
        self._set(name, self.expand(value) if immediate else value, not immediate)
        return i + 1

    def _include(self, directive: str, line: str, path: str, depth: int) -> None:
        names = self.expand(line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else "").split()
        for name in names:
            candidates = [os.path.join(self.directory, name)] if not os.path.isabs(name) else [name]
            found = next((c for c in candidates if os.path.isfile(c)), None)
            if found:
                self.read(found, depth + 1)
            elif directive == "include":
                raise Unreadable(f"{os.path.basename(path)} includes {name}, which make would build first")

    def _assign(self, line: str) -> bool:
        match = _ASSIGN_RE.match(line)
        if not match:
            return False
        _prefix, name, op, value = match.groups()
        name, value = self.expand(name), value.strip()
        if name in self.overrides:
            return True  # a command-line assignment beats the makefile
        if op == "?=" and (name in self.vars or name in os.environ):
            return True
        if op == "!=":
            self._set(name, self.produced_text(self.expand(value)), False)
        elif op in (":=", "::=", ":::="):
            self._set(name, self.expand(value), False)
        elif op == "+=":
            old, recursive = self.vars.get(name, ("", True))
            self._set(name, (old + " " + (value if recursive else self.expand(value))).strip(), recursive)
        else:
            self._set(name, value, True)
        return True

    def _set(self, name: str, value: str, recursive: bool) -> None:
        if name == ".RECIPEPREFIX":
            raise Unreadable(".RECIPEPREFIX changes how recipes are written")
        self.vars[name] = (value, recursive)
        if name == ".DEFAULT_GOAL":
            self.default_goal = self.expand(value).strip()

    def _rule(self, line: str) -> list[_Rule]:
        head, sep, rest = _split_rule(line)
        if not sep:
            raise Unreadable(f"cannot read makefile line: {line[:80]}")
        recipe = ""
        if ";" in rest:
            rest, recipe = rest.split(";", 1)
        if _ASSIGN_RE.match(rest.strip()):
            self._assign(rest.strip())  # target-specific variable: counted as global
            return []
        targets = self.expand(head).split()
        normal, _bar, order = self.expand(rest).partition("|")
        if "%" in normal and ":" in normal:  # static pattern rule: targets: pattern: prereqs
            normal = normal.split(":", 1)[1]
        rules = []
        for target in targets:
            if _is_special_target(target):
                if target == ".PHONY":
                    self.phony.update(normal.split())
                elif target == ".ONESHELL":
                    self.vars[".ONESHELL"] = ("1", False)
                if target != ".DEFAULT":
                    continue
            rule = _Rule(normal.split(), order.split(), [recipe.strip()] if recipe.strip() else [])
            if "%" in target:
                self.patterns.append((target, rule))
            else:
                existing = self.rules.get(target)
                if existing is not None:
                    existing.prereqs += rule.prereqs
                    existing.order_only += rule.order_only
                    if rule.recipe or sep == "::":
                        existing.recipe = (existing.recipe if sep == "::" else []) + rule.recipe
                    rule = existing
                else:
                    self.rules[target] = rule
                if not self.default_goal and (not target.startswith(".") or "/" in target):
                    self.default_goal = target
            rules.append(rule)
        return rules

    # -- expansion --

    def var(self, name: str) -> str:
        value = self._var(name)
        if _PRODUCED_TEXT in value:
            self.produced_reads += 1
        return value

    def _var(self, name: str) -> str:
        if name in self.auto:
            return self.auto[name]
        if len(name) == 2 and name[0] in "@<^+?*%|" and name[1] in "DF":
            value = self.auto.get(name[0], "")
            return " ".join((os.path.dirname(w) or "." if name[1] == "D" else os.path.basename(w))
                            for w in value.split())
        if name in self.overrides:
            return self.overrides[name]
        if name in self.vars:
            value, recursive = self.vars[name]
            return self.expand(value) if recursive else value
        if name in os.environ:
            return os.environ[name]
        return builtin_var(name, self)

    def expand(self, text: str, depth: int = 0) -> str:
        if "$" not in text:
            return text
        if depth > _MAX_DEPTH:
            raise Unreadable("make variables nest too deeply")
        before = self.produced_reads
        out: list[str] = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch != "$" or i + 1 >= len(text):
                out.append(ch)
                i += 1
                continue
            nxt = text[i + 1]
            if nxt == "$":
                out.append("$")
                i += 2
            elif nxt in "({":
                end = _matching(text, i + 1)
                out.append(self._reference(text[i + 2:end], depth + 1))
                i = end + 1
            else:
                out.append(self.var(nxt))
                i += 2
            if sum(len(part) for part in out) > _MAX_TEXT:
                raise Unreadable("make variables expand to too much text")
        result = "".join(out)
        # Text functions may remove or transform the marker. Their result is
        # still unknown when any input came from unobserved command output.
        if self.produced_reads != before and _PRODUCED_TEXT not in result:
            result = _PRODUCED_TEXT + result
        if _PRODUCED_TEXT in result:
            self.produced_reads += 1
        return result

    def produced_text(self, command: str) -> str:
        """Keep a producer's effects, but never invent the bytes it prints."""
        if _PRODUCED_TEXT in command:
            self.opaque_reason = "make command uses command output nuh cannot resolve"
        else:
            self.parse_shell.append(command)
        self.produced_reads += 1
        return _PRODUCED_TEXT

    def _reference(self, body: str, depth: int) -> str:
        name, sep, args = body.partition(" ")
        if not sep:
            name, sep, args = body.partition("\t")
        if sep and name in FUNCTIONS:
            return FUNCTIONS[name](self, split_args(args), depth)
        if ":" in body and "=" in body.split(":", 1)[1]:  # substitution reference $(VAR:.c=.o)
            var, spec = body.split(":", 1)
            old, new = spec.split("=", 1)
            words = self.var(self.expand(var, depth)).split()
            return " ".join(patsubst(old if "%" in old else "%" + old, new if "%" in new else "%" + new, w)
                            for w in words)
        return self.var(self.expand(body, depth))

    # -- what runs --

    def recipe_lines(self, always_make: bool) -> list[str]:
        goals = self.goals or ([self.default_goal] if self.default_goal else [])
        if not goals:
            raise Unreadable("the makefile has no targets")
        lines: list[str] = []
        done: set[str] = set()
        stale: dict[str, bool] = {}
        for goal in goals:
            self._visit(goal, lines, done, stale, always_make, 0)
        return lines

    def _visit(self, target: str, lines: list[str], done: set[str], stale: dict[str, bool],
               always_make: bool, depth: int) -> None:
        if target in done or depth > 50:
            return
        done.add(target)
        found = self._rule_for(target)
        if found is None:
            if os.path.exists(os.path.join(self.directory, target)):
                return
            raise Unreadable(f"no rule for target {target}")
        rule, stem = found
        for prereq in rule.order_only:
            self._visit(prereq, lines, done, stale, always_make, depth + 1)
        if not always_make and not self._out_of_date(target, stale, depth):
            return
        for prereq in rule.prereqs:
            self._visit(prereq, lines, done, stale, always_make, depth + 1)
        self.auto = {"@": target, "<": rule.prereqs[0] if rule.prereqs else "",
                      "^": " ".join(dict.fromkeys(rule.prereqs)), "+": " ".join(rule.prereqs),
                      "?": " ".join(rule.prereqs), "*": stem, "|": " ".join(rule.order_only)}
        try:
            for raw in rule.recipe:
                line = self.expand(raw.lstrip(" \t@-+")).strip()
                line = line.lstrip("@-+").strip()
                if _PRODUCED_TEXT in line:
                    self.opaque_reason = "make recipe uses command output nuh cannot resolve"
                elif line:
                    lines.append(line)
        finally:
            self.auto = {}
        if len(lines) > _MAX_LINES:
            raise Unreadable("the recipes are too long to read")

    def _rule_for(self, target: str) -> tuple[_Rule, str] | None:
        if target in self.rules:
            return self.rules[target], ""
        for pattern, rule in self.patterns:
            stem = pattern_stem(pattern, target)
            if stem is None:
                continue
            prereqs = [p.replace("%", stem) for p in rule.prereqs]
            if all(os.path.exists(os.path.join(self.directory, p)) or p in self.rules for p in prereqs):
                return _Rule(prereqs, rule.order_only, rule.recipe), stem
        if ".DEFAULT" in self.rules:
            return self.rules[".DEFAULT"], ""
        return None

    def _out_of_date(self, target: str, stale: dict[str, bool], depth: int) -> bool:
        if target in stale:
            return stale[target]
        stale[target] = True  # a dependency cycle counts as out of date
        path = os.path.join(self.directory, target)
        found = self._rule_for(target)
        if target in self.phony or not os.path.exists(path):
            result = found is not None
        elif found is None:
            result = False
        else:
            mtime = os.path.getmtime(path)
            result = False
            for prereq in found[0].prereqs:
                prereq_path = os.path.join(self.directory, prereq)
                if (depth < 50 and self._out_of_date(prereq, stale, depth + 1)) or (
                        os.path.exists(prereq_path) and os.path.getmtime(prereq_path) > mtime):
                    result = True
                    break
        stale[target] = result
        return result


# -- helpers --

def _is_special_target(target: str) -> bool:
    """.PHONY, .ONESHELL, .SUFFIXES, .DEFAULT and the like (a dot, then capitals)."""
    name = target[1:].replace("_", "")
    return target.startswith(".") and name.isalpha() and name.isupper()


def _logical_line(raw: list[str], i: int) -> tuple[str, int]:
    """The line at *i* with backslash continuations joined, and the next index.

    A recipe line keeps one line for the shell: the backslash-newline the
    shell would drop is dropped, with the continuation's recipe tab.
    """
    line = raw[i]
    i += 1
    recipe = line.startswith("\t")
    while line.endswith("\\") and i < len(raw):
        nxt = raw[i]
        i += 1
        if recipe:
            line = line[:-1] + (nxt[1:] if nxt.startswith("\t") else nxt)
        else:
            line = line[:-1].rstrip() + " " + nxt.lstrip()
    return line, i


def _strip_comment(line: str) -> str:
    out = []
    escaped = False
    for ch in line:
        if ch == "#" and not escaped:
            break
        escaped = ch == "\\" and not escaped
        out.append(ch)
    return "".join(out)


def _split_rule(line: str) -> tuple[str, str, str]:
    """(targets, ':' or '::', the rest) of a rule line; sep '' when it is not one."""
    depth = 0
    for i, ch in enumerate(line):
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == ":" and depth == 0:
            if line[i + 1:i + 2] == ":":
                return line[:i].rstrip("&"), "::", line[i + 2:]
            return line[:i].rstrip("&"), ":", line[i + 1:]
    return line, "", ""


def _matching(text: str, open_at: int) -> int:
    opener = text[open_at]
    closer = ")" if opener == "(" else "}"
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    raise Unreadable("unbalanced make variable reference")


def resolve(cmd: str, args: list[str]) -> Recipe | None:
    """The recipe lines ``make ARGS`` would run; None for read-only forms (``-n``, ``-p``, ``-q``, ``--help``)."""
    inv = parse_args(args)
    if inv.read_only:
        return None
    directory = os.path.abspath(inv.directory)
    if not os.path.isdir(directory):
        raise Unreadable(f"no directory {inv.directory}")
    files = [f if os.path.isabs(f) else os.path.join(directory, f) for f in inv.files]
    if not files:
        found = next((os.path.join(directory, n) for n in _MAKEFILES if os.path.isfile(os.path.join(directory, n))),
                     None)
        if found is None:
            raise Unreadable(f"no makefile in {directory}")
        files = [found]
    mk = Makefile(directory, inv.overrides, inv.goals)
    try:
        for path in files:
            mk.read(path)
        shell_value, flags = mk.var("SHELL"), mk.var(".SHELLFLAGS")
        if _PRODUCED_TEXT in shell_value or _PRODUCED_TEXT in flags:
            mk.opaque_reason = "make shell invocation uses command output nuh cannot resolve"
        elif shell_value.split() and os.path.basename(shell_value.split()[0]) not in _SHELLS:
            raise Unreadable(f"SHELL is {shell_value}, not a shell nuh reads")
        recipes = mk.recipe_lines(inv.always_make)
    except Unreadable as exc:
        if not mk.parse_shell:
            raise
        # Producers may already have executed while make read the file. A
        # later unreadable structure must not discard their known effects.
        mk.opaque_reason = ("makefile structure uses command output nuh cannot resolve"
                            if _PRODUCED_TEXT in str(exc) else str(exc))
        recipes = []
    lines = mk.parse_shell + recipes
    label = f"{cmd} {' '.join(inv.goals)}".strip()
    return Recipe(label, files[0], lines, directory, ".ONESHELL" in mk.vars, mk.opaque_reason)
