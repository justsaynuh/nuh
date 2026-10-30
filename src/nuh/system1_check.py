"""``nuh system1 check``: load a definition as the system 1 provider does, and say what it asks.

The base and every ``tools:`` block are loaded with ``system1_def.parse``,
each block's key resolved through the same matching a tool call's name goes
through. Gates are compiled. No model is called.
"""

from __future__ import annotations

import contextlib
import io

from nuh import gating, system1_def, system1_gates
from nuh.system1_def import Context, Definition
from nuh.system1_rules import CHOICE, Question

_PREVIEW = 70


class Report:
    """What ``check`` found: the lines describing the definition, and warnings to show."""

    def __init__(self, where: str) -> None:
        self.lines = [f"Checked {where}"]
        self.warnings: list[str] = []

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


def check(where: str) -> list[str]:
    """The report for the definition file *where*; DefinitionError when any part of it cannot be used."""
    data = system1_def.read(where)
    report = Report(where)
    base = _load(data, where, "", report)
    report.lines += describe(base)
    _gates(base, "", report)
    blocks = data.get("tools") or {}
    for key in blocks:
        defn = _load(data, where, key, report)
        report.lines += [f"tools.{key}{_followers(key, blocks)}:", *(f"  {line}" for line in summarize(defn))]
        _gates(defn, f"tools.{key}: ", report)
    return [*report.lines, *(f"warning: {text}" for text in report.warnings), "OK"]


def _load(data: object, where: str, tool: str, report: Report) -> Definition:
    """``system1_def.parse`` for *tool*, its stderr warnings kept once each for the report."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        defn = system1_def.parse(data, where, tool)
    for line in err.getvalue().splitlines():
        report.warn(line.removeprefix(f"nuh: system1: {where}: "))
    return defn


def _followers(key: str, blocks: dict) -> str:
    """The tools Edit's block also serves (MultiEdit and the rest, unless a block of their own wins)."""
    others = [tool for tool in gating.FOLLOWS_EDIT if key == "Edit" and gating.match(tool, blocks) == key]
    return f" (also {', '.join(others)})" if others else ""


def _gates(defn: Definition, label: str, report: Report) -> None:
    for gate, names in system1_gates.unbound_names(defn.gates, defn.questions):
        report.warn(f"{label}gate {gate.name} reads {', '.join(names)}, not a question here: unless it is "
                    "a CEL comprehension's own variable, the gate errors whenever it needs it, failing the review")


def describe(defn: Definition) -> list[str]:
    """The base definition, a line or a few per part."""
    lines = [f"preamble: {_preview(defn.preamble)}", f"context: {context_line(defn.context)}",
             f"questions ({len(defn.questions)}):"]
    width = max(len(name) for name in defn.questions)
    lines += [f"  {name.ljust(width)}  {q.type.ljust(6)}  {decides(q)}" for name, q in defn.questions.items()]
    lines.append(f"gates ({len(defn.gates)}):" if defn.gates else "gates: none")
    lines += [f"  {gate.name}: if {gate.expression} then {gate.then}" for gate in defn.gates]
    return lines


def summarize(defn: Definition) -> list[str]:
    """A tool's merged definition, in short: what it asks, its gates and context."""
    return [f"questions: {', '.join(defn.questions)}",
            f"gates: {', '.join(gate.name for gate in defn.gates) or 'none'}",
            f"context: {context_line(defn.context)}"]


def context_line(context: Context) -> str:
    sizes = f"action_tokens {context.action_tokens}, session_tokens {context.session_tokens}, " \
            f"file_lines {context.file_lines}"
    items = [name for name, on in (("user", context.user), ("project", context.project),
                                   ("cwd_rel", context.cwd_rel), ("git branch", context.git_branch),
                                   ("git dirty", context.git_dirty)) if on]
    return f"{sizes}; sends {', '.join(items) or 'the tool call only'}"


def decides(q: Question) -> str:
    """How question *q*'s answer decides, or that it is only logged."""
    if q.plain:
        return "the top choice decides"
    if q.type == CHOICE:
        parts = [f"p({option}) {cmp} → {decision}" for option, rules in q.options.items()
                 for decision, cmp in rules.items()]
        return f"decides: {'; '.join(parts)}" if parts else "logged only"
    parts = [f"{decision} {cmp}" for decision, cmp in q.thresholds.items()]
    if q.ask_middle:
        parts.append("ask between")
    return f"decides: {', '.join(parts)}" if parts else "logged only"


def _preview(text: str) -> str:
    if not text:
        return "(none)"
    flat = " ".join(text.split())
    return f"{flat[:_PREVIEW]}... ({len(text)} chars)" if len(flat) > _PREVIEW else flat
