"""Static floor rule group 1: self-protection (nuh_admin, project_config, agent_hooks, unguarded_launch)."""

import os
import shlex

import pytest

from nuh import config, hook, install_target, paths, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.cli_parser import build_parser
from nuh.config import NuhConfig

# `nuh <command> -h|--help` on commands that otherwise change nuh: argparse prints the help, nothing runs.
_HELP_FORMS = ["nuh trust --help", "nuh allow -h", "nuh system1 export --help", "nuh trace --prune --help",
               "nuh install --help", "nuh classify --project -h", "nuh forget lang_exec --help", "nuh -h trust"]


def _floor(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


class TestNuhAdmin:
    @pytest.mark.parametrize("command", [
        "nuh allow git_history_rewrite",
        "nuh forget git_history_rewrite",
        "nuh deny lang_exec",
        "nuh classify foo git_safe",
        "nuh trust /",
        "nuh trust example.com.",
        "nuh classify 'foo -h' filesystem_read",  # the -h is inside PREFIX, a value
        "nuh trust -- -h",  # after --, -h is TARGET: a host named -h
        "nuh -- trust -h",
        "nuh trace --prune -- --help",
        "nuh allow-path ~/.aws",
        "nuh install",
        "nuh install --completion-only",  # writes the zsh completion only, still through nuh install
        "nuh install --no-completion",
        "nuh uninstall",
        "nuh update",
        "nuh update --no-completion",
        "python3 -m nuh allow lang_exec",
        "pip uninstall -y nuh",
        "pip3 install nuh==0.1",
        "python -m pip install 'nuh<0.5'",
        "pipx uninstall nuh",
        "pipx uninstall-all",
        "pipx reinstall nuh",
        "pipx runpip nuh install requests",
        "uv tool uninstall nuh",
        "uv pip uninstall nuh",
        "bash -c 'nuh forget lang_exec'",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert rules == ["nuh_admin"]

    @pytest.mark.parametrize("command", [
        "nuh test 'rm -rf /tmp/x'",
        "nuh log",
        "nuh types",
        "nuh status",
        "nuh config show",
        "pip uninstall requests",
        "pipx install black",
    ])
    def test_read_only_or_other_packages_not_admin(self, command):
        assert "nuh_admin" not in _floor(command)[1]

    def test_read_only_subcommands_allowed(self):
        assert classify_command("nuh log").final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        "nuh test 'rm -rf /tmp/x'", "nuh config show", "nuh config path", "nuh log -v", "nuh tail",
        "nuh tail -n 5", "nuh trace", "nuh trace --prune --dry-run", "nuh system1 check",
        "nuh system1 export --stdout", "nuh status", "nuh types", "nuh heal-log", "nuh heal-log -n 5 --json",
        "nuh completion zsh",
        *_HELP_FORMS,
    ])
    def test_every_read_only_subcommand_is_a_read(self, command):
        result = classify_command(command)
        assert [s.action_type for s in result.stages] == [taxonomy.FILESYSTEM_READ]
        assert result.final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", _HELP_FORMS)
    def test_a_help_form_only_prints_help(self, command, capsys):
        """The premise of the read: argparse prints the help and exits 0 before any handler runs."""
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(shlex.split(command)[1:])
        assert exc.value.code == 0
        assert capsys.readouterr().out.startswith("usage: nuh")

    def test_after_double_dash_h_is_an_operand(self):
        assert build_parser().parse_args(["trust", "--", "-h"]).target == "-h"

    def test_global_classify_entry_does_not_reclassify(self):
        config._cached_config = NuhConfig(classify_global={"package_run": ["pipx uninstall"]})
        assert _floor("pipx uninstall nuh") == (taxonomy.ASK, ["nuh_admin"])

    @pytest.mark.parametrize("policy", ["allow", "context"])
    def test_config_cannot_lower_it(self, capsys, policy):
        """actions.nuh_admin below ask warns and is dropped; the floor holds (C2)."""
        config._cached_config = config._merge_configs({"actions": {"nuh_admin": policy}}, {})
        assert "actions.nuh_admin" in capsys.readouterr().err
        assert _floor("nuh allow lang_exec") == (taxonomy.ASK, ["nuh_admin"])

    def test_floor_holds_even_if_a_lowering_reaches_the_classifier(self):
        config._cached_config = NuhConfig(actions={"nuh_admin": "allow"})  # bypasses the config load's check
        assert _floor("nuh allow lang_exec") == (taxonomy.ASK, ["nuh_admin"])

    def test_nuh_test_config_override_cannot_lower_it(self, capsys):
        config.apply_override({"actions": {"nuh_admin": "allow"}})
        assert "actions.nuh_admin" in capsys.readouterr().err
        assert _floor("nuh allow lang_exec") == (taxonomy.ASK, ["nuh_admin"])

    def test_user_can_tighten_it(self):
        config._cached_config = config._merge_configs({"actions": {"nuh_admin": "block"}}, {})
        assert _floor("nuh allow lang_exec") == (taxonomy.BLOCK, ["nuh_admin"])

    @pytest.mark.parametrize("command", [
        "pipx install --force --editable .",
        "pipx install .",
        "pipx install -e '.[dev]'",
        "pip install -e .",
        "pip install --editable=.",
        "pip3 install .",
        "python3 -m pip install -e .",
        "uv pip install -e .",
        "uv tool install --force -e .",
        "pipx install ../checkout",
        "pipx install file://../checkout",
        "pip install dist/nuh-0.9.0-py3-none-any.whl",
        "pipx inject nuh black",
    ])
    def test_reinstall_from_nuh_source_tree(self, tmp_path, monkeypatch, command):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "pyproject.toml").write_text('[build-system]\nrequires = []\n\n[project]\nname = "nuh"\n')
        work = tmp_path / "work"
        work.mkdir()
        (work / "pyproject.toml").write_text('[project]\nname = "nuh"\nversion = "1"\n')
        monkeypatch.chdir(work)
        # A user's own config classifies pipx install; nuh_admin runs before it.
        config._cached_config = NuhConfig(classify_global={"package_install": ["pipx install", "pipx inject"]})
        assert _floor(command) == (taxonomy.ASK, ["nuh_admin"])

    @pytest.mark.parametrize("command", ["pipx install -e .", "pip install -e .", "pip install ./sub"])
    def test_install_of_another_local_project_is_not_admin(self, tmp_path, monkeypatch, command):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "nuh-plugin"\n')
        (tmp_path / "sub").mkdir()
        monkeypatch.chdir(tmp_path)
        assert "nuh_admin" not in _floor(command)[1]

    def test_directory_without_pyproject_is_not_admin(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert "nuh_admin" not in _floor("pip install -e .")[1]

    @pytest.mark.parametrize("text,name", [
        ('[project]\nname = "nuh"\n', "nuh"),
        ("[tool.x]\nname = 'other'\n[project]\nversion = '1'\nname = 'nuh'\n", "nuh"),
        ('[project]\nversion = "1"\n[tool.poetry]\nname = "nuh"\n', ""),
        ('name = "nuh"\n', ""),
    ])
    def test_python_310_fallback_reads_only_the_project_table(self, text, name):
        from nuh import self_protect
        assert self_protect._project_name_by_regex(text) == name

    def test_broken_toml_still_read_narrowly(self, tmp_path):
        from nuh import self_protect
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "nuh"\nbroken = \n')
        assert self_protect.project_name(str(tmp_path)) == "nuh"

    def test_listed_as_a_type(self):
        assert taxonomy.validate_action_type("nuh_admin") == (True, [])
        assert taxonomy.get_policy("nuh_admin") == taxonomy.ASK


class TestProjectConfig:
    def test_write_tool(self, project_root):
        decision = hook.handle_write({"file_path": os.path.join(project_root, ".nuh.yaml"), "content": "x"})
        assert decision["decision"] == taxonomy.ASK
        assert decision["_floor"] == "project_config"

    def test_write_in_subdirectory_too(self, project_root):
        decision = paths.check_path("Edit", os.path.join(project_root, "sub", ".nuh.yaml"))
        assert decision["_floor"] == "project_config"

    def test_read_is_not_floored(self, project_root):
        assert paths.check_path("Read", os.path.join(project_root, ".nuh.yaml")) is None

    @pytest.mark.parametrize("command", [
        "echo 'classify: {}' > .nuh.yaml",
        "sed -i s/ask/allow/ .nuh.yaml",
        "cp other.yaml ./.nuh.yaml",
        "rm .nuh.yaml",
    ])
    def test_bash_writes(self, project_root, monkeypatch, command):
        monkeypatch.chdir(project_root)
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert "project_config" in rules

    def test_project_classify_cannot_loosen_an_unknown_command(self):
        config._cached_config = NuhConfig(classify_project={"filesystem_read": ["frobnicate"]})
        assert classify_command("frobnicate --all").final_decision == taxonomy.ASK


def _fake_env(root) -> str:
    """A venv-shaped directory whose bin holds empty pip/python programs."""
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    for name in ("pip", "pip3", "python", "python3"):
        program = root / "bin" / name
        program.write_text("")
        program.chmod(0o755)
    return os.path.realpath(root)


class TestInstallIntoTheHookEnvironment:
    """Any package installed where the hook runs is nuh_admin: a .pth file runs at every hook start."""

    @pytest.fixture
    def hook_env(self, tmp_path, monkeypatch):
        env = _fake_env(tmp_path / "pipx" / "venvs" / "nuh")
        monkeypatch.setattr(install_target, "hook_envs", lambda: frozenset({env}))
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        project = tmp_path / "project"
        project.mkdir()
        _fake_env(project / ".venv")
        monkeypatch.chdir(project)
        return env

    @pytest.mark.parametrize("command", [
        "{env}/bin/python -m pip install requests",
        "{env}/bin/pip install requests",
        "{env}/bin/python3 -m pip uninstall -y black",
        "uv pip install --python {env}/bin/python requests",
        "uv pip install -p {env} requests",
        "pip install --python {env}/bin/python requests",
        "pipx runpip nuh install requests",
        "pipx inject nuh black",
        "pipx inject nuh --include-apps httpie",
    ])
    def test_floored_ask(self, hook_env, command):
        assert _floor(command.format(env=hook_env)) == (taxonomy.ASK, ["nuh_admin"])

    def test_reason(self, hook_env):
        reason = classify_command(f"{hook_env}/bin/python -m pip install requests").reason
        assert "into the environment nuh runs from" in reason

    def test_pip_on_path_from_the_hook_env(self, hook_env, monkeypatch):
        monkeypatch.setenv("PATH", os.path.join(hook_env, "bin"))
        assert _floor("pip install requests") == (taxonomy.ASK, ["nuh_admin"])

    def test_uv_pip_with_the_hook_env_active(self, hook_env, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", hook_env)
        assert _floor("uv pip install requests") == (taxonomy.ASK, ["nuh_admin"])

    @pytest.mark.parametrize("command", [
        ".venv/bin/pip install requests",
        ".venv/bin/python -m pip install requests",
        "uv pip install requests",
        "pip install --target ./vendor requests",
        "pipx inject black isort",
        "pipx runpip black install isort",
    ])
    def test_other_environments_unchanged(self, hook_env, command):
        assert "nuh_admin" not in _floor(command)[1]

    @pytest.mark.parametrize("command", [
        ".venv/bin/pip install -e .",
        "uv pip install nuh",
        "pip install nuh==0.1",
    ])
    def test_nuh_itself_still_counts_in_any_environment(self, hook_env, command):
        with open("pyproject.toml", "w") as f:  # the project is a nuh checkout
            f.write('[project]\nname = "nuh"\n')
        assert _floor(command) == (taxonomy.ASK, ["nuh_admin"])


HOOK = "~/.claude/hooks/nuh_guard.py"


class TestAgentHooks:
    @pytest.mark.parametrize("command", [
        f"rm -f {HOOK}",
        f"mv {HOOK} /tmp/x",
        f"chmod u+w {HOOK}",
        f"echo x > {HOOK}",
        "mv ~/.claude ~/.claude.bak",
        "rm -rf ~/.codex",
        "sed -i s/x/y/ ~/.claude/settings.json",
        "cp evil.json ~/.claude/settings.local.json",
        "tee ~/.codex/hooks.json",
        "echo x >> ~/.codex/config.toml",
        "sed -i s/x/y/ ~/.config/nuh/config.yaml",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert rules == ["agent_hooks"]

    def test_read_of_settings_is_only_a_sensitive_path(self):
        assert _floor("cat ~/.claude/settings.json")[1] == ["sensitive_paths"]

    @pytest.mark.parametrize("command", [
        "find ~/.claude -name '*.json' -exec rm {} +",
        "find ~/.config -name '*.yaml' -execdir rm {} \\;",
        "find ~/.claude -name '*.json' | xargs rm -f",
    ])
    def test_find_delete_payload_from_a_parent(self, command):
        assert _floor(command) == (taxonomy.ASK, ["agent_hooks"])

    def test_copy_into_home_is_not_a_parent_rename(self):
        assert "agent_hooks" not in _floor("cp notes.txt ~/")[1]

    def test_write_tool_settings(self):
        decision = paths.check_path("Write", "~/.claude/settings.json")
        assert decision["_floor"] == "agent_hooks"

    def test_write_tool_hook_still_blocks(self):
        assert paths.check_path("Write", HOOK)["decision"] == taxonomy.BLOCK

    def test_symlink_to_protected_file(self, tmp_path):
        link = tmp_path / "innocuous.json"
        os.symlink(os.path.expanduser("~/.claude/settings.json"), link)
        assert sensitive_paths.check_protected_write(str(link)).floor == "agent_hooks"


class TestUnguardedLaunch:
    @pytest.mark.parametrize("command", [
        "claude --dangerously-skip-permissions -p hi",
        "claude --bare -p hi",
        "claude --permission-mode bypassPermissions -p hi",
        "claude --settings '{\"disableAllHooks\": true}' -p hi",
        "claude --setting-sources project,local -p hi",
        "CLAUDE_CONFIG_DIR=/tmp/c claude -p hi",
        "env CLAUDE_CONFIG_DIR=/tmp/c claude",
        "export CODEX_HOME=/tmp/c",
        "codex --dangerously-bypass-approvals-and-sandbox exec hi",
        "codex --disable hooks exec hi",
        "codex -c features.hooks=false exec hi",
        "codex -c 'features={hooks=false}' exec hi",
        "nohup claude --dangerously-skip-permissions -p hi",
    ])
    def test_floored_ask(self, command):
        decision, rules = _floor(command)
        assert decision == taxonomy.ASK
        assert rules == ["unguarded_launch"]

    @pytest.mark.parametrize("command", [
        "claude --help",
        "claude --setting-sources user,project -p hi",
        "codex -c features.hooks=true exec hi",
        "CLAUDE_CONFIG_DIR=~/.claude claude -p hi",
    ])
    def test_not_floored(self, command):
        assert "unguarded_launch" not in _floor(command)[1]
