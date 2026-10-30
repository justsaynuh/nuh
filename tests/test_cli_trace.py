"""``nuh trace``: listing, finding and rendering LLM traces, robust to partial and corrupt ones."""

import json
import os
import sys
import time

import pytest

from nuh import cli, cli_parser, cli_trace, trace_names, trace_read
from nuh.bash import classify_command
from nuh.trace_read import ListFilter
from nuh.trace_render import Renderer, bar

CALL = "toolu_01AbCdEfGhIjKlMnOpQrSt9c3f1a"
ANSWERS = {
    "destructive": {"type": "noul", "noul": 0.81, "confidence": 0.7},
    "verdict": {"type": "choice", "choice": "ask", "confidence": 0.62,
                "probabilities": {"allow": 0.3, "ask": 0.62, "block": 0.08}},
    "risk": {"type": "score", "score": 2.4, "confidence": 0.9,
             "probabilities": {"0": 0.05, "1": 0.1, "2": 0.4, "3": 0.35, "4": 0.1}},
}
QUESTIONS = {
    "destructive": {"type": "noul", "instructions": "Could this delete data?"},
    "verdict": {"type": "choice", "instructions": "What should a careful reviewer do?",
                "criteria": {"allow": "Routine", "ask": "Glance at it", "block": "Harmful"}},
    "risk": {"type": "score", "instructions": "How risky is it?",
             "criteria": ["Harmless", "Low", "Moderate", "High", "Severe"]},
}
STEPS = [
    {"step": "static", "decision": "ask", "reason": "unknown → ask", "breakdown": []},
    {"step": "question", "provider": "system1", "name": "destructive", "type": "noul", "answer": 0.81,
     "triggered": "block", "rule": "> 0.7"},
    {"step": "question", "provider": "system1", "name": "verdict", "type": "choice", "answer": "ask", "p": 0.62,
     "triggered": "ask", "rule": "top choice"},
    {"step": "gate", "provider": "system1", "name": "net_and_risky", "then": "ask", "fired": False},
    {"step": "provider", "provider": "system1", "outcome": "decision", "decision": "block", "ms": 412},
    {"step": "llm", "decision": "block", "reason": "system1: block; may delete data (0.81)"},
    {"step": "cap", "from": "block", "to": "ask", "rule": "llm.can_block: false"},
]


def stamp_of(minutes_ago: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(time.time() - minutes_ago * 60)) + ".000000Z"


def make_trace(root, name: str, *, tool: str = "Bash", command: str = "rm -rf build/", decision: str = "ask",
               provider: str = "system1") -> str:
    """A trace directory as the hook writes it, with a system 1 attempt; the call ID is the name's."""
    call = trace_names.parse(name).call
    d = root / name
    d.mkdir(parents=True)
    files = {
        "input.json": {"agent": "claude", "tool_name": tool, "tool_input": {"command": command, "file_path": command},
                       "payload": {"tool_use_id": call, "tool_name": tool}},
        "classification.json": {"reviews": [{"kind": "bash", "tool": tool}]},
        f"request-01-{provider}.json": {
            "attempt": 1, "provider": provider, "sent": True, "method": "POST",
            "url": "http://127.0.0.1:1/v1/systemone", "model": "nimble:9b",
            "body": {"model": "nimble:9b", "state": "Judge the call.\n\n## Tool call under review\nTool: Bash",
                     "questions": QUESTIONS}},
        f"response-01-{provider}.json": {"attempt": 1, "provider": provider, "sent": True,
                                         "parsed": {"answers": ANSWERS, "model": "nimble:9b"}},
        "result.json": {
            "final": {"decision": decision, "reason": "Bash (LLM): system1: block; may delete data (0.81)",
                      "_steps": STEPS},
            "reviews": [{"kind": "bash", "attempts": [f"01-{provider}"],
                         "llm": {"provider": provider, "model": "nimble:9b", "latency_ms": 412,
                                 "reasoning": "system1: block; may delete data (0.81)",
                                 "decision": {"decision": "block"}, "cascade": []},
                         "llm_decision": {"decision": "block"}, "after_cap": {"decision": "ask"}, "capped": True}],
            "total_ms": 431},
    }
    for file, obj in files.items():
        (d / file).write_text(json.dumps(obj))
    return str(d)


def run(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["nuh", "trace", *argv])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out, err = capsys.readouterr()
    return done.value.code, out, err


@pytest.fixture
def traces(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "160")
    return tmp_path / "traces"


# -- The list --


def test_list_is_newest_first_and_stops_at_the_limit(traces, monkeypatch, capsys):
    for i in range(5):
        make_trace(traces, f"{stamp_of(50 - i)}_toolu_call{i}")
    code, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--list", "-n", "3")
    lines = out.splitlines()
    assert code == 0 and len(lines) == 3
    assert [line.split()[2] for line in lines] == ["#_call4", "#_call3", "#_call2"]


def test_list_line_has_the_call_decision_provider_verdict_and_risk(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--list")
    line = out.strip()
    assert "#9c3f1a" in line and "Bash" in line and "rm -rf build/" in line
    assert " ask " in line and "system1" in line and "ask 0.62" in line and "risk 2.40" in line


def test_list_reads_only_the_traces_it_shows(traces, monkeypatch, capsys):
    for i in range(30):
        make_trace(traces, f"{stamp_of(100 - i)}_toolu_c{i:02d}")
    opened = []
    real = trace_read.load
    monkeypatch.setattr(trace_read, "load", lambda path, **kw: opened.append(path) or real(path, **kw))
    run(monkeypatch, capsys, "--dir", str(traces), "--list", "-n", "4")
    assert len(opened) == 4


def test_filters(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(300)}_toolu_aaaold", decision="ask")
    make_trace(traces, f"{stamp_of(3)}_toolu_aaarde", tool="Read", decision="allow", command="/etc/hosts")
    make_trace(traces, f"{stamp_of(2)}_toolu_aaablk", decision="block", provider="ollama")
    make_trace(traces, f"{stamp_of(1)}_toolu_aaaask", decision="ask")

    def calls(*flags: str) -> list[str]:
        out = run(monkeypatch, capsys, "--dir", str(traces), "--list", *flags)[1]
        return [line.split()[2] for line in out.splitlines()]

    assert calls("--asks") == ["#aaaask", "#aaaold"]
    assert calls("--blocks") == ["#aaablk"]
    assert calls("--tool", "read") == ["#aaarde"]
    assert calls("--provider", "ollama") == ["#aaablk"]
    assert calls("-s", "1h") == ["#aaaask", "#aaablk", "#aaarde"]


def test_since_stops_reading_at_the_first_older_name(traces, monkeypatch):
    for i in range(10):
        make_trace(traces, f"{stamp_of(600 - i)}_toolu_c{i}")
    make_trace(traces, f"{stamp_of(1)}_toolu_new")
    opened = []
    real = trace_read.load
    monkeypatch.setattr(trace_read, "load", lambda path, **kw: opened.append(path) or real(path, **kw))
    since = cli_parser.since("1h")
    assert [s.call for s in trace_read.newest(str(traces), ListFilter(since=since), 0)] == ["toolu_new"]
    assert len(opened) == 1


def test_json_list(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--json")
    record = json.loads(out)
    assert (record["call"], record["decision"], record["provider"]) == (CALL, "ask", "system1")
    assert record["verdict"] == {"question": "verdict", "choice": "ask", "p": 0.62}
    assert record["risk"]["score"] == 2.4 and record["error"] is None


def test_non_trace_entries_are_not_listed(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(1)}_{CALL}")
    (traces / "notes").mkdir()
    (traces / "README.txt").write_text("x")
    (traces / "2026-10-20_toolu_x").mkdir()
    _, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--list")
    assert len(out.splitlines()) == 1


@pytest.mark.parametrize("breakage", ["empty", "result-cut-short", "input-not-json", "a-file", "odd-shapes",
                                      "unlistable"])
def test_partial_or_corrupt_traces_are_listed_never_fatal(traces, monkeypatch, capsys, breakage):
    good = make_trace(traces, f"{stamp_of(5)}_toolu_good")
    bad = traces / f"{stamp_of(1)}_toolu_bad"
    if breakage == "a-file":
        bad.write_text("not a directory")
    else:
        bad.mkdir()
    if breakage == "result-cut-short":
        (bad / "input.json").write_text(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        (bad / "result.json").write_text('{"final": {"decis')
    elif breakage == "input-not-json":
        (bad / "input.json").write_bytes(b"\xff\xfe\x00garbage")
    elif breakage == "odd-shapes":
        (bad / "input.json").write_text(json.dumps({"tool_name": ["Bash"], "tool_input": "ls", "payload": 3}))
        (bad / "result.json").write_text(json.dumps({"final": "ask", "reviews": {"x": 1}}))
        (bad / "response-01-system1.json").write_text(
            '{"parsed": {"answers": {"verdict": 7, "risk": {"type": "score", "score": Infinity, "legend": 3}}}}')
    elif breakage == "unlistable":
        bad.chmod(0)
    try:
        code, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--list")
        lines = out.splitlines()
        assert code == 0 and len(lines) == 2
        assert "#u_good" in lines[1]
        assert "(unreadable" in lines[0] or "(partial)" in lines[0]
        code, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "--render", bad.name, "--view", "all")
        assert code == 0 and out
    finally:
        if breakage == "unlistable":
            bad.chmod(0o700)
    assert os.path.isdir(good)


def test_missing_trace_dir_is_an_error_not_a_crash(tmp_path, monkeypatch, capsys):
    code, _, err = run(monkeypatch, capsys, "--dir", str(tmp_path / "nope"), "--list")
    assert code == 1 and "cannot list" in err


def test_tracing_off_says_how_to_point_at_a_dir(monkeypatch, capsys):
    code, _, err = run(monkeypatch, capsys, "--list")
    assert code == 2 and "llm.trace_dir is not set" in err


# -- One trace --


def test_call_reference_finds_the_newest_matching_trace(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(9)}_{CALL}", command="older one")
    make_trace(traces, f"{stamp_of(5)}_toolu_other", command="unrelated")
    make_trace(traces, f"{stamp_of(1)}_{CALL}-0a1b2c3d", command="the retry")
    code, out, _ = run(monkeypatch, capsys, "--dir", str(traces), "#9c3f1a")
    assert code == 0 and "the retry" in out
    assert run(monkeypatch, capsys, "--dir", str(traces), "#zzzzzz")[0] == 1


def test_answers_view(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, path)
    rows: dict[str, str] = {}
    for line in out.splitlines():
        if line.startswith("  ") and line.split():
            rows.setdefault(line.split()[0], line)  # the table's row, not the distribution title below it
    assert "noul" in rows["destructive"] and "yes" in rows["destructive"] and "block (> 0.7)" in rows["destructive"]
    assert "0.62" in rows["verdict"] and "ask (top choice)" in rows["verdict"]
    assert "Moderate" in rows["risk"] and "2.40/4" in rows["risk"]
    assert "2 Moderate" in out and "0.40" in out  # the risk distribution, with the level labels
    assert "net_and_risky → ask  not fired" in out
    assert "block  confidence" not in out  # system 1's reply has no overall confidence
    assert "capped to ask" in out and "via        cap block → ask" in out


def test_prompt_view_shows_the_request_as_sent(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, path, "--view", "prompt")
    assert "## Tool call under review" in out
    assert "[question verdict]" in out and "What should a careful reviewer do?" in out and "ask: Glance at it" in out
    assert "levels: 0 Harmless, 1 Low" in out


def test_path_view_is_the_log_formatter(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, path, "--view", "path")
    view = Renderer(color=False, width=160).log_view
    for row in view.step_rows({"steps": STEPS, "decision": "ask"}):
        assert row in out


def test_prompted_provider_shows_verdict_confidence_and_reasoning(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}", provider="ollama")
    response = traces / os.path.basename(path) / "response-01-ollama.json"
    response.write_text(json.dumps({"parsed": {"decision": "block", "confidence": 0.83, "reasoning": "x"}}))
    _, out, _ = run(monkeypatch, capsys, path)
    assert "question" not in out  # no system 1 table
    assert "ollama nimble:9b" in out and "confidence 0.83" in out and "system1: block; may delete" in out


def test_an_old_decide_trace_shows_as_plain_text(traces, monkeypatch, capsys):
    """A trace the decide backend wrote (removed 2026-10-20) still renders: its verdict and reason, no table."""
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}", provider="decide")
    reply = {"decision": "ask", "reason": "voter risk=mid p=0.55", "pipeline": "default",
             "agents": [{"name": "voter", "verdict": "ask"}]}
    request = {"attempt": 1, "provider": "decide", "sent": True, "method": "POST",
               "url": "http://127.0.0.1:8787/api/decide", "model": "", "timeout": 30, "headers": {},
               "body": {"pipeline": "default", "tool_name": "Bash", "tool_input": {"command": "rm -rf build/"},
                        "client": {"budget_s": 29.0, "max_decision": "ask"}}}
    response = {"attempt": 1, "provider": "decide", "sent": True, "parsed": reply,
                "http": {"status": 200, "latency_ms": 812, "body": reply, "body_raw": json.dumps(reply)},
                "outcome": {"status": "success", "latency_ms": 812, "decision": "ask",
                            "reasoning": "voter risk=mid p=0.55", "error": ""}}
    steps = [{"step": "static", "decision": "ask", "reason": "unknown → ask", "breakdown": []},
             {"step": "llm", "decision": "ask", "reason": "voter risk=mid p=0.55"},
             {"step": "adopt", "from": "ask", "to": "ask", "rule": "llm.authority.Bash: review"}]
    result = {"final": {"decision": "ask", "reason": "Bash (decide:default): voter risk=mid p=0.55", "_steps": steps},
              "reviews": [{"kind": "bash", "attempts": ["01-decide"],
                           "llm": {"provider": "decide", "model": "decide:default", "latency_ms": 812,
                                   "reasoning": "voter risk=mid p=0.55", "decision": {"decision": "ask"},
                                   "cascade": []}}],
              "total_ms": 830}
    for name, obj in (("request-01-decide.json", request), ("response-01-decide.json", response),
                      ("result.json", result)):
        (traces / os.path.basename(path) / name).write_text(json.dumps(obj))
    for view in ("answers", "prompt", "path", "all"):
        code, out, err = run(monkeypatch, capsys, path, "--view", view)
        assert (code, err) == (0, ""), view
    assert "question" not in out  # no system 1 table
    assert "decide" in out and "voter risk=mid p=0.55" in out and "/api/decide" in out


def test_json_for_one_trace_has_answers_and_steps(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}")
    _, out, _ = run(monkeypatch, capsys, path, "--json")
    record = json.loads(out)
    assert record["answers"] == ANSWERS and record["steps"] == STEPS and "result.json" in record["files"]


def test_colour_only_on_a_terminal_and_never_with_no_color(traces, monkeypatch, capsys):
    path = make_trace(traces, f"{stamp_of(1)}_{CALL}")
    assert "\033[" not in run(monkeypatch, capsys, path)[1]  # captured: not a terminal
    monkeypatch.delenv("NO_COLOR")
    assert "\033[" in run(monkeypatch, capsys, path, "--color", "always")[1]
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\033[" not in run(monkeypatch, capsys, path, "--color", "always")[1]


# -- fzf --


def test_picker_runs_fzf_with_preview_and_view_keys(traces, monkeypatch, capsys):
    name = f"{stamp_of(1)}_{CALL}"
    make_trace(traces, name)
    seen = {}

    class Done:
        returncode = 130

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return Done()

    monkeypatch.setattr(cli_trace.shutil, "which", lambda name: "/usr/bin/fzf")
    monkeypatch.setattr(cli_trace.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_trace.subprocess, "run", fake_run)
    code, _, _ = run(monkeypatch, capsys, "--dir", str(traces))
    cmd = seen["cmd"]
    assert code == 0 and cmd[0] == "fzf"
    assert seen["input"].startswith(f"{name}\t")
    preview = cmd[cmd.index("--preview") + 1]
    assert "--view answers --render {1}" in preview and f"--dir {traces}" in preview
    binds = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--bind"]
    assert {b.split(":", 1)[0] for b in binds} == {"alt-a", "alt-p", "alt-d", "alt-r", "enter"}
    assert any("--view prompt" in b for b in binds if b.startswith("alt-p:change-preview("))
    assert "${PAGER:-less -R}" in next(b for b in binds if b.startswith("enter:execute("))
    assert "alt-p prompt" in cmd[cmd.index("--header") + 1]
    assert seen["env"]["SHELL"] == "/bin/sh"


def test_without_fzf_the_list_prints(traces, monkeypatch, capsys):
    make_trace(traces, f"{stamp_of(1)}_{CALL}")
    monkeypatch.setattr(cli_trace.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_trace.sys.stdout, "isatty", lambda: True)
    code, out, _ = run(monkeypatch, capsys, "--dir", str(traces))
    assert code == 0 and "#9c3f1a" in out


# -- Pieces --


def test_names_parse_and_round_trip():
    name = trace_names.dir_name("toolu_01X")
    parsed = trace_names.parse(name)
    assert parsed is not None and parsed.call == "toolu_01X"
    assert trace_names.parse(trace_names.dir_name("")).call == ""
    assert trace_names.parse("20261020T120000.000000Z_toolu_01X-0a1b2c3d").call == "toolu_01X"
    for bad in ("20261320T120000.000000Z_x", "20261020T120000Z_x", "notes", "20261020T120000.000000Z_a/b", ""):
        assert trace_names.parse(bad) is None


def test_bar_is_eighths_and_clamped():
    assert bar(0.0) == " " * 10 and bar(1.0) == "█" * 10 and bar(7.0) == "█" * 10
    assert bar(0.05) == "▌" + " " * 9 and bar(None) == " " * 10


@pytest.mark.parametrize("command", ["nuh trace", "nuh trace '#abc123' --view prompt", "nuh trace --list -n 50"])
def test_nuh_trace_is_a_read(command):
    result = classify_command(command)
    assert result.final_decision == "allow"
    assert result.stages[0].action_type == "filesystem_read"
