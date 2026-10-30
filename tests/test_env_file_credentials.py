"""``*.env`` and ``.envrc`` files, and deploy CLIs' token stores, are credential paths like ``.env``.

``set -a && . ~/.config/x/cloudflare.env && set +a`` loads a secret store;
reading or sourcing one asks under ``credential_paths`` whatever the command
or tool. Templates (``.env.example`` and the like) are unchanged.
"""

import os

import pytest

from nuh import hook, taxonomy
from nuh.bash import classify_command


@pytest.fixture
def in_project(project_root, monkeypatch, tmp_path):
    """Run in the project with a fake HOME (outside it) holding ~/.config/x/cloudflare.env."""
    home = tmp_path / "home"
    (home / ".config" / "x").mkdir(parents=True)
    (home / ".config" / "x" / "cloudflare.env").write_text("CLOUDFLARE_API_TOKEN=t\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    return project_root


@pytest.mark.parametrize("command", [
    "cat ~/.config/x/cloudflare.env",
    "grep TOKEN ~/.config/x/cloudflare.env",
    "source ~/.config/x/cloudflare.env",
    "set -a && . ~/.config/x/cloudflare.env && set +a",
    "cat ~/.envrc",
    "cat deploy/prod.env",
])
def test_env_file_is_a_credential_path(in_project, command):
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert "credential_paths" in result.floor_rules()


def test_read_tool(in_project):
    path = os.path.join(os.environ["HOME"], ".config", "x", "cloudflare.env")
    result = hook.handle_read({"file_path": path})
    assert (result["decision"], result["_floor"]) == (taxonomy.ASK, "credential_paths")


def test_script_sourcing_one_through_a_variable_asks_on_it(in_project):
    path = os.path.join(in_project, "deploy.sh")
    with open(path, "w") as f:
        f.write('#!/bin/bash\nENV_FILE="$HOME/.config/x/cloudflare.env"\nset -a\n. "$ENV_FILE"\nset +a\n')
    os.chmod(path, 0o755)
    result = classify_command("./deploy.sh")
    assert (result.final_decision, result.stages[0].floor) == (taxonomy.ASK, "credential_paths")


@pytest.mark.parametrize("command", [
    "cat .env.example",
    "cat config/app.env.example",
    "cat .env.sample",
    "cat .env.template",
    "cat environment.md",
])
def test_templates_and_lookalikes_stay_allowed(in_project, command):
    assert classify_command(command).final_decision == taxonomy.ALLOW
