"""The suite stays out of the developer's home (tests/write_guard.py, tests/__init__.py)."""

import os
import subprocess
import sys
import uuid

import pytest

from nuh import config, log, path_names
from tests import write_guard
from tests.hook_env import fake_home


class TestHomeIsolated:
    def test_nuh_resolves_home_to_the_fake_one(self):
        assert write_guard.real_home() != fake_home()
        assert path_names._HOME == fake_home()
        real_nuh = os.path.join(write_guard.real_home(), ".config", "nuh")
        for resolved in (config._GLOBAL_CONFIG, log.LOG_PATH):
            assert not resolved.startswith(real_nuh + os.sep)

    def test_a_subprocess_inherits_the_fake_home(self):
        out = subprocess.run([sys.executable, "-c", "import os; print(os.path.expanduser('~'))"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == fake_home()


class TestStrayWritesCaught:
    def test_write_under_the_real_home_is_recorded(self):
        # A directory that does not exist: the audit event fires, the open fails, nothing lands.
        target = os.path.join(write_guard.real_home(), f"nuh-guard-probe-{uuid.uuid4().hex}", "f")
        with pytest.raises(FileNotFoundError):
            open(target, "w")
        assert write_guard.take() == [f"open {target}"]

    def test_os_level_write_is_recorded(self):
        target = os.path.join(write_guard.real_home(), f"nuh-guard-probe-{uuid.uuid4().hex}", "f")
        with pytest.raises(FileNotFoundError):
            os.open(target, os.O_WRONLY | os.O_CREAT)
        assert write_guard.take() == [f"open {target}"]

    def test_read_is_not_a_write(self):
        target = os.path.join(write_guard.real_home(), f"nuh-guard-probe-{uuid.uuid4().hex}")
        with pytest.raises(FileNotFoundError):
            open(target)
        assert write_guard.take() == []

    def test_write_in_the_repository_is_allowed(self, tmp_path):
        (tmp_path / "f").write_text("x")
        assert write_guard.take() == []
