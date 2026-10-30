"""Want 8b: what the file a Write or Edit changes holds now, for the LLM review."""

import json
import os
from pathlib import Path

import pytest

from nuh import file_context, llm, llm_providers
from nuh.file_context import FileContext

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def numbered(count: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, count + 1))


def edit(path: Path, old: str, new: str = "changed") -> dict:
    return {"file_path": str(path), "old_string": old, "new_string": new}


class TestBuild:
    def test_new_file_gets_nothing(self, project_root: str) -> None:
        tool_input = {"file_path": os.path.join(project_root, "new.py"), "content": "x"}
        assert file_context.build("Write", tool_input, {}) is None

    def test_write_over_small_file_sends_it_whole(self, project_root: str) -> None:
        path = Path(project_root, "app.py")
        path.write_text("a = 1\nb = 2\n")
        ctx = file_context.build("Write", {"file_path": str(path), "content": "x"}, {})
        assert ctx == FileContext(str(path), "a = 1\nb = 2", False, "1-2")

    def test_write_over_large_file_sends_its_head_within_budget(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        ctx = file_context.build("Write", {"file_path": str(path), "content": "x"}, {})
        assert ctx is not None and ctx.truncated
        assert len(ctx.excerpt) <= file_context.DEFAULT_CHARS
        assert ctx.excerpt.startswith("line 1\nline 2\n")
        last = int(ctx.lines.split("-")[1])
        assert ctx.excerpt.endswith(f"line {last}")

    def test_edit_of_small_file_sends_it_whole(self, project_root: str) -> None:
        path = Path(project_root, "app.py")
        path.write_text(numbered(50))
        ctx = file_context.build("Edit", edit(path, "line 25\n"), {})
        assert ctx is not None and not ctx.truncated and ctx.lines == "1-50"

    def test_edit_of_large_file_sends_lines_around_the_edit(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        ctx = file_context.build("Edit", edit(path, "line 2500\nline 2501\n"), {})
        assert ctx is not None and ctx.truncated
        assert ctx.lines == "2470-2531"  # 30 lines either side of lines 2500-2501
        assert ctx.excerpt.startswith("line 2470\n") and ctx.excerpt.endswith("line 2531")

    def test_multiedit_gets_one_window_per_edit(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        edits = [{"old_string": "line 100\n", "new_string": "x"}, {"old_string": "line 4000\n", "new_string": "y"},
                 {"old_string": "not in the file", "new_string": "z"}]
        ctx = file_context.build("MultiEdit", {"file_path": str(path), "edits": edits}, {})
        assert ctx is not None and ctx.lines == "70-130,3970-4030"
        assert "line 130\n...\nline 3970" in ctx.excerpt

    def test_nearby_edits_share_a_window(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        edits = [{"old_string": "line 100\n", "new_string": "x"}, {"old_string": "line 120\n", "new_string": "y"}]
        ctx = file_context.build("MultiEdit", {"file_path": str(path), "edits": edits}, {})
        assert ctx is not None and ctx.lines == "70-150"

    def test_windows_shrink_to_fit_the_budget(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        ctx = file_context.build("Edit", edit(path, "line 2500\n"), {"file_context_chars": 200})
        assert ctx is not None and len(ctx.excerpt) <= 200
        first, last = map(int, ctx.lines.split("-"))
        assert first < 2500 < last and 2500 - first == last - 2500

    def test_configured_lines(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        ctx = file_context.build("Edit", edit(path, "line 2500\n"), {"file_context_lines": 2})
        assert ctx is not None and ctx.lines == "2498-2502"

    def test_zero_lines_turns_it_off(self, project_root: str) -> None:
        path = Path(project_root, "app.py")
        path.write_text("a = 1\n")
        assert file_context.build("Edit", edit(path, "a = 1"), {"file_context_lines": 0}) is None

    def test_bad_setting_warns_and_uses_the_default(self, project_root: str, capsys) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        ctx = file_context.build("Edit", edit(path, "line 2500\n"), {"file_context_lines": "lots"})
        assert ctx is not None and ctx.lines == "2470-2530"
        assert "llm.file_context_lines must be an integer" in capsys.readouterr().err

    def test_old_string_missing_from_a_large_file_sends_nothing(self, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        assert file_context.build("Edit", edit(path, "nowhere"), {}) is None

    def test_sensitive_path_is_never_sent(self, project_root: str) -> None:
        path = Path(project_root, ".env")
        path.write_text(f"AWS_KEY={AWS_KEY}\n")
        assert file_context.build("Edit", edit(path, "AWS_KEY"), {}) is None

    def test_symlink_to_a_sensitive_path_is_never_sent(self, project_root: str) -> None:
        Path(project_root, ".env").write_text(f"AWS_KEY={AWS_KEY}\n")
        os.symlink(os.path.join(project_root, ".env"), os.path.join(project_root, "notes.txt"))
        assert file_context.build("Edit", edit(Path(project_root, "notes.txt"), "AWS_KEY"), {}) is None

    def test_binary_file_is_left_out(self, project_root: str) -> None:
        path = Path(project_root, "blob.bin")
        path.write_bytes(b"\x00\x01binary")
        assert file_context.build("Write", {"file_path": str(path), "content": "x"}, {}) is None

    def test_fifo_is_never_opened(self, project_root: str) -> None:
        path = os.path.join(project_root, "pipe")
        os.mkfifo(path)  # opening it for reading would block with no writer
        assert file_context.build("Write", {"file_path": path, "content": "x"}, {}) is None

    def test_other_tools_get_nothing(self, project_root: str) -> None:
        path = Path(project_root, "nb.ipynb")
        path.write_text("{}\n")
        assert file_context.build("NotebookEdit", {"notebook_path": str(path)}, {}) is None
        assert file_context.build("Bash", {"command": "ls"}, {}) is None


# -- The provider prompt --


class FakeHTTP:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, req, timeout=None):
        from unittest.mock import MagicMock
        self.prompts.append(json.loads(req.data)["messages"][1]["content"])
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps(
            {"message": {"content": json.dumps({"decision": "allow", "reasoning": "stub"})}}).encode()
        return resp


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHTTP:
    fake = FakeHTTP()
    monkeypatch.setattr(llm_providers.urllib.request, "urlopen", fake)
    return fake


def ollama(**settings: object) -> dict:
    return {"providers": ["ollama"], "ollama": {"url": "http://localhost:11434/api/chat", "model": "t"},
            **settings}


class TestPrompt:
    def test_prompt_shows_the_file(self, http, project_root: str) -> None:
        path = Path(project_root, "app.py")
        path.write_text("import os\nb = 2\n")
        llm.try_llm_write("Edit", edit(path, "b = 2"), {"decision": "allow"}, ollama())
        assert "Current content of the file being changed, the whole file" in http.prompts[0]
        assert "---\nimport os\nb = 2\n---" in http.prompts[0]

    def test_prompt_names_the_excerpt_lines(self, http, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        llm.try_llm_write("Edit", edit(path, "line 2500\n"), {"decision": "allow"}, ollama())
        assert "being changed, lines 2470-2530 of it" in http.prompts[0]

    def test_tight_prompt_shrinks_the_file_not_the_decision(self, http, project_root: str) -> None:
        path = Path(project_root, "big.py")
        path.write_text(numbered(5000))
        result = llm.try_llm_write("Edit", edit(path, "line 2500\n"), {"decision": "allow"},
                                   ollama(max_prompt_chars=3200, context_chars=0))
        assert result.decision["decision"] == "allow"
        prompt = http.prompts[0]
        assert "line 2500" in prompt.split("Current content")[1]
        assert len(prompt) < 3200

    def test_redacting_provider_gets_the_file_redacted(self, http, project_root: str) -> None:
        path = Path(project_root, "settings.py")
        path.write_text(f"KEY = '{AWS_KEY}'\nDEBUG = True\n")
        cfg = ollama()
        cfg["ollama"]["redact_secrets"] = True
        result = llm.try_llm_write("Edit", edit(path, "DEBUG = True"), {"decision": "allow"}, cfg)
        assert AWS_KEY not in http.prompts[0] and "[redacted: AWS access key ID]" in http.prompts[0]
        assert result.decision["decision"] == "allow"  # the verdict on a redacted prompt stands
