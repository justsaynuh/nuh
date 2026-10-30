"""System 1 gates: CEL expressions over every answer, each naming a decision (the complex form of system1_rules.py).

A gate is ``{name, if: <CEL>, then: allow|ask|block|uncertain}``. It reads
every question, deciding or not, bound by name:

- noul:   ``q.p``        P(yes)
- score:  ``q.score``    the level, and ``q.label``, its criteria label
- choice: ``q.choice``   the top option, and ``q.p.<option>``, each option's probability

    destructive.p > 0.8 && network.p > 0.5          then: block

A question with no answer is unbound. Gates join the thresholds and the
strictest decision of the two wins (system1_rules.strictest).

cel-python is imported only when a gate runs, which is only after a model
call. A gate that cannot run (a bad expression, an error evaluating it, a
result that is not true or false) is a failure of the system 1 attempt,
never skipped: GateError says why.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from nuh.system1_rules import CHOICE, DECISIONS, NOUL, UNCERTAIN, DefinitionError, Question, Value

THEN = (*DECISIONS, UNCERTAIN)
_KEYS = frozenset({"name", "if", "then"})
_lock = threading.Lock()  # celpy's parser keeps the text being parsed on the instance


class GateError(Exception):
    """A gate that could not run; the message names it and the cause."""


@dataclass(frozen=True)
class Gate:
    name: str
    expression: str
    then: str


@dataclass(frozen=True)
class Fired:
    """A gate's result: whether it fired, and the questions its expression reads."""

    gate: Gate
    fired: bool
    refs: tuple[str, ...]


def parse(raw: object) -> list[Gate]:
    """A definition's ``gates``: a list of ``{name?, if, then}`` (DefinitionError on anything malformed)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DefinitionError("gates must be a list")
    gates, names = [], set()
    for i, item in enumerate(raw, 1):
        where = f"gates[{i}]"
        if not isinstance(item, dict):
            raise DefinitionError(f"{where} must be a mapping with if and then")
        unknown = sorted(set(item) - _KEYS)
        if unknown:
            raise DefinitionError(f"{where}: unknown key {unknown[0]!r}")
        name, expression, then = item.get("name", f"gate{i}"), item.get("if"), item.get("then")
        if not isinstance(name, str) or not name or name in names:
            raise DefinitionError(f"{where}.name must be a unique name")
        if not isinstance(expression, str) or not expression.strip():
            raise DefinitionError(f"{where}.if must be a CEL expression")
        if then not in THEN:
            raise DefinitionError(f"{where}.then must be allow, ask, block or uncertain, not {then!r}")
        names.add(name)
        gates.append(Gate(name, expression.strip(), then))
    return gates


def bindings(questions: dict[str, Question], values: dict[str, Value]) -> dict:
    """Every answered question as the gates read it."""
    out = {}
    for name, value in values.items():
        q = questions[name]
        if q.type == CHOICE:
            out[name] = {"choice": value.choice, "p": dict(value.probabilities)}
        elif q.type == NOUL:
            out[name] = {"p": value.number}
        else:
            out[name] = {"score": value.number, "label": value.label}
    return out


def evaluate(gates: list[Gate], questions: dict[str, Question], values: dict[str, Value]) -> list[Fired]:
    """Run every gate on the answers; GateError when one cannot run."""
    if not gates:
        return []
    import celpy  # lazy: only a definition with gates needs it
    env = celpy.Environment()
    activation = {name: celpy.json_to_cel(v) for name, v in bindings(questions, values).items()}
    return [_run(celpy, env, gate, activation, questions) for gate in gates]


def unbound_names(gates: list[Gate], questions: dict[str, Question]) -> list[tuple[Gate, list[str]]]:
    """Compile every gate without answers (``nuh system1 check``): each gate's names that are not questions.

    Such a name is unbound when the gate runs, which fails the attempt when
    the gate needs it (unless it is a CEL comprehension's own variable). DefinitionError on
    bad CEL.
    """
    if not gates:
        return []
    import celpy
    env = celpy.Environment()
    out = []
    for gate in gates:
        try:
            with _lock:
                tree = env.compile(gate.expression)
                env.program(tree)
        except celpy.CELParseError as exc:
            raise DefinitionError(f"gate {gate.name}: bad CEL: {_short(exc)}") from exc
        names = {str(t.children[0]) for t in tree.iter_subtrees() if t.data == "ident"}
        unknown = sorted(names - set(questions))
        if unknown:
            out.append((gate, unknown))
    return out


def _run(celpy, env, gate: Gate, activation: dict, questions: dict) -> Fired:
    try:
        with _lock:
            tree = env.compile(gate.expression)
            program = env.program(tree)
        result = program.evaluate(activation)
    except celpy.CELParseError as exc:
        raise GateError(f"gate {gate.name}: bad CEL: {_short(exc)}") from exc
    except celpy.CELEvalError as exc:
        raise GateError(f"gate {gate.name}: {_short(exc)}") from exc
    if isinstance(result, celpy.CELEvalError):
        raise GateError(f"gate {gate.name}: {_short(result)}")
    if not isinstance(result, celpy.celtypes.BoolType):
        raise GateError(f"gate {gate.name}: gave {type(result).__name__}, not true or false")
    refs = tuple(sorted({str(t.children[0]) for t in tree.iter_subtrees() if t.data == "ident"} & set(questions)))
    return Fired(gate, bool(result), refs)


def _short(exc: BaseException) -> str:
    """celpy's message without the activation dump it appends (answers stay out of logs and stderr)."""
    msg = str(exc.args[0]) if exc.args else str(exc)
    return msg.split(" (in activation", 1)[0]
