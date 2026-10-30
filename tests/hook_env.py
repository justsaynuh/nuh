"""The suite's fake HOME, and the environment for tests that run the hook (or nuh) as a subprocess.

A process that resolves ~ to the developer's home reads their real
~/.config/nuh/config.yaml (LLM enabled means live calls to their Ollama) and
appends fake tools to their real nuh.log. tests/__init__.py points HOME at
an empty directory before nuh is imported, so import-time and per-call paths
agree and every subprocess inherits it.

The fake home sits beside the repo: outside /tmp (the "full" profile trusts
/tmp), outside the repo (which would make ~ "inside project"), and on a
developer machine under /home like the real one, so the catastrophic floor
still sees `rm -rf /home` as a delete above ~. Since tests/__init__.py makes
it the whole suite's HOME, ``hook_env`` mostly documents intent; it still
matters for a test that changed HOME itself.
"""

import atexit
import os
import shutil
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOME: str | None = None


def fake_home() -> str:
    """The suite's empty HOME, made on first use beside the repository and removed at exit."""
    global _HOME
    if _HOME is None:
        _HOME = tempfile.mkdtemp(prefix="nuh-test-home-", dir=os.path.dirname(_REPO_ROOT))
        atexit.register(shutil.rmtree, _HOME, True)
    return _HOME


def hook_env(base: dict | None = None) -> dict:
    """A copy of *base* (default os.environ) with HOME pointing at an empty dir."""
    env = dict(os.environ if base is None else base)
    env["HOME"] = fake_home()
    return env
