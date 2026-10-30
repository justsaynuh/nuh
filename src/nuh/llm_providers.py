"""Prompted providers (system 2): Ollama, OpenAI, OpenRouter, Anthropic, Cortex, and their transport.

``_post`` is the only urlopen call in nuh, so every provider attempt reaches
the trace directory (trace.py). The one exception is the ``llm.self_heal``
worker (llm_heal_worker.py), which calls ``_exchange`` directly: its probes
and replay run after the hook call, outside any trace.
"""

import json
import os
import queue
import sys
import threading
import time
import urllib.request
from urllib.error import URLError

from nuh import llm_action, llm_heal
from nuh import trace as tracing
from nuh.llm_types import LLMResult, PromptParts
from nuh.redact import redact_secrets

_TIMEOUT_LOCAL = 10
_TIMEOUT_REMOTE = 10
SYSTEM1 = "system1"  # the system 1 provider (system1.py): not prompted, so not in _PROVIDERS

# The env var each keyed provider reads its API key from unless ``key_env`` names another.
_DEFAULT_KEY_ENVS = {
    "cortex": "SNOWFLAKE_PAT",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# -- Providers --


def _post(req: urllib.request.Request, timeout: float) -> bytes:
    """Send a provider request and return the raw response body.

    The only urlopen call in nuh: every provider goes through here, so every
    attempt reaches the trace directory (tests/test_trace.py pins this).
    *timeout* bounds the whole request (``_exchange``); past it the request
    fails with TimeoutError, as a socket timeout does.
    """
    tracing.record_request(req, timeout)
    t0 = time.monotonic()
    try:
        status, raw = _exchange(req, timeout)
    except Exception as exc:
        tracing.record_http_error(exc, int((time.monotonic() - t0) * 1000))
        if llm_heal.is_timeout(exc):
            llm_heal.note_timeout(req)  # the cascade may hand it to a heal worker (llm.self_heal)
        raise
    tracing.record_http_response(status, raw, int((time.monotonic() - t0) * 1000))
    return raw


def _exchange(req: urllib.request.Request, timeout: float) -> tuple[object, bytes]:
    """(status, body) of *req*, connect to last byte within *timeout* seconds in all.

    urllib applies its timeout to each socket operation, so a server that
    sends its headers or body a byte at a time keeps the request alive far
    past it, and past the cascade's budget (cascade_budget.py) until Claude
    Code kills the hook with nothing logged. The request runs on a daemon
    thread instead, and the caller stops waiting when *timeout* runs out. The
    abandoned thread ends when the server stops or a socket operation times
    out; the hook process exits long before either matters.
    """
    done: queue.SimpleQueue = queue.SimpleQueue()

    def run() -> None:
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            done.put((getattr(resp, "status", None), resp.read(), None))
        except BaseException as exc:
            # Handed to the caller, which raises it as its own.
            done.put((None, b"", exc))

    threading.Thread(target=run, name="nuh-llm-request", daemon=True).start()
    try:
        status, raw, exc = done.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"no complete response within {timeout:g} s") from None
    if exc is not None:
        raise exc
    return status, raw


def _parse_response(raw: str) -> LLMResult | None:
    """Parse LLM response JSON into LLMResult.

    Only accepts clean JSON or markdown-fenced JSON. The previous
    find("{")/rfind("}") fallback was removed to prevent echo attacks
    where injected JSON in transcript/file content could be extracted
    as the real decision (FD-068).
    """
    tracing.record_content(raw)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        raw = raw.strip()

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    tracing.record_parsed(obj)

    decision = obj.get("decision", "").lower()
    # The prompt offers allow, block or uncertain; a model that says ask decided to ask.
    if decision not in ("allow", "block", "ask", "uncertain"):
        return None

    reasoning = str(obj.get("reasoning", ""))[:200]
    return LLMResult(decision, reasoning)


def _prompt_as_messages(prompt: PromptParts) -> list[dict]:
    """Convert PromptParts to a messages list for chat APIs."""
    return [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]


def _call_ollama(
    config: dict, prompt: PromptParts,
) -> LLMResult | None:
    """Call Ollama API. /api/chat by default, /api/generate for legacy."""
    url = config.get("url", "http://localhost:11434/api/chat")
    model = config.get("model", "qwen3.5:9b")
    timeout = config.get("timeout", _TIMEOUT_LOCAL)
    # Always explicit: past the server's context size Ollama silently drops
    # the start of the prompt, which holds the instructions.
    num_ctx = llm_action.provider_token_limit("ollama", config)

    if "/api/generate" in url:
        payload: dict = {
            "model": model,
            "prompt": f"{prompt.system}\n\n{prompt.user}",
            "stream": False,
            "options": {"num_ctx": num_ctx},
        }
    else:
        payload = {
            "model": model,
            "messages": _prompt_as_messages(prompt),
            "stream": False,
            "options": {"num_ctx": num_ctx},
        }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
    )

    data = json.loads(_post(req, timeout))

    if "/api/generate" in url:
        result = _parse_response(data.get("response", ""))
    else:
        result = _parse_response(data.get("message", {}).get("content", ""))
    return _check_prompt_read(data, num_ctx, prompt, result)


class PromptCutError(Exception):
    """Ollama dropped part of the prompt unannounced: the model may not have seen the action, so no verdict."""


def _check_prompt_read(
    data: dict, num_ctx: int, prompt: PromptParts, result: LLMResult | None,
) -> LLMResult | None:
    """*result*, or PromptCutError when Ollama's ``prompt_eval_count`` says the prompt may have been cut."""
    count = data.get("prompt_eval_count")
    cut = llm_action.prompt_cut_reason(count, num_ctx, len(prompt.user))
    tracing.record_prompt_check({
        "prompt_eval_count": count, "num_ctx": num_ctx,
        "prompt_chars": len(prompt.system) + len(prompt.user), "suspect_cut": bool(cut),
    })
    if cut:
        raise PromptCutError(cut)
    return result


def _call_openai_compat(
    config: dict,
    prompt: PromptParts,
    timeout: int,
    default_url: str,
    default_model: str,
    default_key_env: str,
) -> LLMResult | None:
    """Call an OpenAI-compatible chat completions API."""
    url = config.get("url", default_url)
    if not url:
        sys.stderr.write("nuh: LLM: no URL configured\n")
        return None
    key_env = config.get("key_env", default_key_env)
    key = os.environ.get(key_env, "")
    if not key:
        sys.stderr.write(f"nuh: LLM: {key_env} not set\n")
        return None
    model = config.get("model", default_model)
    timeout = config.get("timeout", timeout)

    body = json.dumps({
        "model": model,
        "messages": _prompt_as_messages(prompt),
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })

    data = json.loads(_post(req, timeout))
    content = data["choices"][0]["message"]["content"]
    return _parse_response(content)


def _call_cortex(
    config: dict, prompt: PromptParts,
) -> LLMResult | None:
    """Call Snowflake Cortex REST API (inference:complete endpoint).

    Auto-derives URL from account name if not set explicitly.
    Requires SNOWFLAKE_PAT env var (or custom key_env) for auth.
    """
    url = config.get("url", "")
    if not url:
        account = (
            config.get("account", "")
            or os.environ.get("SNOWFLAKE_ACCOUNT", "")
        )
        if not account:
            sys.stderr.write("nuh: LLM: cortex — no account or URL configured\n")
            return None
        url = (
            f"https://{account}.snowflakecomputing.com"
            "/api/v2/cortex/inference:complete"
        )

    key_env = config.get("key_env", _DEFAULT_KEY_ENVS["cortex"])
    pat = os.environ.get(key_env, "")
    if not pat:
        sys.stderr.write(f"nuh: LLM: {key_env} not set\n")
        return None

    model = config.get("model", "claude-haiku-4-5")
    timeout = config.get("timeout", _TIMEOUT_REMOTE)

    body = json.dumps({
        "model": model,
        "messages": _prompt_as_messages(prompt),
        "stream": False,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {pat}",
        "X-Snowflake-Authorization-Token-Type":
            "PROGRAMMATIC_ACCESS_TOKEN",
    })

    data = json.loads(_post(req, timeout))
    content = data["choices"][0]["message"]["content"]
    return _parse_response(content)


def _call_openrouter(
    config: dict, prompt: PromptParts,
) -> LLMResult | None:
    """Call OpenRouter API."""
    return _call_openai_compat(
        config, prompt, _TIMEOUT_REMOTE,
        default_url="https://openrouter.ai/api/v1/chat/completions",
        default_model="google/gemini-3.1-flash-lite-preview",
        default_key_env=_DEFAULT_KEY_ENVS["openrouter"],
    )


def _call_openai_responses(
    config: dict,
    prompt: PromptParts,
    timeout: int,
    default_url: str,
    default_model: str,
    default_key_env: str,
) -> LLMResult | None:
    """Call OpenAI Responses API (/v1/responses)."""
    url = config.get("url", default_url)
    if not url:
        sys.stderr.write("nuh: LLM: no URL configured\n")
        return None
    key_env = config.get("key_env", default_key_env)
    key = os.environ.get(key_env, "")
    if not key:
        sys.stderr.write(f"nuh: LLM: {key_env} not set\n")
        return None
    model = config.get("model", default_model)
    timeout = config.get("timeout", timeout)

    body = json.dumps({
        "model": model,
        "input": prompt.user,
        "instructions": prompt.system,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })

    data = json.loads(_post(req, timeout))
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    return _parse_response(c["text"])
    return None


def _call_openai(
    config: dict, prompt: PromptParts,
) -> LLMResult | None:
    """Call OpenAI Responses API."""
    return _call_openai_responses(
        config, prompt, _TIMEOUT_REMOTE,
        default_url="https://api.openai.com/v1/responses",
        default_model="gpt-5.3-codex",
        default_key_env=_DEFAULT_KEY_ENVS["openai"],
    )


def _call_anthropic(
    config: dict, prompt: PromptParts,
) -> LLMResult | None:
    """Call Anthropic Messages API."""
    url = config.get("url", "https://api.anthropic.com/v1/messages")
    key_env = config.get("key_env", _DEFAULT_KEY_ENVS["anthropic"])
    key = os.environ.get(key_env, "")
    if not key:
        sys.stderr.write(f"nuh: LLM: {key_env} not set\n")
        return None
    model = config.get("model", "claude-haiku-4-5")
    timeout = config.get("timeout", _TIMEOUT_REMOTE)

    body = json.dumps({
        "model": model,
        "max_tokens": 256,
        "system": prompt.system,
        "messages": [{"role": "user", "content": prompt.user}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })

    data = json.loads(_post(req, timeout))
    content = data["content"][0]["text"]
    return _parse_response(content)


_PROVIDERS = {
    "ollama": _call_ollama,
    "cortex": _call_cortex,
    "openrouter": _call_openrouter,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


def _call_provider(
    name: str, config: dict, prompt: PromptParts,
    trace: "tracing.Trace | None" = None,
) -> tuple[LLMResult | None, int, str]:
    """Dispatch to the named provider. Returns (result, elapsed_ms, err).

    With a trace, the call runs as one recorded attempt: _post writes the
    request and response, and the outcome is added here.
    """
    if trace is None:
        return _dispatch_provider(name, config, prompt)
    with trace.attempt(name) as attempt:
        result, elapsed, err = _dispatch_provider(name, config, prompt)
        attempt.finish(result, elapsed, err)
    return result, elapsed, err


def _dispatch_provider(
    name: str, config: dict, prompt: PromptParts,
) -> tuple[LLMResult | None, int, str]:
    fn = _PROVIDERS.get(name)
    if fn is None:
        return None, 0, f"unknown provider: {name}"
    t0 = time.monotonic()
    try:
        if _provider_redacts(name, config):
            # The placeholders say what was masked; the verdict on the masked prompt stands.
            prompt = prompt._replace(user=redact_secrets(prompt.user))
        result = fn(config, prompt)
        elapsed = int((time.monotonic() - t0) * 1000)
        if result is None:
            return None, elapsed, "provider returned None (missing key or config)"
        return result, elapsed, ""
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        tracing.record_exception(exc)
        err = _describe_provider_error(exc)
        sys.stderr.write(f"nuh: LLM {name}: {err}\n")
        return None, elapsed, err


def _provider_redacts(name: str, config: object) -> bool:
    """``llm.<name>.redact_secrets``: mask secrets in what this provider is sent (default off)."""
    if not isinstance(config, dict) or "redact_secrets" not in config:
        return False
    value = config["redact_secrets"]
    if not isinstance(value, bool):
        sys.stderr.write(f"nuh: config: llm.{name}.redact_secrets must be true or false; using false\n")
        return False
    return value


def _describe_provider_error(exc: Exception) -> str:
    if isinstance(exc, PromptCutError):
        return str(exc)
    if isinstance(exc, (URLError, OSError, TimeoutError)):
        return f"{type(exc).__name__}: {exc}"
    if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError)):
        return f"bad response format: {exc}"
    return f"unexpected error: {exc}"


_DEFAULT_MODELS = {
    "ollama": "qwen3.5:9b",
    "cortex": "claude-haiku-4-5",
    "openrouter": "google/gemini-3.1-flash-lite-preview",
    "openai": "gpt-5.3-codex",
    "anthropic": "claude-haiku-4-5",
    SYSTEM1: "nimble:9b",
}

# Every provider name ``llm.providers`` may list: the prompted ones, and system 1 (system1.py).
NAMES = (*_PROVIDERS, SYSTEM1)


def _configured_providers(llm_config: dict) -> list[tuple[str, dict]]:
    """(name, config) of each provider in cascade order that has a config section."""
    names = llm_config.get("providers") or []
    return [(name, llm_config.get(name)) for name in names if llm_config.get(name)]


def prompted_providers(llm_config: dict) -> list[tuple[str, dict]]:
    """The configured providers that read nuh's prompt: all but system 1, which is asked its own questions."""
    return [(name, section) for name, section in _configured_providers(llm_config) if name != SYSTEM1]
