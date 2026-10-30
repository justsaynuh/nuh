"""Reading LLM trace directories for ``nuh trace``: newest first, robust to partial or corrupt traces.

A trace directory's name starts with a sortable UTC timestamp (trace_names),
so the list sorts names and opens only as many traces as it shows. A trace
that cannot be read (a file missing, cut short or not JSON, a directory that
cannot be listed) is still listed, with what went wrong; nothing here raises
for a trace's contents.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from nuh import trace_names
from nuh.log import input_summary
from nuh.log_query import LogFilter

SYSTEM1 = "system1"
_ATTEMPT_FILE = re.compile(r"(request|response)-(\d{2,})-([A-Za-z0-9_.-]+)\.json")
_VERDICT, _RISK = "verdict", "risk"  # the shipped definition's names; other definitions use the first of each type


@dataclass
class Trace:
    """One trace directory as read: each JSON file parsed, or why it could not be."""

    path: str
    name: str
    stamp: datetime | None
    call: str
    files: dict[str, object] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # file name (or "" for the directory) -> what failed
    listing: list[str] = field(default_factory=list)

    def get(self, name: str) -> dict:
        value = self.files.get(name)
        return value if isinstance(value, dict) else {}

    def attempts(self, kind: str) -> list[tuple[str, str]]:
        """``(file name, provider)`` of each ``request``/``response`` file, in attempt order."""
        found = []
        for name in self.listing:
            match = _ATTEMPT_FILE.fullmatch(name)
            if match and match[1] == kind:
                found.append((int(match[2]), name, match[3]))
        return [(name, provider) for _, name, provider in sorted(found)]


def root_dir(cfg: object) -> str:
    """The global ``llm.trace_dir``, even where a project's ``trace: false`` stops new traces."""
    from nuh.config_llm import parse_trace_dir  # lazy import to avoid circular
    llm = getattr(cfg, "llm", None)
    return parse_trace_dir(llm.get("trace_dir")) if isinstance(llm, dict) else ""


def names(root: str) -> list[str]:
    """The trace directory names under *root*, newest first (OSError when *root* cannot be listed)."""
    found = [name for name in os.listdir(root) if trace_names.looks_like(name)]
    found.sort(reverse=True)
    return found


def find(root: str, ref: str) -> str:
    """The newest trace name under *root* for call reference *ref* (``#abc123``, an ID's start or end), or ""."""
    flt = LogFilter(call=ref)
    for name in names(root):
        rest = name.split("_", 1)[1]
        for call in dict.fromkeys((trace_names.call_of(name), rest)):
            if call and flt.matches({"tool_use_id": call}):
                return name
    return ""


def load(path: str, *, wanted: tuple[str, ...] | None = None) -> Trace:
    """Read the trace at *path*: every JSON file, or only *wanted* names and response files."""
    name = os.path.basename(os.path.normpath(path))
    parsed = trace_names.parse(name)
    trace = Trace(path, name, parsed.stamp if parsed else None, parsed.call if parsed else "")
    try:
        trace.listing = sorted(os.listdir(path))
    except OSError as exc:
        trace.errors[""] = f"cannot list: {exc.strerror or exc}"
        return trace
    for file in trace.listing:
        if not file.endswith(".json"):
            continue
        if wanted is not None and file not in wanted and not file.startswith("response-"):
            continue
        _read_json(trace, file)
    for required in ("input.json", "result.json"):
        if (wanted is None or required in wanted) and required not in trace.listing:
            trace.errors[required] = "missing"
    return trace


def _read_json(trace: Trace, file: str) -> None:
    try:
        with open(os.path.join(trace.path, file), "rb") as f:
            trace.files[file] = json.loads(f.read())
    except OSError as exc:
        trace.errors[file] = f"cannot read: {exc.strerror or exc}"
    except (ValueError, RecursionError) as exc:
        # Cut short by a full disk or a killed hook, or not JSON at all.
        trace.errors[file] = f"not JSON: {type(exc).__name__}"


# -- Summary: one list line's worth --


@dataclass
class Summary:
    name: str
    path: str
    stamp: datetime | None
    call: str
    tool: str = ""
    input: str = ""
    decision: str = ""
    reason: str = ""
    provider: str = ""
    model: str = ""
    verdict: dict = field(default_factory=dict)  # {"question", "choice", "p"} of system 1's verdict question
    risk: dict = field(default_factory=dict)     # {"question", "score", "label"} of system 1's risk question
    error: str = ""                              # why (part of) the trace could not be read

    def as_json(self) -> dict:
        return {"dir": self.name, "path": self.path, "time": self.stamp.isoformat() if self.stamp else None,
                "call": self.call, "tool": self.tool, "input": self.input, "decision": self.decision,
                "reason": self.reason, "provider": self.provider, "model": self.model,
                "verdict": self.verdict or None, "risk": self.risk or None, "error": self.error or None}


def summarize(trace: Trace) -> Summary:
    """The list line's facts about *trace*; what could not be read is named in ``error``."""
    out = Summary(trace.name, trace.path, trace.stamp, trace.call)
    inp = trace.get("input.json")
    payload = inp.get("payload") if isinstance(inp.get("payload"), dict) else {}
    out.call = _text(payload.get("tool_use_id")) or trace.call
    out.tool = _text(inp.get("tool_name"))
    tool_input = inp.get("tool_input")
    if out.tool and isinstance(tool_input, dict):
        out.input = _text(input_summary(out.tool, tool_input))
    result = trace.get("result.json")
    final = result.get("final") if isinstance(result.get("final"), dict) else {}
    out.decision, out.reason = _text(final.get("decision")), _text(final.get("reason"))
    llm = used_review(result).get("llm")
    if isinstance(llm, dict):
        out.provider, out.model = _text(llm.get("provider")), _text(llm.get("model"))
    out.verdict, out.risk = _system1_headline(system1_answers(trace))
    problems = [f"{name or 'directory'}: {why}" for name, why in trace.errors.items()]
    if "input.json" in trace.files and not out.tool:
        problems.append("input.json: no tool name")
    if "result.json" in trace.files and not out.decision:
        problems.append("result.json: no final decision")
    out.error = "; ".join(problems)
    return out


def used_review(result: dict) -> dict:
    """The review whose LLM answered last (an apply_patch has one per operation)."""
    reviews = [r for r in result.get("reviews") or [] if isinstance(r, dict)] \
        if isinstance(result.get("reviews"), list) else []
    answered = [r for r in reviews if isinstance(r.get("llm"), dict)]
    return (answered or reviews or [{}])[-1]


def system1_answers(trace: Trace) -> dict:
    """The last system 1 reply's ``answers`` (question -> answer), or {}."""
    for name, provider in reversed(trace.attempts("response")):
        if provider != SYSTEM1:
            continue
        parsed = trace.get(name).get("parsed")
        answers = parsed.get("answers") if isinstance(parsed, dict) else None
        if isinstance(answers, dict):
            return answers
    return {}


def _system1_headline(answers: dict) -> tuple[dict, dict]:
    verdict_q = _pick(answers, _VERDICT, "choice")
    risk_q = _pick(answers, _RISK, "score")
    verdict, risk = {}, {}
    if verdict_q:
        answer = answers[verdict_q]
        choice = _text(answer.get("choice"))
        verdict = {"question": verdict_q, "choice": choice, "p": choice_p(answer, choice)}
    if risk_q:
        answer = answers[risk_q]
        risk = {"question": risk_q, "score": number(answer.get("score")), "label": _text(answer.get("label"))}
    return verdict, risk


def _pick(answers: dict, preferred: str, kind: str) -> str:
    if isinstance(answers.get(preferred), dict):
        return preferred
    return next((q for q, a in answers.items() if isinstance(a, dict) and a.get("type") == kind), "")


def choice_p(answer: dict, choice: str) -> float | None:
    """The probability of *choice* in a choice answer, else its confidence."""
    probs = answer.get("probabilities")
    if isinstance(probs, dict) and number(probs.get(choice)) is not None:
        return number(probs[choice])
    return number(answer.get("confidence"))


def number(value: object) -> float | None:
    """*value* as a finite number (JSON may hold Infinity or NaN), else None."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


# -- The list --


@dataclass
class ListFilter:
    """Which traces ``nuh trace`` lists. Unset fields do not filter."""

    since: datetime | None = None
    tool: str | None = None
    decision: str | None = None
    provider: str | None = None

    def needs_contents(self) -> bool:
        return any(v is not None for v in (self.tool, self.decision, self.provider))

    def matches(self, summary: Summary) -> bool:
        return ((self.tool is None or summary.tool.casefold() == self.tool.casefold())
                and (self.decision is None or summary.decision == self.decision)
                and (self.provider is None or summary.provider.casefold() == self.provider.casefold()))


_SUMMARY_FILES = ("input.json", "result.json")


def newest(root: str, flt: ListFilter, limit: int) -> Iterator[Summary]:
    """Summaries of the newest *limit* (0 = all) traces under *root* that pass *flt*, newest first."""
    shown = 0
    for name in names(root):
        parsed = trace_names.parse(name)
        if parsed is None:
            continue  # the shape of a trace name, but not a real date: nuh did not write it
        if flt.since is not None and parsed.stamp < flt.since:
            return  # names are newest first: every later one is older still
        summary = summarize_safely(os.path.join(root, name))
        if flt.needs_contents() and not flt.matches(summary):
            continue
        yield summary
        shown += 1
        if 0 < limit <= shown:
            return


def summarize_safely(path: str) -> Summary:
    """summarize(), with a trace whose JSON has an unexpected shape shown as unreadable instead of raising."""
    trace = load(path, wanted=_SUMMARY_FILES)
    try:
        return summarize(trace)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # One malformed trace must not end the listing: it is listed with the
        # error, and the rest of the directory still shows.
        return Summary(trace.name, path, trace.stamp, trace.call, error=f"unexpected content: {exc}")
