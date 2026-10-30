"""Behavioral coverage for Codex patch inspection."""

from pathlib import Path

import pytest

from nuh import hook_write, path_names, paths, sensitive_paths
from nuh.config import NuhConfig
from nuh.patch import MAX_PATCH_CHARS, handle_apply_patch, parse_patch


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch: pytest.MonkeyPatch, project_root: str) -> None:
    import nuh.config
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(nuh.config, "get_config", lambda: NuhConfig())
    monkeypatch.setattr(hook_write, "_should_llm_inspect_write", lambda _: False)


def inspect(body: str) -> dict:
    return handle_apply_patch({"command": f"*** Begin Patch\n{body}\n*** End Patch"})


def test_mixed_patch_preserves_files(project_root: str) -> None:
    original = Path(project_root, "old.txt")
    original.write_text("old\n")
    result = inspect("*** Add File: new.txt\n+safe\n*** Update File: old.txt\n*** Move to: moved.txt\n"
                     "@@\n-old\n+new\n*** Delete File: gone.txt")
    assert result["decision"] == "allow"
    assert original.read_text() == "old\n"
    assert not Path(project_root, "new.txt").exists()
    assert not Path(project_root, "moved.txt").exists()


@pytest.mark.parametrize("body", [
    "*** Add File: safe.txt\n+safe\n*** Delete File: /etc/shadow",
    "*** Delete File: /etc/shadow\n*** Add File: safe.txt\n+safe",
    "*** Update File: safe.txt\n*** Move to: /etc/shadow\n@@\n-old\n+new",
    "*** Update File: /etc/shadow\n*** Move to: safe.txt\n@@\n-old\n+new",
    "*** Add File: .env\n+safe\n*** Delete File: /etc/shadow",
])
def test_every_path_is_checked(body: str) -> None:
    assert inspect(body)["decision"] == "block"


@pytest.mark.parametrize("directory", ["_HOOKS_DIR", "_CODEX_HOOKS_DIR"])
def test_delete_and_move_cannot_modify_guard(directory: str) -> None:
    target = getattr(path_names, directory) + "/nuh_guard.py"
    assert inspect(f"*** Delete File: {target}")["decision"] == "block"
    assert inspect(f"*** Update File: safe\n*** Move to: {target}\n@@\n-a\n+b")["decision"] == "block"


def test_symlink_escape(project_root: str, tmp_path: Path) -> None:
    Path(project_root, "escape").symlink_to(tmp_path)
    assert inspect("*** Add File: escape/new.txt\n+safe")["decision"] == "ask"


def test_literal_variable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCH_TARGET", "/etc/shadow")
    assert inspect("*** Add File: $PATCH_TARGET\n+safe")["decision"] == "allow"
    assert inspect("*** Add File: ~/.aws/credentials\n+safe")["decision"] == "allow"


def test_literal_sensitive_path_does_not_expand_allow_exemption(
    monkeypatch: pytest.MonkeyPatch, project_root: str,
) -> None:
    import nuh.config
    config = NuhConfig(sensitive_basenames={"deploy.cfg": "ask"},
                       allow_paths={f"{project_root}/approved/deploy.cfg": [project_root]})
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = False
    monkeypatch.setenv("SAFE", "approved")
    assert inspect("*** Add File: $SAFE/deploy.cfg\n+safe")["decision"] == "ask"
    assert inspect("*** Add File: approved/deploy.cfg\n+safe")["decision"] == "allow"


def test_allow_paths_cannot_exempt_a_builtin_entry(monkeypatch: pytest.MonkeyPatch, project_root: str) -> None:
    """.env is a built-in credential entry, a floor: allow_paths leaves it in place (C2)."""
    import nuh.config
    config = NuhConfig(allow_paths={f"{project_root}/approved/.env": [project_root]})
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    result = inspect("*** Add File: approved/.env\n+safe")
    assert (result["decision"], result["_floor"]) == ("ask", "credential_paths")


def test_literal_project_root_does_not_expand_allow_exemption(
    monkeypatch: pytest.MonkeyPatch, project_root: str,
) -> None:
    import nuh.config
    actual_root = Path(project_root, "$ROOT")
    actual_root.mkdir()
    monkeypatch.chdir(actual_root)
    paths.set_project_root(str(actual_root))
    monkeypatch.setenv("ROOT", "other-project")
    config = NuhConfig(allow_paths={
        str(actual_root / ".env"): [str(Path(project_root, "other-project"))],
    })
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    assert inspect("*** Add File: .env\n+safe")["decision"] == "ask"


@pytest.mark.parametrize("filename", ["hooks.json", "config.toml"])
def test_codex_registration_is_sensitive_inside_trusted_directory(
    monkeypatch: pytest.MonkeyPatch, filename: str,
) -> None:
    import nuh.config
    config = NuhConfig(trusted_paths=[path_names._HOME])
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    target = str(Path(path_names._HOME, ".codex", filename))
    assert inspect(f"*** Add File: {target}\n+safe")["decision"] == "ask"


def test_content_in_later_file_and_retained_context() -> None:
    assert inspect("*** Add File: safe\n+hello\n*** Update File: run.sh\n@@\n rm\n+-rf /")["decision"] == "ask"
    assert inspect("*** Update File: run.sh\n@@\n-rm -rf /\n+echo safe")["decision"] == "allow"


@pytest.mark.parametrize("body", [
    "*** Unknown: file", "*** Add File: \n+safe", "*** Add File: a\nsafe",
    "*** Delete File: a\n+content", "*** Update File: a", "*** Update File: a\n@@",
    "*** Update File: a\n@@\n?bad", "*** Update File: a\n@@\n+x\n*** Move to: b",
    "*** Add File: a\n+x\n*** Add File: a\n+y", "*** Add File: a\n+x\ntrailing garbage",
    "*** Add File: a\n+x\n*** End Patch\n*** Delete File: /etc/shadow",
])
def test_malformed_or_unsupported_is_blocked(body: str) -> None:
    assert inspect(body)["decision"] == "block"


@pytest.mark.parametrize("command", [None, 12, {}, [], "", "*** Begin Patch\n*** End Patch",
                                     "x" * (MAX_PATCH_CHARS + 1)])
def test_invalid_input_is_blocked(command: object) -> None:
    assert handle_apply_patch({"command": command})["decision"] == "block"


def test_delete_obeys_action_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    import nuh.config
    config = NuhConfig()
    config.actions = {"filesystem_delete": "block"}
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    assert inspect("*** Delete File: ordinary.txt")["decision"] == "block"


def test_blocked_path_never_calls_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_llm(*args: object) -> dict:
        raise AssertionError("blocked content must not reach an LLM")

    monkeypatch.setattr(hook_write, "_llm_veto_gate", unexpected_llm)
    assert inspect("*** Add File: /etc/shadow\n+private content")["decision"] == "block"


@pytest.mark.parametrize("action", ["filesystem_write", "filesystem_delete"])
def test_move_obeys_both_action_policies(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    import nuh.config
    config = NuhConfig()
    config.actions = {action: "block"}
    monkeypatch.setattr(nuh.config, "get_config", lambda: config)
    assert inspect("*** Update File: old\n*** Move to: new\n@@\n-a\n+b")["decision"] == "block"


def test_path_ask_does_not_hide_content_block() -> None:
    from nuh import content
    content._content_policies["destructive"] = "block"
    assert inspect("*** Add File: .env\n+rm -rf /")["decision"] == "block"


def test_size_limit_prevents_tail_content_bypass() -> None:
    patch = "*** Begin Patch\n*** Add File: large\n+" + "x" * MAX_PATCH_CHARS
    patch += "\n*** Add File: later\n+rm -rf /\n*** End Patch"
    assert handle_apply_patch({"command": patch})["decision"] == "block"


def test_parser_preserves_operation_data() -> None:
    operation = parse_patch("*** Begin Patch\n*** Update File: a\n*** Move to: b\n@@ section\n-old\n+new\n"
                            "*** End of File\n*** End Patch\n")[0]
    assert (operation.kind, operation.path, operation.destination) == ("update", "a", "b")
    assert "new" in operation.content
    assert "old" not in operation.content


def test_llm_receives_patch_semantics_and_each_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from nuh.llm_prompt import _build_write_prompt

    prompts: list[str] = []

    def review(tool: str, tool_input: dict, decision: dict) -> tuple[dict, dict]:
        prompts.append(_build_write_prompt(tool, tool_input, decision).user)
        return {"decision": "allow"}, {}

    monkeypatch.setattr(hook_write, "_should_llm_inspect_write", lambda _: True)
    monkeypatch.setattr(hook_write, "_try_llm_write", review)
    result = inspect(
        "*** Update File: executor.php\n*** Move to: live/executor.php\n"
        "@@ first\n unchanged();\n-old_call();\n+new_call();\n"
        "@@ second\n-return false;\n+return true;\n"
        "*** Add File: new.php\n+<?php echo 'hello';"
    )
    assert result["decision"] == "allow"
    assert len(prompts) == 2
    assert all("Tool: apply_patch" in prompt for prompt in prompts)
    assert "omitted file content is preserved" in prompts[0]
    assert "*** Update File: executor.php\n*** Move to: live/executor.php" in prompts[0]
    assert " unchanged();\n-old_call();\n+new_call();" in prompts[0]
    assert "@@ second\n-return false;\n+return true;" in prompts[0]
    assert "*** Add File: new.php\n+<?php echo 'hello';" in prompts[1]
    assert all("Content about to be written:" not in prompt for prompt in prompts)


def test_patch_llm_prompt_sends_patch_whole_and_raw() -> None:
    from nuh.llm_prompt import _build_write_prompt

    secret = "AKIAIOSFODNN7EXAMPLE"
    tail = "+import os; os.system('rm -rf ~')"
    patch = f"*** Update File: a\n@@\n-{secret}\n+safe\n" + "+x\n" * 4000 + tail
    prompt = _build_write_prompt("apply_patch", {"patch": patch}, {"decision": "allow"}).user
    assert patch in prompt


def test_patch_llm_veto_still_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_write, "_should_llm_inspect_write", lambda _: True)
    monkeypatch.setattr(hook_write, "_try_llm_write", lambda *args: (
        {"decision": "ask", "reason": "review needed"}, {},
    ))
    assert inspect("*** Update File: a\n@@\n-old\n+new")["decision"] == "ask"
