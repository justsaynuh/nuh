"""The notice's shape: what a Claude user sees for an ask or a block (notice.py).

Safety against hostile text is tested in test_notice_markdown.py; these pin
the one fixed layout through the real hook:

    {icon} **{verdict}** {tool} · {target} · {reason} · {call}

with the reason that set the final decision (🤖 when it is the LLM's) and
"(🤖 wanted X)" when the LLM's own verdict differs from the final one.
"""

import io
import os
import sys

from nuh import agents, cli, config, notice
from nuh.config import _merge_configs
from tests.llm_stub import llm_config
from tests.md_check import read
from tests.test_notice_markdown import run

CALL = "toolu_01AbCdEfGhIja7Kq2x"


def bash(command: str, call: str = CALL) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, **({"tool_use_id": call} if call else {})}


def use_stub(stub_model, **llm_extra: object) -> None:
    config._cached_config = _merge_configs({"llm": {**llm_config(stub_model.url), **llm_extra}}, {})


# -- The approved look --


def test_static_ask(monkeypatch, project_root):
    message = run(monkeypatch, bash("curl -sI https://evil.example/"))["systemMessage"]
    assert message == ("⚠️ **nuh?** Bash · `curl -sI https://evil.example/` · unknown host: `evil.example` · "
                       "`#a7Kq2x`")


def test_llm_reason_that_set_the_ask(stub_model, project_root, hook_log, monkeypatch):
    use_stub(stub_model)
    stub_model.answer("ask", "posts a dotfile to an outside host (ask 0.62)")
    message = run(monkeypatch, bash("frobnicate --sync"))["systemMessage"]
    assert message == ("⚠️ **nuh?** Bash · `frobnicate --sync` · "
                       "🤖 posts a dotfile to an outside host (ask 0.62) · `#a7Kq2x`")


def test_llm_wanted_block_on_a_static_ask(stub_model, project_root, hook_log, monkeypatch):
    use_stub(stub_model)
    stub_model.answer("block", "exfiltrates keys")
    message = run(monkeypatch, bash("curl -sI https://evil.example/"))["systemMessage"]
    assert message == ("⚠️ **nuh?** Bash · `curl -sI https://evil.example/` · unknown host: `evil.example` "
                       "(🤖 wanted block) · `#a7Kq2x`")


def test_llm_wanted_allow_on_an_ask_that_held():
    # An old log line's "floor" hold step reads as the new human_only / tool steps do.
    for held in ({"step": "floor", "from": "allow", "to": "ask", "rule": "llm.authority.Bash: raise"},
                 {"step": "tool", "from": "allow", "to": "ask", "rule": "tools.Bash: check"}):
        decision = {"decision": "ask", "reason": "unknown host: evil.example",
                    "_steps": [{"step": "static", "decision": "ask", "reason": "unknown host: evil.example"},
                               {"step": "llm", "decision": "allow", "reason": "a read of a public page"}, held]}
        message = notice.for_decision(notice.ASK, decision, "Bash", {"command": "curl -sI https://evil.example/"},
                                      CALL)
        assert message.endswith("· unknown host: `evil.example` (🤖 wanted allow) · `#a7Kq2x`")


def test_llm_raised_a_write_and_wanted_more(stub_model, project_root, hook_log, monkeypatch):
    use_stub(stub_model)
    stub_model.answer("block", "writes outside the project via tee /etc/x")
    path = f"{project_root}/run.sh"
    out = run(monkeypatch, {"tool_name": "Write", "tool_input": {"file_path": path, "content": "echo\n"},
                            "tool_use_id": "toolu_Vc81sZ"})
    message = out["systemMessage"]
    assert message.startswith("⚠️ **nuh?** Write · `")
    assert read(message).spans[0].endswith("/project/run.sh")
    assert message.endswith(" · 🤖 writes outside the project via tee `/etc/x` (wanted block) · `#Vc81sZ`")


def test_block_head(monkeypatch, secrets_home):
    message = run(monkeypatch, bash("cat ~/.ssh/id_ed25519", "toolu_Lp0xT3"))["systemMessage"]
    assert message == "⛔ **nuh!** Bash · `cat ~/.ssh/id_ed25519` · targets sensitive path: `~/.ssh` · `#Lp0xT3`"


# -- What the notice leaves out (it stays in the log) --


def test_no_hint_floor_tag_cap_arrow_or_confidence(stub_model, project_root, hook_log, monkeypatch):
    message = run(monkeypatch, bash("echo ok​ x", ""))["systemMessage"]
    assert message == "⚠️ **nuh?** Bash · hidden characters · `echo ok\\u200b x`"  # no floor tag, no call
    use_stub(stub_model)
    stub_model.answer("block", "likely harmful")
    message = run(monkeypatch, bash("frobnicate --wipe"))["systemMessage"]
    assert message.endswith("· unknown → ask (🤖 wanted block) · `#a7Kq2x`")  # the static reason; no cap arrow
    assert "block → ask" not in message and "nuh classify" not in message and "likely harmful" not in message


def test_content_varies_note_is_gone(monkeypatch, project_root):
    path = f"{project_root}/k.py"
    out = run(monkeypatch, {"tool_name": "Write", "tool_input": {"file_path": path,
                                                                 "content": "KEY = 'AKIA1234567890ABCDEF'\n"}})
    assert "cannot be remembered" not in out["systemMessage"]
    assert out["systemMessage"].endswith("· content inspection \\[secret\\]: AWS access key ID")


def test_target_the_reason_already_shows_is_not_repeated(monkeypatch, project_root, tmp_path):
    path = str(tmp_path / "elsewhere" / "notes.txt")
    message = run(monkeypatch, {"tool_name": "Write", "tool_input": {"file_path": path, "content": "hi\n"}})
    assert len(read(message["systemMessage"]).spans) == 1  # the reason's path


def test_tool_named_by_the_reason_is_not_repeated(monkeypatch, project_root):
    message = run(monkeypatch, {"tool_name": "mcp__files__write_file", "tool_input": {}})["systemMessage"]
    assert message == "⚠️ **nuh?** unrecognized tool: `mcp__files__write_file`"


def test_sensitive_path_shows_home_as_tilde(monkeypatch, project_root):
    path = os.path.expanduser("~/.aws/credentials")
    message = run(monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": path}})["systemMessage"]
    assert message.startswith("⚠️ **nuh?** Read · `~/.aws/credentials` · targets sensitive path: `~/.aws`")


# -- The call ID finds the log entry --


def test_the_call_id_finds_the_entry_in_nuh_log(monkeypatch, project_root, hook_log):
    run(monkeypatch, bash("ls", "toolu_01Other0a7Kq2"))  # another call whose ID holds the same characters
    message = run(monkeypatch, bash("curl -sI https://evil.example/"))["systemMessage"]
    shown = read(message).spans[-1]
    assert shown == "#a7Kq2x"
    for query in (shown, shown[1:]):
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr(sys, "argv", ["nuh", "log", "--call", query, "-v"])
        cli.main()
        assert f"call      {CALL}" in out.getvalue()
        assert "1. static       ask" in out.getvalue()
        assert "toolu_01Other0a7Kq2" not in out.getvalue()


# -- Formatter edges --


def test_long_command_keeps_its_start_and_long_path_its_end():
    command = "frobnicate " + "x" * 200
    shown = notice.render(notice.ASK, "unknown → ask", tool="Bash", target=command)
    assert read(shown).spans[0] == command[:79] + "…"
    path = "/srv/" + "d/" * 100 + "file.txt"
    shown = notice.render(notice.ASK, "outside project", tool="Write", target=path)
    assert read(shown).spans[0] == "…" + path[-79:]


def test_empty_parts_drop_with_their_separator():
    assert notice.render(notice.BLOCK, "", tool="Bash") == "⛔ **nuh!** Bash"
    assert notice.render(notice.ASK, "x", call_id="toolu_01xyzabc") == "⚠️ **nuh?** x · `#xyzabc`"
    assert notice.render(notice.ASK, "", tool="Bash", by_llm=True, wanted="block") == \
        "⚠️ **nuh?** Bash · 🤖 (wanted block)"


def test_internal_error():
    shown = agents.format_error("boom", agents.CLAUDE)["systemMessage"]
    assert shown == "💥 **nuh: internal error** · blocked for safety · boom · To bypass: `nuh uninstall` · " \
                    "To debug: `nuh log`"
