"""Unit tests for the decision logging module."""

import json
import os
from unittest.mock import patch

import pytest

from nuh import log, steps
from nuh.log_query import LogFilter, read_history


def _read(limit: int = 50, **filters) -> list[dict]:
    """Matching entries, newest first (the order the old read_log returned)."""
    return list(reversed(read_history(LogFilter(**filters), limit)))


@pytest.fixture(autouse=True)
def _use_tmp_log(tmp_path, monkeypatch):
    """Redirect log to tmp_path for all tests."""
    log_path = str(tmp_path / "nuh.log")
    monkeypatch.setattr(log, "LOG_PATH", log_path)
    monkeypatch.setattr(log, "_CONFIG_DIR", str(tmp_path))


# -- input_summary --


class TestInputSummary:
    def test_bash_command(self):
        result = log.input_summary("Bash", {"command": "git status"})
        assert result == "git status"

    def test_bash_truncated_at_200(self):
        long_cmd = "x" * 300
        result = log.input_summary("Bash", {"command": long_cmd})
        assert len(result) == 200

    def test_bash_summary_is_not_masked(self):
        """The log stores input raw: an exported value is kept for replay."""
        assert log.input_summary("Bash", {"command": "export A=1 && export B=2"}) == "export A=1 && export B=2"

    def test_read_file_path(self):
        result = log.input_summary("Read", {"file_path": "/tmp/foo.py"})
        assert result == "/tmp/foo.py"

    def test_glob_pattern(self):
        result = log.input_summary("Glob", {"pattern": "**/*.py"})
        assert result == "**/*.py"

    def test_grep_with_path(self):
        result = log.input_summary("Grep", {"pattern": "TODO", "path": "src/"})
        assert result == "pattern=TODO path=src/"

    def test_grep_without_path(self):
        result = log.input_summary("Grep", {"pattern": "TODO"})
        assert result == "pattern=TODO"

    def test_write_path_only(self):
        result = log.input_summary("Write", {"file_path": "/tmp/x.py", "content": "SECRET=abc"})
        assert result == "/tmp/x.py"
        assert "SECRET" not in result

    def test_edit_path_only(self):
        result = log.input_summary("Edit", {"file_path": "/tmp/x.py", "new_string": "password=abc"})
        assert result == "/tmp/x.py"
        assert "password" not in result

    def test_mcp_tool(self):
        result = log.input_summary("mcp__postgres__query", {"query": "SELECT * FROM users"})
        assert "query=" in result
        assert "SELECT" in result

    def test_mcp_tool_empty_input(self):
        result = log.input_summary("mcp__foo__bar", {})
        assert result == ""

    def test_unknown_tool(self):
        result = log.input_summary("Agent", {"foo": "bar"})
        assert result == ""


# -- log_decision --


class TestLogDecision:
    def test_writes_jsonl(self, tmp_path):
        log.log_decision({"decision": "allow", "tool": "Bash"})
        path = str(tmp_path / "nuh.log")
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["decision"] == "allow"
        assert entry["tool"] == "Bash"
        assert "ts" in entry

    def test_appends_multiple(self, tmp_path):
        log.log_decision({"decision": "allow"})
        log.log_decision({"decision": "block"})
        path = str(tmp_path / "nuh.log")
        with open(path) as f:
            lines = [line for line in f.readlines() if line.strip()]
        assert len(lines) == 2

    def test_preserves_existing_ts(self):
        log.log_decision({"decision": "allow", "ts": "2026-01-01T00:00:00"})
        # Didn't crash, ts preserved

    def test_llm_fields_preserved(self, tmp_path):
        log.log_decision({
            "decision": "allow",
            "tool": "Bash",
            "llm_provider": "openrouter",
            "llm_model": "gemini-flash",
            "llm_latency_ms": 500,
            "llm_reasoning": "safe operation",
            "llm_cascade": [{"provider": "openrouter", "status": "success", "latency_ms": 500}],
        })
        entries = _read()
        assert len(entries) == 1
        assert entries[0]["llm_provider"] == "openrouter"
        assert entries[0]["llm_model"] == "gemini-flash"
        assert entries[0]["llm_cascade"][0]["status"] == "success"

    def test_never_raises_on_bad_path(self, monkeypatch):
        monkeypatch.setattr(log, "LOG_PATH", "/nonexistent/dir/nuh.log")
        monkeypatch.setattr(log, "_CONFIG_DIR", "/nonexistent/dir")
        # Should not raise
        log.log_decision({"decision": "allow"})


# -- verbosity --


class TestVerbosityFiltering:
    def test_blocks_only_skips_allow(self, tmp_path):
        log.log_decision({"decision": "allow"}, {"verbosity": "blocks_only"})
        path = str(tmp_path / "nuh.log")
        assert not os.path.exists(path) or os.path.getsize(path) == 0

    def test_blocks_only_skips_ask(self, tmp_path):
        log.log_decision({"decision": "ask"}, {"verbosity": "blocks_only"})
        path = str(tmp_path / "nuh.log")
        assert not os.path.exists(path) or os.path.getsize(path) == 0

    def test_blocks_only_writes_block(self, tmp_path):
        log.log_decision({"decision": "block"}, {"verbosity": "blocks_only"})
        path = str(tmp_path / "nuh.log")
        assert os.path.getsize(path) > 0

    def test_decisions_skips_allow(self, tmp_path):
        log.log_decision({"decision": "allow"}, {"verbosity": "decisions"})
        path = str(tmp_path / "nuh.log")
        assert not os.path.exists(path) or os.path.getsize(path) == 0

    def test_decisions_writes_ask(self, tmp_path):
        log.log_decision({"decision": "ask"}, {"verbosity": "decisions"})
        path = str(tmp_path / "nuh.log")
        assert os.path.getsize(path) > 0

    def test_decisions_writes_block(self, tmp_path):
        log.log_decision({"decision": "block"}, {"verbosity": "decisions"})
        path = str(tmp_path / "nuh.log")
        assert os.path.getsize(path) > 0

    def test_all_writes_everything(self, tmp_path):
        for d in ("allow", "ask", "block"):
            log.log_decision({"decision": d}, {"verbosity": "all"})
        path = str(tmp_path / "nuh.log")
        with open(path) as f:
            lines = [line for line in f.readlines() if line.strip()]
        assert len(lines) == 3


# -- rotation --


class TestRotation:
    def test_rotates_on_size(self, tmp_path):
        log_path = str(tmp_path / "nuh.log")
        # Write enough to exceed a tiny max_size
        for i in range(100):
            log.log_decision({"decision": "allow", "data": "x" * 100}, {"max_size_bytes": 500})
        # Backup should exist
        backup = str(tmp_path / "nuh.log.1")
        assert os.path.exists(backup)
        # Main log should be smaller than total written
        assert os.path.getsize(log_path) < 100 * 200

    def test_no_rotation_on_empty_log(self, tmp_path):
        """FD-084: empty log file is not rotated."""
        log_path = tmp_path / "nuh.log"
        log_path.touch()  # empty file
        log._rotate()
        assert not os.path.exists(str(tmp_path / "nuh.log.1"))


# -- reading back (log_query.read_history) --


class TestReadHistory:
    def test_empty_log(self, tmp_path):
        assert _read() == []

    def test_returns_oldest_first(self, tmp_path):
        log.log_decision({"decision": "allow", "tool": "Bash", "ts": "2026-01-01T00:00:01"})
        log.log_decision({"decision": "block", "tool": "Read", "ts": "2026-01-01T00:00:02"})
        entries = read_history(LogFilter(), 50)
        assert [e["ts"] for e in entries] == ["2026-01-01T00:00:01", "2026-01-01T00:00:02"]

    def test_filter_by_decision(self, tmp_path):
        log.log_decision({"decision": "allow"})
        log.log_decision({"decision": "block"})
        entries = _read(decision="block")
        assert len(entries) == 1
        assert entries[0]["decision"] == "block"

    def test_filter_by_tool_ignores_case(self, tmp_path):
        log.log_decision({"decision": "allow", "tool": "Bash"})
        log.log_decision({"decision": "allow", "tool": "Read"})
        entries = _read(tool="bash")
        assert len(entries) == 1
        assert entries[0]["tool"] == "Bash"

    def test_limit_keeps_the_newest(self, tmp_path):
        for i in range(20):
            log.log_decision({"decision": "allow", "i": i})
        assert [e["i"] for e in read_history(LogFilter(), 5)] == [15, 16, 17, 18, 19]

    def test_handles_corrupt_lines(self, tmp_path):
        log_path = str(tmp_path / "nuh.log")
        with open(log_path, "w") as f:
            f.write('{"decision": "allow"}\n')
            f.write('not json\n')
            f.write('{"decision": "block"}\n')
        assert len(_read()) == 2


# -- build_entry_v2 --


class TestBuildEntry:
    """Structured entry builder (nuh-4gm)."""

    def _build(self, **kwargs):
        defaults = dict(
            tool="Bash", input_summary="ls", decision="allow", reason="",
            agent="claude", hook_version="0.6.0", total_ms=18,
            meta={}, transcript_path="",
        )
        defaults.update(kwargs)
        with patch("nuh.paths.get_project_root", return_value="/tmp/project"):
            return log.build_entry(**defaults)

    def test_core_fields_present(self):
        entry = self._build()
        assert "id" in entry
        assert "ts" not in entry  # ts added by log_decision, not builder
        assert entry["user"] != ""  # OS user should be set
        assert entry["agent"] == "claude"
        assert entry["hook_version"] == "0.6.0"
        assert entry["tool"] == "Bash"
        assert entry["input"] == "ls"
        assert entry["project"] == "/tmp/project"
        assert entry["decision"] == "allow"
        assert entry["reason"] == ""
        assert entry["ms"] == 18

    def test_id_length_16_hex(self):
        entry = self._build()
        assert len(entry["id"]) == 16
        int(entry["id"], 16)  # valid hex

    def test_id_unique(self):
        e1 = self._build()
        e2 = self._build()
        assert e1["id"] != e2["id"]

    def test_action_type_first_ask(self):
        """Multi-stage: picks first ask stage's action_type."""
        meta = {"stages": [
            {"action_type": "filesystem_read", "decision": "allow"},
            {"action_type": "network_outbound", "decision": "ask"},
        ]}
        entry = self._build(meta=meta)
        assert entry["action_type"] == "network_outbound"

    def test_action_type_fallback_first_stage(self):
        """All allow: picks first stage's action_type."""
        meta = {"stages": [
            {"action_type": "git_safe", "decision": "allow"},
            {"action_type": "filesystem_read", "decision": "allow"},
        ]}
        entry = self._build(meta=meta)
        assert entry["action_type"] == "git_safe"

    def test_action_type_empty_meta(self):
        entry = self._build(meta={})
        assert entry["action_type"] == ""

    def test_steps_logged_in_order(self):
        trail = [{"step": "static", "decision": "ask", "reason": "Bash: unknown → ask",
                  "breakdown": [{"action_type": "unknown", "decision": "ask"}], "composition": "pipe"},
                 {"step": "llm", "decision": "block", "reason": "posts a dotfile"},
                 {"step": "cap", "from": "block", "to": "ask", "rule": "llm.can_block: false"}]
        entry = self._build(meta={"stages": [{"action_type": "unknown", "decision": "ask"}]}, steps=trail)
        assert entry["steps"] == trail
        assert "classify" not in entry  # Bash's stages are the static step's breakdown

    def test_steps_absent_without_steps(self):
        entry = self._build(meta={})
        assert "steps" not in entry

    def test_steps_are_bounded(self):
        """A huge reason or breakdown cannot bloat the log line (steps.for_log); the trace keeps them whole."""
        stages = [{"action_type": "unknown", "decision": "ask", "reason": "r" * 5000} for _ in range(100)]
        trail = [{"step": "static", "decision": "ask", "reason": "x" * 20_000, "breakdown": stages},
                 {"step": "llm", "decision": "ask", "reason": "y" * 20_000}]
        entry = self._build(meta={}, steps=trail)
        static, llm = entry["steps"]
        assert len(static["breakdown"]) == steps.BREAKDOWN_MAX
        assert static["breakdown_omitted"] == 100 - steps.BREAKDOWN_MAX
        assert static["breakdown"][0]["reason"].startswith("r" * steps.BREAKDOWN_REASON_MAX + "…[")
        assert static["reason"] == "x" * steps.REASON_MAX + f"…[{20_000 - steps.REASON_MAX} more chars]"
        assert len(llm["reason"]) < steps.REASON_MAX + 30
        assert len(json.dumps(entry)) < 20_000
        assert len(trail[0]["reason"]) == 20_000  # the in-process steps are not cut

    def test_llm_nested(self):
        meta = {
            "llm_provider": "openrouter",
            "llm_model": "gemini-flash",
            "llm_latency_ms": 500,
            "llm_reasoning": "safe",
            "llm_cascade": [{"provider": "openrouter", "status": "success"}],
        }
        entry = self._build(meta=meta)
        assert "llm" in entry
        assert entry["llm"]["provider"] == "openrouter"
        assert entry["llm"]["model"] == "gemini-flash"
        assert entry["llm"]["ms"] == 500
        assert "reasoning" not in entry["llm"]  # the llm step holds the verdict and reasoning
        assert entry["llm"]["cascade"][0]["status"] == "success"

    def test_llm_absent_without_provider(self):
        entry = self._build(meta={})
        assert "llm" not in entry

    def test_llm_prompt_included(self):
        meta = {"llm_provider": "openrouter", "llm_prompt": "full prompt text"}
        entry = self._build(meta=meta)
        assert entry["llm"]["prompt"] == "full prompt text"

    def test_session_from_transcript(self):
        entry = self._build(transcript_path="/Users/me/.claude/transcript/abc123.jsonl")
        assert entry["session"] == "abc123.jsonl"

    def test_session_empty_without_transcript(self):
        entry = self._build(transcript_path="")
        assert entry["session"] == ""

    def test_hint_included(self):
        meta = {"hint": "nuh trust /tmp"}
        entry = self._build(meta=meta)
        assert entry["hint"] == "nuh trust /tmp"

    def test_hint_absent(self):
        entry = self._build(meta={})
        assert "hint" not in entry

    def test_content_match_included(self):
        meta = {"content_match": "destructive"}
        entry = self._build(meta=meta)
        assert entry["content_match"] == "destructive"

    def test_no_legacy_flat_fields(self):
        """Structured entry should not have flat legacy field names."""
        meta = {"stages": [{"action_type": "git_safe", "decision": "allow"}]}
        entry = self._build(meta=meta)
        assert "input_summary" not in entry
        assert "total_ms" not in entry
        assert "llm_provider" not in entry


class TestBuildEntryRoundTrip:
    """build_entry entries survive write → read cycle."""

    def test_round_trip(self, tmp_path):
        """Entry built by build_entry is written and read back correctly."""
        with patch("nuh.paths.get_project_root", return_value="/tmp/project"):
            entry = log.build_entry(
                tool="Bash", input_summary="ls", decision="allow", reason="",
                agent="claude", hook_version="0.6.0", total_ms=18, meta={},
            )
        log.log_decision(entry)
        entries = _read()
        assert len(entries) == 1
        assert entries[0]["id"] == entry["id"]
        assert entries[0]["input"] == "ls"
        assert entries[0]["ms"] == 18
        assert entries[0]["project"] == "/tmp/project"
