"""Static floor rule 6: package runners are unwrapped and the command they run is classified."""

import os
import stat

import pytest

from nuh import config, runners, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig


@pytest.fixture
def workspace(project_root, monkeypatch, tmp_path):
    """A project with node_modules/.bin/tsc and a PATH holding only rm, git and pytest."""
    monkeypatch.chdir(project_root)
    bin_dir = tmp_path / "pathbin"
    bin_dir.mkdir()
    for name in ("rm", "git", "pytest", "eslint"):
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))
    local = os.path.join(project_root, "node_modules", ".bin")
    os.makedirs(local)
    open(os.path.join(local, "tsc"), "w").close()
    return project_root


class TestUnwrap:
    @pytest.mark.parametrize("tokens,inner", [
        (["uv", "run", "pytest", "-q"], ["pytest", "-q"]),
        (["uv", "--directory", "x", "run", "--with", "requests", "--frozen", "pytest"], ["pytest"]),
        (["uv", "run", "-p", "3.12", "--", "pytest"], ["pytest"]),
        (["uv", "run", "-m", "pytest", "-q"], ["python", "-m", "pytest", "-q"]),
        (["uv", "run", "script.py", "a"], ["python", "script.py", "a"]),
        (["poetry", "-C", "app", "run", "pytest"], ["pytest"]),
        (["npx", "-c", "echo hi"], ["sh", "-c", "echo hi"]),
        (["npm", "--prefix", "web", "exec", "--", "tsc"], ["tsc"]),
        (["pnpm", "-C", "web", "exec", "tsc"], ["tsc"]),
        (["yarn", "exec", "tsc"], ["tsc"]),
        (["bundle", "exec", "--gemfile", "G", "rake", "test"], ["rake", "test"]),
        (["mise", "exec", "node@20", "--", "node", "-v"], ["node", "-v"]),
        (["mise", "x", "-c", "make test"], ["sh", "-c", "make test"]),
    ])
    def test_inner_command(self, workspace, tokens, inner):
        assert runners.unwrap(tokens).inner == inner

    @pytest.mark.parametrize("tokens", [
        ["npx", "cowsay", "hi"],
        ["uvx", "black", "."],
        ["pipx", "run", "--spec", "x", "black"],
        ["pnpm", "dlx", "create-vite"],
        ["bunx", "cowsay"],
        ["yarn", "dlx", "create-react-app"],
        ["mise", "exec", "node@20"],
    ])
    def test_fetched_package_or_no_command_is_not_unwrapped(self, workspace, tokens):
        assert runners.unwrap(tokens).inner is None

    @pytest.mark.parametrize("tokens,fetched", [
        (["npx", "-y", "rimraf@5", "dist"], ["rimraf", "dist"]),
        (["bunx", "cowsay@1.6", "hi"], ["cowsay", "hi"]),
        (["uvx", "black", "."], ["black", "."]),
    ])
    def test_fetched_package_names_what_it_runs(self, workspace, tokens, fetched):
        assert runners.unwrap(tokens).fetched == fetched

    @pytest.mark.parametrize("tokens,inner", [
        (["npx", "tsc", "--noEmit"], ["tsc", "--noEmit"]),
        (["npx", "rm", "-rf", "x"], ["rm", "-rf", "x"]),
        (["uvx", "git", "status"], ["git", "status"]),
        (["bun", "x", "./tool.sh"], ["./tool.sh"]),
    ])
    def test_local_program_is_unwrapped(self, workspace, tokens, inner):
        assert runners.unwrap(tokens).inner == inner

    @pytest.mark.parametrize("tokens", [["uv", "pip", "list"], ["npm", "install"], ["pnpm", "add", "x"],
                                        ["poetry", "install"], ["bundle", "install"], ["cargo", "run"]])
    def test_not_a_runner(self, tokens):
        assert runners.unwrap(tokens) is None


class TestClassification:
    @pytest.mark.parametrize("command", [
        "uv run rm -rf ~",
        "uv tool run rm -rf ~",
        "npx rm -rf /",
        "npx -c 'rm -rf ~'",
        "pnpm exec rm -rf ~",
        "bunx rm -rf ~",
        "bundle exec rm -rf ~",
        "mise exec -- rm -rf ~",
    ])
    def test_destructive_inner_command_blocks(self, workspace, command):
        assert classify_command(command).final_decision == taxonomy.BLOCK

    @pytest.mark.parametrize("command", [
        "rimraf ~",
        "npx rimraf ~",
        "npx -y rimraf@5 /",
        "npm exec -- rimraf ~",
        "pnpm dlx rimraf $HOME",
        "bunx rimraf ~",
        "yarn dlx rimraf ~",
        "npx del-cli ~",
    ])
    def test_fetched_rimraf_is_a_recursive_delete(self, workspace, command):
        result = classify_command(command)
        assert (result.final_decision, result.floor_rules()) == (taxonomy.BLOCK, ["catastrophic"])

    def test_fetched_rimraf_outside_the_project_asks(self, workspace):
        assert classify_command("npx rimraf /etc/foo").final_decision == taxonomy.ASK

    @pytest.mark.parametrize("command", [
        "poetry run git push --force",
        "yarn exec git reset --hard",
        "uv run curl -X POST https://example.com/x",
    ])
    def test_inner_policy_applies(self, workspace, command):
        assert classify_command(command).final_decision == taxonomy.ASK

    @pytest.mark.parametrize("command", [
        "uv run pytest -q",
        "uv run -m pytest",
        "poetry run pytest",
        "npx tsc --noEmit",
        "npx cowsay hi",
        "npm exec -- eslint .",
        "uvx ruff check",
        "pnpm dlx create-vite",
        "npx rimraf ./dist",
        "rimraf dist",
    ])
    def test_benign_runs_stay_allowed(self, workspace, command):
        assert classify_command(command).final_decision == taxonomy.ALLOW

    def test_a_fetched_name_only_tightens(self, workspace):
        """A fetched package named like an allowed program keeps the runner's own classification."""
        config._cached_config = NuhConfig(actions={"package_run": "ask"})
        assert classify_command("npx cat notes.txt").final_decision == taxonomy.ASK
        assert classify_command("npx rimraf ./dist").stages[0].action_type == taxonomy.PACKAGE_RUN

    def test_unknown_inner_keeps_the_runners_classification(self, workspace):
        # tsc is not in nuh's tables: the runner's package_run stands, as before.
        result = classify_command("npx tsc")
        assert result.stages[0].action_type == taxonomy.PACKAGE_RUN

    def test_user_classify_for_the_runner_wins(self, workspace):
        config._cached_config = NuhConfig(classify_global={"filesystem_read": ["uv run"]})
        result = classify_command("uv run rm -rf build")
        assert result.stages[0].action_type == taxonomy.FILESYSTEM_READ
