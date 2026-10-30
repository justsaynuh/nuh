"""install_target: which Python environment pip and uv pip install into."""

import os
import sys

import pytest

from nuh import install_target
from nuh.install_target import SYSTEM, USER, hook_envs as real_hook_envs


def _venv(root) -> str:
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return os.path.realpath(root)


class TestHookEnvs:
    @pytest.fixture(autouse=True)
    def _no_pipx(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPX_HOME", str(tmp_path / "pipx"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def test_venv_interpreter(self, tmp_path, monkeypatch):
        env = _venv(tmp_path / "venv")
        monkeypatch.setattr(sys, "prefix", env)
        assert real_hook_envs() == {env}

    def test_system_interpreter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        assert real_hook_envs() == {SYSTEM}

    def test_pipx_nuh_venv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        pipx = _venv(tmp_path / "pipx" / "venvs" / "nuh")
        assert real_hook_envs() == {SYSTEM, pipx}

    def test_default_pipx_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PIPX_HOME")
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        pipx = _venv(tmp_path / "home" / ".local" / "share" / "pipx" / "venvs" / "nuh")
        assert pipx in real_hook_envs()


class TestProgramEnv:
    def test_symlinked_venv_python_is_the_venv(self, tmp_path):
        env = _venv(tmp_path / "venv")
        os.symlink(os.path.realpath(sys.executable), os.path.join(env, "bin", "python"))
        assert install_target.program_env(os.path.join(env, "bin", "python")) == env

    def test_relative_to_cwd(self, tmp_path, monkeypatch):
        env = _venv(tmp_path / ".venv")
        monkeypatch.chdir(tmp_path)
        assert install_target.program_env(".venv/bin/pip") == env

    def test_outside_a_venv_is_system(self, tmp_path):
        (tmp_path / "bin").mkdir()
        assert install_target.program_env(str(tmp_path / "bin" / "pip")) == SYSTEM

    def test_not_on_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        assert install_target.program_env("pip") is None


class TestPipEnv:
    def test_target_dir(self, tmp_path):
        assert install_target.pip_env("pip", ["install", "-t", str(tmp_path), "x"]) == os.path.realpath(tmp_path)

    def test_user_site(self):
        assert install_target.pip_env("pip", ["install", "--user", "x"]) == USER

    def test_python_option(self, tmp_path):
        env = _venv(tmp_path / "venv")
        assert install_target.pip_env("pip", ["--python", env, "install", "x"]) == env


class TestUvPipEnv:
    @pytest.fixture(autouse=True)
    def _no_active_venv(self, monkeypatch):
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    def test_nearest_dot_venv_upward(self, tmp_path, monkeypatch):
        env = _venv(tmp_path / ".venv")
        (tmp_path / "pkg" / "sub").mkdir(parents=True)
        monkeypatch.chdir(tmp_path / "pkg" / "sub")
        assert install_target.uv_pip_env(["install", "-e", "."]) == env

    def test_active_venv_wins(self, tmp_path, monkeypatch):
        _venv(tmp_path / ".venv")
        active = _venv(tmp_path / "active")
        monkeypatch.setenv("VIRTUAL_ENV", active)
        monkeypatch.chdir(tmp_path)
        assert install_target.uv_pip_env(["install", "x"]) == active

    def test_python_option_beats_the_active_venv(self, tmp_path, monkeypatch):
        chosen = _venv(tmp_path / "chosen")
        monkeypatch.setenv("VIRTUAL_ENV", _venv(tmp_path / "active"))
        assert install_target.uv_pip_env(["install", "--python", f"{chosen}/bin/python", "x"]) == chosen

    def test_version_request_is_unknown(self):
        assert install_target.uv_pip_env(["install", "-p", "3.12", "x"]) is None

    def test_no_environment_found(self, monkeypatch):
        monkeypatch.chdir("/")  # tmp_path sits inside this checkout, which has a .venv
        assert install_target.uv_pip_env(["install", "x"]) is None


class TestIsHookEnv:
    def test_user_site_counts_for_a_system_hook(self, monkeypatch):
        monkeypatch.setattr(install_target, "hook_envs", lambda: frozenset({SYSTEM}))
        assert install_target.is_hook_env(USER)

    def test_user_site_does_not_reach_a_venv_hook(self, tmp_path, monkeypatch):
        env = _venv(tmp_path / "venv")
        monkeypatch.setattr(install_target, "hook_envs", lambda: frozenset({env}))
        assert not install_target.is_hook_env(USER)
        assert install_target.is_hook_env(env)
        assert not install_target.is_hook_env(None)
