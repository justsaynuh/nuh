"""Codex subagent context: the user's messages come from the root session.

Record shapes follow real Codex 0.162 rollouts under ~/.codex/sessions: a
``session_meta`` first line (a subagent's names ``parent_thread_id`` and
``subagent_history_start_ordinal``), forked parent history below that ordinal
(``inherited_user_message``), the brief as an ``agent_message`` with an
encrypted body, and the subagent's own calls after it.
"""

import datetime
import json

import pytest

from nuh import codex_lineage
from nuh.codex_lineage import LineageError, find_root, newest_timestamp
from nuh.transcript import Budget, TranscriptRef, build_context
from tests.test_transcript_context import AGENT_HDR, BRIEF_HDR, FIRST_HDR, TOOL_HDR, USER_HDR, section

ROOT_TAIL = "7d91-aac7-df69067bbf8a"
CHILD_TAIL = "7d82-96cf-53b2df952cdd"
GRANDCHILD_TAIL = "73b3-a340-e213a1bf7939"
BRIEF = "Message Type: NEW_TASK\nTask name: /root/plan\nSender: /root\nPayload:\n"


def thread_id(born: datetime.datetime, tail: str) -> str:
    """A UUIDv7-style id whose first 48 bits are *born* (local time) in ms, as Codex mints them."""
    h = f"{int(born.timestamp() * 1000):012x}"
    return f"{h[:8]}-{h[8:]}-{tail}"


def utc(born: datetime.datetime, seconds: float = 0) -> str:
    t = (born + datetime.timedelta(seconds=seconds)).astimezone(datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def rollout_path(sessions, born: datetime.datetime, tid: str):
    return sessions / born.strftime("%Y/%m/%d") / f"rollout-{born:%Y-%m-%dT%H-%M-%S}-{tid}.jsonl"


def meta(tid: str, born: datetime.datetime, parent: str = "", root: str = "", start: int = 9,
         agent_path: str = "/root/plan", depth: int = 1) -> dict:
    payload = {"session_id": root or tid, "id": tid}
    if parent:
        payload.update(forked_from_id=parent, parent_thread_id=parent)
    payload.update(timestamp=utc(born), cwd="/home/u/proj", originator="codex-tui", cli_version="0.162.1")
    if parent:
        payload.update(
            source={"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": depth,
                                                  "agent_path": agent_path, "agent_nickname": "Socrates",
                                                  "agent_role": "default"}}},
            thread_source="subagent", agent_nickname="Socrates", agent_role="default", agent_path=agent_path)
    else:
        payload.update(source="vscode", thread_source="user")
    payload.update(model_provider="openai", base_instructions={"text": "You are Codex, an agent based on GPT-6."})
    if parent:
        payload.update(history_mode="paginated", subagent_history_start_ordinal=start, multi_agent_version="v2")
    return {"timestamp": utc(born), "ordinal": 0, "type": "session_meta", "payload": payload}


def rec(ordinal: int, ts: str, rtype: str, payload: dict, **metadata) -> dict:
    r = {"timestamp": ts, "ordinal": ordinal, "type": rtype, "payload": payload}
    if metadata:
        r["metadata"] = {"client_authored": False, **metadata}
    return r


def user_msg(ordinal: int, ts: str, text: str, **metadata) -> dict:
    return rec(ordinal, ts, "response_item", {"type": "message", "id": f"msg_{ordinal}", "role": "user",
                                              "content": [{"type": "input_text", "text": text}]}, **metadata)


def assistant_msg(ordinal: int, ts: str, text: str) -> dict:
    return rec(ordinal, ts, "response_item", {"type": "message", "id": f"msg_a{ordinal}", "role": "assistant",
                                              "content": [{"type": "output_text", "text": text}]})


def exec_call(ordinal: int, ts: str, cmd: str, call_id: str) -> dict:
    return rec(ordinal, ts, "response_item", {
        "type": "custom_tool_call", "id": f"ctc_{ordinal}", "status": "completed", "call_id": call_id,
        "name": "exec", "input": f'const r = await tools.exec_command({{cmd:"{cmd}"}});text(r.output);\n'})


def brief(ordinal: int, ts: str, recipient: str = "/root/plan") -> list[dict]:
    return [
        rec(ordinal, ts, "inter_agent_communication_metadata", {"trigger_turn": True}),
        rec(ordinal + 1, ts, "response_item", {
            "type": "agent_message", "id": f"amsg_{ordinal}", "author": "/root", "recipient": recipient,
            "content": [{"type": "input_text", "text": BRIEF},
                        {"type": "encrypted_content", "encrypted_content": "gAAAAABqzL8ikDVb9F5Lupuod"}]},
            user_input_order=0),
    ]


def line(record: dict) -> str:
    """One rollout line, compact like Codex writes them."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"


def write(path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(map(line, records)))
    return str(path)


def write_root(sessions, born, tid=None, late_message=False):
    """The session the user types into, with two user turns, its own work, and a spawn."""
    tid = tid or thread_id(born, ROOT_TAIL)
    records = [
        meta(tid, born),
        user_msg(5, utc(born, 1), "# AGENTS.md instructions\n\n<INSTRUCTIONS>rules</INSTRUCTIONS>"),
        user_msg(8, utc(born, 1), "Profile the parser"),
        assistant_msg(20, utc(born, 30), "Root agent prose that the subagent did not write."),
        exec_call(21, utc(born, 31), "root-only-command", "call_root"),
        user_msg(30, utc(born, 60), "use the test server"),
        rec(40, utc(born, 90), "response_item", {"type": "function_call", "name": "spawn_agent",
                                                 "arguments": json.dumps({"task_name": "plan",
                                                                          "message": "gAAAAABqzL8"}),
                                                 "call_id": "call_spawn"}),
        rec(50, utc(born, 200), "response_item", {   # a subagent reporting back
            "type": "agent_message", "id": "amsg_50", "author": "/root/plan", "recipient": "/root",
            "content": [{"type": "input_text", "text": "Message Type: MESSAGE\nTask name: /root\n"
                                                       "Sender: /root/plan\nPayload:\n"},
                        {"type": "encrypted_content", "encrypted_content": "gAAAAABqzWHeeF4L"}]},
            user_input_order=0),
    ]
    if late_message:
        records.append(user_msg(90, utc(born, 3600), "a message typed after the subagent's call"))
    return write(rollout_path(sessions, born, tid), *records), tid


def write_child(sessions, born, parent, root, tail=CHILD_TAIL, depth=1, agent_path="/root/plan"):
    """A spawned subagent: forked history (ordinals < 9), its brief, then its own work."""
    tid = thread_id(born, tail)
    records = [
        meta(tid, born, parent=parent, root=root, start=9, agent_path=agent_path, depth=depth),
        user_msg(4, utc(born), "# AGENTS.md instructions\n\n<INSTRUCTIONS>rules</INSTRUCTIONS>",
                 inherited_user_message=True),
        user_msg(7, utc(born), "Profile the parser", inherited_user_message=True, user_input_order=0),
        assistant_msg(8, utc(born), "Inherited parent prose."),
        *brief(14, utc(born, 1), agent_path),
        assistant_msg(20, utc(born, 5), "Reading the plan first."),
        exec_call(21, utc(born, 6), "cat plan.md", "call_c1"),
        exec_call(25, utc(born, 9), "uptime", "call_c2"),
    ]
    return write(rollout_path(sessions, born, tid), *records), tid


ROOT_BORN = datetime.datetime(2026, 10, 12, 7, 4, 11)
CHILD_BORN = datetime.datetime(2026, 10, 12, 7, 6, 10)


def build(path, tid, budget=None):
    return build_context(TranscriptRef(path, agent_id=tid), budget or Budget())


class TestRootSession:
    def test_user_turns_come_from_the_root(self, tmp_path, capsys):
        sessions = tmp_path / "sessions"
        root_path, root_id = write_root(sessions, ROOT_BORN)
        child_path, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        text, stats = build(child_path, child_id)
        users = section(text, USER_HDR)
        assert "- Profile the parser" in users and "- use the test server" in users
        assert "AGENTS.md" not in text
        assert stats["source"] == "codex-subagent"
        assert (stats["parent"], stats["parent_hops"]) == (root_path, 1)
        assert capsys.readouterr().err == ""

    def test_brief_is_agent_written_and_inherited_history_is_dropped(self, tmp_path):
        sessions = tmp_path / "sessions"
        _, root_id = write_root(sessions, ROOT_BORN)
        child_path, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        text, _ = build(child_path, child_id)
        briefs = section(text, BRIEF_HDR)
        assert "Message Type: NEW_TASK" in briefs and "Payload: (encrypted, not readable)" in briefs
        assert "Profile the parser" not in briefs  # the user's words, forked from the parent
        assert "gAAAA" not in text
        assert "Inherited parent prose." not in text

    def test_agent_work_comes_from_the_subagent(self, tmp_path):
        sessions = tmp_path / "sessions"
        _, root_id = write_root(sessions, ROOT_BORN)
        child_path, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        text, _ = build(child_path, child_id)
        assert "Reading the plan first." in section(text, AGENT_HDR)
        tools = section(text, TOOL_HDR)
        assert "cat plan.md" in tools and "uptime" in tools
        assert "root-only-command" not in text
        assert "Root agent prose" not in text

    def test_root_messages_after_the_call_are_cut_off(self, tmp_path):
        sessions = tmp_path / "sessions"
        _, root_id = write_root(sessions, ROOT_BORN, late_message=True)
        child_path, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        text, _ = build(child_path, child_id)
        assert "- use the test server" in section(text, USER_HDR)
        assert "typed after the subagent's call" not in text

    def test_grandchild_follows_two_links(self, tmp_path):
        sessions = tmp_path / "sessions"
        root_path, root_id = write_root(sessions, ROOT_BORN)
        _, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        grand_path, grand_id = write_child(sessions, CHILD_BORN + datetime.timedelta(minutes=3), child_id,
                                           root_id, tail=GRANDCHILD_TAIL, depth=2, agent_path="/root/plan/sub")
        text, stats = build(grand_path, grand_id)
        assert "- use the test server" in section(text, USER_HDR)
        assert (stats["parent"], stats["parent_hops"]) == (root_path, 2)

    def test_parent_in_previous_day_directory(self, tmp_path):
        sessions = tmp_path / "sessions"
        root_born = datetime.datetime(2026, 10, 11, 23, 58, 2)
        child_born = datetime.datetime(2026, 10, 12, 0, 3, 40)
        root_path, root_id = write_root(sessions, root_born)
        child_path, child_id = write_child(sessions, child_born, root_id, root_id)
        assert "/2026/10/11/" in root_path and "/2026/10/12/" in child_path
        text, stats = build(child_path, child_id)
        assert "- use the test server" in section(text, USER_HDR)
        assert stats["parent"] == root_path

    def test_parent_without_a_time_in_its_id_is_found_by_widening(self, tmp_path):
        """No UUIDv7 date hint: the search walks back from the child's date."""
        sessions = tmp_path / "sessions"
        root_born = datetime.datetime(2026, 10, 5, 9, 0, 0)
        root_id = "3f2a9c41-0d6e-4b1a-9f00-2c7d5e8a1b42"
        root_path, _ = write_root(sessions, root_born, tid=root_id)
        child_path, child_id = write_child(sessions, CHILD_BORN, root_id, root_id)
        assert find_root(child_path).root_path == root_path


class TestFallback:
    def test_missing_parent_keeps_todays_context_and_reports(self, tmp_path, capsys):
        sessions = tmp_path / "sessions"
        gone = thread_id(ROOT_BORN, ROOT_TAIL)
        child_path, child_id = write_child(sessions, CHILD_BORN, gone, gone)
        text, stats = build(child_path, child_id)
        assert USER_HDR not in text and FIRST_HDR not in text
        assert "Profile the parser" in section(text, BRIEF_HDR)  # labeled agent-written, as before
        assert "cat plan.md" in section(text, TOOL_HDR)
        assert "parent" not in stats
        assert f"nuh: transcript: codex parent lookup: parent thread {gone} not found" in capsys.readouterr().err

    def test_top_level_session_is_silent(self, tmp_path, capsys):
        root_path, _ = write_root(tmp_path / "sessions", ROOT_BORN)
        text, stats = build_context(TranscriptRef(root_path), Budget())
        assert "- use the test server" in section(text, USER_HDR)
        assert "Sender: /root/plan" not in text  # a subagent's report is neither the user nor a brief
        assert stats["source"] == "codex" and "parent" not in stats
        assert capsys.readouterr().err == ""

    def test_rollout_without_parent_link_is_silent(self, tmp_path, capsys):
        root_path, root_id = write_root(tmp_path / "sessions", ROOT_BORN)
        assert find_root(root_path) is None
        build(root_path, root_id)
        assert capsys.readouterr().err == ""

    def test_parent_cycle_hits_the_hop_cap(self, tmp_path, capsys):
        sessions = tmp_path / "sessions"
        a_born, b_born = ROOT_BORN, CHILD_BORN
        a_id, b_id = thread_id(a_born, ROOT_TAIL), thread_id(b_born, CHILD_TAIL)
        write(rollout_path(sessions, a_born, a_id), meta(a_id, a_born, parent=b_id))
        child_path, child_id = write_child(sessions, b_born, a_id, a_id)
        text, _ = build(child_path, child_id)
        assert USER_HDR not in text
        assert "thread chain longer than" in capsys.readouterr().err

    def test_file_holding_another_thread_is_rejected(self, tmp_path):
        sessions = tmp_path / "sessions"
        root_id = thread_id(ROOT_BORN, ROOT_TAIL)
        write(rollout_path(sessions, ROOT_BORN, root_id), meta(thread_id(ROOT_BORN, GRANDCHILD_TAIL), ROOT_BORN))
        child_path, _ = write_child(sessions, CHILD_BORN, root_id, root_id)
        with pytest.raises(LineageError, match="holds thread"):
            find_root(child_path)

    def test_child_whose_first_line_is_not_session_meta_reports(self, tmp_path, capsys):
        path = write(tmp_path / "sessions/2026/10/12/rollout-2026-10-12T07-06-10-x.jsonl",
                     user_msg(1, utc(CHILD_BORN), "hello"))
        build(path, "x")
        assert "is not session_meta" in capsys.readouterr().err


class TestNewestTimestamp:
    def test_small_file(self, tmp_path):
        path = write(tmp_path / "r.jsonl", user_msg(1, "2026-10-12T11:00:00.000Z", "a"),
                     user_msg(2, "2026-10-12T11:00:05.000Z", "b"))
        assert newest_timestamp(path) == "2026-10-12T11:00:05.000Z"

    def test_spaced_json_spelling(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text(json.dumps(user_msg(1, "2026-10-12T11:00:03.000Z", "a")) + "\n")
        assert newest_timestamp(str(path)) == "2026-10-12T11:00:03.000Z"

    def test_timestamp_inside_message_text_is_not_a_record(self, tmp_path):
        fake = '\n{"timestamp":"2099-01-01T00:00:00.000Z"}'
        path = write(tmp_path / "r.jsonl", user_msg(1, "2026-10-12T11:00:00.000Z", fake))
        assert newest_timestamp(path) == "2026-10-12T11:00:00.000Z"

    def test_last_record_longer_than_a_chunk(self, tmp_path):
        path = write(tmp_path / "r.jsonl", user_msg(1, "2026-10-12T11:00:00.000Z", "a"),
                     user_msg(2, "2026-10-12T11:00:09.000Z", "x" * 200_000))
        assert newest_timestamp(path) == "2026-10-12T11:00:09.000Z"

    @pytest.mark.parametrize("shift", [-20, -8, -1, 0, 1, 8, 20])
    def test_line_start_straddling_the_chunk_boundary(self, tmp_path, shift):
        last_len = len(line(user_msg(2, "2026-10-12T11:00:07.000Z", "")))
        # The last line starts `shift` bytes inside (or, negative, before) the
        # final 64 KB chunk, so its opening newline may sit in the chunk before.
        pad = codex_lineage._CHUNK - shift - last_len
        path = write(tmp_path / "r.jsonl", user_msg(1, "2026-10-12T11:00:00.000Z", "a"),
                     user_msg(2, "2026-10-12T11:00:07.000Z", "y" * pad))
        assert newest_timestamp(path) == "2026-10-12T11:00:07.000Z"

    def test_file_with_no_record_stamps(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text('{"type":"session_meta"}\n')
        assert newest_timestamp(str(path)) == ""
