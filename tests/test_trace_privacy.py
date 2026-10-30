"""LLM trace directory: private modes, no work when off, a broken dir never changes a decision, unique names."""

import os
import re
import stat

import pytest

from nuh import trace, trace_names
from tests.trace_helpers import BASH, DIR_NAME, invoke, ollama_reply, only_dir, set_config, use_http


pytestmark = pytest.mark.usefixtures("trace_isolated")


# -- Privacy and robustness --


def test_modes_are_private(monkeypatch, tmp_path):
    traces = tmp_path / "missing" / "traces"
    set_config(traces)
    use_http(monkeypatch, ollama_reply("allow"))
    old = os.umask(0)
    try:
        invoke(monkeypatch, BASH)
    finally:
        os.umask(old)

    assert stat.S_IMODE(traces.stat().st_mode) == 0o700
    d = only_dir(traces)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for f in d.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_tracing_off_does_no_trace_work(monkeypatch, tmp_path):
    set_config("")
    http = use_http(monkeypatch, ollama_reply("allow"))

    def no_trace(*args):
        raise AssertionError("a trace was created with tracing off")

    monkeypatch.setattr(trace, "Trace", no_trace)
    before = sorted(tmp_path.iterdir())

    out = invoke(monkeypatch, BASH)

    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert len(http.sent) == 1
    assert sorted(tmp_path.iterdir()) == before


def _disk_full(*args):
    raise OSError(28, "No space left on device")


@pytest.mark.parametrize("breakage", ["parent-is-a-file", "disk-full"])
def test_broken_trace_dir_leaves_decision_unchanged(monkeypatch, tmp_path, capsys, breakage):
    set_config("")
    use_http(monkeypatch, ollama_reply("block", "risky"))
    baseline = invoke(monkeypatch, BASH)
    capsys.readouterr()

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    set_config(blocker / "traces" if breakage == "parent-is-a-file" else tmp_path / "traces")
    if breakage == "disk-full":
        monkeypatch.setattr(trace.Trace, "_write", _disk_full)
    use_http(monkeypatch, ollama_reply("block", "risky"))

    assert invoke(monkeypatch, BASH) == baseline
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("nuh: trace:")]
    assert len(errors) == 1


def test_two_invocations_never_share_a_directory(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces)
    names = iter(["20260927T000000.000000Z_fixed", "20260927T000000.000000Z_fixed",
                  "20260927T000000.000001Z_fresh"])
    monkeypatch.setattr(trace_names, "dir_name", lambda *_: next(names))
    use_http(monkeypatch, ollama_reply("allow"), ollama_reply("allow"))

    invoke(monkeypatch, BASH)
    invoke(monkeypatch, BASH)

    dirs = sorted(p.name for p in traces.iterdir())
    assert dirs == ["20260927T000000.000000Z_fixed", "20260927T000000.000001Z_fresh"]
    for name in dirs:
        assert (traces / name / "result.json").is_file()


def test_real_names_are_unique_per_invocation(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces)
    use_http(monkeypatch, ollama_reply("allow"), ollama_reply("allow"))

    invoke(monkeypatch, BASH)
    invoke(monkeypatch, BASH)

    dirs = sorted(p.name for p in traces.iterdir())
    assert len(dirs) == 2
    assert all(DIR_NAME.match(name) for name in dirs)


def test_name_is_the_tool_call_id_when_the_payload_has_one(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces)
    use_http(monkeypatch, ollama_reply("allow"))

    invoke(monkeypatch, {**BASH, "tool_use_id": "toolu_01AbC/../x y"})

    name = only_dir(traces).name
    assert re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z_toolu_01AbCxy", name)
    assert name.split("_", 1)[0] == name[:23]  # a reader parses the stamp before the first "_"


def test_overlong_call_id_is_capped():
    name = trace_names.dir_name(trace_names.call_id({"tool_use_id": "t" * 300}))
    assert name.split("_", 1)[1] == "t" * 64


def test_unusable_call_id_falls_back_to_uuid():
    assert DIR_NAME.match(trace_names.dir_name(trace_names.call_id({"tool_use_id": "/.."})))
    assert DIR_NAME.match(trace_names.dir_name(trace_names.call_id({"tool_use_id": 42})))


def test_call_id_collision_keeps_the_id_and_adds_a_suffix(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    set_config(traces)
    stamp = "20260927T000000.000000Z"
    (traces / f"{stamp}_toolu_1").mkdir(parents=True)
    monkeypatch.setattr(trace_names, "datetime", _FrozenClock)
    use_http(monkeypatch, ollama_reply("allow"))

    invoke(monkeypatch, {**BASH, "tool_use_id": "toolu_1"})

    fresh = [p.name for p in traces.iterdir() if p.name != f"{stamp}_toolu_1"]
    assert len(fresh) == 1
    assert re.fullmatch(rf"{stamp}_toolu_1-[0-9a-f]{{8}}", fresh[0])
    assert (traces / fresh[0] / "result.json").is_file()


class _FrozenClock:
    """Stands in for trace_names.datetime so every name in one test shares a timestamp."""

    @staticmethod
    def now(tz=None):
        from datetime import datetime, timezone
        return datetime(2026, 9, 27, tzinfo=timezone.utc)
