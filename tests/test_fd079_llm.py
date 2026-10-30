"""Tests for FD-079: Script Execution Inspection, the LLM side.

Covers: the LLM veto gate, prompt enrichment, and reading the script for the LLM.
"""

import os

from nuh.bash import classify_command
from tests.script_exec_helpers import _write


# ===================================================================
# 5. LLM VETO GATE
# ===================================================================

class TestVetoGate:
    """LLM veto gate: fires for clean scripts, can only block."""

    def test_has_script_true_for_clean(self, project_root):
        from nuh.bash_review import runs_clean_script
        path = os.path.join(project_root, "clean.py")
        _write(path)
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = classify_command("python clean.py")
            assert runs_clean_script(result) is True
        finally:
            os.chdir(old_cwd)

    def test_has_script_false_for_inline_clean(self):
        """Clean inline code asks, so the LLM sees it on the ask path, not the veto path."""
        from nuh.bash_review import ASK, route, runs_clean_script
        result = classify_command("python -c 'print(1)'")
        assert runs_clean_script(result) is False
        assert route(result, "decide").kind == ASK

    def test_has_script_false_for_not_found(self, project_root):
        from nuh.bash_review import runs_clean_script
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = classify_command("python missing.py")
            assert runs_clean_script(result) is False
        finally:
            os.chdir(old_cwd)

    def test_has_script_false_for_dangerous(self, project_root):
        """Dangerous scripts resolve to ask, not allow — veto gate doesn't fire."""
        from nuh.bash_review import runs_clean_script
        path = os.path.join(project_root, "evil.py")
        _write(path, "import os\nos.remove('/')\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = classify_command("python evil.py")
            assert runs_clean_script(result) is False
        finally:
            os.chdir(old_cwd)

    def _handle_with_mock_llm(self, command, llm_return, project_root=None):
        """Run handle_bash with a mocked _try_llm."""
        import nuh.hook as hook_mod
        original = hook_mod._try_llm
        hook_mod._try_llm = lambda result: llm_return
        try:
            return hook_mod.handle_bash({"command": command})
        finally:
            hook_mod._try_llm = original

    def test_veto_gate_llm_blocks(self, project_root):
        from nuh.config import get_config
        # llm.can_block: true lets the block stand (the default caps it to ask)
        get_config().llm_can_block = True
        path = os.path.join(project_root, "sneaky.py")
        _write(path, "# looks clean but LLM disagrees\nprint('hi')\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = self._handle_with_mock_llm(
                "python sneaky.py",
                ({"decision": "block", "reason": "LLM threat"}, {"llm_provider": "test"}),
            )
            assert result["decision"] == "block"
        finally:
            os.chdir(old_cwd)

    def test_veto_gate_llm_block_capped_to_ask(self, project_root):
        """Default llm.can_block false: the LLM block is capped to ask, and that ask raises the allow."""
        path = os.path.join(project_root, "sneaky.py")
        _write(path, "print('hi')\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = self._handle_with_mock_llm(
                "python sneaky.py",
                ({"decision": "block", "reason": "LLM threat"}, {"llm_provider": "test"}),
            )
            assert result["decision"] == "ask"
            assert result["reason"] == "LLM suggested block: LLM threat"
        finally:
            os.chdir(old_cwd)

    def test_veto_gate_llm_allows(self, project_root):
        path = os.path.join(project_root, "safe.py")
        _write(path)
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = self._handle_with_mock_llm(
                "python safe.py",
                ({"decision": "allow", "reason": "safe"}, {"llm_provider": "test"}),
            )
            assert result["decision"] == "allow"
        finally:
            os.chdir(old_cwd)

    def test_veto_gate_llm_error_keeps_allow(self, project_root):
        path = os.path.join(project_root, "safe.py")
        _write(path)
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = self._handle_with_mock_llm(
                "python safe.py",
                (None, {}),  # LLM unavailable
            )
            assert result["decision"] == "allow"
        finally:
            os.chdir(old_cwd)

    def test_inline_reaches_the_llm_on_the_ask_path(self, project_root):
        """Clean inline code asks; the LLM may allow it."""
        from nuh.bash_review import ASK, route
        result = classify_command("python -c 'print(1)'")
        assert result.final_decision == "ask"
        assert route(result, "decide").kind == ASK

    def test_veto_gate_skips_not_found(self, project_root):
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            result = self._handle_with_mock_llm(
                "python nonexistent.py",
                (None, {}),
            )
            assert result["decision"] == "ask"
        finally:
            os.chdir(old_cwd)


# ===================================================================
# 6. LLM PROMPT ENRICHMENT
# ===================================================================

class TestPromptEnrichment:
    """Script content and content inspection results in LLM prompt."""

    def _build_prompt_for(self, command, project_root=None):
        from nuh.llm_prompt import _build_prompt
        result = classify_command(command)
        return _build_prompt(result)

    def test_prompt_includes_script_content(self, project_root):
        path = os.path.join(project_root, "hello.py")
        _write(path, "print('hello world')\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            prompt = self._build_prompt_for("python hello.py")
            assert "Script about to execute:" in prompt.user
            assert "print('hello world')" in prompt.user
        finally:
            os.chdir(old_cwd)

    def test_prompt_includes_no_flags(self, project_root):
        path = os.path.join(project_root, "clean.py")
        _write(path, "x = 1\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            prompt = self._build_prompt_for("python clean.py")
            assert "Content inspection: no flags" in prompt.user
        finally:
            os.chdir(old_cwd)

    def test_prompt_includes_match_details(self, project_root):
        path = os.path.join(project_root, "danger.py")
        _write(path, "import os\nos.remove('/etc/passwd')\n")
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            prompt = self._build_prompt_for("python danger.py")
            assert "Content inspection:" in prompt.user
            assert "os.remove" in prompt.user
        finally:
            os.chdir(old_cwd)

    def test_prompt_includes_inline_code(self):
        """Inline code is now included in LLM prompt for enrichment (nah-koi.1)."""
        from nuh.llm_prompt import _build_prompt
        result = classify_command("python -c 'print(1)'")
        prompt = _build_prompt(result)
        assert "Program 1 about to execute (python, from inline argument" in prompt.user
        assert "```\nprint(1)\n```" in prompt.user

    def test_prompt_no_content_for_nonexistent(self, project_root):
        old_cwd = os.getcwd()
        os.chdir(project_root)
        try:
            prompt = self._build_prompt_for("python nonexistent.py")
            assert "Script about to execute:" not in prompt.user
        finally:
            os.chdir(old_cwd)


# ===================================================================
# 7. _read_script_for_llm UNIT TESTS
# ===================================================================

class TestReadScriptForLlm:
    """Direct tests for the LLM script reader."""

    def test_basic_read(self, project_root):
        from nuh.llm_prompt import _read_script_for_llm
        path = os.path.join(project_root, "test.py")
        _write(path, "print('hello')\n")
        content = _read_script_for_llm(["python", path])
        assert content == "print('hello')\n"

    def test_inline_returns_code_string(self):
        """Inline code is now returned for LLM enrichment (nah-koi.1)."""
        from nuh.llm_prompt import _read_script_for_llm
        assert _read_script_for_llm(["python", "-c", "print(1)"]) == "print(1)"

    def test_module_returns_none(self):
        from nuh.llm_prompt import _read_script_for_llm
        assert _read_script_for_llm(["python", "-m", "http.server"]) is None

    def test_value_flag_skipped(self, project_root):
        from nuh.llm_prompt import _read_script_for_llm
        path = os.path.join(project_root, "script.py")
        _write(path, "x = 1\n")
        content = _read_script_for_llm(["python", "-W", "ignore", path])
        assert content == "x = 1\n"

    def test_single_token_direct_exec(self, project_root):
        from nuh.llm_prompt import _read_script_for_llm
        path = os.path.join(project_root, "run.py")
        _write(path, "print('direct')\n")
        content = _read_script_for_llm([path])
        assert content == "print('direct')\n"

    def test_nonexistent_returns_none(self):
        from nuh.llm_prompt import _read_script_for_llm
        assert _read_script_for_llm(["python", "/tmp/fd079_nonexistent.py"]) is None

    def test_empty_tokens_returns_none(self):
        from nuh.llm_prompt import _read_script_for_llm
        assert _read_script_for_llm([]) is None

    def test_size_cap(self, project_root):
        from nuh.llm_prompt import _read_script_for_llm
        path = os.path.join(project_root, "big.py")
        _write(path, "x" * 20000)
        content = _read_script_for_llm(["python", path], max_chars=100)
        assert len(content) == 100
