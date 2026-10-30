"""project_root: git_or_launch_dir and project_lifts_sensitive (global-only settings)."""

import io
import json
import os

import pytest

from nuh import config, hook, paths, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig


def _use(**cfg) -> None:
    config._cached_config = NuhConfig(**cfg)
    sensitive_paths.reset_sensitive_paths()
    paths.reset_project_root()


@pytest.fixture
def no_git(monkeypatch):
    """The cwd is outside any git repository (tests run inside the worktree, so fake it)."""
    monkeypatch.setattr(paths, "_git_root", lambda: None)


@pytest.fixture
def docs(tmp_path):
    """A user-sensitive folder with the launch folder Club and a sibling Other in it."""
    root = tmp_path / "Documents"
    for name in ("Club", "Other"):
        (root / name).mkdir(parents=True)
        (root / name / "notes.txt").write_text("hi\n")
    return root


def _lifting(docs, monkeypatch, **extra) -> str:
    """Launch in docs/Club with ``docs: ask`` as the user's own entry and both settings on."""
    launch = str(docs / "Club")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", launch)
    monkeypatch.chdir(launch)
    entries = {str(docs): "ask", **extra}
    _use(project_root="git_or_launch_dir", project_lifts_sensitive=True,
         sensitive_paths=dict(entries), sensitive_paths_global=dict(entries))
    return launch


class TestLaunchDirRoot:
    def test_launch_dir_without_git(self, no_git, docs, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(docs / "Club"))
        _use(project_root="git_or_launch_dir")
        assert paths.get_project_root() == os.path.realpath(docs / "Club")

    def test_default_ignores_launch_dir(self, no_git, docs, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(docs / "Club"))
        _use()
        assert paths.get_project_root() is None

    def test_git_root_wins(self, docs, monkeypatch):
        monkeypatch.setattr(paths, "_git_root", lambda: str(docs))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(docs / "Club"))
        _use(project_root="git_or_launch_dir")
        assert paths.get_project_root() == str(docs)

    @pytest.mark.parametrize("launch", ["/", "~", "home_parent"])
    def test_root_home_and_above_refused(self, no_git, monkeypatch, capsys, launch):
        home = os.path.realpath(os.path.expanduser("~"))
        target = {"/": "/", "~": home, "home_parent": os.path.dirname(home)}[launch]
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", target)
        _use(project_root="git_or_launch_dir")
        assert paths.get_project_root() is None
        assert "contains $HOME" in capsys.readouterr().err

    @pytest.mark.parametrize("launch", ["relative/dir", "/no/such/dir/anywhere"])
    def test_not_an_absolute_directory_refused(self, no_git, monkeypatch, capsys, launch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", launch)
        _use(project_root="git_or_launch_dir")
        assert paths.get_project_root() is None
        assert "not an absolute directory" in capsys.readouterr().err

    def test_boundary_uses_launch_dir(self, no_git, docs, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(docs / "Club"))
        _use(project_root="git_or_launch_dir")
        assert paths.check_project_boundary("Write", str(docs / "Club" / "x.txt")) is None
        assert paths.check_project_boundary("Write", str(docs / "Other" / "x.txt"))["decision"] == taxonomy.ASK


class TestCodexCwd:
    def test_payload_cwd_is_the_launch_dir(self, no_git, docs, monkeypatch):
        monkeypatch.delenv("NUH_AGENT", raising=False)
        # A Claude launch dir inherited by Codex must not count: Codex uses its payload cwd.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(docs / "Other"))
        monkeypatch.setattr(hook, "_log_hook_decision", lambda *args: None)
        _use(project_root="git_or_launch_dir")
        seen = []

        def handler(tool_input: dict) -> dict:
            seen.append(paths.get_project_root())
            return {"decision": taxonomy.ALLOW}

        monkeypatch.setitem(hook.HANDLERS, "Bash", handler)
        payload = {"turn_id": "t", "cwd": str(docs / "Club"), "tool_name": "Bash", "tool_input": {"command": "ls"}}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr("sys.stdout", io.StringIO())
        hook.main()
        assert seen == [os.path.realpath(docs / "Club")]
        assert paths._launch_dir is None


class TestLiftsSensitive:
    def test_read_and_write_inside_allowed(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        assert hook.handle_read({"file_path": f"{launch}/notes.txt"})["decision"] == taxonomy.ALLOW
        written = hook.handle_write({"file_path": f"{launch}/new.txt", "content": "hello\n"})
        assert written["decision"] == taxonomy.ALLOW

    def test_sibling_still_asks(self, no_git, docs, monkeypatch):
        _lifting(docs, monkeypatch)
        result = hook.handle_read({"file_path": str(docs / "Other" / "notes.txt")})
        assert result["decision"] == taxonomy.ASK
        assert "sensitive path" in result["reason"]

    @pytest.mark.parametrize("command", ["cat ./notes.txt", "cat {launch}/notes.txt", "rm {launch}/notes.txt"])
    def test_bash_inside_allowed(self, no_git, docs, monkeypatch, command):
        launch = _lifting(docs, monkeypatch)
        assert classify_command(command.format(launch=launch)).final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", ["cat {other}/notes.txt", "cat ../Other/notes.txt",
                                         "cat {launch}/.*", "cat {launch}/$X/notes.txt"])
    def test_bash_sibling_or_expanding_word_asks(self, no_git, docs, monkeypatch, command):
        launch = _lifting(docs, monkeypatch)
        result = classify_command(command.format(launch=launch, other=docs / "Other"))
        assert result.final_decision == taxonomy.ASK

    def test_symlink_out_of_the_project_asks(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        os.symlink(docs / "Other" / "notes.txt", os.path.join(launch, "link.txt"))
        assert hook.handle_read({"file_path": f"{launch}/link.txt"})["decision"] == taxonomy.ASK

    def test_entry_inside_the_project_still_holds(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch, **{str(docs / "Club" / "secret"): "block"})
        os.mkdir(os.path.join(launch, "secret"))
        result = hook.handle_read({"file_path": f"{launch}/secret/x"})
        assert result["decision"] == taxonomy.BLOCK

    def test_block_entry_containing_the_project_is_lifted(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        _use(project_root="git_or_launch_dir", project_lifts_sensitive=True,
             sensitive_paths={str(docs): "block"}, sensitive_paths_global={str(docs): "block"})
        assert hook.handle_read({"file_path": f"{launch}/notes.txt"})["decision"] == taxonomy.ALLOW
        assert hook.handle_read({"file_path": str(docs / "Other" / "notes.txt")})["decision"] == taxonomy.BLOCK

    def test_project_config_entry_not_lifted(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        # In the merged sensitive_paths only (a .nuh.yaml tightening), not the user's own.
        _use(project_root="git_or_launch_dir", project_lifts_sensitive=True, sensitive_paths={str(docs): "ask"})
        assert hook.handle_read({"file_path": f"{launch}/notes.txt"})["decision"] == taxonomy.ASK

    def test_built_in_credential_inside_project_still_protected(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        os.makedirs(os.path.join(launch, ".ssh"))
        for target in (f"{launch}/.ssh/id_rsa", f"{launch}/.env"):
            result = hook.handle_read({"file_path": target})
            assert result["decision"] == taxonomy.ASK
            assert result["_floor"] == "credential_paths"
        assert classify_command("cat .env").floor_rules() == ["credential_paths"]

    def test_project_at_a_built_in_credential_dir(self, monkeypatch):
        aws = os.path.realpath(os.path.expanduser("~/.aws"))
        home = os.path.realpath(os.path.expanduser("~"))
        _use(project_lifts_sensitive=True, sensitive_paths={home: "ask"}, sensitive_paths_global={home: "ask"})
        paths.set_project_root(aws)
        result = hook.handle_read({"file_path": f"{aws}/credentials"})
        assert result["decision"] == taxonomy.ASK
        assert result["_floor"] == "credential_paths"

    def test_user_override_of_a_built_in_is_not_lifted(self, monkeypatch):
        aws = os.path.realpath(os.path.expanduser("~/.aws"))
        _use(project_lifts_sensitive=True, sensitive_paths={aws: "block"}, sensitive_paths_global={aws: "block"})
        paths.set_project_root(aws)
        assert hook.handle_read({"file_path": f"{aws}/x"})["decision"] == taxonomy.BLOCK

    def test_protected_writes_unaffected(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        result = hook.handle_write({"file_path": f"{launch}/.nuh.yaml", "content": "x: 1\n"})
        assert result["decision"] == taxonomy.ASK
        assert result["_floor"] == "project_config"

    def test_secret_content_unaffected(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        result = hook.handle_write({"file_path": f"{launch}/k.txt",
                                    "content": "-----BEGIN PRIVATE KEY-----\n"})
        assert result["decision"] == taxonomy.ASK
        assert "private key" in result["reason"]

    def test_settings_off_unchanged(self, no_git, docs, monkeypatch):
        launch = str(docs / "Club")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", launch)
        monkeypatch.chdir(launch)
        _use(sensitive_paths={str(docs): "ask"}, sensitive_paths_global={str(docs): "ask"})
        assert paths.get_project_root() is None
        assert hook.handle_read({"file_path": f"{launch}/notes.txt"})["decision"] == taxonomy.ASK
        assert classify_command("cat ./notes.txt").final_decision == taxonomy.ASK

    def test_lift_off_with_launch_root_still_asks(self, no_git, docs, monkeypatch):
        launch = _lifting(docs, monkeypatch)
        _use(project_root="git_or_launch_dir",
             sensitive_paths={str(docs): "ask"}, sensitive_paths_global={str(docs): "ask"})
        assert hook.handle_read({"file_path": f"{launch}/notes.txt"})["decision"] == taxonomy.ASK


class TestGlobalOnly:
    def test_project_config_cannot_set_either(self, capsys):
        cfg = config._merge_configs({}, {"project_root": "git_or_launch_dir", "project_lifts_sensitive": True})
        assert (cfg.project_root, cfg.project_lifts_sensitive) == ("git", False)
        err = capsys.readouterr().err
        assert "project_root is global-only" in err
        assert "project_lifts_sensitive is global-only" in err

    def test_global_config_can(self):
        cfg = config._merge_configs({"project_root": "git_or_launch_dir", "project_lifts_sensitive": True,
                                     "sensitive_paths": {"~/Documents": "ask"}},
                                    {"sensitive_paths": {"~/Work": "block"}})
        assert (cfg.project_root, cfg.project_lifts_sensitive) == ("git_or_launch_dir", True)
        assert cfg.sensitive_paths_global == {"~/Documents": "ask"}

    @pytest.mark.parametrize("key,value,expected", [
        ("project_root", "launch_dir", "must be one of"),
        ("project_lifts_sensitive", "yes", "must be true or false"),
    ])
    def test_bad_values_warn_and_default(self, capsys, key, value, expected):
        cfg = config._merge_configs({key: value}, {})
        assert (cfg.project_root, cfg.project_lifts_sensitive) == ("git", False)
        assert expected in capsys.readouterr().err

    def test_mode_read_from_global_file_before_merge(self, tmp_path, monkeypatch):
        global_file = tmp_path / "config.yaml"
        global_file.write_text("project_root: git_or_launch_dir\n")
        monkeypatch.setattr(config, "_GLOBAL_CONFIG", str(global_file))
        config.reset_config()
        assert config.project_root_mode() == "git_or_launch_dir"
