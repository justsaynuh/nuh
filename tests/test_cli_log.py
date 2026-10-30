"""``nuh log`` / ``nuh tail``: filters, views, call references and following a rotating log."""

import itertools
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from nuh import cli, cli_log, log, log_query
from nuh.log_query import LogFilter, parse_since, read_history, session_id, short_call
from nuh.log_view import LogView, use_color

SESSION = "3f2a9c1e-1111-2222-3333-444455556666"
CALL = "toolu_01AbCdEfGhIjKlMnOpQrStUv"
# An entry the decide backend wrote (removed 2026-10-20): it shows as any provider's, its reply unread.
DECIDE_LLM = {
    "provider": "decide", "model": "decide:strict", "ms": 812, "decision": "ask",
    "reasoning": "touches credentials",
    "cascade": [{"provider": "decide", "status": "uncertain", "latency_ms": 812}],
    "response": {
        "pipeline": "strict", "decision": "ask", "reason": "touches credentials",
        "reasons": ["touches credentials", "writes outside the project"],
        "rules_fired": ["guard-verdict/ask", "scope/outside"], "capped_by": "max_decision",
        "short_circuit": False,
        "agents": [
            {"name": "guard", "var": "g", "model": "guard:9b", "knob": "p=0.61", "verdict": "ask", "ms": 640},
            {"name": "scope", "role": "scope", "model": "scope:3b", "verdict": "allow", "ms": 170},
        ],
    },
}


def stamp(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")


def entry(**fields) -> dict:
    base = {"id": "a1b2c3d4e5f60718", "ts": stamp(), "tool": "Bash", "input": "ls -la",
            "decision": "allow", "reason": "", "action_type": "filesystem_read", "project": "/work/alpha",
            "session": f"{SESSION}.jsonl", "ms": 12}
    base.update(fields)
    return base


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = tmp_path / "nuh.log"
    monkeypatch.setattr(log, "LOG_PATH", str(path))
    monkeypatch.setattr(log, "_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    return path


def write(path, *entries: dict) -> None:
    with open(path, "a") as f:
        for item in entries:
            f.write(json.dumps(item) + "\n")


def run(monkeypatch, capsys, *argv: str) -> list[str]:
    """Output lines of ``nuh <argv>``, without date separator lines."""
    monkeypatch.setattr(sys, "argv", ["nuh", *argv])
    cli.main()
    return [line for line in capsys.readouterr().out.splitlines() if not line.startswith("─")]


def inputs(lines: list[str]) -> list[str]:
    """The input column of compact lines whose input is one word (the tests' ``cmdN`` markers)."""
    return [word for line in lines for word in line.split() if word.startswith("cmd")]


# -- Filters --


class TestFilters:
    def test_decision_flags_old_and_new(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd1"), entry(input="cmd2", decision="block"),
              entry(input="cmd3", decision="ask"))
        assert inputs(run(monkeypatch, capsys, "log", "--blocks")) == ["cmd2"]
        assert inputs(run(monkeypatch, capsys, "log", "--asks")) == ["cmd3"]
        assert inputs(run(monkeypatch, capsys, "log", "-d", "allow")) == ["cmd1"]

    def test_decision_flags_conflict(self, log_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            run(monkeypatch, capsys, "log", "--blocks", "-d", "ask")
        assert exc.value.code == 2

    def test_tool_action_type_and_project(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd1", tool="Read", action_type="filesystem_read"),
              entry(input="cmd2", action_type="git_write", project="/work/beta"),
              entry(input="cmd3", action_type="git_write"))
        assert inputs(run(monkeypatch, capsys, "log", "-t", "read")) == ["cmd1"]
        assert inputs(run(monkeypatch, capsys, "log", "--action-type", "git_write")) == ["cmd2", "cmd3"]
        assert inputs(run(monkeypatch, capsys, "log", "-p", "beta")) == ["cmd2"]

    def test_grep_is_a_case_insensitive_regex_on_the_input(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd1 git PUSH --force"), entry(input="cmd2 git status"))
        assert inputs(run(monkeypatch, capsys, "log", "-g", r"push\s+--f")) == ["cmd1"]

    def test_bad_grep_is_a_usage_error(self, log_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            run(monkeypatch, capsys, "log", "-g", "(")
        assert exc.value.code == 2
        assert "bad regex" in capsys.readouterr().err

    def test_since(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd1", ts=stamp(180)), entry(input="cmd2", ts=stamp(30)),
              entry(input="cmd3", ts=stamp(1)))
        assert inputs(run(monkeypatch, capsys, "log", "-s", "1h")) == ["cmd2", "cmd3"]
        assert inputs(run(monkeypatch, capsys, "log", "--since", "10m")) == ["cmd3"]

    def test_bad_since_is_a_usage_error(self, log_path, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            run(monkeypatch, capsys, "log", "-s", "yesterday")
        assert "bad --since" in capsys.readouterr().err

    def test_parse_since_units_and_today(self):
        now = datetime(2026, 10, 16, 15, 30, tzinfo=timezone.utc)
        assert parse_since("90s", now) == now - timedelta(seconds=90)
        assert parse_since("2h", now) == now - timedelta(hours=2)
        assert parse_since("1d", now) == now - timedelta(days=1)
        today = parse_since("today", now)
        assert (today.hour, today.minute, today.second) == (0, 0, 0)
        assert today <= now and now - today < timedelta(days=1)

    def test_llm_and_errors(self, log_path, monkeypatch, capsys):
        failed = {"provider": "(none)", "cascade": [
            {"provider": "ollama", "status": "error", "latency_ms": 5000, "error": "timed out"}]}
        write(log_path, entry(input="cmd1"), entry(input="cmd2", llm=DECIDE_LLM),
              entry(input="cmd3", llm=failed), entry(input="cmd4", llm={"provider": "(none)", "skipped": "error"}))
        assert inputs(run(monkeypatch, capsys, "log", "--llm")) == ["cmd2", "cmd3", "cmd4"]
        assert inputs(run(monkeypatch, capsys, "log", "--errors")) == ["cmd3", "cmd4"]

    def test_call_prefix_with_or_without_scheme_and_entry_id_fallback(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd1", tool_use_id=CALL), entry(input="cmd2", id="feedbeef00112233"),
              entry(input="cmd3", tool_use_id="toolu_01Zz"))
        assert inputs(run(monkeypatch, capsys, "log", "--call", "toolu_01AbC")) == ["cmd1"]
        assert inputs(run(monkeypatch, capsys, "log", "--call", "01AbCdEf")) == ["cmd1"]
        assert inputs(run(monkeypatch, capsys, "log", "--call", "feedbeef")) == ["cmd2"]

    @pytest.mark.parametrize("query", [
        CALL,                                # the whole tool_use_id
        CALL.removeprefix("toolu_"),         # whole, without toolu_ (longer than the 10-char column)
        "01AbCdEfGhIjK",                     # a prefix past the 10-char column
        "MnOpQrStUv",                        # its tail
        f"{SESSION}:{CALL}",                 # the call ID: session ID + tool_use_id
        f"{SESSION[:8]}:{CALL}",             # a session prefix, as the compact line shows it
        f"{SESSION[:8]}:QrStUv",             # session prefix + tail
    ])
    def test_call_forms(self, log_path, monkeypatch, capsys, query):
        write(log_path, entry(input="cmd1", tool_use_id=CALL),
              entry(input="cmd2", tool_use_id="toolu_01ZzYyXxWwVvUuTtSsRrQqPp"),
              entry(input="cmd3", tool_use_id=CALL, session="0199aaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl"))
        expected = ["cmd1"] if ":" in query else ["cmd1", "cmd3"]
        assert inputs(run(monkeypatch, capsys, "log", "--call", query)) == expected

    def test_call_in_a_rotated_log_with_all(self, log_path, monkeypatch, capsys):
        write(f"{log_path}.1", entry(input="cmdold", tool_use_id=CALL))
        write(log_path, *(entry(input=f"cmd{i}", tool_use_id=f"toolu_01other{i}") for i in range(3)))
        assert inputs(run(monkeypatch, capsys, "log", "-a", "-n", "0", "--call", "MnOpQrStUv")) == ["cmdold"]

    @pytest.mark.parametrize("query", ["AbCdEf", f"{SESSION[:8]}:", f"ffffffff:{CALL}", "toolu_", ":"])
    def test_call_forms_that_match_nothing(self, log_path, monkeypatch, capsys, query):
        write(log_path, entry(input="cmd1", tool_use_id=CALL))
        assert inputs(run(monkeypatch, capsys, "log", "--call", query)) == []

    def test_session_prefix(self, log_path, monkeypatch, capsys):
        codex = "rollout-2026-10-16T10-00-00-0199aaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl"
        write(log_path, entry(input="cmd1"), entry(input="cmd2", session=codex), entry(input="cmd3", session=""))
        assert inputs(run(monkeypatch, capsys, "log", "--session", "3F2A9C")) == ["cmd1"]
        assert inputs(run(monkeypatch, capsys, "log", "--session", "0199aaaa")) == ["cmd2"]


# -- Which entries, in what order --


class TestHistory:
    def test_limit_keeps_the_newest_oldest_first_and_reverse(self, log_path, monkeypatch, capsys):
        write(log_path, *(entry(input=f"cmd{i}") for i in range(5)))
        assert inputs(run(monkeypatch, capsys, "log", "-n", "2")) == ["cmd3", "cmd4"]
        assert inputs(run(monkeypatch, capsys, "log", "-n", "2", "-r")) == ["cmd4", "cmd3"]

    def test_rotated_logs_fill_the_limit_and_all_reads_every_one(self, log_path, monkeypatch, capsys):
        write(f"{log_path}.2", entry(input="cmd0"))
        write(f"{log_path}.1", entry(input="cmd1"), entry(input="cmd2"))
        write(log_path, entry(input="cmd3"), entry(input="cmd4"))
        assert inputs(run(monkeypatch, capsys, "log", "-n", "3")) == ["cmd2", "cmd3", "cmd4"]
        assert inputs(run(monkeypatch, capsys, "log", "-n", "0")) == ["cmd3", "cmd4"]
        assert inputs(run(monkeypatch, capsys, "log", "-n", "0", "-a")) == ["cmd0", "cmd1", "cmd2", "cmd3", "cmd4"]

    def test_since_does_not_open_backups_older_than_the_window(self, log_path, monkeypatch):
        write(f"{log_path}.2", entry(input="cmd0", ts=stamp(600)))
        write(f"{log_path}.1", entry(input="cmd1", ts=stamp(300)), entry(input="cmd2", ts=stamp(5)))
        write(log_path, entry(input="cmd3", ts=stamp(1)))
        opened: list[str] = []
        real = log_query._read_lines
        monkeypatch.setattr(log_query, "_read_lines", lambda p: opened.append(p) or real(p))
        flt = LogFilter(since=parse_since("1h"))
        assert [e["input"] for e in read_history(flt, 0, all_logs=True)] == ["cmd2", "cmd3"]
        assert opened == [str(log_path), f"{log_path}.1"]

    def test_json_passes_entries_through(self, log_path, monkeypatch, capsys):
        items = [entry(input="cmd1", tool_use_id=CALL), entry(input="cmd2", llm=DECIDE_LLM)]
        write(log_path, *items)
        assert [json.loads(line) for line in run(monkeypatch, capsys, "log", "--json")] == items

    def test_empty(self, log_path, monkeypatch, capsys):
        assert run(monkeypatch, capsys, "log") == ["No log entries found."]


# -- Compact view --


class TestCompact:
    def view(self, width: int = 200, **kw) -> LogView:
        return LogView(color=False, width=width, today=date(2000, 1, 1), **kw)

    def test_line_shows_session_call_subagent_old_decide_verdict_and_trace(self):
        line = self.view().render(entry(
            tool_use_id=CALL, agent_id="a22280014490bc9de", agent_type="team-skills:implementer",
            decision="ask", input="cat ~/.aws/credentials", reason="Bash (decide:strict): touches credentials",
            llm=DECIDE_LLM, trace_dir="20261016T120000.000000Z_" + CALL)).splitlines()[-1]
        assert line.split()[1:4] == ["3f2a9c1e", "01AbCdEfGh", "?"]
        for part in ("sub:implementer", "cat ~/.aws/credentials", "[filesystem_read]",
                     "touches credentials", "decide→ask", "12ms"):
            assert part in line
        assert line.endswith(" T")

    def test_entry_id_stands_in_for_a_missing_call_id(self):
        line = self.view().render(entry(id="feedbeef00112233")).splitlines()[-1]
        assert line.split()[2] == "feedbeef00"

    def test_subagent_without_type_shows_its_id(self):
        assert "sub:a2228001 " in self.view().render(entry(agent_id="a22280014490bc9de")).splitlines()[-1]

    def test_provider_llm_and_failed_llm(self):
        ollama = {"provider": "ollama", "model": "qwen", "decision": "allow", "reasoning": "just lists files"}
        line = self.view().render(entry(llm=ollama)).splitlines()[-1]
        assert "ollama→allow" in line and "just lists files" in line
        failed = {"provider": "decide", "model": "decide:strict", "cascade": [
            {"provider": "decide", "status": "error", "latency_ms": 3000, "error": "HTTP 503"}]}
        line = self.view().render(entry(llm=failed)).splitlines()[-1]
        assert "decide:error" in line and "HTTP 503" in line

    def test_a_capped_verdict_shows_what_the_llm_said(self):
        capped = {"provider": "ollama", "model": "qwen", "decision": "block", "capped": "ask", "reasoning": "x"}
        assert "ollama:block→ask" in self.view().render(entry(llm=capped)).splitlines()[-1]
        uncapped = {"provider": "ollama", "model": "qwen", "decision": "block", "reasoning": "x"}
        assert "ollama→block" in self.view().render(entry(llm=uncapped)).splitlines()[-1]

    def test_an_old_decide_line_shows_what_it_logged(self):
        # Before llm.capped, a reply the decide backend capped was logged as llm.decision uncertain;
        # its reply is no longer read, so the line shows the logged verdict as it is.
        old = {**DECIDE_LLM, "decision": "uncertain"}
        line = self.view().render(entry(llm=old)).splitlines()[-1]
        assert "decide→uncertain" in line and "touches credentials" in line

    def test_long_input_and_reason_fit_the_width(self):
        long = entry(tool_use_id=CALL, input="x" * 300, reason="r" * 300, llm=DECIDE_LLM, trace_dir="t")
        line = self.view(width=110).render(long).splitlines()[-1]
        assert len(line) == 110
        assert "…" in line and "decide→ask" in line

    def test_full_input_shows_the_whole_bash_command(self):
        command = "echo " + "y" * 400
        item = entry(input=command[:200], tool_input={"command": command})
        assert command in self.view(width=80, show_full_input=True).render(item)
        assert command not in self.view(width=80).render(item)

    def test_date_separator_when_the_day_changes(self):
        view = self.view()
        first = view.render(entry(ts="2026-10-15T12:00:00+00:00"))
        same = view.render(entry(ts="2026-10-15T12:05:00+00:00"))
        next_day = view.render(entry(ts="2026-10-17T12:00:00+00:00"))
        assert first.startswith("── ") and "\n" in first
        assert "\n" not in same
        assert next_day.startswith("── ")

    def test_colour(self, monkeypatch):
        class Tty:
            def isatty(self) -> bool:
                return True

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm")
        assert use_color(Tty())
        assert not use_color(object())
        monkeypatch.setenv("NO_COLOR", "1")
        assert not use_color(Tty())
        monkeypatch.setenv("NO_COLOR", "")
        monkeypatch.setenv("TERM", "dumb")
        assert not use_color(Tty())
        coloured = LogView(color=True, width=200).render(entry(decision="block"))
        assert "\033[31m" in coloured
        assert "\033[" not in self.view().render(entry(decision="block"))


# -- Details view --


def test_details_block_for_an_old_decide_entry():
    item = entry(tool_use_id=CALL, agent_id="a22280014490bc9de", agent_type="implementer", decision="ask",
                 reason="Bash (decide:strict): touches credentials", llm=DECIDE_LLM,
                 trace_dir="20261016T120000.000000Z_toolu_01AbCdEfGhIjKlMnOpQrStUv",
                 classify={"stages": [{"action_type": "filesystem_read", "decision": "ask", "reason": "creds"}]},
                 hint="To always allow: nuh allow filesystem_read")
    text = LogView(color=False, width=200, details=True).render(item)
    lines = [line.strip() for line in text.splitlines()]
    assert "[ASK]" in lines[0] and "sub:implementer" in lines[0]
    assert f"call      {CALL}" in lines
    assert f"session   {SESSION}" in lines
    assert "subagent  a22280014490bc9de  (implementer)" in lines
    assert "llm       decide→ask  decide:strict  812ms" in lines
    assert "verdict      ask" in lines
    assert "reason       touches credentials" in lines
    assert "cascade      decide uncertain 812ms" in lines
    for gone in ("writes outside the project", "rules fired", "capped by", "member"):  # the backend's reply, unread
        assert gone not in text
    assert "1. filesystem_read      → ask   (creds)" in lines
    assert f"trace dir {item['trace_dir']}" in lines
    assert "total     12ms" in lines


def test_nuh_log_shows_an_old_decide_entry(log_path, monkeypatch, capsys):
    write(log_path, entry(input="cmd1", tool_use_id=CALL, decision="ask", llm=DECIDE_LLM))
    (line,) = run(monkeypatch, capsys, "log")
    assert "decide→ask" in line and "touches credentials" in line
    details = [line.strip() for line in run(monkeypatch, capsys, "log", "-v")]
    assert "llm       decide→ask  decide:strict  812ms" in details
    assert "cascade      decide uncertain 812ms" in details


def test_details_marks_an_entry_id_reference_and_separates_blocks():
    view = LogView(color=False, width=200, details=True)
    first = view.render(entry(id="feedbeef00112233"))
    second = view.render(entry())
    assert "call      feedbeef00112233  (log entry id)" in first
    assert second.startswith("\n")


def test_details_full_input_adds_the_tool_input():
    item = entry(tool_input={"command": "ls -la"})
    assert '"command": "ls -la"' in LogView(color=False, width=200, details=True, show_full_input=True).render(item)
    assert "tool_in" not in LogView(color=False, width=200, details=True).render(item)


def test_reference_helpers():
    assert short_call(CALL) == "01AbCdEfGh"
    assert short_call("call_xyz") == "xyz"
    assert session_id({"session": "notes.jsonl"}) == "notes"


# -- Following --


class Script:
    """A stand-in for time.sleep that performs the next scripted step on each call."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)

    def __call__(self, _seconds: float) -> None:
        if not self.steps:
            raise KeyboardInterrupt  # the script ran out before the expected entries arrived
        self.steps.pop(0)()


def follow(path, steps, count: int, *, from_start: bool = False) -> list[dict]:
    handle = None
    if os.path.exists(path):
        handle = open(path, "rb")
        if not from_start:
            handle.seek(0, os.SEEK_END)
    return list(itertools.islice(cli_log.follow_entries(str(path), handle, sleep=Script(*steps)), count))


class TestFollow:
    def test_new_entries_across_a_rotation(self, tmp_path):
        path = tmp_path / "nuh.log"
        write(path, entry(input="cmd0"))

        def rotate() -> None:
            write(path, entry(input="cmd2"))  # lands in the old file just before the rename
            os.rename(path, f"{path}.1")
            write(path, entry(input="cmd3"))

        got = follow(path, [lambda: write(path, entry(input="cmd1")), rotate], 3)
        assert [e["input"] for e in got] == ["cmd1", "cmd2", "cmd3"]

    def test_waits_for_a_rotated_log_to_be_recreated(self, tmp_path):
        path = tmp_path / "nuh.log"
        write(path, entry(input="cmd0"))
        got = follow(path, [lambda: os.unlink(path), lambda: None, lambda: write(path, entry(input="cmd1"))], 1)
        assert [e["input"] for e in got] == ["cmd1"]

    def test_waits_for_the_log_to_exist(self, tmp_path):
        path = tmp_path / "nuh.log"
        got = follow(path, [lambda: None, lambda: write(path, entry(input="cmd1"))], 1)
        assert [e["input"] for e in got] == ["cmd1"]

    def test_a_line_in_the_middle_of_being_written_waits_for_its_end(self, tmp_path):
        path = tmp_path / "nuh.log"
        path.write_text("")
        line = json.dumps(entry(input="cmd1")) + "\n"

        def part(text: str):
            def step() -> None:
                with open(path, "a") as f:
                    f.write(text)
            return step

        got = follow(path, [part(line[:20]), part(line[20:])], 1)
        assert [e["input"] for e in got] == ["cmd1"]

    def test_a_truncated_log_is_reread_from_the_start(self, tmp_path):
        path = tmp_path / "nuh.log"
        write(path, entry(input="cmd0", pad="x" * 200))

        def truncate_and_write() -> None:
            with open(path, "w") as f:
                f.write(json.dumps(entry(input="cmd1")) + "\n")

        got = follow(path, [truncate_and_write], 1)
        assert [e["input"] for e in got] == ["cmd1"]

    def test_tail_shows_the_backlog_then_new_matching_entries(self, log_path, monkeypatch, capsys):
        write(log_path, *(entry(input=f"cmd{i}", decision="block") for i in range(4)))
        monkeypatch.setattr(cli_log.time, "sleep", Script(
            lambda: write(log_path, entry(input="cmd4"), entry(input="cmd5", decision="block"))))
        assert inputs(run(monkeypatch, capsys, "tail", "-n", "2", "--blocks")) == ["cmd2", "cmd3", "cmd5"]

    def test_log_follow_finishes_a_half_written_last_line(self, log_path, monkeypatch, capsys):
        write(log_path, entry(input="cmd0"))
        line = json.dumps(entry(input="cmd1")) + "\n"
        with open(log_path, "a") as f:
            f.write(line[:15])

        def finish() -> None:
            with open(log_path, "a") as f:
                f.write(line[15:])

        monkeypatch.setattr(cli_log.time, "sleep", Script(finish))
        assert inputs(run(monkeypatch, capsys, "log", "-f")) == ["cmd0", "cmd1"]

    def test_follow_rejects_reverse(self, log_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            run(monkeypatch, capsys, "log", "-f", "-r")
        assert exc.value.code == 2
