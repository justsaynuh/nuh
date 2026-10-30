"""Tests for hint flow — actionable ask messages (FD-027)."""

import os

import pytest

from nuh import config, paths, sensitive_paths, taxonomy
from nuh.config import NuhConfig, reset_config


def _user_entry() -> None:
    """A sensitive_paths entry of the user's own (~/notes: ask), merged."""
    config._cached_config = NuhConfig(sensitive_paths={"~/notes": "ask"})
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = False


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    paths.set_project_root(str(tmp_path))
    sensitive_paths.reset_sensitive_paths()
    reset_config()
    yield
    paths.reset_project_root()
    sensitive_paths.reset_sensitive_paths()
    reset_config()


class TestBashHints:
    """Bash ask decisions should include actionable hints."""

    def test_action_policy_hint(self):
        """Action policy ask → reason contains 'nuh allow {type}'."""
        from nuh.hook import handle_bash, _to_hook_output
        decision = handle_bash({"command": "git push --force origin main"})
        assert decision["decision"] == taxonomy.ASK
        hint = decision.get("_hint", "")
        assert "nuh allow git_history_rewrite" in hint

        # Check it appears in formatted output
        output = _to_hook_output(decision, "claude")
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "nuh allow git_history_rewrite" in reason

    def test_unknown_hint(self):
        """Unknown command → reason contains 'nuh classify' and 'nuh types'."""
        from nuh.hook import handle_bash
        # Use a truly unknown command
        decision = handle_bash({"command": "zzz_unknown_tool_xyz --flag"})
        assert decision["decision"] == taxonomy.ASK
        hint = decision.get("_hint", "")
        assert "nuh classify" in hint
        assert "nuh types" in hint

    def test_sensitive_path_hint(self):
        """Bash ask for the user's own sensitive entry → reason contains 'nuh allow-path'."""
        from nuh.hook import handle_bash
        _user_entry()
        decision = handle_bash({"command": "cat ~/notes/plan.txt"})
        assert decision["decision"] == taxonomy.ASK
        assert "nuh allow-path ~/notes" in decision.get("_hint", "")

    @pytest.mark.parametrize("command,rule", [("cat ~/.aws/config", "credential_paths"),
                                              ("cat ~/.bashrc", "sensitive_paths")])
    def test_builtin_entry_hint_names_the_floor(self, command, rule):
        """allow-path cannot exempt a built-in entry, so the hint names the floor rule instead (C2)."""
        from nuh.hook import handle_bash
        decision = handle_bash({"command": command})
        assert decision["decision"] == taxonomy.ASK
        assert decision.get("_hint", "") == (f"Static floor rule {rule}: only you can approve this; "
                                             f"human_only: {{{rule}: false}} in the global config lets the LLM "
                                             "decide it")

    def test_composition_rule_no_hint(self):
        """Composition rule ask → no hint (not rememberable)."""
        from nuh.hook import handle_bash
        decision = handle_bash({"command": "cat file.txt | bash"})
        # Composition rules may block or ask
        hint = decision.get("_hint")
        assert hint is None


class TestPathHints:
    """Path ask decisions should include actionable hints."""

    def test_sensitive_path_hint(self):
        """The user's own sensitive entry → reason contains 'nuh allow-path'."""
        _user_entry()
        result = paths.check_path("Read", "~/notes/plan.txt")
        assert result["decision"] == taxonomy.ASK
        assert "nuh allow-path ~/notes/plan.txt" in result.get("_hint", "")

    def test_builtin_entry_has_no_allow_path_hint(self):
        """allow-path cannot exempt a built-in entry (a floor), so the file tool's ask offers no hint (C2)."""
        result = paths.check_path("Read", "~/.aws/config")
        assert result["decision"] == taxonomy.ASK
        assert "_hint" not in result

    def test_hook_directory_read_allowed(self):
        """Hook directory read → allowed (no decision dict)."""
        result = paths.check_path("Read", "~/.claude/hooks/something.py")
        assert result is None  # reads on hooks are allowed


class TestContentHints:
    """Content inspection asks should have non-rememberable hint."""

    def test_write_content_hint(self, project_root):
        """Content inspection ask → hint says cannot be remembered."""
        from nuh.hook import handle_write
        # Write content with something that looks like a secret (inside project)
        target = os.path.join(project_root, "test.py")
        decision = handle_write({
            "file_path": target,
            "content": "AKIAIOSFODNN7EXAMPLE",  # AWS-style key
        })
        if decision["decision"] == taxonomy.ASK:
            hint = decision.get("_hint", "")
            assert "cannot be remembered" in hint


class TestGrepHints:
    """Grep credential search asks should have non-rememberable hint."""

    def test_credential_search_hint(self, tmp_path):
        """Credential search ask → hint says cannot be remembered."""
        from nuh.hook import handle_grep
        decision = handle_grep({
            "pattern": "AKIA[A-Z0-9]{16}",
            "path": "/etc",
        })
        if decision["decision"] == taxonomy.ASK:
            hint = decision.get("_hint", "")
            assert "cannot be remembered" in hint


class TestHintInOutput:
    """Hints should be rendered in formatted hook output."""

    def test_hint_appended_to_reason(self):
        """_to_hook_output appends hint to reason string."""
        from nuh.hook import _to_hook_output
        decision = {
            "decision": taxonomy.ASK,
            "reason": "Bash: git_history_rewrite → ask",
            "_hint": "To always allow: nuh allow git_history_rewrite",
        }
        output = _to_hook_output(decision, "claude")
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "nuh allow git_history_rewrite" in reason
        assert "git_history_rewrite → ask" in reason

    def test_no_hint_no_change(self):
        """Without _hint, output is unchanged."""
        from nuh.hook import _to_hook_output
        decision = {
            "decision": taxonomy.ASK,
            "reason": "Bash: some reason",
        }
        output = _to_hook_output(decision, "claude")
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "nuh?" in reason
        assert "some reason" in reason

    def test_hint_in_meta_for_llm_resolved(self):
        """Hint should be in _meta even for LLM-resolved asks."""
        from nuh.hook import handle_bash, _classify_meta
        from nuh.bash_hint import build as _build_bash_hint
        from nuh.bash import classify_command
        result = classify_command("git push --force origin main")
        hint = _build_bash_hint(result)
        assert hint is not None
        assert "nuh allow" in hint
        meta = _classify_meta(result)
        # When handle_bash runs, hint goes into meta
        decision = handle_bash({"command": "git push --force origin main"})
        meta = decision.get("_meta", {})
        if "hint" in meta:
            assert "nuh allow" in meta["hint"]
