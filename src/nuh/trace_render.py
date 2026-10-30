"""Rendering one LLM trace for ``nuh trace``: the answers, the prompt, the decision path and the raw files.

Every value comes from the trace's own JSON and is read defensively (a
partial trace renders what it has). The decision path is ``nuh log -v``'s
step formatter (log_view.LogView.step_rows) over ``result.json``'s steps.
"""

from __future__ import annotations

import json
import textwrap

from nuh import steps as steps_mod
from nuh.log_view import BOLD, CYAN, DECISION_STYLE, DIM, MAGENTA, RED, YELLOW, LogView, one_line
from nuh.notice import CALL_CHARS
from nuh.trace_read import SYSTEM1, Trace, choice_p, number, summarize, system1_answers
from nuh.trace_views import ALL, ANSWERS, PATH, PROMPT, RAW, VIEWS

_TITLES = {ANSWERS: "answers", PROMPT: "prompt", PATH: "decision path", RAW: "raw files"}
_BAR = 10
_EIGHTHS = " ▏▎▍▌▋▊▉█"
_LABEL = 11


def bar(fraction: float | None, width: int = _BAR) -> str:
    """*fraction* (0..1, clamped) as a bar of *width* cells, in eighths."""
    if fraction is None:
        return " " * width
    eighths = round(max(0.0, min(1.0, fraction)) * width * 8)
    full, part = divmod(eighths, 8)
    text = "█" * full + (_EIGHTHS[part] if part else "")
    return text.ljust(width)


def _dict(obj: object, key: str) -> dict:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def _list(obj: object, key: str) -> list:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, list) else []


def _str(obj: object, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, str) else ""


def _num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def final_steps(trace: Trace) -> list[dict]:
    """The decision's steps as ``result.json`` keeps them (``final._steps``)."""
    return [s for s in _list(_dict(trace.get("result.json"), "final"), steps_mod.KEY) if isinstance(s, dict)]


class Renderer:
    """Renders traces as text for a terminal of *width* columns, in colour when *color*."""

    def __init__(self, *, color: bool, width: int) -> None:
        self.log_view = LogView(color=color, width=width, details=True)
        self.width = max(40, width)

    def paint(self, style: str, text: str) -> str:
        return self.log_view.paint(style, text)

    def row(self, label: str, value: str, style: str = MAGENTA) -> str:
        return f"  {self.paint(style, label.ljust(_LABEL))}{value}".rstrip()

    def wrap(self, text: str, indent: int) -> list[str]:
        """*text* wrapped to the width at *indent*, its own line breaks kept."""
        room = max(20, self.width - indent)
        pad = " " * indent
        out = []
        for line in text.splitlines() or [""]:
            out.extend((pad + piece).rstrip() for piece in (textwrap.wrap(line, room, replace_whitespace=False,
                                                                          drop_whitespace=False) or [""]))
        return out

    def render(self, trace: Trace, view: str) -> str:
        out = self.header(trace)
        for name in VIEWS if view == ALL else (view,):
            if view == ALL:
                out += ["", self.paint(BOLD, f"── {_TITLES[name]} ──")]
            out += {ANSWERS: self.answers, PROMPT: self.prompt, PATH: self.path, RAW: self.raw}[name](trace)
        return "\n".join(out)

    # -- header --

    def header(self, trace: Trace) -> list[str]:
        summary = summarize(trace)
        when = trace.stamp.astimezone().isoformat(sep=" ", timespec="milliseconds") if trace.stamp else "?"
        decision = summary.decision or "?"
        head = [self.paint(BOLD, when), self.paint(CYAN, f"#{summary.call[-CALL_CHARS:]}" if summary.call else "#-"),
                self.paint(BOLD, summary.tool or "?"),
                self.paint(DECISION_STYLE.get(decision, ""), f"[{decision.upper()}]")]
        if summary.provider:
            head.append(summary.provider + (f" {summary.model}" if summary.model else ""))
        total = trace.get("result.json").get("total_ms")
        if isinstance(total, int):
            head.append(self.paint(DIM, f"{total}ms"))
        out = ["  ".join(head)]
        if summary.input:
            out.append(self.row("input", one_line(summary.input)))
        out.append(self.row("trace", trace.path, DIM))
        for name, why in trace.errors.items():
            out.append(self.row("unreadable", self.paint(RED, f"{name or 'directory'}: {why}")))
        return out

    # -- answers --

    def answers(self, trace: Trace) -> list[str]:
        out: list[str] = []
        answers = system1_answers(trace)
        if answers:
            out += self._question_table(trace, answers)
            out += self._distributions(trace, answers)
            out += self._gates(trace)
        for review in [r for r in _list(trace.get("result.json"), "reviews") if isinstance(r, dict)]:
            out += self._review(trace, review)
        final = _dict(trace.get("result.json"), "final")
        if final:
            decision = _str(final, "decision") or "?"
            out.append(self.row("decision", self.paint(DECISION_STYLE.get(decision, BOLD), decision.upper())))
            if _str(final, "reason"):
                out += self._wrapped_row("reason", _str(final, "reason"))
            merges = [s for s in final_steps(trace) if "from" in s and "to" in s]
            for step in merges:
                out.append(self.row("via", f"{_str(step, 'step')} {_str(step, 'from')} → {_str(step, 'to')}"
                                    + self.paint(DIM, f"  ({_str(step, 'rule')})" if _str(step, "rule") else "")))
        elif "result.json" not in trace.files:
            out.append(self.row("decision", self.paint(YELLOW, "? (no result.json: the hook did not finish)")))
        return out

    def _wrapped_row(self, label: str, text: str) -> list[str]:
        lines = self.wrap(text, _LABEL + 2)
        lines[0] = self.row(label, lines[0].lstrip())
        return lines

    def _question_table(self, trace: Trace, answers: dict) -> list[str]:
        asked = _questions(trace)
        decided = {_str(s, "name"): s for s in final_steps(trace) if s.get("step") == steps_mod.QUESTION}
        names = list(dict.fromkeys([*asked, *answers]))
        width = max([8, *map(len, names)])
        out = [self.paint(DIM, f"  {'question'.ljust(width)}  {'type':<6}  {'answer':<14}  "
                               f"{'value'.ljust(_BAR)} {'':<6}  {'conf':<4}  decided")]
        for name in names:
            answer = answers.get(name) if isinstance(answers.get(name), dict) else {}
            kind = _str(answer, "type") or _str(asked.get(name), "type")
            shown, fraction, value = _answer_cells(answer, kind, _labels(answer, asked.get(name)))
            why = self._decided(decided.get(name, {}))
            conf = number(answer.get("confidence"))
            out.append(f"  {name.ljust(width)}  {kind:<6}  {shown[:14]:<14}  {self.paint(CYAN, bar(fraction))} "
                       f"{value:<6}  {_num(conf):<4}  {why}".rstrip())
        return out

    def _decided(self, step: dict) -> str:
        """What a question triggered, and by which threshold: ``block (> 0.7)``."""
        said, rule = _str(step, "triggered"), _str(step, "rule")
        if not said:
            return ""
        return self.paint(DECISION_STYLE.get(said, ""), said) + (self.paint(DIM, f" ({rule})") if rule else "")

    def _distributions(self, trace: Trace, answers: dict) -> list[str]:
        asked = _questions(trace)
        out = []
        for name, answer in answers.items():
            probs = answer.get("probabilities") if isinstance(answer, dict) else None
            if not isinstance(probs, dict) or not probs:
                continue
            kind = _str(answer, "type") or _str(asked.get(name), "type")
            labels = _labels(answer, asked.get(name))
            out.append(self.paint(DIM, f"  {name} {'distribution' if kind == 'score' else 'probabilities'}"))
            keys = [str(k) for k in probs]
            width = max(len(_key_label(k, labels)) for k in keys)
            for key in keys:
                p = number(probs.get(key))
                out.append(f"    {_key_label(key, labels).ljust(width)}  {self.paint(CYAN, bar(p))} {_num(p)}")
        return out

    def _gates(self, trace: Trace) -> list[str]:
        gates = [s for s in final_steps(trace) if s.get("step") == steps_mod.GATE]
        out = []
        for gate in gates:
            state = self.paint(YELLOW, "fired") if gate.get("fired") is True else self.paint(DIM, "not fired")
            out.append(self.row("gate", f"{_str(gate, 'name') or '?'} → {_str(gate, 'then') or '?'}  {state}"))
        return out

    def _review(self, trace: Trace, review: dict) -> list[str]:
        """One review's LLM: provider, its verdict (confidence, cap), reasoning and failed cascade attempts."""
        llm = _dict(review, "llm")
        out = []
        if llm:
            provider, model, ms = _str(llm, "provider") or "?", _str(llm, "model"), llm.get("latency_ms")
            extra = (f"  {ms}ms" if isinstance(ms, int) and ms else "") + (
                f"  [{_str(review, 'kind')}]" if _str(review, "kind") else "")
            out.append(self.row("provider", f"{provider} {model}".rstrip() + self.paint(DIM, extra)))
            said = _str(_dict(review, "llm_decision"), "decision") or _str(_dict(llm, "decision"), "decision")
            conf = _confidence(trace, provider)
            verdict = self.paint(DECISION_STYLE.get(said, ""), said or "-")
            verdict += f"  confidence {conf:.2f}" if conf is not None else ""
            if review.get("capped") is True:
                verdict += self.paint(YELLOW, f"  capped to {_str(_dict(review, 'after_cap'), 'decision')}")
            out.append(self.row("llm", verdict))
            if _str(llm, "reasoning"):
                out += self._wrapped_row("reasoning", _str(llm, "reasoning"))
            for attempt in [a for a in _list(llm, "cascade") if isinstance(a, dict) and _str(a, "error")]:
                failed = self.paint(RED, _str(attempt, "error"))
                out.append(self.row("cascade", f"{_str(attempt, 'provider')} {failed}"))
        error = _dict(review, "error")
        if error:
            out.append(self.row("error", self.paint(RED, f"{_str(error, 'type')}: {_str(error, 'message')}")))
        return out

    # -- prompt --

    def prompt(self, trace: Trace) -> list[str]:
        out = []
        for name, _provider in trace.attempts("request"):
            request = trace.get(name)
            if not request:
                out.append(self.row(name, self.paint(RED, trace.errors.get(name, "unreadable"))))
                continue
            head = f"{_str(request, 'method')} {_str(request, 'url')}".strip()
            out.append(self.paint(BOLD, name) + "  " + self.paint(DIM, head or "(no HTTP request was made)"))
            body = request.get("body")
            if isinstance(body, dict):
                out += self._body(body)
            elif _str(request, "body_raw"):
                out += self.wrap(_str(request, "body_raw"), 4)
        return out or [self.row("prompt", self.paint(DIM, "no request recorded"))]

    def _body(self, body: dict) -> list[str]:
        out = []
        if _str(body, "model"):
            out.append(self.row("model", _str(body, "model")))
        if isinstance(body.get("system"), (str, list)):
            out += self._message("system", body["system"])
        if isinstance(body.get("state"), str):
            out += self._message("state", body["state"])
        if isinstance(body.get("prompt"), str):
            out += self._message("prompt", body["prompt"])
        for message in _list(body, "messages"):
            if isinstance(message, dict):
                out += self._message(_str(message, "role") or "message", message.get("content"))
        for qname, question in _dict(body, "questions").items():
            out += self._question(qname, question)
        if len(out) <= 1:
            out += self.wrap(json.dumps(body, indent=2, ensure_ascii=False), 4)
        return out

    def _message(self, role: str, content: object) -> list[str]:
        if isinstance(content, list):  # content blocks: [{"type": "text", "text": ...}]
            content = "\n".join(_str(b, "text") or json.dumps(b, ensure_ascii=False) for b in content)
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        return [self.paint(MAGENTA, f"  [{role}]"), *self.wrap(text, 4)]

    def _question(self, name: str, question: object) -> list[str]:
        out = [self.paint(MAGENTA, f"  [question {name}]") + self.paint(DIM, f" {_str(question, 'type')}")]
        out += self.wrap(_str(question, "instructions"), 4)
        criteria = question.get("criteria") if isinstance(question, dict) else None
        if isinstance(criteria, dict):
            out += [line for key, text in criteria.items() for line in self.wrap(f"{key}: {text}", 6)]
        elif isinstance(criteria, list):
            out += self.wrap("levels: " + ", ".join(f"{i} {c}" for i, c in enumerate(criteria)), 6)
        return out

    # -- decision path --

    def path(self, trace: Trace) -> list[str]:
        final = _dict(trace.get("result.json"), "final")
        trail = final_steps(trace)
        if not trail:
            return [self.row("steps", self.paint(DIM, "none recorded (no result.json, or a trace from before steps)"))]
        return self.log_view.step_rows({"steps": trail, "decision": _str(final, "decision")})

    # -- raw --

    def raw(self, trace: Trace) -> list[str]:
        out = []
        for name in trace.listing:
            out.append(self.paint(BOLD, f"── {name} ──"))
            if name in trace.files:
                out.append(json.dumps(trace.files[name], indent=2, ensure_ascii=False))
            elif name in trace.errors:
                out.append(self.paint(RED, trace.errors[name]))
            else:
                out.append(self.paint(DIM, "(not JSON; not shown)"))
        return out


def _questions(trace: Trace) -> dict:
    """The system 1 questions as sent (name -> {type, instructions, criteria}), from the last request."""
    for name, provider in reversed(trace.attempts("request")):
        if provider == SYSTEM1:
            asked = _dict(trace.get(name).get("body"), "questions")
            if asked:
                return asked
    return {}


def _labels(answer: dict, asked: object) -> list[str]:
    """A score's level labels: the reply's ``legend``, else the question's criteria."""
    legend = answer.get("legend")
    if isinstance(legend, dict):
        return [str(legend[k]) for k in sorted(legend, key=int)] if all(str(k).isdigit() for k in legend) else []
    if isinstance(legend, list):
        return [str(item) for item in legend]
    criteria = asked.get("criteria") if isinstance(asked, dict) else None
    return [str(c) for c in criteria] if isinstance(criteria, list) else []


def _key_label(key: str, labels: list[str]) -> str:
    if key.isdigit() and int(key) < len(labels):
        return f"{key} {labels[int(key)]}"
    return key


def _answer_cells(answer: dict, kind: str, labels: list[str]) -> tuple[str, float | None, str]:
    """``(answer, bar fraction, value)`` of one answer, by its type."""
    if kind == "choice":
        choice = _str(answer, "choice")
        p = choice_p(answer, choice)
        return choice or "-", p, _num(p)
    if kind == "score":
        score = number(answer.get("score"))
        if score is None:
            return "-", None, "-"
        label = _str(answer, "label") or (labels[min(len(labels) - 1, max(0, round(score)))] if labels else "")
        top = max(1, len(labels) - 1)
        return label or _num(score), score / top, f"{score:.2f}" + (f"/{top}" if labels else "")
    p = number(answer.get(kind)) if kind else None
    if p is None:
        return "-", None, "-"
    return ("yes" if p >= 0.5 else "no"), p, _num(p)


def _confidence(trace: Trace, provider: str) -> float | None:
    """A prompted provider's own confidence, when its parsed reply has one."""
    for name, attempt_provider in reversed(trace.attempts("response")):
        if attempt_provider == provider:
            return number(_dict(trace.get(name), "parsed").get("confidence"))
    return None
