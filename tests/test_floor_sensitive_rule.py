"""Floor rule sensitive_paths: every non-credential sensitive-path ask is floored."""

import os

import pytest

from nuh import config, hook, llm, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.llm_types import LLMCallResult, ProviderAttempt


def _allow() -> LLMCallResult:
    return LLMCallResult(decision={"decision": "allow", "reason": "LLM: fine"}, provider="test",
                         reasoning="fine", cascade=[ProviderAttempt("test", "success", 5)])


@pytest.fixture
def docs(tmp_path, project_root, monkeypatch):
    """A user sensitive_paths entry outside the project; the cwd is the project."""
    monkeypatch.chdir(project_root)
    folder = tmp_path / "docs"
    folder.mkdir()
    return str(folder)


@pytest.fixture
def llm_allows(monkeypatch):
    """Every LLM review allows; returns the list of reviews made."""
    asked: list[str] = []

    def review(*args, **_kwargs):
        asked.append(str(args[0]) if args and isinstance(args[0], str) else "Bash")
        return _allow()

    monkeypatch.setattr(llm, "try_llm", review)
    monkeypatch.setattr(llm, "try_llm_write", review)
    return asked


def _configure(docs: str, **global_cfg) -> None:
    llm_section = {"enabled": True}
    config._cached_config = config._merge_configs(
        {"llm": llm_section, "sensitive_paths": {docs: "ask"}, **global_cfg}, {})
    sensitive_paths.reset_sensitive_paths()


class TestFloored:
    def test_bash_read_stays_ask_with_llm_allow(self, docs, llm_allows):
        _configure(docs)
        result = hook.handle_bash({"command": f"cat {docs}/x.txt"})
        assert (result["decision"], result["_floor"]) == (taxonomy.ASK, "sensitive_paths")
        assert llm_allows == []

    def test_write_stays_ask_under_review(self, docs, llm_allows):
        _configure(docs)
        result = hook.handle_write({"file_path": f"{docs}/x.txt", "content": "hi\n"})
        assert (result["decision"], result["_floor"]) == (taxonomy.ASK, "sensitive_paths")
        assert llm_allows == []

    @pytest.mark.parametrize("tool,field", [("Read", "file_path"), ("Glob", "path"), ("Grep", "path")])
    def test_file_tools_carry_the_rule(self, docs, tool, field):
        _configure(docs)
        result = hook.HANDLERS[tool]({field: f"{docs}/x.txt", "pattern": "x"})
        assert result["_floor"] == "sensitive_paths"

    def test_shell_init_write_is_floored(self, docs):
        _configure(docs)
        result = hook.handle_write({"file_path": os.path.expanduser("~/.zshrc"), "content": "alias x=y\n"})
        assert (result["decision"], result["_floor"]) == (taxonomy.ASK, "sensitive_paths")

    def test_block_stays_block(self, docs):
        config._cached_config = config._merge_configs({"sensitive_paths": {docs: "block"}}, {})
        sensitive_paths.reset_sensitive_paths()
        assert hook.handle_read({"file_path": f"{docs}/x.txt"})["decision"] == taxonomy.BLOCK

    def test_specific_rules_keep_their_id(self, docs):
        _configure(docs)
        settings = hook.handle_write({"file_path": os.path.expanduser("~/.claude/settings.json"), "content": "{}"})
        assert settings["_floor"] == "agent_hooks"
        assert classify_command(f"cat {docs}/server.pem").floor_rules() == ["credential_paths"]

    def test_allow_path_hint_kept(self, docs):
        _configure(docs)
        result = hook.handle_bash({"command": f"cat {docs}/x.txt"})
        assert result["_hint"].startswith("To always allow: nuh allow-path")


class TestReview:
    def test_human_only_false_lets_the_llm_lower_bash(self, docs, llm_allows):
        _configure(docs, human_only={"sensitive_paths": False})
        assert hook.handle_bash({"command": f"cat {docs}/x.txt"})["decision"] == taxonomy.ALLOW

    def test_human_only_false_lets_the_llm_lower_write(self, docs, llm_allows):
        _configure(docs, human_only={"sensitive_paths": False})
        result = hook.handle_write({"file_path": f"{docs}/x.txt", "content": "hi\n"})
        assert result["decision"] == taxonomy.ALLOW
        assert "lowered to allow by LLM review" in result["reason"]

    def test_credential_in_the_same_stage_keeps_its_floor(self, docs, llm_allows):
        _configure(docs, human_only={"sensitive_paths": False})
        result = hook.handle_bash({"command": f"cat {docs}/x.txt {docs}/server.pem"})
        assert (result["decision"], result["_floor"]) == (taxonomy.ASK, "credential_paths")
        assert llm_allows == []

    def test_credential_symlink_behind_a_user_entry_keeps_its_floor(self, docs, llm_allows):
        _configure(docs, human_only={"sensitive_paths": False})
        os.symlink(os.path.expanduser("~/.aws/credentials"), os.path.join(docs, "creds"))
        result = hook.handle_read({"file_path": f"{docs}/creds"})
        assert result["_floor"] == "credential_paths"
