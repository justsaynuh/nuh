"""System 1's definition, thresholds, explanations, gates and state text (no model, no hook)."""

import json
import sys

import pytest

from nuh import system1, system1_def, system1_gates, system1_state
from nuh import system1_rules as rules
from nuh.log_view import LogView
from nuh.system1_rules import Comparison, DefinitionError, Value
from nuh.tool_call import ToolCall


def q(name: str = "q", **raw) -> rules.Question:
    return rules.question(name, {"instructions": "?", **raw})


# -- Comparisons and overlap --


@pytest.mark.parametrize("a,b,overlap", [
    ("< 0.2", "> 0.7", False),
    ("< 0.2", "> 0.2", False),
    ("<= 0.2", ">= 0.2", True),
    ("< 0.2", ">= 0.2", False),
    ("> 0.2", ">= 0.1", True),   # the design's example: both bound from below
    ("< 0.2", "< 0.1", True),
    ("<= 0.5", "> 0.4", True),
])
def test_overlap(a, b, overlap):
    ca, cb = rules.comparison(a, "x"), rules.comparison(b, "x")
    assert ca.overlaps(cb) is overlap and cb.overlaps(ca) is overlap


@pytest.mark.parametrize("raw", ["0.2", "< x", "=< 0.2", "<", 0.2, None, "< 0.2 or more"])
def test_bad_comparisons_are_refused(raw):
    with pytest.raises(DefinitionError, match="is not a comparison"):
        rules.comparison(raw, "questions.q.allow")


def test_overlapping_thresholds_are_refused_at_load():
    with pytest.raises(DefinitionError, match="allow > 0.2 and block >= 0.1 overlap"):
        q(type="noul", allow="> 0.2", block=">= 0.1")


def test_overlap_inside_a_choice_option_is_refused():
    with pytest.raises(DefinitionError, match="allow > 0.5 and ask >= 0.5 overlap"):
        q(type="choice", criteria={"allow": {"text": "ok", "allow": "> 0.5", "ask": ">= 0.5"}, "ask": "look"})


@pytest.mark.parametrize("raw,message", [
    ({"type": "noul", "blok": "> 0.7"}, "unknown key 'blok'"),
    ({"type": "yesno"}, "type must be noul, score or choice"),
    ({"type": "noul", "ask": "> 0.5"}, "ask must be true or false"),
    ({"type": "score", "criteria": []}, "criteria must list the score's labels"),
    ({"type": "choice", "criteria": {"allow": "x", "ask": "y"}, "block": "> 0.5"}, "go on its options"),
    ({"type": "choice", "criteria": {"allow": "only"}}, "at least two options"),
    ({"type": "noul", "criteria": {"maybe": "x"}}, "may only describe true and false"),
])
def test_malformed_questions_are_refused(raw, message):
    with pytest.raises(DefinitionError, match=message):
        rules.question("q", {"instructions": "?", **raw})


# -- Triggers --


def test_one_value_thresholds_and_the_middle():
    destructive = q("destructive", type="noul", allow="< 0.2", block="> 0.7", ask=True)
    assert [t.decision for t in rules.triggers(destructive, Value(number=0.1))] == ["allow"]
    assert [t.decision for t in rules.triggers(destructive, Value(number=0.5))] == ["ask"]
    assert [t.decision for t in rules.triggers(destructive, Value(number=0.8))] == ["block"]


def test_yes_as_the_good_answer():
    requested = q("requested", type="noul", allow=">= 0.7", block="< 0.1")
    assert [t.decision for t in rules.triggers(requested, Value(number=0.75))] == ["allow"]
    assert rules.triggers(requested, Value(number=0.5)) == []  # no ask: true, so the middle triggers nothing


def test_a_score_that_can_only_block():
    risk = q("risk", type="score", criteria=["Harmless", "Low", "Moderate", "High", "Severe"], block=">= 3")
    assert rules.triggers(risk, Value(number=1.0)) == []
    assert [t.decision for t in rules.triggers(risk, Value(number=3.2))] == ["block"]


def test_detailed_choice_thresholds_on_each_option():
    verdict = q("verdict", type="choice", criteria={
        "allow": {"text": "Routine", "allow": "> 0.6"}, "ask": {"text": "Glance", "ask": "> 0.3"},
        "block": {"text": "Harmful", "block": "> 0.4"}})
    probs = {"allow": 0.55, "ask": 0.35, "block": 0.1}
    found = rules.triggers(verdict, Value(choice="allow", probabilities=probs))
    assert [(t.decision, t.option, t.p) for t in found] == [("ask", "ask", 0.35)]


def test_plain_choice_decides_only_when_its_options_are_decisions():
    plain = q("verdict", type="choice", criteria={"allow": "a", "ask": "b", "block": "c"})
    other = q("kind", type="choice", criteria={"read": "a", "write": "b"})
    assert plain.deciding and not other.deciding
    assert rules.triggers(other, Value(choice="read", probabilities={"read": 0.9})) == []


def test_a_deciding_question_without_an_answer_is_uncertain():
    verdict = q("verdict", type="choice", criteria={"allow": "a", "ask": "b"})
    assert [t.decision for t in rules.triggers(verdict, None)] == ["uncertain"]
    assert rules.triggers(q("logged", type="noul"), None) == []


@pytest.mark.parametrize("decisions,expected", [
    ([], "uncertain"),
    (["allow"], "allow"),
    (["allow", "uncertain"], "uncertain"),
    (["uncertain", "ask"], "ask"),
    (["ask", "block", "allow"], "block"),
])
def test_strictest_wins(decisions, expected):
    assert rules.strictest([rules.Trigger(d, "q") for d in decisions]) == expected


@pytest.mark.parametrize("raw,expected", [
    ({"type": "score", "score": 2.6}, ("High", 2.6)),
    ({"type": "score", "score": 9}, ("Severe", 9.0)),
    ({"type": "score", "score": "high"}, None),
    ("nope", None),
])
def test_reading_a_score(raw, expected):
    risk = q("risk", type="score", criteria=["Harmless", "Low", "Moderate", "High", "Severe"])
    value = rules.read(risk, raw)
    assert (None if value is None else (value.label, value.number)) == expected


def test_a_choice_outside_the_options_is_no_answer():
    verdict = q("verdict", type="choice", criteria={"allow": "a", "ask": "b"})
    assert rules.read(verdict, {"choice": "maybe", "probabilities": {"maybe": 1.0}}) is None


# -- Explanations --


def _shipped() -> system1_def.Definition:
    return system1_def.load({})


def _reply(**answers) -> dict:
    return {"answers": answers}


def test_explanations_name_what_triggered():
    defn = _shipped()
    answer = system1.decide(defn, _reply(verdict={"choice": "ask", "probabilities": {"allow": 0.3, "ask": 0.48}}))
    assert (answer.outcome, answer.decision) == ("decision", "ask")
    assert answer.reasoning == "system1: ask; plausible, but a human should glance at it"


def test_uncertain_says_what_each_decider_answered():
    defn = system1_def.parse({"questions": {"verdict": {"type": "choice", "instructions": "?", "criteria": {
        "allow": {"text": "ok", "allow": "> 0.6"}, "ask": "look"}}}}, "test")
    answer = system1.decide(defn, _reply(verdict={"choice": "allow", "probabilities": {"allow": 0.52}}))
    assert (answer.outcome, answer.reasoning) == ("uncertain", "system1: uncertain (allow)")


def test_a_score_explains_with_its_label():
    defn = system1_def.parse({"questions": {"risk": {
        "type": "score", "instructions": "?", "criteria": ["Harmless", "Low", "Moderate", "High"],
        "explain": "risk: {label}", "block": ">= 2.5"}}}, "test")
    answer = system1.decide(defn, _reply(risk={"score": 2.9}))
    assert answer.reasoning == "system1: block; risk: High"


def test_a_lower_option_that_triggers_keeps_its_probability_in_the_step_not_the_reason():
    """The reason is the notice's: no numbers. The step's rule holds the option's p, which ``nuh log -v`` shows."""
    defn = system1_def.parse({"questions": {"verdict": {"type": "choice", "instructions": "?", "criteria": {
        "allow": {"text": "Routine", "allow": "> 0.6"}, "ask": {"text": "Glance at it", "ask": "> 0.3"},
        "block": "Harmful"}}}}, "test")
    answer = system1.decide(defn, _reply(verdict={"choice": "allow", "probabilities": {
        "allow": 0.55, "ask": 0.35, "block": 0.1}}))
    assert (answer.decision, answer.reasoning) == ("ask", "system1: ask; glance at it")
    (step,) = answer.steps[:1]
    assert (step["p"], step["triggered"], step["rule"]) == (0.55, "ask", "p(ask)=0.35 > 0.3")
    rows = LogView(color=False, width=200, details=True).step_rows({"decision": "ask", "steps": [step]})
    assert any("verdict  choice allow 0.55 p(ask)=0.35 > 0.3 → ask" in row for row in rows)


def test_uncertain_names_labels_and_choices_but_no_numbers():
    defn = system1_def.parse({"questions": {
        "destructive": {"type": "noul", "instructions": "?", "allow": "< 0.2", "block": "> 0.7"},
        "risk": {"type": "score", "instructions": "?", "criteria": ["Harmless", "Low", "High"], "block": ">= 1.5"},
        "verdict": {"type": "choice", "instructions": "?", "criteria": {
            "allow": {"text": "ok", "allow": "> 0.6"}, "ask": "look"}}}}, "test")
    answer = system1.decide(defn, _reply(destructive={"noul": 0.5}, risk={"score": 1.2},
                                         verdict={"choice": "allow", "probabilities": {"allow": 0.52}}))
    assert (answer.outcome, answer.reasoning) == ("uncertain", "system1: uncertain (risk Low; allow)")
    assert [s.get("answer") for s in answer.steps[:3]] == [0.5, 1.2, "allow"]  # the numbers stay in the steps


# -- Definitions --


def test_definition_errors_name_the_file():
    with pytest.raises(DefinitionError, match=r"^/x/s1\.yaml: questions must name at least one question"):
        system1_def.parse({"questions": {}}, "/x/s1.yaml")


def test_unsupported_context_keys_warn_and_are_ignored(capsys):
    defn = system1_def.parse({"context": {"tool_calls": True, "prior_decisions": "llm", "agent": False,
                                          "session_tokens": 400}, "questions": {"v": {
                                              "type": "noul", "instructions": "?"}}}, "s.yaml")
    err = capsys.readouterr().err
    assert "context.tool_calls is not supported yet; ignored" in err
    assert "context.prior_decisions is not supported yet" in err
    assert "context.agent" not in err  # false is what nuh does anyway
    assert defn.context.session_tokens == 400


def test_the_shipped_default_loads_without_pyyaml(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert list(system1_def.load({}).questions) == ["destructive", "out_of_scope", "network", "verdict", "risk"]


# -- The state text (stable for replay) --


def test_action_text_forms():
    assert system1_state.action_text({"command": "ls", "description": "list"}) == "ls"
    assert system1_state.action_text({"command": "ls", "timeout": 5}) == 'ls\n--- other fields ---\n{"timeout": 5}'
    assert system1_state.action_text({"file_path": "/p/a", "content": "x"}) == "File: /p/a\n--- content ---\nx"
    assert system1_state.action_text({"file_path": "/p/a", "old_string": "o", "new_string": "n", "replace_all": True}) \
        == 'File: /p/a\n--- replace ---\no\n--- with ---\nn\n--- other fields ---\n{\n  "replace_all": true\n}'
    assert system1_state.action_text({"pattern": "é"}) == '{\n  "pattern": "é"\n}'


def test_clip_keeps_both_ends():
    text, cut = system1_state.clip("a" * 50 + "b" * 50, 10)  # 40 chars kept
    assert cut == 60
    assert text == "a" * 20 + "\n…[60 chars clipped]…\n" + "b" * 20


def test_session_text_cuts_the_oldest_at_a_line():
    assert system1_state.session_text(["one", "two"], 100) == "[user] one\n[user] two"
    assert system1_state.session_text(["x" * 30, "keep"], 3) == "…\n[user] keep"


def test_user_lines_lead_with_the_first_message():
    assert system1_state.user_lines(["  ", "later"], "first") == ["first", "later"]
    assert system1_state.user_lines(["first", "later"], "first") == ["first", "later"]


def test_state_without_user_messages():
    defn = system1_def.parse({"preamble": "P", "context": {"user": False}, "questions": {"v": {
        "type": "noul", "instructions": "?"}}}, "t")
    state, clipped = system1_state.build(defn, ToolCall("Bash", {"command": "ls"}, "/w", "codex"), ["hi"], "")
    assert clipped == 0
    assert state.startswith("P\n\n## Tool call under review\nAgent: codex\nTool: Bash\nWorking directory: /w\n")
    assert "[user]" not in state


# -- Gates --


def _gated(*gates: dict) -> system1_def.Definition:
    with open(system1_def.SHIPPED) as f:
        data = json.load(f)
    data["gates"] = list(gates)
    return system1_def.parse(data, "gated")


_ALLOWED = {"verdict": {"choice": "allow", "probabilities": {"allow": 0.95, "ask": 0.04, "block": 0.01}},
            "destructive": {"noul": 0.9}, "network": {"noul": 0.6}, "out_of_scope": {"noul": 0.1},
            "risk": {"score": 1.2}}


@pytest.mark.parametrize("raw,message", [
    ([{"if": "x", "then": "deny"}], "then must be allow, ask, block or uncertain"),
    ([{"then": "block"}], "if must be a CEL expression"),
    ([{"if": "x", "then": "ask", "when": 1}], "unknown key 'when'"),
    ([{"name": "g", "if": "x", "then": "ask"}, {"name": "g", "if": "y", "then": "ask"}], "unique name"),
    ({"if": "x"}, "gates must be a list"),
])
def test_malformed_gates_are_refused(raw, message):
    with pytest.raises(DefinitionError, match=message):
        system1_gates.parse(raw)


def test_a_firing_gate_beats_the_verdict_and_explains_with_its_questions():
    defn = _gated({"name": "wipe_and_send", "if": "destructive.p > 0.8 && network.p > 0.5", "then": "block"})
    answer = system1.decide(defn, _reply(**_ALLOWED))
    assert (answer.outcome, answer.decision) == ("decision", "block")
    assert answer.reasoning == ("system1: block; may delete or overwrite data outside build, cache or temp; "
                                "contacts the local network or an unfamiliar host")
    assert answer.steps[-1] == {"step": "gate", "provider": "system1", "name": "wipe_and_send", "then": "block",
                                "fired": True}


def test_an_uncertain_gate_beats_an_allow():
    defn = _gated({"name": "odd", "if": "verdict.choice == 'allow' && risk.score > 1.0", "then": "uncertain"})
    answer = system1.decide(defn, _reply(**_ALLOWED))
    assert answer.outcome == "uncertain"
    assert answer.reasoning == "system1: uncertain (allow; gate odd)"


def test_a_gate_that_does_not_fire_leaves_the_verdict():
    defn = _gated({"if": "destructive.p > 0.95", "then": "block"})
    answer = system1.decide(defn, _reply(**_ALLOWED))
    assert (answer.decision, answer.steps[-1]["fired"], answer.steps[-1]["name"]) == ("allow", False, "gate1")


@pytest.mark.parametrize("expression,message", [
    ("destructive.p >", "gate gate1: bad CEL"),
    ("missing.p > 0.5", "gate gate1: undeclared reference to 'missing'"),
    ("destructive.p", "gate gate1: gave DoubleType, not true or false"),
])
def test_a_gate_that_cannot_run_is_a_failure(expression, message):
    answer = system1.decide(_gated({"if": expression, "then": "block"}), _reply(**_ALLOWED))
    assert answer.outcome == "failure"
    assert answer.error.startswith(message)
    assert "0.9" not in answer.error  # celpy's activation dump (the answers) stays out


def test_no_gates_never_imports_cel(monkeypatch):
    monkeypatch.setitem(sys.modules, "celpy", None)
    assert system1.decide(_shipped(), _reply(**_ALLOWED)).decision == "allow"


def test_comparison_text():
    assert str(Comparison("<=", 0.2)) == "<= 0.2"
