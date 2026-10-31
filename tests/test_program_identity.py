"""Static floor rule 6: a program spelled with a slash is that file, not the tool its basename names."""

import os
import pathlib
import stat

import pytest

from nuh import config, program_identity, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig

PIP_WRAPPER = (
    "#!/work/.venv/bin/python3\n"
    "# -*- coding: utf-8 -*-\n"
    "import re\n"
    "import sys\n"
    "from _pytest.config import _console_main\n"
    "if __name__ == '__main__':\n"
    "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
    "    sys.exit(_console_main())\n"
)
UV_WRAPPER = (
    "#!/work/.venv/bin/python\n"
    "# -*- coding: utf-8 -*-\n"
    "import sys\n"
    "from ruff.__main__ import find_ruff_bin\n"
    'if __name__ == "__main__":\n'
    '    if sys.argv[0].endswith("-script.pyw"):\n'
    "        sys.argv[0] = sys.argv[0][:-11]\n"
    '    elif sys.argv[0].endswith(".exe"):\n'
    "        sys.argv[0] = sys.argv[0][:-4]\n"
    "    sys.exit(find_ruff_bin())\n"
)


def _executable(path, text: str | bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    return pathlib.Path(project_root)


class TestPathSpelledProgramIsAFile:
    def test_dot_slash_git_is_a_script(self, in_project):
        _executable(in_project / "git",
                    "#!/bin/sh\necho hello\n")
        result = classify_command("./git status")
        assert result.stages[0].action_type == taxonomy.LANG_EXEC
        assert result.stages[0].reason.startswith("script clean:")

    def test_dot_slash_git_with_destructive_content_is_not_allowed(self, in_project):
        _executable(in_project / "git", "#!/bin/sh\nrm -rf ~/\n")
        assert classify_command("./git status").final_decision != taxonomy.ALLOW

    def test_missing_file_asks(self, in_project):
        result = classify_command("bin/git status")
        assert result.final_decision == taxonomy.ASK
        assert "script not found" in result.reason

    def test_interpreter_name_runs_the_file_itself(self, in_project):
        _executable(in_project / "tools" / "python", "#!/bin/sh\nrm -rf ~/\n")
        result = classify_command("tools/python harmless.py")
        assert result.final_decision != taxonomy.ALLOW
        assert "rm -rf" in result.reason  # tools/python's own content, not harmless.py's

    def test_compiled_program_outside_a_venv_asks(self, in_project):
        _executable(in_project / "build" / "app", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
        result = classify_command("./build/app --run")
        assert result.final_decision == taxonomy.ASK
        assert "not a script nuh can inspect" in result.reason

    def test_nuh_admin_still_named(self, in_project):
        assert taxonomy.classify_tokens(["./nuh", "allow", "lang_exec"]) == taxonomy.NUH_ADMIN

    def test_profile_none_keeps_basename(self):
        assert taxonomy.classify_tokens(["./git", "status"], profile="none") != taxonomy.LANG_EXEC


class TestToolIdentityKept:
    def test_standard_directory(self):
        assert taxonomy.classify_tokens(["/usr/bin/git", "status"],
                                        builtin_table=taxonomy.get_builtin_table()) == taxonomy.GIT_SAFE

    def test_same_file_as_path_lookup(self, tmp_path, monkeypatch):
        tool = _executable(tmp_path / "bin" / "mytool", "#!/bin/sh\n")
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        assert program_identity.is_tool(tool)
        other = _executable(tmp_path / "elsewhere" / "mytool", "#!/bin/sh\n")
        assert not program_identity.is_tool(other)

    def test_symlink_into_standard_directory(self, tmp_path, monkeypatch):
        # An empty PATH, so the path-lookup rule cannot name either link a tool: only where it points decides.
        # (A CI Python under /opt/hostedtoolcache is on PATH, which made a sys.executable link a tool there.)
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        into_bin = tmp_path / "sh"
        into_bin.symlink_to(os.path.realpath("/bin/sh"))
        assert program_identity.is_tool(str(into_bin))
        elsewhere = tmp_path / "mytool"
        elsewhere.symlink_to(_executable(tmp_path / "opt" / "mytool", "#!/bin/sh\n"))
        assert not program_identity.is_tool(str(elsewhere))

    def test_user_classified_path(self, tmp_path):
        tool = _executable(tmp_path / "skills" / "bin" / "session-transcript", "#!/bin/sh\n")
        config._cached_config = NuhConfig(classify_global={"filesystem_read": [tool]})
        assert program_identity.is_tool(tool)
        assert taxonomy.classify_tokens([tool, "x"], global_table=taxonomy.build_user_table(
            config._cached_config.classify_global)) == taxonomy.FILESYSTEM_READ

    def test_not_path_spelled(self):
        assert program_identity.is_tool("git")


class TestVenvEntryPoints:
    @pytest.fixture
    def venv(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        return tmp_path / ".venv" / "bin"

    @pytest.mark.parametrize("wrapper", [PIP_WRAPPER, UV_WRAPPER])
    def test_console_wrapper_is_the_tool(self, venv, wrapper):
        assert program_identity.is_tool(_executable(venv / "pytest", wrapper))

    def test_wrapper_runs_as_its_tool(self, venv):
        path = _executable(venv / "pytest", PIP_WRAPPER)
        assert taxonomy.classify_tokens([path, "tests/"], builtin_table=taxonomy.get_builtin_table()) \
            == taxonomy.PACKAGE_RUN

    def test_compiled_entry_point_is_the_tool(self, venv):
        assert program_identity.is_tool(_executable(venv / "ruff", b"\x7fELF\x02\x01\x01" + b"\x00" * 32))

    @pytest.mark.parametrize("text", [
        "#!/bin/sh\nrm -rf ~\n",
        PIP_WRAPPER.replace("sys.exit(_console_main())", "import os; os.system('id')\n    sys.exit(_console_main())"),
        PIP_WRAPPER.replace("re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])", "__import__('os').getcwd()"),
        PIP_WRAPPER + "print('extra')\n",
    ])
    def test_hand_written_script_is_a_script(self, venv, text):
        assert not program_identity.is_tool(_executable(venv / "git", text))

    def test_bin_without_pyvenv_cfg_is_not_a_venv(self, tmp_path):
        assert not program_identity.is_tool(_executable(tmp_path / "bin" / "pytest", PIP_WRAPPER))
