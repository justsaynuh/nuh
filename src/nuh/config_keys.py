"""The keys a config may set. Any other is warned about once on stderr, listed by ``nuh config show``, and ignored.

A misplaced key is otherwise silent: ``tools:`` written under ``llm:`` was
accepted and the tools words never applied. Checked: the top level of the
global config and of a project's ``.nuh.yaml``, the global ``llm`` section,
and each provider's subsection under it. ``llm.context`` checks its own keys
(transcript.budget_from_config).
"""

from __future__ import annotations

import sys

# Top-level keys of the global config. A project's .nuh.yaml may write any of them (the
# global-only ones warn where they are read) and ``trace``, its own.
TOP = frozenset({
    "profile", "classify", "actions", "sensitive_paths_default", "sensitive_paths", "allow_paths",
    "known_registries", "exec_sinks", "sensitive_basenames", "decode_commands", "content_patterns",
    "credential_patterns", "llm", "trusted_paths", "db_targets", "log", "tools", "human_only", "hook",
    "protect_package", "project_root", "project_lifts_sensitive", "completion_hint",
})
PROJECT_ONLY = frozenset({"trace"})


def token_limit_key(name: str) -> str:
    """The key holding prompted provider *name*'s token limit (llm_action.provider_token_limit).

    Ollama's ``num_ctx`` is sent with each request; the hosted APIs size
    their own context, so theirs, ``max_prompt_tokens``, only bounds the prompt.
    """
    return "num_ctx" if name == "ollama" else "max_prompt_tokens"


_COMMON = frozenset({"url", "model", "timeout", "redact_secrets"})
_KEYED = _COMMON | {"key_env"}
# Each provider's subsection (llm_providers.NAMES), by what its code reads. System 1 is
# not prompted, so it has no token limit.
PROVIDERS: dict[str, frozenset[str]] = {
    "ollama": _COMMON | {token_limit_key("ollama")},
    "cortex": _KEYED | {"account", token_limit_key("cortex")},
    "openrouter": _KEYED | {token_limit_key("openrouter")},
    "openai": _KEYED | {token_limit_key("openai")},
    "anthropic": _KEYED | {token_limit_key("anthropic")},
    "system1": _KEYED | {"keep_alive", "definition"},
}
LLM = frozenset({
    "enabled", "providers", "can_block", "on_uncertain", "on_failure", "self_heal", "trace_dir", "trace_keep_days",
    "context", "context_chars", "file_context_lines", "file_context_chars", "max_action_chars", "max_prompt_chars",
}) | frozenset(PROVIDERS)
# Top-level keys people put under llm by mistake: the warning says where they go.
_TOP_LEVEL_ELSEWHERE = frozenset({"tools", "human_only"})


def unknown(global_cfg: dict, project_cfg: dict) -> list[str]:
    """The keys neither config uses, dotted (``llm.ollama.port``), each warned about once on stderr.

    A project's ``llm`` is ignored whole elsewhere, so only its top level is
    checked here.
    """
    found: list[str] = []

    def report(key: str, message: str) -> None:
        if key not in found:
            sys.stderr.write(f"nuh: config: {message}\n")
            found.append(key)

    for key in global_cfg:
        if key not in TOP:
            report(str(key), f"unknown key {key}; ignored")
    for key in project_cfg:
        if key not in TOP and key not in PROJECT_ONLY:
            report(f"{key} (.nuh.yaml)", f"unknown key {key} in .nuh.yaml; ignored")
    llm = global_cfg.get("llm")
    for key, value in (llm.items() if isinstance(llm, dict) else ()):
        name = f"llm.{key}"
        if key in _TOP_LEVEL_ELSEWHERE:
            report(name, f"{name} is not an llm setting and is ignored; {key} is a top-level key")
        elif key not in LLM:
            report(name, f"unknown key {name}; ignored")
        elif key in PROVIDERS and isinstance(value, dict):
            for sub in value:
                if sub not in PROVIDERS[key]:
                    report(f"{name}.{sub}", f"unknown key {name}.{sub}; ignored")
    return found
