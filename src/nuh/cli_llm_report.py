"""``nuh test``'s LLM output: the effective settings, the review's result, and the exchanges sent.

Secrets never print: a token or API key shows only whether it is set and
where it comes from, and request headers and URLs go through the trace
module's redaction.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from nuh.trace_redact import redact_url

if TYPE_CHECKING:
    from nuh.llm import LLMCallResult
    from nuh.trace import MemoryTrace


# -- Settings --


def settings_lines(llm_config: dict) -> list[str]:
    """The LLM settings this run uses, one ``name: value`` line each, secrets reduced to set / not set."""
    from nuh.llm_cap import parse_can_block
    return _provider_lines(llm_config) + [f"can_block: {str(parse_can_block(llm_config)).lower()}"]


def print_settings(llm_config: dict, context: list[str]) -> None:
    print("LLM settings:")
    for line in settings_lines(llm_config):
        print(f"  {line}")
    if context:
        print(f"  context:      {', '.join(context)}")


def _provider_lines(llm_config: dict) -> list[str]:
    from nuh import llm_providers
    configured = llm_providers._configured_providers(llm_config)
    lines = [f"providers:    {', '.join(n for n, _ in configured) or 'none configured'}"]
    for name, section in configured:
        section = section if isinstance(section, dict) else {}
        default_timeout = llm_providers._TIMEOUT_LOCAL if name == "ollama" else llm_providers._TIMEOUT_REMOTE
        url = section.get("url")
        lines.append(
            f"{name + ':':<13} model={section.get('model', llm_providers._DEFAULT_MODELS.get(name, ''))}"
            f" url={redact_url(str(url)) if url else '(provider default)'}"
            f" timeout={_seconds(section.get('timeout', default_timeout))}{_key_note(name, section)}"
        )
    return lines


def _seconds(raw: object) -> str:
    """A configured timeout as shown; a bad value is shown as is (the provider call reports it)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return f"{raw:g}s"
    return repr(raw)


def _key_note(name: str, section: dict) -> str:
    from nuh import llm_providers
    env = section.get("key_env", llm_providers._DEFAULT_KEY_ENVS.get(name))
    if not env:
        return ""
    return f" key=${env} ({'set' if os.environ.get(str(env)) else 'NOT SET'})"


# -- Results --


def print_bash_result(llm_call: LLMCallResult) -> None:
    if llm_call.too_large:
        print(f"LLM decision: ASK (not sent: {llm_call.decision['reason']})")
        return
    if llm_call.decision is not None:
        said = llm_call.decision.get("decision", "")
        print(f"LLM decision: {said.upper()}")
        print(f"LLM provider: {llm_call.provider} ({llm_call.model})")
        print(f"LLM latency:  {llm_call.latency_ms}ms")
        if llm_call.reasoning:
            print(f"LLM reason:   {llm_call.reasoning}")
        if llm_call.failed:
            _print_errors(llm_call)
        else:
            _print_cap(llm_call.decision, said)
        return
    print("LLM decision: (no providers configured)")


def _print_errors(llm_call: LLMCallResult) -> None:
    for attempt in llm_call.cascade:
        if attempt.error:
            print(f"LLM error:    {attempt.provider}: {attempt.error}")


def _print_cap(decision: dict, said: str) -> None:
    """The decision after ``llm.can_block`` when the cap changed *said*, as the hook applies it."""
    from nuh import llm_cap
    can_block = llm_cap.configured()
    capped = llm_cap.cap(dict(decision), can_block)
    if capped.get("decision") != said:
        print(f"After cap:    {capped['decision'].upper()} (llm.can_block: {str(can_block).lower()})")


def print_write_result(decision: dict) -> None:
    """The LLM part of a write-like decision, from the metadata the hook attaches."""
    meta = decision.get("_meta") or {}
    if meta.get("llm_skip_reason"):
        print(f"LLM step:     skipped, {meta['llm_skip_reason']}")
        return
    if "llm_provider" not in meta and "llm_skipped" not in meta:
        blocked = decision.get("decision") == "block"
        print("LLM review:   not sent (the static checks block first)" if blocked
              else "LLM decision: (no providers responded)")
        return
    verdict = meta.get("llm_decision") or ""
    print(f"LLM decision: {verdict.upper() if verdict else '(no verdict)'}")
    if meta.get("llm_provider"):
        print(f"LLM provider: {meta['llm_provider']} ({meta.get('llm_model', '')})")
        print(f"LLM latency:  {meta.get('llm_latency_ms', 0)}ms")
    if meta.get("llm_capped"):
        print(f"After cap:    {meta['llm_capped'].upper()} (llm.can_block: false)")
    if meta.get("llm_skipped"):
        print(f"LLM skipped:  {meta['llm_skipped']}")
    if meta.get("llm_reasoning"):
        print(f"LLM reason:   {meta['llm_reasoning']}")
    for attempt in meta.get("llm_cascade", []):
        if attempt.get("error"):
            print(f"LLM error:    {attempt['provider']}: {attempt['error']}")


# -- Exchanges (--show-request) --


def print_exchanges(memory: MemoryTrace) -> None:
    """Each attempt's request as sent (auth redacted) and the raw response, from the in-memory trace."""
    files = memory.files
    stems = sorted(name[len("request-"):-len(".json")] for name in files if name.startswith("request-"))
    if not stems:
        print("Request:      none sent")
    for stem in stems:
        request = files[f"request-{stem}.json"]
        print(f"\n--- request {stem}")
        if request.get("sent"):
            print(f"{request['method']} {request['url']} (timeout {request['timeout']}s)")
            for name, value in request["headers"].items():
                print(f"{name}: {value}")
            print(_pretty(request["body"], request["body_raw"]))
            prompt = _prompt_text(request["body"])
            if prompt:
                print(f"\n--- prompt {stem} (the body's text fields, unescaped)\n{prompt}")
        else:
            print("(no HTTP request was made)")
        _print_response(stem, files.get(f"response-{stem}.json") or {})


def _print_response(stem: str, response: dict) -> None:
    print(f"\n--- response {stem}")
    http = response.get("http")
    if http:
        print(f"HTTP {http.get('status')} ({http.get('latency_ms')}ms)")
        print(_pretty(http.get("body"), http.get("body_raw")))
    exc = response.get("exception") or (http or {}).get("error")
    if exc:
        print(f"error: {exc['type']}: {exc['message']}")
    outcome = response.get("outcome") or {}
    if outcome.get("error") and not exc:
        print(f"error: {outcome['error']}")


def _pretty(body: object, raw: object) -> str:
    if body is not None:
        return json.dumps(body, indent=2, ensure_ascii=False)
    return str(raw) if raw else "(empty body)"


def _prompt_text(body: object) -> str:
    """A prompted provider's system and user text from its request body; "" for system 1's."""
    if not isinstance(body, dict):
        return ""
    parts = [f"[{key}]\n{body[key]}" for key in ("system", "instructions") if isinstance(body.get(key), str)]
    for message in body.get("messages") or []:
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            parts.append(f"[{message.get('role', '')}]\n{message['content']}")
    parts += [f"[{key}]\n{body[key]}" for key in ("prompt", "input") if isinstance(body.get(key), str)]
    return "\n\n".join(parts)
