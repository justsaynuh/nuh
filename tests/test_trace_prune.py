"""Trace retention (llm.trace_keep_days): what a prune may delete, how often it runs, and its CLI."""

import os
import sys
import time

import pytest

from nuh import cli, config, trace_prune
from nuh.bash import classify_command
from nuh.config import _merge_configs
from tests.test_trace import BASH, invoke, ollama_reply, set_config, use_http

DAY = 24 * 3600
NOW = time.time()


def stamp(days_ago: float) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(NOW - days_ago * DAY)) + ".000000Z"


def trace_dir(root, days_ago: float, call: str = "toolu_01x", *, files=("input.json", "result.json",
                                                                          "request-01-system1.json",
                                                                          "response-01-system1.json")):
    d = root / f"{stamp(days_ago)}_{call}"
    d.mkdir(parents=True)
    for name in files:
        (d / name).write_text("{}")
    return d


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "traces"
    r.mkdir()
    return r


# -- What a prune deletes --


def test_deletes_only_trace_dirs_past_the_cutoff(root, tmp_path):
    old = [trace_dir(root, 40, f"toolu_old{i}") for i in range(3)]
    fresh = trace_dir(root, 2, "toolu_fresh")
    future = trace_dir(root, -400, "toolu_future")  # a clock that was wrong: never old
    outcome = trace_prune.prune(str(root), 30, now=NOW)
    assert sorted(outcome.deleted) == sorted(d.name for d in old)
    assert outcome.bytes == 3 * 4 * 2 and not outcome.errors
    assert not any(d.exists() for d in old)
    assert fresh.is_dir() and future.is_dir()


def test_never_deletes_anything_that_is_not_a_trace_dir(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "input.json").write_text("precious")
    (outside / "photo.jpg").write_text("precious")
    planted = {
        "a file named like a trace": root / f"{stamp(90)}_toolu_file",
        "a symlink named like a trace": root / f"{stamp(90)}_toolu_link",
        "an old-looking name that does not parse": root / "20200101T000000Z_toolu_x",
        "a bad date": root / "20201340T000000.000000Z_toolu_x",
        "notes": root / "notes",
    }
    planted["a file named like a trace"].write_text("not a dir")
    planted["a symlink named like a trace"].symlink_to(outside, target_is_directory=True)
    planted["an old-looking name that does not parse"].mkdir()
    (planted["an old-looking name that does not parse"] / "input.json").write_text("{}")
    planted["a bad date"].mkdir()
    planted["notes"].mkdir()
    (root / "README.txt").write_text("mine")
    mixed = trace_dir(root, 90, "toolu_mixed")
    (mixed / "my-notes.txt").write_text("mine")
    nested = trace_dir(root, 90, "toolu_nested")
    (nested / "sub").mkdir()
    linked = trace_dir(root, 90, "toolu_linked", files=("result.json",))
    os.symlink(outside / "input.json", linked / "input.json")

    for dry in (True, False):
        outcome = trace_prune.prune(str(root), 30, dry_run=dry, now=NOW)
        assert outcome.deleted == [] and not outcome.errors
        assert sorted(name for name, _ in outcome.kept) == sorted([mixed.name, nested.name, linked.name])

    assert all(os.path.lexists(p) for p in planted.values())
    assert (root / "README.txt").read_text() == "mine"
    assert sorted(p.name for p in mixed.iterdir()) == ["input.json", "my-notes.txt", "request-01-system1.json",
                                                       "response-01-system1.json", "result.json"]
    assert (nested / "sub").is_dir() and (nested / "input.json").exists()
    assert (linked / "result.json").exists() and os.path.islink(linked / "input.json")
    assert sorted(p.name for p in outside.iterdir()) == ["input.json", "photo.jpg"]
    assert (outside / "input.json").read_text() == "precious"


def test_dry_run_deletes_nothing_and_counts_the_same(root):
    old = trace_dir(root, 31)
    dry = trace_prune.prune(str(root), 30, dry_run=True, now=NOW)
    assert dry.deleted == [old.name] and old.is_dir()
    real = trace_prune.prune(str(root), 30, now=NOW)
    assert real.deleted == dry.deleted and real.bytes == dry.bytes and not old.exists()


def test_zero_keeps_forever(root):
    old = trace_dir(root, 3000)
    assert trace_prune.prune(str(root), 0, now=NOW).deleted == []
    assert trace_prune.maybe_prune(str(root), 0) is None
    assert old.is_dir() and not os.path.exists(trace_prune.marker_path(str(root)))


# -- At most once a day, from the hook --


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(trace_prune, "_spawn", lambda root, days: calls.append((root, days)) or "child")
    return calls


def test_the_first_trace_only_starts_the_clock(root, spawned):
    marker = trace_prune.marker_path(str(root))
    assert marker == str(root.parent / ".traces.pruned")  # beside the trace dir, never in it
    assert trace_prune.maybe_prune(str(root), 30, now=NOW) is None
    assert os.path.isfile(marker) and spawned == []
    assert trace_prune.maybe_prune(str(root), 30, now=NOW + DAY - 60) is None
    assert spawned == []


@pytest.mark.parametrize("age", [DAY + 1, -3600 * 24 * 365])  # a day old, or stamped in the future
def test_a_stale_marker_starts_one_prune_and_is_restamped(root, spawned, age):
    marker = trace_prune.marker_path(str(root))
    trace_prune.write_marker(marker, {"armed": "x"})
    os.utime(marker, (NOW - age, NOW - age))
    assert trace_prune.maybe_prune(str(root), 30, now=NOW) == "child"
    assert spawned == [(str(root), 30)]
    assert abs(os.stat(marker).st_mtime - time.time()) < 60
    assert trace_prune.maybe_prune(str(root), 30) is None and len(spawned) == 1


def test_the_child_prunes_and_records_its_outcome(root):
    old, fresh = trace_dir(root, 45), trace_dir(root, 1)
    child = trace_prune._spawn(str(root), 30)
    assert child.wait(timeout=30) == 0
    assert not old.exists() and fresh.is_dir()
    record = trace_prune.read_marker(trace_prune.marker_path(str(root)))
    assert (record["deleted"], record["automatic"], record["errors"]) == (1, True, [])


def test_the_hook_prunes_after_printing_its_decision(monkeypatch, tmp_path, project_root):
    traces = tmp_path / "traces"
    set_config(traces)
    old = trace_dir(traces, 60)
    marker = trace_prune.marker_path(str(traces))
    trace_prune.write_marker(marker, {})
    os.utime(marker, (NOW - 2 * DAY, NOW - 2 * DAY))
    printed_first = []
    monkeypatch.setattr(trace_prune, "_spawn", lambda root, days: printed_first.append(sys.stdout.getvalue()))
    use_http(monkeypatch, ollama_reply("allow"))
    assert invoke(monkeypatch, BASH)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert len(printed_first) == 1 and '"permissionDecision": "allow"' in printed_first[0]
    assert old.is_dir()  # the hook itself deletes nothing: the child does


def test_a_broken_marker_never_changes_the_decision(monkeypatch, tmp_path, project_root, capsys):
    traces = tmp_path / "traces"
    set_config(traces)
    os.makedirs(trace_prune.marker_path(str(traces)))  # a directory where the marker file goes
    os.utime(trace_prune.marker_path(str(traces)), (NOW - 2 * DAY, NOW - 2 * DAY))
    use_http(monkeypatch, ollama_reply("allow"))
    assert invoke(monkeypatch, BASH)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "nuh: trace: IsADirectoryError" in capsys.readouterr().err


# -- Config and CLI --


@pytest.mark.parametrize("raw,expected,warned", [(None, 30, False), (7, 7, False), (0, 0, False),
                                                 (-1, 30, True), ("7", 30, True), (True, 30, True)])
def test_keep_days_config(raw, expected, warned, capsys):
    llm = {} if raw is None else {"trace_keep_days": raw}
    assert _merge_configs({"llm": llm}, {}).llm_trace_keep_days == expected
    assert ("llm.trace_keep_days" in capsys.readouterr().err) == warned


def run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["nuh", *argv])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out, err = capsys.readouterr()
    return done.value.code, out, err


def test_config_show_has_keep_days(monkeypatch, capsys):
    config._cached_config = _merge_configs({"llm": {"trace_keep_days": 0}}, {})
    monkeypatch.setattr(sys, "argv", ["nuh", "config", "show"])
    cli.main()
    shown = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert ["llm.trace_keep_days:", "0", "(keep", "forever)"] in shown


def test_cli_dry_run_then_prune(root, monkeypatch, capsys):
    old = trace_dir(root, 40)
    code, out, _ = run(monkeypatch, capsys, "trace", "--dir", str(root), "--prune", "--dry-run")
    assert code == 0 and "Would delete 1 trace directories older than 30 days" in out and old.is_dir()
    code, out, _ = run(monkeypatch, capsys, "trace", "--dir", str(root), "--prune")
    assert code == 0 and "Deleted 1 trace directories" in out and not old.exists()
    code, out, _ = run(monkeypatch, capsys, "trace", "--dir", str(root), "--prune", "--dry-run")
    assert "Last prune:" in out and "deleted 1" in out


def test_cli_refuses_prune_mixups(root, monkeypatch, capsys):
    assert run(monkeypatch, capsys, "trace", "--dir", str(root), "--dry-run")[0] == 2
    assert run(monkeypatch, capsys, "trace", "--dir", str(root), "--prune", "#abc123")[0] == 2
    assert run(monkeypatch, capsys, "trace", "--dir", str(root), "--pru")[0] == 2  # no abbreviations


@pytest.mark.parametrize("command,admin", [
    ("nuh trace --prune", True),
    ("nuh trace --dir /x --prune", True),
    ("nuh trace --prune -- --dry-run", True),
    ("python3 -m nuh trace --prune", True),
    ("nuh trace --prune --dry-run", False),
    ("nuh trace", False),
    ("nuh trace -- --prune", False),
])
def test_only_a_real_prune_is_nuh_admin(command, admin):
    result = classify_command(command)
    assert ("nuh_admin" in result.floor_rules()) == admin
    assert result.final_decision == ("ask" if admin else "allow")

