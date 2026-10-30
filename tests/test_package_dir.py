"""Static floor rule package_dir: writes to the directory nuh runs from ask; no config switches it off."""

import os

import pytest

from nuh import config, hook, path_names, paths, protected_paths, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.config import _merge_configs

PKG = path_names.PACKAGE_DIR
MODULE = os.path.join(PKG, "bash.py")


def _floor(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


class TestWriteTools:
    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_check_path_floors_every_write_tool(self, tool):
        decision = paths.check_path(tool, MODULE)
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "package_dir"

    def test_write_handler(self):
        decision = hook.handle_write({"file_path": MODULE, "content": "x = 1\n"})
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "package_dir"

    def test_multiedit_handler(self):
        decision = hook.handle_multiedit({"file_path": MODULE, "edits": [{"old_string": "a", "new_string": "b"}]})
        assert decision["_floor"] == "package_dir"

    def test_new_file_in_package(self):
        assert paths.check_path("Write", os.path.join(PKG, "data", "new.json"))["_floor"] == "package_dir"

    @pytest.mark.parametrize("tool", ["Read", "Glob", "Grep"])
    def test_reads_are_not_floored(self, tool):
        decision = paths.check_path(tool, MODULE)
        assert decision is None or decision.get("_floor") != "package_dir"


class TestBash:
    @pytest.mark.parametrize("command", [
        f"echo x > {MODULE}",
        f"echo x >> {MODULE}",
        f"rm {MODULE}",
        f"rm -rf {PKG}",
        f"sed -i s/a/b/ {MODULE}",
        f"cp /tmp/evil.py {MODULE}",
        f"cp /tmp/evil.py {PKG}/",
        f"cp -t {PKG} /tmp/evil.py",
        f"mv /tmp/evil.py {MODULE}",
        f"chmod +w {MODULE}",
        f"tee {MODULE}",
        f"mv {os.path.dirname(PKG)} /tmp/moved",
        f"rm -rf {os.path.dirname(os.path.dirname(PKG))}",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision in (taxonomy.ASK, taxonomy.BLOCK)
        assert "package_dir" in rules

    @pytest.mark.parametrize("command", [
        f"cat {MODULE}",
        f"grep -n def {MODULE}",
        f"ls {PKG}",
        f"cp {MODULE} /tmp/copy.py",
        f"cp -r {PKG} /tmp/nuh-copy",
        f"rsync -a {PKG}/ /tmp/nuh-copy/",
        f"diff {MODULE} /tmp/copy.py",
        f"git -C {os.path.dirname(os.path.dirname(PKG))} checkout main",
        f"git -C {os.path.dirname(os.path.dirname(PKG))} merge main",
    ])
    def test_not_floored(self, command):
        assert "package_dir" not in _floor(command)[1]

    def test_copying_into_home_is_not_a_parent_rename(self):
        assert "package_dir" not in _floor("cp notes.txt ~/")[1]


CHECKOUT = os.path.dirname(os.path.dirname(PKG))


class TestFindDeletesUnderAParent:
    """find descends into nuh's code from a parent root: delete payloads get -delete's parent check."""

    @pytest.mark.parametrize("command", [
        f"find {CHECKOUT} -name '*.py' -delete",
        f"find {CHECKOUT} -name '*.py' -exec rm {{}} +",
        f"find {CHECKOUT} -name '*.py' -execdir rm -f {{}} \\;",
        f"find {CHECKOUT} -type d -name src -exec rm -rf {{}} +",
        f"find {CHECKOUT} -name '*.py' -ok unlink {{}} \\;",
        f"find {CHECKOUT} -name '*.py' -exec shred -u {{}} +",
        f"find {CHECKOUT} -name '*.py' | xargs rm",
        f"find {CHECKOUT} -name '*.py' -print0 | xargs -0 rm -f",
        f"find {CHECKOUT} -name '*.py' | xargs -I{{}} rm {{}}",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "package_dir" in rules

    def test_relative_root_from_the_checkout(self, monkeypatch):
        monkeypatch.chdir(CHECKOUT)
        assert "package_dir" in _floor("find . -name '*.pyc' -exec rm {} +")[1]

    @pytest.mark.parametrize("command", [
        f"find {CHECKOUT} -name '*.py' -exec grep -n def {{}} +",
        f"find {CHECKOUT} -name '*.py' | xargs wc -l",
        "find /tmp/build -name '*.o' -exec rm {} +",
        f"ls {CHECKOUT} | xargs rm",
    ])
    def test_not_floored(self, command):
        assert "package_dir" not in _floor(command)[1]


class TestOtherCopies:
    def test_other_worktree_is_writable(self, tmp_path, monkeypatch):
        live = tmp_path / "live" / "src" / "nuh"
        other = tmp_path / "other" / "src" / "nuh"
        live.mkdir(parents=True)
        other.mkdir(parents=True)
        monkeypatch.setattr(protected_paths, "PACKAGE_DIR", str(live))
        assert sensitive_paths.check_protected_write(str(other / "bash.py")) is None
        assert sensitive_paths.check_protected_write(str(live / "bash.py")).floor == "package_dir"

    def test_symlink_into_package(self, tmp_path):
        link = tmp_path / "innocuous.py"
        os.symlink(MODULE, link)
        assert sensitive_paths.check_protected_write(str(link)).floor == "package_dir"


class TestConfig:
    @pytest.mark.parametrize("value", [False, "no"])
    def test_switching_it_off_warns_and_keeps_the_floor(self, capsys, value):
        """protect_package: false would lower floor package_dir: ignored with a warning (C2)."""
        config._cached_config = _merge_configs({"protect_package": value}, {})
        assert f"protect_package: {value!r} is ignored" in capsys.readouterr().err
        assert sensitive_paths.check_protected_write(MODULE).floor == "package_dir"
        assert _floor(f"rm {MODULE}") == (taxonomy.ASK, ["package_dir"])

    def test_true_is_silent(self, capsys):
        _merge_configs({"protect_package": True}, {})
        assert capsys.readouterr().err == ""
