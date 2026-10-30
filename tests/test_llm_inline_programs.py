"""The LLM reviewer sees the whole program a lang_exec stage runs, and the whole action.

Inline programs (heredoc, here-string, ``-c``, write-then-run) are carried on
``StageResult.inline_program`` from the classifier to the prompt; nothing in
the action is cut, and an action too large to send gets ask without an LLM call.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from nuh import bash_review, config, hook, llm_meta, llm_providers, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig
from nuh.exec_source import FROM_ARGUMENT, FROM_STDIN, FROM_WRITTEN_SCRIPT
from nuh.llm import try_llm
from nuh.llm_prompt import _build_prompt

PAYLOAD = "subprocess.run(['rm', '-rf', os.path.expanduser('~')])"


class FakeHTTP:
    """Stands in for urlopen: records request bodies, answers with one Ollama decision."""

    def __init__(self, decision: str = "allow", **extra: object) -> None:
        self.decision = decision
        self.extra = extra
        self.bodies: list[dict] = []

    def __call__(self, req, timeout=None):
        self.bodies.append(json.loads(req.data))
        resp = MagicMock()
        resp.status = 200
        reply = {"message": {"content": json.dumps({"decision": self.decision, "reasoning": "stub"})},
                 **self.extra}
        resp.read.return_value = json.dumps(reply).encode()
        return resp


def _llm_config(**overrides: object) -> dict:
    return {"enabled": True, "providers": ["ollama"],
            "ollama": {"url": "http://localhost:11434/api/chat", "model": "test"}, **overrides}


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHTTP:
    fake = FakeHTTP()
    monkeypatch.setattr(llm_providers.urllib.request, "urlopen", fake)
    return fake


def _use_llm(can_block: bool = True, **overrides: object) -> None:
    config._cached_config = NuhConfig(llm=_llm_config(**overrides), llm_can_block=can_block)


def _script(lines: int = 30) -> str:
    body = ["import json", "from pathlib import Path", ""]
    body += [f"value_{i} = {i} * 2  # step {i}" for i in range(lines - 4)]
    body.append("print(json.dumps({'done': True}))")
    return "\n".join(body)


def _heredoc(body: str, prefix: str = "cd x && ") -> str:
    return f"{prefix}python3 - <<'EOF'\n{body}\nEOF"


# -- The program text reaches the prompt --


class TestProgramInPrompt:
    @pytest.mark.parametrize("cmd, code, source", [
        (_heredoc("import os\nprint(os.getcwd())", prefix=""), "import os\nprint(os.getcwd())", FROM_STDIN),
        ("python3 -c 'import os; print(os.getcwd())'", "import os; print(os.getcwd())", FROM_ARGUMENT),
        ("python3 <<< \"print('hi')\"", "print('hi')", FROM_STDIN),
    ])
    def test_inline_program_shown_whole(self, project_root, cmd, code, source):
        prompt = _build_prompt(classify_command(cmd)).user
        assert f"Program 1 about to execute (python3, from {source}" in prompt
        assert f"```\n{code}\n```" in prompt
        assert "Content inspection: no flags" in prompt

    def test_write_then_run_body_shown(self, project_root, monkeypatch):
        monkeypatch.chdir(project_root)
        cmd = "cat > run.py <<'EOF'\nprint('written')\nEOF\npython3 run.py"
        prompt = _build_prompt(classify_command(cmd)).user
        assert f"from {FROM_WRITTEN_SCRIPT}" in prompt
        assert "```\nprint('written')\n```" in prompt

    def test_driving_stage_is_the_program_not_cd(self, project_root):
        result = classify_command(_heredoc(_script()))
        assert result.final_decision == taxonomy.ASK
        assert result.stages[0].tokens[0] == "cd"
        prompt = _build_prompt(result).user
        assert "Action type: lang_exec" in prompt
        assert "Structural reason: lang_exec: inline code, content scan clean (needs an LLM verdict)" in prompt

    def test_long_program_whole_and_not_duplicated(self, project_root):
        body = _script(30) + "\n" + "# padding line\n" * 600 + PAYLOAD
        prompt = _build_prompt(classify_command(_heredoc(body))).user
        assert prompt.count(PAYLOAD) == 1
        assert f"```\n{body}\n```" in prompt
        assert f"[program 1: {len(body)} characters, shown below]" in prompt
        assert "cd x && python3 - <<'EOF'" in prompt  # the shell around it stays visible

    def test_several_programs_each_shown(self, project_root):
        cmd = "python3 -c 'print(1)' && node -e 'console.log(2)'"
        prompt = _build_prompt(classify_command(cmd)).user
        assert "Program 1 of 2 about to execute (python3" in prompt
        assert "Program 2 of 2 about to execute (node" in prompt

    def test_review_question_names_what_to_look_for(self, project_root):
        prompt = _build_prompt(classify_command("python3 -c 'print(1)'")).user
        for phrase in ("deletes, moves or overwrites files", "credentials", "dotfiles",
                       "sends data over the network", "spawns shells or other commands"):
            assert phrase in prompt

    def test_script_file_payload_after_8k_reaches_prompt(self, project_root, monkeypatch):
        monkeypatch.chdir(project_root)
        script = "x = 1\n" * 2000 + "import shutil\nshutil.rmtree('/srv/data')\n"
        assert len(script) > 8192
        with open(os.path.join(project_root, "big.py"), "w") as f:
            f.write(script)
        prompt = _build_prompt(classify_command("python3 big.py")).user
        assert f"Script about to execute:\n```\n{script}\n```" in prompt


# -- The veto gate (FD-079) on a clean-scan heredoc --


class TestHeredocVeto:
    CMD = _heredoc("import os\nfrom pathlib import Path\nPath(os.path.expanduser('~/notes.txt')).write_text('')")

    def test_clean_scan_asks_for_an_llm_verdict(self, project_root):
        result = classify_command(self.CMD)
        assert result.final_decision == taxonomy.ASK
        assert bash_review.route(result, "decide").kind == bash_review.ASK

    def test_llm_block_blocks(self, project_root, http):
        http.decision = "block"
        _use_llm(can_block=True)
        out = hook.handle_bash({"command": self.CMD})
        assert out["decision"] == taxonomy.BLOCK
        assert "Path(os.path.expanduser('~/notes.txt')).write_text('')" in http.bodies[0]["messages"][1]["content"]

    @pytest.mark.parametrize("decision,expected", [("allow", taxonomy.ALLOW), ("uncertain", taxonomy.ASK)])
    def test_llm_verdict_decides(self, project_root, http, decision, expected):
        http.decision = decision
        _use_llm(can_block=True)
        assert hook.handle_bash({"command": self.CMD})["decision"] == expected
        assert len(http.bodies) == 1


# -- Too large to send whole: ask, no LLM call --


class TestTooLarge:
    def test_ask_path(self, project_root, http):
        _use_llm(max_action_chars=1000)
        out = hook.handle_bash({"command": "somethingunknown123 " + "a" * 2000})
        assert out["decision"] == taxonomy.ASK
        assert "too large for LLM review (" in out["reason"] and "chars > 1000)" in out["reason"]
        assert out["_meta"]["llm_skipped"] == "too_large"
        assert http.bodies == []

    def test_inline_program_too_large_asks(self, project_root, http):
        _use_llm(max_action_chars=1000)
        cmd = _heredoc("x = 1\n" * 400)
        assert classify_command(cmd).final_decision == taxonomy.ASK
        out = hook.handle_bash({"command": cmd})
        assert out["decision"] == taxonomy.ASK
        assert "too large for LLM review" in out["reason"]
        assert http.bodies == []

    def test_too_large_asks_even_when_the_llm_can_block(self, project_root, http):
        _use_llm(can_block=True, max_action_chars=1000)
        out = hook.handle_bash({"command": "somethingunknown123 " + "a" * 2000})
        assert out["decision"] == taxonomy.ASK

    def test_write_veto_asks(self, project_root, http):
        _use_llm(max_action_chars=1000)
        out = hook.handle_write({"file_path": os.path.join(project_root, "a.txt"), "content": "z" * 1500})
        assert out["decision"] == taxonomy.ASK
        assert "Write: too large for LLM review (1500 chars > 1000)" in out["reason"]
        assert http.bodies == []

    def test_within_limit_is_sent(self, project_root, http):
        _use_llm(max_action_chars=1000)
        hook.handle_write({"file_path": os.path.join(project_root, "a.txt"), "content": "z" * 900})
        assert len(http.bodies) == 1


# -- Trace and log record the action --


def test_action_stats_recorded(project_root, http):
    _use_llm(can_block=True)
    result = classify_command(_heredoc(_script()))
    call = try_llm(result, _llm_config())
    assert call.action["programs"] == [{"interpreter": "python3", "source": FROM_STDIN, "chars": len(_script())}]
    meta = llm_meta.build(call, config._cached_config)
    assert meta["llm_action"]["inline_programs"] == 1
    assert meta["llm_action"]["inline_program_chars"] == len(_script())


class TestInlineCleanWithoutVerdict:
    """Static floor section 8: clean inline code with no LLM verdict asks."""

    CMD = "python3 -c 'print(sum(range(10)))'"

    def test_llm_off_asks(self, project_root):
        assert hook.handle_bash({"command": self.CMD})["decision"] == taxonomy.ASK

    def test_llm_allow_allows(self, project_root, http):
        http.decision = "allow"
        _use_llm()
        assert hook.handle_bash({"command": self.CMD})["decision"] == taxonomy.ALLOW

    def test_floored_neighbour_keeps_the_llm_out(self, project_root, http):
        http.decision = "allow"
        _use_llm()
        out = hook.handle_bash({"command": self.CMD + " && printenv"})
        assert out["decision"] == taxonomy.ASK
        assert http.bodies == []
