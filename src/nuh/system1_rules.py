"""How system 1's answers become a decision: the simple form (system1_gates.py is the complex one).

Each question is asked and its answer logged. A question decides when it
carries thresholds:

- one value (``noul`` is P(yes), ``score`` the level): ``allow`` and
  ``block`` hold comparisons on it (``"< 0.2"``); ``ask: true`` makes the
  middle, where neither matched, ask. A missing key cannot trigger its decision.
- choice, detailed form: each option holds ``text`` and comparisons on its
  own probability (``ask`` is a comparison here too).
- choice, plain form (``allow: "description"``): the top choice is the
  decision, when every option is named allow, ask or block.

The strictest decision triggered anywhere wins, gates included
(system1_gates.py), ranked allow < uncertain < ask < block: an uncertain gate
or a deciding question left unanswered beats an allow, never an ask or block.
Nothing triggered is uncertain: an allow has to be triggered.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field

ALLOW, ASK, BLOCK, UNCERTAIN = "allow", "ask", "block", "uncertain"
DECISIONS = (ALLOW, ASK, BLOCK)
RANK = {ALLOW: 0, UNCERTAIN: 1, ASK: 2, BLOCK: 3}
NOUL, SCORE, CHOICE = "noul", "score", "choice"
TYPES = (NOUL, SCORE, CHOICE)
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_COMPARISON = re.compile(r"\s*(<=|>=|<|>)\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*")
_QUESTION_KEYS = frozenset({"type", "instructions", "criteria", "explain", ALLOW, ASK, BLOCK})
_OPTION_KEYS = frozenset({"text", ALLOW, ASK, BLOCK})


class DefinitionError(Exception):
    """A system 1 definition that cannot be used; the message names what is wrong."""


@dataclass(frozen=True)
class Comparison:
    op: str
    value: float

    def matches(self, x: float) -> bool:
        return _OPS[self.op](x, self.value)

    def overlaps(self, other: Comparison) -> bool:
        """True when some number satisfies both comparisons."""
        up_a, up_b = self.op[0] == "<", other.op[0] == "<"
        if up_a == up_b:
            return True  # both bound from the same side: everything past the tighter one matches both
        upper, lower = (self, other) if up_a else (other, self)
        if lower.value != upper.value:
            return lower.value < upper.value
        return upper.op == "<=" and lower.op == ">="

    def __str__(self) -> str:
        return f"{self.op} {self.value:g}"


def comparison(raw: object, where: str) -> Comparison:
    """``"< 0.2"`` as a Comparison, or DefinitionError naming *where* it is."""
    match = _COMPARISON.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        raise DefinitionError(f"{where}: {raw!r} is not a comparison (<, <=, > or >= and a number)")
    return Comparison(match[1], float(match[2]))


@dataclass(frozen=True)
class Question:
    """One question as asked, and the thresholds (if any) by which it decides."""

    name: str
    type: str
    instructions: str
    criteria: object = None                   # sent to the model: None, {true, false}, labels, or options
    explain: str = ""                          # the reason text when it triggers ({label} for a score)
    thresholds: dict = field(default_factory=dict)  # one value: allow / block -> Comparison
    ask_middle: bool = False                   # one value: neither threshold matched -> ask
    options: dict = field(default_factory=dict)     # detailed choice: option -> {decision: Comparison}
    texts: dict = field(default_factory=dict)  # choice: option -> its description
    plain: bool = False                        # plain choice whose options are decisions: the top one decides

    @property
    def deciding(self) -> bool:
        return bool(self.thresholds) or self.ask_middle or self.plain or any(self.options.values())


def question(name: str, raw: object) -> Question:
    """Parse and check one question of a definition (DefinitionError on anything malformed)."""
    where = f"questions.{name}"
    if not isinstance(raw, dict):
        raise DefinitionError(f"{where} must be a mapping")
    unknown = sorted(set(raw) - _QUESTION_KEYS)
    if unknown:
        raise DefinitionError(f"{where}: unknown key {unknown[0]!r}")
    kind, instructions, explain = raw.get("type"), raw.get("instructions"), raw.get("explain", "")
    if kind not in TYPES:
        raise DefinitionError(f"{where}.type must be noul, score or choice, not {kind!r}")
    if not isinstance(instructions, str) or not instructions.strip():
        raise DefinitionError(f"{where}.instructions must be text")
    if not isinstance(explain, str):
        raise DefinitionError(f"{where}.explain must be text")
    base = {"name": name, "type": kind, "instructions": instructions.strip(), "explain": explain.strip()}
    if kind == CHOICE:
        return _choice(where, raw, base)
    return _one_value(where, raw, base)


def _one_value(where: str, raw: dict, base: dict) -> Question:
    criteria = raw.get("criteria")
    if base["type"] == SCORE:
        if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) and c for c in criteria):
            raise DefinitionError(f"{where}.criteria must list the score's labels, lowest first")
    elif criteria is not None:
        if not isinstance(criteria, dict) or set(criteria) - {"true", "false"} or \
                not all(isinstance(v, str) for v in criteria.values()):
            raise DefinitionError(f"{where}.criteria of a noul question may only describe true and false")
        criteria = {k: v for k, v in criteria.items() if v} or None
    thresholds = {d: comparison(raw[d], f"{where}.{d}") for d in (ALLOW, BLOCK) if d in raw}
    if ALLOW in thresholds and BLOCK in thresholds and thresholds[ALLOW].overlaps(thresholds[BLOCK]):
        raise DefinitionError(f"{where}: allow {thresholds[ALLOW]} and block {thresholds[BLOCK]} overlap")
    ask = raw.get(ASK, False)
    if not isinstance(ask, bool):
        raise DefinitionError(f"{where}.ask must be true or false (the middle asks) on a {base['type']} question")
    return Question(**base, criteria=criteria, thresholds=thresholds, ask_middle=ask)


def _choice(where: str, raw: dict, base: dict) -> Question:
    for key in (ALLOW, ASK, BLOCK):
        if key in raw:
            raise DefinitionError(f"{where}.{key}: a choice question's thresholds go on its options (criteria)")
    criteria = raw.get("criteria")
    if not isinstance(criteria, dict) or len(criteria) < 2:
        raise DefinitionError(f"{where}.criteria must name at least two options")
    texts, options = {}, {}
    for option, spec in criteria.items():
        texts[option], options[option] = _option(f"{where}.criteria.{option}", spec)
    plain = all(isinstance(spec, str) for spec in criteria.values()) and set(criteria) <= set(DECISIONS)
    return Question(**base, criteria={o: (t or None) for o, t in texts.items()}, options=options, texts=texts,
                    plain=plain)


def _option(where: str, spec: object) -> tuple[str, dict]:
    if isinstance(spec, str):
        return spec.strip(), {}
    if not isinstance(spec, dict):
        raise DefinitionError(f"{where} must be its description, or text plus thresholds")
    unknown = sorted(set(spec) - _OPTION_KEYS)
    if unknown:
        raise DefinitionError(f"{where}: unknown key {unknown[0]!r}")
    text = spec.get("text", "")
    if not isinstance(text, str):
        raise DefinitionError(f"{where}.text must be text")
    found = {d: comparison(spec[d], f"{where}.{d}") for d in DECISIONS if d in spec}
    pairs = [(a, b) for i, a in enumerate(found) for b in list(found)[i + 1:]]
    for a, b in pairs:
        if found[a].overlaps(found[b]):
            raise DefinitionError(f"{where}: {a} {found[a]} and {b} {found[b]} overlap")
    return text.strip(), found


# -- Answers --


@dataclass(frozen=True)
class Value:
    """One question's answer: a number (noul, score), or a choice with its option probabilities."""

    number: float | None = None
    label: str = ""                  # score: the criteria label nearest the level
    choice: str = ""
    probabilities: dict = field(default_factory=dict)


def read(q: Question, raw: object) -> Value | None:
    """*q*'s answer from the reply, or None when it is missing or malformed."""
    if not isinstance(raw, dict):
        return None
    if q.type == CHOICE:
        probs = raw.get("probabilities")
        probs = {k: float(v) for k, v in probs.items() if _is_number(v)} if isinstance(probs, dict) else {}
        choice = raw.get("choice")
        return Value(choice=choice, probabilities=probs) if isinstance(choice, str) and choice in q.texts else None
    number = raw.get(q.type)
    if not _is_number(number):
        return None
    label = ""
    if q.type == SCORE:
        label = q.criteria[min(len(q.criteria) - 1, max(0, round(float(number))))]
    return Value(number=float(number), label=label)


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class Trigger:
    """A question, option or gate that triggered *decision*."""

    decision: str
    question: str = ""     # the question (or a gate's name, with gate set)
    option: str = ""       # choice: the option whose probability (or top place) triggered
    p: float | None = None  # choice: that option's probability
    gate: bool = False
    rule: str = ""         # what matched: "> 0.7", "p(risky)=0.42 >= 0.3", "top choice", "between thresholds"


def triggers(q: Question, value: Value | None) -> list[Trigger]:
    """What *q*'s answer triggers; a deciding question with no answer is uncertain."""
    if not q.deciding:
        return []
    if value is None:
        return [Trigger(UNCERTAIN, q.name, rule="no answer")]
    if q.type != CHOICE:
        hit = [Trigger(d, q.name, rule=str(cmp)) for d, cmp in q.thresholds.items() if cmp.matches(value.number)]
        if not hit and q.ask_middle:
            hit = [Trigger(ASK, q.name, rule="between thresholds")]
        return hit
    if q.plain:
        return [Trigger(value.choice, q.name, value.choice, value.probabilities.get(value.choice), rule="top choice")]
    out = []
    for option, cmps in q.options.items():
        p = value.probabilities.get(option)
        # The rule holds the option's probability: a question step shows only the top choice's.
        out += [Trigger(d, q.name, option, p, rule=f"p({option})={round(p, 4):g} {cmp}")
                for d, cmp in cmps.items() if p is not None and cmp.matches(p)]
    return out


def strictest(found: list[Trigger]) -> str:
    """The decision: the strictest triggered (allow < uncertain < ask < block); uncertain when nothing triggered."""
    return max((t.decision for t in found), key=RANK.__getitem__, default=UNCERTAIN)


# -- Explanations (decision 5: built from what triggered, no explainer model) --


def explain(decision: str, found: list[Trigger], questions: dict, values: dict, gate_refs: dict) -> str:
    """The reason for *decision*: ``system1: ask; may delete or overwrite data``.

    For uncertain: each deciding question's categorical answer (a choice, a
    score's label) or its missing one, ``system1: uncertain (allow)``.
    *gate_refs* maps a gate's name to the questions its expression reads.

    The reason carries no numbers: it is what the user's notice shows. The
    probabilities, levels and the thresholds they met are in the question
    steps (system1._question_step), which ``nuh log -v`` and ``nuh trace`` show.
    """
    if decision == UNCERTAIN:
        return f"system1: uncertain ({_uncertain_details(found, questions, values)})"
    parts: list[str] = []
    for t in (t for t in found if t.decision == decision):
        if t.gate:
            refs = gate_refs.get(t.question) or []
            parts += [_said(questions[r], values.get(r)) for r in refs if r in questions] or [f"gate {t.question}"]
        elif t.option:
            q = questions[t.question]
            parts.append(q.explain or _lowered(q.texts.get(t.option, "")) or f"{q.name}: {t.option}")
        else:
            parts.append(_said(questions[t.question], values.get(t.question)))
    parts = list(dict.fromkeys(p for p in parts if p))
    return "; ".join([f"system1: {decision}", *parts])


def _said(q: Question, value: Value | None) -> str:
    """What question *q*'s answer says, as a reason: its explain text (a score's with its label)."""
    if value is None:
        return f"{q.name}: no answer"
    if q.type == CHOICE:
        return q.explain or _lowered(q.texts.get(value.choice, "")) or f"{q.name}: {value.choice}"
    return (q.explain or q.name).replace("{label}", value.label)


def _uncertain_details(found: list[Trigger], questions: dict, values: dict) -> str:
    """What the deciding questions said without deciding; a noul answer is only a number, so it is left out."""
    details = []
    for q in questions.values():
        if not q.deciding:
            continue
        value = values.get(q.name)
        if value is None:
            details.append(f"{q.name} no answer")
        elif q.type == CHOICE:
            details.append(value.choice)
        elif q.type == SCORE:
            details.append(f"{q.name} {value.label}")
    details += [f"gate {t.question}" for t in found if t.gate and t.decision == UNCERTAIN]
    return "; ".join(details) or "nothing triggered"


def _lowered(text: str) -> str:
    """*text* with its first letter lowered, to read inside a reason."""
    return text[:1].lower() + text[1:] if text else ""
