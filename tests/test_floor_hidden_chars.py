"""Static floor rule group 5: hidden characters in commands, scripts and written content."""

import pytest

from nuh import config, hidden_chars, hook, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig


class TestFind:
    @pytest.mark.parametrize("text", [
        "ls​ -la",
        "echo safe\rrm -rf build",
        "echo ‮evil",
        "echo ⁦x⁩",
        "a\x1b[2Kb",
        "a\x00b",
        "a\x7fb",
        "a\x85b",
        "soft­hyphen",
        "bom﻿",
        "fillerㅤ",
        "tag\U000E0041",
        "form\x0cfeed",
    ])
    def test_hidden(self, text):
        assert hidden_chars.find(text) >= 0

    @pytest.mark.parametrize("text", [
        "plain ascii",
        "tab\tand\nnewline",
        "windows line\r\nending",
        "final cr\r",
        "zwj family \U0001F468‍\U0001F469",
        "accents: café ñ ü",
        "rlm mark ‏",
        "england \U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    ])
    def test_ordinary(self, text):
        assert hidden_chars.find(text) == -1

    def test_reason_shows_escaped_form(self):
        assert hidden_chars.reason("echo safe\rrm -rf build", "command") == \
            "command holds hidden characters: echo safe\\x0drm -rf build"

    def test_excerpt_is_bounded(self):
        text = "x" * 200 + "​" + "y" * 200
        excerpt = hidden_chars.escaped_excerpt(text, hidden_chars.find(text))
        assert "\\u200b" in excerpt
        assert len(excerpt) < 80


class TestBash:
    @pytest.mark.parametrize("command", [
        "ls​ -la",
        "echo safe\rrm -rf build",
        "git log ‮",
        "python3 -c 'print(1)​'",
        "cat <<'EOF'\nhi‮\nEOF",
    ])
    def test_floored_ask(self, command):
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules()[0] == "hidden_chars"
        assert "\\u" in result.reason or "\\x" in result.reason

    def test_a_block_stays_a_block(self):
        assert classify_command("rm -rf / ​").final_decision == taxonomy.BLOCK

    def test_never_reaches_llm(self, monkeypatch):
        config._cached_config = NuhConfig(llm={"enabled": True})
        monkeypatch.setattr(hook, "_try_llm", lambda r: pytest.fail("LLM called on hidden characters"))
        assert hook.handle_bash({"command": "ls​"})["_floor"] == "hidden_chars"


class TestScriptsAndWrites:
    def test_scanned_script(self, project_root, monkeypatch):
        monkeypatch.chdir(project_root)
        with open("run.sh", "w") as f:
            f.write("echo ok‮\n")
        result = classify_command("bash run.sh")
        assert result.final_decision == taxonomy.ASK
        assert result.floor_rules() == ["hidden_chars"]

    def test_write_content(self, project_root):
        decision = hook.handle_write({"file_path": f"{project_root}/a.txt", "content": "x​y"})
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "hidden_chars"

    def test_edit_content(self, project_root):
        decision = hook.handle_edit({"file_path": f"{project_root}/a.txt", "new_string": "x\ry"})
        assert decision["_floor"] == "hidden_chars"

    def test_crlf_write_is_fine(self, project_root):
        decision = hook.handle_write({"file_path": f"{project_root}/a.txt", "content": "a\r\nb\r\n"})
        assert decision["decision"] == taxonomy.ALLOW
