"""Replays of real calls whose LLM block was dropped: an LLM block always counts.

Eight Bash calls from a real decision log (2026-10-15/16) ended as allow
although the LLM said block: the default cap turned the block into an ask
(``max_decision: ask`` then, ``can_block: false`` now), and the hook kept the
static allow instead of that ask. Each test runs the whole hook (hook.main)
against a stub model (tests/llm_stub.py) that says block. The calls of
2026-10-16 went to the decide backend, since removed; the same inputs now go
to a provider, and nuh applies the cap the decide backend used to.

The inputs are trimmed to what the classification needs. Paths under the
real checkout became paths in the test project; script contents were not
logged, so each script is a clean stand-in that keeps the static allow.
"""

import io
import json
import os

import pytest

from nuh import config, hook
from nuh.log_query import outcome
from nuh.log_view import LogView
from nuh.config import _merge_configs
from tests.llm_stub import last_entry, llm_config

STATUS_PROGRAM = (
    "import json,urllib.request\n"
    "s=json.load(urllib.request.urlopen('http://192.0.2.110:8787/api/status',timeout=30))\n"
    "print(sorted(s.keys()))\n"
)
NUH_TEST_SCRIPT = '#!/bin/bash\nnuh test --no-llm "$@" | grep -E "^(Decision|Reason)"\n'


@pytest.fixture
def blocking(stub_model):
    """The stub model, answering block to every request."""
    stub_model.answer("block", "network read from an external address")
    return stub_model


@pytest.fixture
def home(project_root, tmp_path, monkeypatch) -> str:
    """Run in the test project with a temporary HOME."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.chdir(project_root)
    return str(fake)


def _script(project_root: str, rel: str, text: str = NUH_TEST_SCRIPT) -> str:
    path = os.path.join(project_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)
    return path


def _use(section: dict, can_block: bool) -> None:
    config._cached_config = _merge_configs({"llm": {**section, "can_block": can_block}}, {})


def _run(monkeypatch, command: str, tail: str, project_root: str) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "replay",
               "tool_use_id": f"toolu_replay{tail}", "cwd": project_root}
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"]


EXPECTED = {False: "ask", True: "deny"}  # can_block → the decision Claude Code receives


# -- Inline programs (2026-10-15) --

def _provider_cases(root: str) -> dict[str, str]:
    bench_dir = f"{root}/.scratch/bench"
    return {
        # 2026-10-15T19:31:20, filesystem_read
        "dJoBDacjge": (f'cd {bench_dir} && grep -E "^[0-9]{{2}}:[0-9]{{2}}" runs.log | tail -12; '
                       f'python3 -c "\n{STATUS_PROGRAM}" 2>&1 | head -30'),
        # 2026-10-15T21:21:53, lang_exec
        "sBv3bNyXsn": f'python3 -c "\n{STATUS_PROGRAM}"',
        # 2026-10-15T21:22:06, filesystem_read
        "dsGd9mjzFm": (f'cd {root}/.scratch && grep -n "category" corpora/conv.py; '
                       f'ls -la corpora/; python3 -c "\n{STATUS_PROGRAM}"'),
    }


@pytest.mark.parametrize("can_block", [False, True])
@pytest.mark.parametrize("tail", ["dJoBDacjge", "sBv3bNyXsn", "dsGd9mjzFm"])
def test_provider_block_on_inline_code(blocking, home, project_root, hook_log, monkeypatch, tail, can_block):
    """Then a clean inline program was a static allow; now it asks for a verdict, and the block counts."""
    _use(llm_config(blocking.url), can_block)
    out = _run(monkeypatch, _provider_cases(project_root)[tail], tail, project_root)
    assert len(blocking.requests) == 1
    assert out["permissionDecision"] == EXPECTED[can_block]
    said = outcome(last_entry(hook_log))
    assert (said.llm_decision, said.capped) == ("block", "" if can_block else "ask")


@pytest.mark.parametrize("can_block", [False, True])
def test_provider_block_on_a_clean_script(blocking, home, project_root, hook_log, monkeypatch, can_block):
    """The same program as a clean script file stays a static allow; the LLM block still counts."""
    _script(project_root, "probe.py", STATUS_PROGRAM)
    _use(llm_config(blocking.url), can_block)
    out = _run(monkeypatch, "python3 probe.py", "sBv3bNyXsn", project_root)
    assert len(blocking.requests) == 1
    assert out["permissionDecision"] == EXPECTED[can_block]
    said = outcome(last_entry(hook_log))
    assert (said.llm_decision, said.capped) == ("block", "" if can_block else "ask")


# -- Scripts and inline programs (2026-10-16, then reviewed by the decide backend) --

def _script_cases(root: str) -> dict[str, str]:
    nt = f"{root}/.scratch/nt.sh"
    multi = ".scratch/agent6/multi.sh"
    return {
        # 2026-10-16T15:34:47, lang_exec
        "cpDu1wgN2V": f"bash {nt} ':(){{ :|:& }};:'; bash {nt} 'sudo rm -rf /'; bash {nt} 'rm -rf ./*'",
        # 2026-10-16T18:20:46, filesystem_read; trimmed to its inline program (the full call reads
        # ~/.config/nuh, a sensitive path that now blocks statically before any LLM step)
        "78yhDigs45": ('mkdir -p .scratch/cfgprobe && python3 -c "\nimport yaml\n'
                       "c=yaml.safe_load(open('.scratch/cfgprobe/in.yaml'))\n"
                       "yaml.safe_dump(c,open('.scratch/cfgprobe/out.yaml','w'))\""),
        # 2026-10-16T18:51:57, filesystem_write
        "GURibGNPxR": (f"chmod +x {multi} && {multi} 'uv run pytest' 'uv run rm -rf ~' 'npx rm -rf /' "
                       "'pnpm exec rm -rf ~' 'yarn exec git push --force' 'uv tool run rm -rf ~'"),
        # 2026-10-16T23:24:44, filesystem_read; the heredoc trimmed (its /proc/self/environ line now asks statically)
        "1wKubfVT26": (f"head -5 src/types.json; grep -n nuh_admin src/types.json | head -20; "
                       f"cat > .scratch/agent6/p_env.txt <<'EOF'\nenv\nprintenv HOME\nps aux\nEOF\n"
                       f"{multi} .scratch/agent6/p_env.txt"),
        # 2026-10-16T23:33:36, lang_exec
        "nfgvXjDRnw": ('PYTHONPATH=src python3 -c "\nfrom nuh.bash import classify_command\n'
                       "r = classify_command('echo hi')\nprint(r.final_decision)\""),
    }


@pytest.mark.parametrize("can_block", [False, True])
@pytest.mark.parametrize("tail", ["cpDu1wgN2V", "78yhDigs45", "GURibGNPxR", "1wKubfVT26", "nfgvXjDRnw"])
def test_capped_block_on_scripts(blocking, home, project_root, hook_log, monkeypatch, tail, can_block):
    _script(project_root, ".scratch/nt.sh")
    _script(project_root, ".scratch/agent6/multi.sh")
    _use(llm_config(blocking.url), can_block)
    out = _run(monkeypatch, _script_cases(project_root)[tail], tail, project_root)
    assert len(blocking.requests) == 1
    assert out["permissionDecision"] == EXPECTED[can_block]
    said = outcome(last_entry(hook_log))
    assert (said.llm_decision, said.capped) == ("block", "" if can_block else "ask")


def test_the_log_views_show_the_capped_block(blocking, home, project_root, hook_log, monkeypatch):
    _script(project_root, ".scratch/nt.sh")
    _use(llm_config(blocking.url), False)
    _run(monkeypatch, _script_cases(project_root)["cpDu1wgN2V"], "cpDu1wgN2V", project_root)
    entry = last_entry(hook_log)
    assert "ollama:block→ask" in LogView(color=False, width=200).render(entry)
    details = [line.strip() for line in LogView(color=False, width=200, details=True).render(entry).splitlines()]
    assert "2. llm          block" in " ".join(details)
    assert "3. cap          block → ask  (llm.can_block: false)" in details


@pytest.mark.parametrize("can_block", [False, True])
def test_a_model_ask_on_a_static_allow_asks(stub_model, home, project_root, hook_log, monkeypatch, can_block):
    """A model's ask is the LLM's ask, a permission prompt: it raises a static allow (2026-10-18).

    One user's log held 40 of these in three days, from the decide backend,
    logged as ``llm.decision: uncertain`` and allowed.
    """
    _script(project_root, ".scratch/nt.sh")
    stub_model.answer("ask", "voter unsure")
    _use(llm_config(stub_model.url), can_block)
    out = _run(monkeypatch, f"bash {project_root}/.scratch/nt.sh 'ls'", "uncapped", project_root)
    assert out["permissionDecision"] == "ask"
    assert "voter unsure" in out["permissionDecisionReason"]
    entry = last_entry(hook_log)
    said = outcome(entry)
    assert (entry["decision"], said.llm_decision, said.capped) == ("ask", "ask", "")
    assert "ollama→ask" in LogView(color=False, width=200).render(entry)
    details = [line.strip() for line in LogView(color=False, width=200, details=True).render(entry).splitlines()]
    assert "2. llm          ask   voter unsure" in details
    assert "3. escalate     allow → ask  (stricter)" in details
