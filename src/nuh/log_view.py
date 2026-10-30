"""Rendering decision-log entries for ``nuh log`` / ``nuh tail``: compact lines and details blocks."""

from __future__ import annotations

import json
import os
from datetime import date, datetime

from nuh import steps
from nuh.log_query import (SHORT_CALL_LEN, SHORT_SESSION_LEN, call_ref, entry_time, llm_failed, outcome,
                           session_id, short_call, subagent_label)

DIM, BOLD, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN = "2", "1", "31", "32", "33", "34", "35", "36"
DECISION_STYLE = {"allow": GREEN, "ask": YELLOW, "block": RED}
DECISION_MARK = {"allow": " ", "ask": "?", "block": "!"}
TRACE_MARK = "T"
_LABEL = 10  # details-view label column (longest label, "trace dir", plus a space)


def use_color(stream: object) -> bool:
    """Colour only on a terminal, and never with NO_COLOR set (non-empty) or TERM=dumb."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def truncate(text: str, limit: int) -> str:
    """*text* cut to *limit* visible characters, ending in an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return "" if limit <= 0 else text[:limit - 1] + "…"


def one_line(text: str) -> str:
    return text.replace("\r", "").replace("\n", "⏎")


def _str(obj: dict, key: str) -> str:
    value = obj.get(key)
    return value if isinstance(value, str) else ""


def _dict(obj: dict, key: str) -> dict:
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _list(obj: dict, key: str) -> list:
    value = obj.get(key)
    return value if isinstance(value, list) else []


def _num(value: object) -> str:
    """A number as a step shows it (``0.81``, ``2.4``); anything else as is."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:g}"


def _split_budget(first: int, second: int, avail: int) -> tuple[int, int]:
    """Share *avail* columns between two texts; the first keeps at least 3/5 when both overflow."""
    if first + second <= avail:
        return first, second
    take = max(0, min(first, max(avail * 3 // 5, avail - second)))
    return take, max(0, avail - take)


def llm_summary(entry: dict) -> tuple[str, str, str]:
    """``(tag, style, reason)`` for the entry's LLM step; all empty when it had none.

    ``<provider>→<decision>``; a verdict the ``llm.can_block`` cap changed
    ``<provider>:<verdict>→<capped>`` (``ollama:block→ask``); a failed step
    ``<provider>:error`` with the error as its reason. Every provider shows
    the same way, including ones nuh no longer has (older entries).
    """
    llm = _dict(entry, "llm")
    if not llm:
        return "", "", ""
    provider = _str(llm, "provider") or "(none)"
    if llm_failed(entry):
        errors = [_str(s, "error") for s in _list(llm, "cascade") if isinstance(s, dict)]
        reason = next((e for e in reversed(errors) if e), "") or _str(llm, "error")
        return f"{'llm' if provider == '(none)' else provider}:error", RED, reason
    if provider == "(none)":
        skipped = _str(llm, "skipped")
        return (f"llm:skipped({skipped})" if skipped else "llm:none"), DIM, ""
    said = outcome(entry)
    if said.capped:
        return f"{provider}:{said.llm_decision or '?'}→{said.capped}", CYAN, said.llm_reason
    return f"{provider}→{said.llm_decision or '?'}", CYAN, said.llm_reason


def full_input(entry: dict) -> str:
    """The whole command for Bash (``input`` is cut at 200 chars), else the input summary."""
    command = _dict(entry, "tool_input").get("command")
    if entry.get("tool") == "Bash" and isinstance(command, str) and command:
        return command
    return _str(entry, "input")


class LogView:
    """Renders entries one at a time, remembering what it printed (date changes, block spacing)."""

    def __init__(self, *, color: bool, width: int, details: bool = False,
                 show_full_input: bool = False, today: date | None = None) -> None:
        self.color = color
        self.width = width
        self.details = details
        self.show_full_input = show_full_input
        self.today = today or datetime.now().astimezone().date()
        self._last_day: date | None = None
        self._count = 0

    def paint(self, style: str, text: str) -> str:
        return f"\033[{style}m{text}\033[0m" if self.color and style and text else text

    def render(self, entry: dict) -> str:
        """The text to print for *entry* (possibly several lines, no trailing newline)."""
        first = self._count == 0
        self._count += 1
        if self.details:
            block = self._details(entry)
            return block if first else "\n" + block
        when = entry_time(entry)
        local = when.astimezone() if when else None
        line = self._compact(entry, local)
        if local is not None and local.date() != self._last_day:
            day = local.date()
            show = self._last_day is not None or day != self.today
            self._last_day = day
            if show:
                return self.paint(DIM, f"── {day:%a %Y-%m-%d} ──") + "\n" + line
        return line

    # -- compact --

    def _compact(self, entry: dict, local: datetime | None) -> str:
        decision = _str(entry, "decision").lower() or "?"
        session = session_id(entry)[:SHORT_SESSION_LEN] or "-"
        call = short_call(call_ref(entry)) or "-"
        head = [
            (DIM, local.strftime("%H:%M:%S") if local else "--:--:--"),
            (BLUE, session.ljust(SHORT_SESSION_LEN)),
            (CYAN, call.ljust(SHORT_CALL_LEN)),
            (DECISION_STYLE.get(decision, ""), f"{DECISION_MARK.get(decision, ' ')} {decision.upper():<5}"),
            (BOLD, (_str(entry, "tool") or "?").ljust(6)),
        ]
        sub = subagent_label(entry)
        if sub:
            head.append((MAGENTA, sub))
        head_text = " ".join(self.paint(style, text) for style, text in head) + " "
        head_len = sum(len(text) for _, text in head) + len(head)

        tag, tag_style, llm_reason = llm_summary(entry)
        reason = _str(entry, "reason")
        if llm_reason and llm_reason not in reason:
            reason = f"{reason} · {llm_reason}" if reason else llm_reason
        action = _str(entry, "action_type")
        fixed = [(tag_style, f"  {tag}" if tag else ""),
                 (DIM, f" {entry['ms']}ms" if isinstance(entry.get("ms"), int) else ""),
                 (DIM, f" {TRACE_MARK}" if entry.get("trace_dir") else "")]
        fixed_len = sum(len(text) for _, text in fixed)

        # The input and the why ([action] — reason) share what the fixed parts leave.
        inp = one_line(full_input(entry) if self.show_full_input else _str(entry, "input"))
        why = (f" [{action}]" if action else "") + (
            f" — {one_line(reason)}" if reason and reason != action else "")
        avail = self.width - head_len - fixed_len
        if self.show_full_input:
            why_room = max(avail - len(inp), 40)
        else:
            inp_room, why_room = _split_budget(len(inp), len(why), avail)
            inp = truncate(inp, inp_room)
        why = truncate(why, why_room) if why_room >= 6 else ""

        return (head_text + inp + self.paint(DIM, why)
                + "".join(self.paint(style, text) for style, text in fixed))

    # -- details --

    def _row(self, label: str, value: object, style: str = MAGENTA) -> str:
        return f"  {self.paint(style, label.ljust(_LABEL))}{value}".rstrip()

    def _details(self, entry: dict) -> str:
        decision = _str(entry, "decision").lower() or "?"
        when = entry_time(entry)
        stamp = when.astimezone().isoformat(sep=" ", timespec="milliseconds") if when else "?"
        header = [self.paint(BOLD, stamp),
                  self.paint(DECISION_STYLE.get(decision, ""), f"[{decision.upper()}]"),
                  self.paint(BOLD, _str(entry, "tool") or "?")]
        sub = subagent_label(entry)
        if sub:
            header.append(self.paint(MAGENTA, sub))
        out = ["  ".join(header)]
        out.extend(self._ref_rows(entry))
        for label, key in (("project", "project"), ("input", "input")):
            if _str(entry, key):
                out.append(self._row(label, _str(entry, key)))
        if self.show_full_input and isinstance(entry.get("tool_input"), dict):
            dumped = json.dumps(entry["tool_input"], indent=2, ensure_ascii=False)
            out.append(self._row("tool_in", dumped.replace("\n", "\n" + " " * (_LABEL + 2))))
        for label, key in (("action", "action_type"), ("reason", "reason")):
            if _str(entry, key):
                out.append(self._row(label, _str(entry, key)))
        if isinstance(entry.get("steps"), list):
            out.extend(self.step_rows(entry))
        else:
            out.extend(self._stage_rows(entry))  # an entry logged before steps
        out.extend(self._llm_rows(entry))
        if _str(entry, "trace_dir"):
            out.append(self._row("trace dir", entry["trace_dir"]))
        for label, key, style in (("warning", "warning", YELLOW), ("hint", "hint", DIM)):
            if _str(entry, key):
                out.append(self._row(label, _str(entry, key), style))
        if entry.get("content_match"):
            out.append(self._row("content", json.dumps(entry["content_match"], ensure_ascii=False)))
        if isinstance(entry.get("ms"), int):
            out.append(self._row("total", f"{entry['ms']}ms", DIM))
        return "\n".join(out)

    def _ref_rows(self, entry: dict) -> list[str]:
        ref = call_ref(entry)
        rows = [self._row("call", self.paint(CYAN, ref or "-")
                          + ("" if _str(entry, "tool_use_id") else self.paint(DIM, "  (log entry id)")))]
        session = session_id(entry)
        if session:
            rows.append(self._row("session", self.paint(BLUE, session)))
        agent_id = _str(entry, "agent_id")
        if agent_id:
            kind = _str(entry, "agent_type")
            rows.append(self._row("subagent", agent_id + (f"  ({kind})" if kind else "")))
        return rows

    def _stage_rows(self, entry: dict) -> list[str]:
        stages = [s for s in _list(_dict(entry, "classify"), "stages") if isinstance(s, dict)]
        rows = [self._row("stages", "")] if stages else []
        for i, stage in enumerate(stages, 1):
            reason = _str(stage, "reason")
            rows.append(f"    {i}. {_str(stage, 'action_type') or '?':<20} → "
                        f"{_str(stage, 'decision') or '?':<5} {self.paint(DIM, '(' + reason + ')') if reason else ''}"
                        .rstrip())
        return rows

    def step_rows(self, entry: dict) -> list[str]:
        """Each step in the order it ran, one line each; the static breakdown below its step."""
        rows = [self._row("steps", "")]
        for i, step in enumerate((s for s in entry["steps"] if isinstance(s, dict)), 1):
            rows.append(f"   {i:>2}. {_str(step, 'step') or '?':<12} {self._step_text(step)}".rstrip())
            for item in _list(step, "breakdown"):
                if isinstance(item, dict):
                    rows.append(f"         {self._breakdown_text(item)}".rstrip())
            if isinstance(step.get("breakdown_omitted"), int):
                rows.append(self.paint(DIM, f"         … {step['breakdown_omitted']} more"))
        said = outcome(entry)
        why = f"reason from {said.reason_source}"
        if said.wanted:
            why += f" · LLM wanted {said.wanted}"
        rows.append(self._row("outcome", why))
        return rows

    def _step_text(self, step: dict) -> str:
        kind = _str(step, "step")
        target = f"  [{_str(step, 'target')}]" if _str(step, "target") else ""
        if kind in (steps.STATIC, steps.LLM):
            decision = _str(step, "decision")
            text = self.paint(DECISION_STYLE.get(decision, ""), f"{decision or '-':<5}")
            extra = [_str(step, "reason")]
            if kind == steps.LLM and _str(step, "error"):
                extra.insert(0, self.paint(RED, f"no verdict ({_str(step, 'error')})"))
            if _str(step, "composition"):
                extra.append(self.paint(DIM, f"[{_str(step, 'composition')}]"))
            return " ".join([text, *(e for e in extra if e)]) + target
        system1 = {steps.QUESTION: self._question_text, steps.GATE: self._gate_text,
                   steps.PROVIDER: self._provider_text}.get(kind)
        if system1 is not None:
            return system1(step) + target
        if "from" in step or "to" in step:
            rule = _str(step, "rule")
            return (f"{_str(step, 'from') or '-'} → {_str(step, 'to') or '-'}"
                    + (self.paint(DIM, f"  ({rule})") if rule else "") + target)
        rest = {k: v for k, v in step.items() if k != "step"}
        return json.dumps(rest, ensure_ascii=False)  # a step kind this version does not know

    def _question_text(self, step: dict) -> str:
        """``destructive  noul 0.81 > 0.7 → block``: the answer, then the threshold that fired and its decision."""
        answer = step.get("answer")
        if answer is None:
            shown = self.paint(DIM, "(no answer)")
        elif isinstance(step.get("p"), (int, float)):
            shown = f"{answer} {_num(step['p'])}"  # a choice: the top option and its probability
        else:
            shown = " ".join(p for p in (_num(answer), _str(step, "label")) if p)
        text = f"{_str(step, 'name') or '?'}  {_str(step, 'type')} {shown}"
        triggered = _str(step, "triggered")
        if triggered:
            rule = _str(step, "rule")
            text += (f" {rule}" if rule else "") + " → " + self.paint(DECISION_STYLE.get(triggered, ""), triggered)
        return text

    def _gate_text(self, step: dict) -> str:
        """``net_and_risky → ask (not fired)``."""
        then = _str(step, "then") or "?"
        if step.get("fired") is True:
            return f"{_str(step, 'name') or '?'} → {self.paint(DECISION_STYLE.get(then, ''), then)} (fired)"
        return f"{_str(step, 'name') or '?'} → {then} " + self.paint(DIM, "(not fired)")

    def _provider_text(self, step: dict) -> str:
        """``system1 nimble:9b 412ms ok``; ``uncertain``, or ``failed: <error>``."""
        ms = f"{step['ms']}ms" if isinstance(step.get("ms"), int) else ""
        head = " ".join(p for p in (_str(step, "provider") or "?", _str(step, "model"), ms) if p)
        outcome = _str(step, "outcome")
        if outcome == "decision":
            return f"{head} {self.paint(GREEN, 'ok')}"
        if outcome == "failure":
            return f"{head} " + self.paint(RED, f"failed: {_str(step, 'error') or '?'}")
        return f"{head} {self.paint(YELLOW, outcome or '?')}"

    def _breakdown_text(self, item: dict) -> str:
        name = _str(item, "action_type") or _str(item, "check") or "?"
        reason = _str(item, "reason")
        text = f"{name:<20} → {_str(item, 'decision') or '?':<5}"
        if reason:
            text += " " + self.paint(DIM, f"({reason})")
        for label in ("rule", "target"):
            if _str(item, label):
                text += self.paint(DIM, f" {label} {_str(item, label)}")
        return text

    def _llm_rows(self, entry: dict) -> list[str]:
        llm = _dict(entry, "llm")
        if not llm:
            return []
        tag, style, reason = llm_summary(entry)
        model = _str(llm, "model")
        ms = llm.get("ms")
        head = self.paint(style, tag) + (f"  {model}" if model else "")
        rows = [self._row("llm", head + (f"  {ms}ms" if isinstance(ms, int) and ms else ""))]
        sub = " " * 4
        legacy = not isinstance(entry.get("steps"), list)  # newer entries show the verdict as steps
        said = outcome(entry)
        if legacy and said.llm_decision:
            rows.append(f"{sub}verdict      {said.llm_decision}"
                        + (f", capped to {said.capped}" if said.capped else ""))
        if reason and (legacy or reason != said.llm_reason):
            rows.append(f"{sub}reason       {reason}")
        if _str(llm, "skipped"):
            why = _str(llm, "skip_reason")
            rows.append(f"{sub}skipped      {_str(llm, 'skipped')}" + (f" — {why}" if why else ""))
        if _str(llm, "error"):
            rows.append(f"{sub}error        {_str(llm, 'error')}")
        for step in _list(llm, "cascade"):
            if isinstance(step, dict):
                rows.append(self._cascade_row(step))
        return rows

    def _cascade_row(self, step: dict) -> str:
        status = _str(step, "status") or "?"
        style = GREEN if status == "success" else YELLOW if status == "uncertain" else RED  # uncertain: older logs
        text = f"    cascade      {_str(step, 'provider') or '?'} {self.paint(style, status)}"
        if isinstance(step.get("latency_ms"), int):
            text += f" {step['latency_ms']}ms"
        if _str(step, "error"):
            text += " — " + self.paint(RED, step["error"])
        return text
