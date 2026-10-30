"""LLM trace directory: llm.trace_dir and a project's trace: false, and URL redaction."""

import os
from pathlib import Path

import pytest

from nuh import config, trace_redact
from nuh.config import _merge_configs
from tests.trace_helpers import BASH, invoke, ollama_reply, use_http


pytestmark = pytest.mark.usefixtures("trace_isolated")


# -- Config --


def test_trace_dir_expands_home():
    cfg = _merge_configs({"llm": {"trace_dir": "~/nuh-traces"}}, {})
    assert cfg.llm_trace_dir == os.path.join(os.path.expanduser("~"), "nuh-traces")


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_trace_dir_unset_or_empty_is_off(raw):
    assert _merge_configs({"llm": {"trace_dir": raw}}, {}).llm_trace_dir == ""


@pytest.mark.parametrize("raw", ["relative/traces", 42])
def test_bad_trace_dir_is_off_with_warning(raw, capsys):
    assert _merge_configs({"llm": {"trace_dir": raw}}, {}).llm_trace_dir == ""
    assert "llm.trace_dir" in capsys.readouterr().err


def test_project_config_cannot_enable_tracing():
    assert _merge_configs({}, {"llm": {"trace_dir": "/tmp/stolen"}}).llm_trace_dir == ""
    assert _merge_configs({}, {"trace": True}).llm_trace_dir == ""


def test_project_trace_false_turns_tracing_off():
    on = {"llm": {"trace_dir": "/var/traces"}}
    assert _merge_configs(on, {}).llm_trace_dir == "/var/traces"
    assert _merge_configs(on, {"trace": True}).llm_trace_dir == "/var/traces"
    cfg = _merge_configs(on, {"trace": False})
    assert cfg.llm_trace_dir == "" and cfg.trace_off


def test_project_trace_off_survives_an_llm_override():
    config._cached_config = _merge_configs({"llm": {"trace_dir": "/var/traces"}}, {"trace": False})
    config.apply_llm_override({"trace_dir": "/var/other"})
    assert config.get_config().llm_trace_dir == ""


def test_malformed_project_trace_warns_and_leaves_tracing_on(capsys):
    cfg = _merge_configs({"llm": {"trace_dir": "/var/traces"}}, {"trace": "off please"})
    assert cfg.llm_trace_dir == "/var/traces"
    assert ".nuh.yaml trace" in capsys.readouterr().err


def test_project_opt_out_writes_no_trace_end_to_end(monkeypatch, tmp_path, project_root):
    traces = tmp_path / "traces"
    global_yaml = tmp_path / "global.yaml"
    global_yaml.write_text(
        f"llm:\n  enabled: true\n  trace_dir: {traces}\n  providers: [ollama]\n"
        "  ollama:\n    url: http://localhost:11434/api/chat\n    model: qwen-test\n")
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(global_yaml))
    use_http(monkeypatch, ollama_reply("allow"), ollama_reply("allow"))

    config.reset_config()
    invoke(monkeypatch, BASH)
    assert len(list(traces.iterdir())) == 1

    (Path(project_root) / ".nuh.yaml").write_text("trace: false\n")
    config.reset_config()
    invoke(monkeypatch, BASH)
    assert len(list(traces.iterdir())) == 1


def test_redact_url_hides_credential_query_values():
    url = trace_redact.redact_url("https://api.test/v1?key=abc&model=m")
    assert "abc" not in url
    assert "model=m" in url
