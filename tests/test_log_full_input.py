"""Full tool input in the decision log: stored raw, size cap, multi-backup rotation."""

import json
import os
from unittest.mock import patch

import pytest

from nuh import log
from nuh.log_query import LogFilter, read_history
from nuh.log import cap_tool_input


@pytest.fixture(autouse=True)
def _use_tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(log, "LOG_PATH", str(tmp_path / "nuh.log"))
    monkeypatch.setattr(log, "_CONFIG_DIR", str(tmp_path))


def _written(tmp_path) -> dict:
    with open(tmp_path / "nuh.log") as f:
        return json.loads(f.readlines()[-1])


def _entry(tool_input: dict, tool: str = "Bash") -> dict:
    with patch("nuh.paths.get_project_root", return_value="/tmp/project"):
        return log.build_entry(
            tool=tool, input_summary=log.input_summary(tool, tool_input),
            decision="ask", reason="r", agent="claude", hook_version="0",
            total_ms=1, meta={}, tool_input=tool_input,
        )


class TestFullInput:
    def test_long_command_logged_whole(self, tmp_path):
        cmd = "echo " + "a" * 500
        log.log_decision(_entry({"command": cmd, "description": "d"}))
        entry = _written(tmp_path)
        assert entry["tool_input"] == {"command": cmd, "description": "d"}
        assert len(entry["input"]) == 200  # short summary unchanged
        assert "tool_input_truncated" not in entry

    def test_cap_marks_truncation(self, tmp_path):
        log.log_decision(_entry({"file_path": "/p/big", "content": "z" * 50}, tool="Write"),
                         {"max_input_chars": 10})
        entry = _written(tmp_path)
        assert entry["tool_input"]["content"] == "z" * 10 + "…[truncated 40 chars]"
        assert entry["tool_input"]["file_path"] == "/p/big"
        assert entry["tool_input_truncated"] is True

    def test_default_cap(self):
        capped, truncated = cap_tool_input({"content": "q" * 20_001})
        assert truncated
        assert capped["content"].startswith("q" * 20_000 + "…[truncated 1 chars]")

    def test_non_string_values_kept(self):
        capped, truncated = cap_tool_input({"n": 3, "b": True, "x": None})
        assert capped == {"n": 3, "b": True, "x": None} and not truncated

    def test_bad_cap_warns_and_uses_default(self, tmp_path, capsys):
        log.log_decision(_entry({"command": "ls"}), {"max_input_chars": "lots"})
        assert _written(tmp_path)["tool_input"] == {"command": "ls"}
        assert "log.max_input_chars" in capsys.readouterr().err


# Built at runtime: nuh scans Write content for key-shaped text.
KEY_MARKER = "-----BEGIN " + "OPENSSH" + " PRIVATE KEY-----"
URL_PASSWORD = "Pw" + "4e8c1d9b" * 2


class TestStoredRaw:
    """The log keeps input as it was: the secret is already in the transcript, and replay needs it."""

    def test_secrets_and_exports_are_not_masked(self, tmp_path):
        command = f"export TOKEN=hunter2 && echo '{KEY_MARKER}' && psql postgres://app:{URL_PASSWORD}@db/app"
        entry = _entry({"command": command})
        entry["reason"] = f"reads {KEY_MARKER}"
        log.log_decision(entry)
        stored = _written(tmp_path)
        assert stored["tool_input"]["command"] == command
        assert stored["input"] == command
        assert stored["reason"] == f"reads {KEY_MARKER}"


class TestRotation:
    def _fill(self, n: int, cfg: dict) -> None:
        for i in range(n):
            log.log_decision({"decision": "allow", "i": i, "pad": "x" * 100}, cfg)

    def test_keeps_configured_backups(self, tmp_path):
        self._fill(200, {"max_size_bytes": 300, "backups": 3})
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["nuh.log", "nuh.log.1", "nuh.log.2", "nuh.log.3"]

    def test_backups_shift_oldest_last(self, tmp_path):
        self._fill(40, {"max_size_bytes": 300, "backups": 5})

        def first_i(name: str) -> int:
            with open(tmp_path / name) as f:
                return json.loads(f.readline())["i"]

        assert first_i("nuh.log.1") > first_i("nuh.log.2") > first_i("nuh.log.3")

    def test_zero_backups_discards(self, tmp_path):
        self._fill(20, {"max_size_bytes": 300, "backups": 0})
        assert sorted(p.name for p in tmp_path.iterdir()) == ["nuh.log"]

    def test_default_size_is_50mb(self):
        assert log._DEFAULT_MAX_SIZE == 50_000_000
        assert log._DEFAULT_BACKUPS == 5

    def test_reading_back_spans_backups(self, tmp_path):
        self._fill(30, {"max_size_bytes": 1000, "backups": 10})
        entries = read_history(LogFilter(), 30)
        assert [e["i"] for e in entries] == list(range(30))

    def test_reading_back_without_live_file(self, tmp_path):
        self._fill(5, {})
        os.rename(tmp_path / "nuh.log", tmp_path / "nuh.log.1")
        assert [e["i"] for e in read_history(LogFilter(), 50)] == [0, 1, 2, 3, 4]


class TestSubagentIds:
    def _build(self, subagent):
        with patch("nuh.paths.get_project_root", return_value="/tmp/project"):
            return log.build_entry(
                tool="Bash", input_summary="ls", decision="allow", reason="",
                agent="claude", hook_version="0", total_ms=1, meta={},
                subagent=subagent,
            )

    def test_subagent_ids_logged(self):
        entry = self._build({"agent_id": "a1b2", "agent_type": "Explore",
                             "tool_use_id": "toolu_01"})
        assert (entry["agent_id"], entry["agent_type"], entry["tool_use_id"]) == (
            "a1b2", "Explore", "toolu_01")

    def test_empty_ids_omitted(self):
        entry = self._build({"agent_id": "", "agent_type": "", "tool_use_id": "toolu_01"})
        assert "agent_id" not in entry and "agent_type" not in entry
        assert entry["tool_use_id"] == "toolu_01"
        assert "agent_id" not in self._build(None)
