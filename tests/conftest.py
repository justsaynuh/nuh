"""Shared fixtures for nuh tests."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nuh import config, content, hook, install_target, llm_heal, log, paths, sensitive_paths, taxonomy
from nuh.config import NuhConfig, reset_config
from nuh.context import reset_known_hosts
from tests import llm_stub, system1_helpers, trace_helpers, write_guard

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest's temp dirs out of /tmp.

    The default "full" profile trusts /tmp (and /private/tmp), and on Linux
    pytest's tmp_path lives under /tmp, so every test project would classify
    as a trusted path instead of "inside project". macOS puts tmp_path under
    /var/folders, which is why this never showed upstream. An explicit
    --basetemp still wins.
    """
    if config.option.basetemp is None and _under_trusted_tmp(tempfile.gettempdir()):
        config.option.basetemp = os.path.join(_REPO_ROOT, ".pytest-tmp")


def _under_trusted_tmp(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real == t or real.startswith(t + os.sep) for t in ("/tmp", "/private/tmp"))


@pytest.fixture(autouse=True)
def _no_stray_writes():
    """Fail the test that wrote under the real home outside the repository (tests/write_guard.py)."""
    before = write_guard.take()
    yield
    strays = write_guard.take()
    assert not strays, f"wrote under the real home: {strays[:5]}"
    assert not before, f"wrote under the real home before this test: {before[:5]}"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run for a stray write no test fixture saw (collection, module-scoped fixtures)."""
    strays = write_guard.take()
    if strays:
        session.config.get_terminal_writer().line(f"wrote under the real home: {strays[:5]}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _isolate_user_files(tmp_path_factory, monkeypatch):
    """Never read the developer's ~/.config/nuh/config.yaml or write their nuh.log.

    Tests assert default policy; a real config (allowed action types, trusted
    paths, LLM settings) silently changes those answers. Tests that need a
    global config patch _GLOBAL_CONFIG themselves, which overrides this.
    """
    home = tmp_path_factory.getbasetemp() / "user-config"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(home / "config.yaml"))
    monkeypatch.setattr(log, "_CONFIG_DIR", str(home))
    monkeypatch.setattr(log, "LOG_PATH", str(home / "nuh.log"))
    # The hook's environment would be the test interpreter's venv, which is
    # also the pip on PATH here; tests that need one set install_target.hook_envs.
    monkeypatch.setattr(install_target, "hook_envs", lambda: frozenset({str(home / "hook-env")}))


@pytest.fixture(autouse=True)
def _no_heal_workers(monkeypatch):
    """Start no ``llm.self_heal`` worker (llm_heal.py) unless a test puts the real ``_spawn`` back.

    Provider timeouts are common in the suite, against made-up hosts: a
    worker would probe them for real, from a detached process.
    """
    monkeypatch.setattr(llm_heal, "_spawn", lambda job, directory: None)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset project root, config cache, and sensitive paths between tests for isolation."""
    reset_config()
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = True  # prevent real config from polluting tests
    taxonomy.reset_exec_sinks()
    taxonomy._exec_sinks_merged = True
    taxonomy.reset_decode_commands()
    taxonomy._decode_commands_merged = True
    reset_known_hosts()
    import nuh.context
    nuh.context._known_hosts_merged = True
    content.reset_content_patterns()
    content._content_patterns_merged = True
    yield
    paths.reset_project_root()
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = True
    taxonomy.reset_exec_sinks()
    taxonomy._exec_sinks_merged = True
    taxonomy.reset_decode_commands()
    taxonomy._decode_commands_merged = True
    reset_known_hosts()
    nuh.context._known_hosts_merged = True
    content.reset_content_patterns()
    content._content_patterns_merged = True
    reset_config()
    hook._transcript_path = ""
    hook._agent_id = ""
    hook._tool_use_id = ""
    hook._payload = {}
    hook._agent = ""
    hook._call = None


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Launch in Documents/Club/Minutes 2026 (a space in it) with ``~/Documents: ask`` lifted; a sibling Other.

    The project_lifts_sensitive setup of the test_project_lift_* files.
    """
    monkeypatch.setattr(paths, "_git_root", lambda: None)
    docs = tmp_path / "Documents"
    launch = docs / "Club" / "Minutes 2026"
    for folder in (launch, docs / "Other"):
        folder.mkdir(parents=True)
        (folder / "notes.txt").write_text("hi\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(launch))
    monkeypatch.chdir(launch)
    entries = {str(docs): "ask"}
    config._cached_config = NuhConfig(project_root="git_or_launch_dir", project_lifts_sensitive=True,
                                      sensitive_paths=dict(entries), sensitive_paths_global=dict(entries))
    sensitive_paths.reset_sensitive_paths()
    paths.reset_project_root()
    return launch


@pytest.fixture
def secrets_home(project_root, tmp_path, monkeypatch):
    """Run in a project with src/a.py and src/b.py; a temporary HOME whose ~/.ssh and ~/.aws block.

    The built-in ~/.ssh and ~/.aws rules name the real home, so the config
    names the temporary one's.
    """
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("CDPATH", raising=False)
    os.makedirs(os.path.join(project_root, "src"))
    for name in ("a.py", "b.py"):
        with open(os.path.join(project_root, "src", name), "w") as f:
            f.write("x = 1\n")
    fake = tmp_path / "home"
    for folder, names in ((".ssh", ("id_rsa", "config")), (".aws", ("credentials",))):
        (fake / folder).mkdir(parents=True)
        for name in names:
            (fake / folder / name).write_text("secret\n")
    monkeypatch.setenv("HOME", str(fake))
    with open(config._GLOBAL_CONFIG, "w") as f:
        f.write("sensitive_paths:\n  ~/.ssh: block\n  ~/.aws: block\n")
    config.reset_config()
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = False
    yield str(fake)
    os.remove(config._GLOBAL_CONFIG)


@pytest.fixture
def stub_model(monkeypatch):
    """A stub Ollama model on 127.0.0.1 (tests/llm_stub.py); the agent detected from the payload alone."""
    monkeypatch.delenv("NUH_AGENT", raising=False)
    with llm_stub.serve() as stub:
        yield stub


@pytest.fixture
def hint_isolated(tmp_path):
    """The hint battery's setting (test_hint_battery*.py): fresh config + project root for deterministic results."""
    root = str(tmp_path / "project")
    os.makedirs(root, exist_ok=True)
    paths.set_project_root(root)
    # Block real config — use defaults only
    with patch("nuh.config._GLOBAL_CONFIG", "/tmp/_nuh_test_nonexistent.yaml"):
        reset_config()
        # Clear default trusted_paths so /tmp is "outside project" for tests
        from nuh.config import get_config
        get_config().trusted_paths = []
        yield
    paths.reset_project_root()
    reset_config()


@pytest.fixture
def cli_reset(tmp_path):
    """tmp_path/project and a fresh config: the setting of test_cli.py, test_cli_nuh_test.py and test_cli_claude.py."""
    paths.set_project_root(str(tmp_path / "project"))
    (tmp_path / "project").mkdir()
    reset_config()
    yield
    paths.reset_project_root()
    reset_config()


@pytest.fixture
def trace_isolated(monkeypatch, project_root):
    """The LLM trace tests' setting (test_trace*.py): agent from the payload, a provider key, no decision log."""
    monkeypatch.delenv("NUH_AGENT", raising=False)
    monkeypatch.setenv(trace_helpers.KEY_ENV, trace_helpers.KEY)
    monkeypatch.setattr(hook, "_log_hook_decision", lambda *args: None)


@pytest.fixture
def hook_log(monkeypatch, tmp_path):
    """The decision log, in this test's tmp dir."""
    path = tmp_path / "nuh.log"
    monkeypatch.setattr(log, "LOG_PATH", str(path))
    return path


@pytest.fixture
def project_root(tmp_path):
    """Set project root to a temp dir. Use for context-dependent tests."""
    root = str(tmp_path / "project")
    os.makedirs(root, exist_ok=True)
    paths.set_project_root(root)
    return root


@pytest.fixture
def s1():
    """A stub /v1/systemone server on 127.0.0.1 (tests/system1_helpers.py)."""
    server, _thread = system1_helpers.serve(system1_helpers.S1Handler)
    server.stub = system1_helpers.Server()
    server.stub.url = f"http://127.0.0.1:{server.server_port}"
    yield server.stub
    server.shutdown()
    server.server_close()


@pytest.fixture
def env(project_root, tmp_path, monkeypatch) -> dict:
    """A fake HOME, its own global config dir, the log in tmp, and a clean script file in the project."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    (home / ".config").mkdir()
    monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(home / ".config" / "config.yaml"))
    monkeypatch.setattr(log, "LOG_PATH", str(tmp_path / "nuh.log"))
    Path(project_root, "probe.py").write_text("print(sum(range(10)))\n")
    return {"home": str(home), "log": tmp_path / "nuh.log", "config_dir": home / ".config"}
