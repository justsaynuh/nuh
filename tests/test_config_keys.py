"""Unknown config keys warn once on stderr, show in ``nuh config show``, and are ignored (config_keys)."""

import argparse

import pytest

from nuh import cli, config, config_keys, llm_action, llm_providers, paths
from nuh.config import _merge_configs

# Every key the docs describe, each provider with every key its code reads.
FULL = {
    "profile": "full", "classify": {"git_safe": ["mytool status"]}, "actions": {"git_safe": "allow"},
    "sensitive_paths_default": "ask", "sensitive_paths": {"~/secret": "block"}, "allow_paths": {},
    "known_registries": [], "exec_sinks": [], "sensitive_basenames": {}, "decode_commands": [],
    "content_patterns": {}, "credential_patterns": {}, "trusted_paths": ["/srv/scratch"], "db_targets": [],
    "log": {}, "tools": {"Bash": "decide"}, "human_only": {"git_history_rewrite": True},
    "hook": {"deadline_s": 10, "on_error": "ask"}, "protect_package": True, "project_root": "git",
    "project_lifts_sensitive": False,
    "llm": {
        "enabled": True, "providers": ["system1", "ollama", "openrouter"], "can_block": False,
        "on_uncertain": "ask", "on_failure": "ask", "self_heal": False, "trace_dir": "~/traces", "trace_keep_days": 7,
        "context": {"user_turns": 3}, "context_chars": 4000, "file_context_lines": 30, "file_context_chars": 2000,
        "max_action_chars": 4000, "max_prompt_chars": 16000,
        "system1": {"url": "http://127.0.0.1:11434", "model": "nimble:9b", "timeout": 5, "key_env": "S1_KEY",
                    "keep_alive": "10m", "definition": "~/s1.yaml", "redact_secrets": True},
        "ollama": {"url": "http://127.0.0.1:11434/api/chat", "model": "qwen3.5:9b", "timeout": 10,
                   "redact_secrets": False, "num_ctx": 24576},
        "cortex": {"url": "https://x.invalid.", "account": "acme", "key_env": "C", "model": "m", "timeout": 9,
                   "max_prompt_tokens": 32768},
        "openrouter": {"key_env": "OPENROUTER_API_KEY", "model": "m", "max_prompt_tokens": 32768},
        "openai": {"key_env": "OPENAI_API_KEY", "max_prompt_tokens": 32768},
        "anthropic": {"key_env": "ANTHROPIC_API_KEY", "max_prompt_tokens": 32768},
    },
}


def _load(capsys, global_cfg: dict, project_cfg: dict | None = None) -> tuple[config.NuhConfig, str]:
    cfg = _merge_configs(global_cfg, project_cfg or {})
    return cfg, capsys.readouterr().err


def test_a_full_valid_config_warns_nothing(capsys):
    cfg, err = _load(capsys, FULL, {"trace": False, "actions": {"git_safe": "ask"}})
    assert err == "" and cfg.unknown == []


@pytest.mark.parametrize("key", ["tools", "human_only"])
def test_a_top_level_key_under_llm_says_where_it_goes(capsys, key):
    cfg, err = _load(capsys, {"llm": {"enabled": True, key: {"Bash": "approve"}}})
    assert err == f"nuh: config: llm.{key} is not an llm setting and is ignored; {key} is a top-level key\n"
    assert cfg.unknown == [f"llm.{key}"] and cfg.tools == {} and cfg.human_only == {}


def test_top_level_tools_still_applies(capsys):
    cfg, err = _load(capsys, {"tools": {"mcp__forge__*": "approve"}})
    assert err == "" and cfg.tools == {"mcp__forge__*": "approve"}


@pytest.mark.parametrize("global_cfg,key", [
    ({"llm": {"ollama": {"url": "http://127.0.0.1:11434", "port": 11434}}}, "llm.ollama.port"),
    ({"llm": {"ollama": {"key_env": "X"}}}, "llm.ollama.key_env"),   # ollama sends no key
    ({"llm": {"system1": {"defintion": "~/s1.yaml"}}}, "llm.system1.defintion"),
    ({"llm": {"can_blok": True}}, "llm.can_blok"),
    ({"llm": {"backends": ["ollama"]}}, "llm.backends"),             # providers is the cascade list
    ({"trusted_path": ["/srv"]}, "trusted_path"),
    ({"trace": False}, "trace"),                                     # a project's key, not the global config's
])
def test_an_unknown_key_warns_once(capsys, global_cfg, key):
    cfg, err = _load(capsys, global_cfg)
    assert err == f"nuh: config: unknown key {key}; ignored\n"
    assert cfg.unknown == [key]


def test_an_unknown_project_key_names_the_file(capsys):
    cfg, err = _load(capsys, {}, {"action": {"git_safe": "ask"}})
    assert err == "nuh: config: unknown key action in .nuh.yaml; ignored\n"
    assert cfg.unknown == ["action (.nuh.yaml)"]


def test_old_keys_are_unknown_like_any_other(capsys):
    cfg, err = _load(capsys, {"active_allow": True, "llm": {"eligible": "all"}}, {"active_allow": True})
    assert err == ("nuh: config: unknown key active_allow; ignored\n"
                   "nuh: config: unknown key active_allow in .nuh.yaml; ignored\n"
                   "nuh: config: unknown key llm.eligible; ignored\n")
    assert cfg.unknown == ["active_allow", "active_allow (.nuh.yaml)", "llm.eligible"]


def test_provider_tables_cover_every_provider():
    assert set(config_keys.PROVIDERS) == set(llm_providers.NAMES)


@pytest.mark.parametrize("name", sorted(set(llm_providers.NAMES) - {llm_providers.SYSTEM1}))
def test_a_prompted_providers_token_limit_is_read_and_warns_nothing(capsys, name):
    # Read through a computed key (ollama's num_ctx, the others' max_prompt_tokens), which a
    # table written by grepping for literal keys misses.
    section = {"model": "m", "num_ctx" if name == "ollama" else "max_prompt_tokens": 5000}
    assert llm_action.provider_token_limit(name, section) == 5000
    cfg, err = _load(capsys, {"llm": {"providers": [name], name: section}})
    assert err == "" and cfg.unknown == []


def test_a_token_limit_key_on_the_wrong_provider_is_unknown(capsys):
    cfg, _ = _load(capsys, {"llm": {"ollama": {"max_prompt_tokens": 5000}, "openai": {"num_ctx": 5000},
                                      "system1": {"num_ctx": 5000}}})
    assert cfg.unknown == ["llm.ollama.max_prompt_tokens", "llm.openai.num_ctx", "llm.system1.num_ctx"]


def test_config_show_lists_unknown_keys(tmp_path, monkeypatch, capsys):
    paths.set_project_root(str(tmp_path / "project"))
    (tmp_path / "config.yaml").write_text("llm:\n  tools:\n    Bash: approve\n")
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(tmp_path / "config.yaml"))
    config.reset_config()
    cli.cmd_config(argparse.Namespace(config_command="show"))
    out = capsys.readouterr().out
    assert "  unknown keys (ignored):" in out and "['llm.tools']" in out
