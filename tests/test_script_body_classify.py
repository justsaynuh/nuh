"""A clean shell script's commands are classified with the same tables as any command.

The content scan behind "script clean" looks for patterns; a script that
publishes or sources a credential file matched none, so ``./deploy.sh`` was a
static allow. Its commands are now judged like a recipe's lines: an unknown
command rests on the scan, any other ask or block is the script's.
"""

import os

import pytest

from nuh import taxonomy
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


def _script(root: str, name: str, body: str) -> None:
    path = os.path.join(root, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


@pytest.mark.parametrize("command", ["./release.sh", "bash release.sh", "sh ./release.sh"])
def test_body_that_publishes_asks(in_project, command):
    _script(in_project, "release.sh", "#!/bin/bash\nset -e\nnpm publish\n")
    stage = classify_command(command).stages[0]
    assert (stage.action_type, stage.decision, stage.floor) == (taxonomy.NETWORK_WRITE, taxonomy.ASK, "")
    assert "npm publish" in stage.reason


def test_body_sourcing_a_file_through_a_variable_is_judged_on_that_file(in_project):
    _script(in_project, "deploy.sh", '#!/bin/bash\nENV_FILE="$HOME/.config/x/cloudflare.env"\n'
                                     'set -a\n. "$ENV_FILE"\nset +a\necho ready\n')
    result = classify_command("./deploy.sh")
    assert result.final_decision == taxonomy.ASK
    assert "(command: . /" in result.stages[0].reason  # the `.` of the expanded $ENV_FILE is what asks


def test_sourced_script_body_is_classified(in_project):
    _script(in_project, "lib.sh", "npm publish\n")
    assert classify_command("source ./lib.sh").final_decision == taxonomy.ASK


def test_script_calling_itself_asks_without_blocking(in_project):
    _script(in_project, "again.sh", "#!/bin/sh\necho again\n./again.sh\n")
    result = classify_command("./again.sh")
    assert result.final_decision == taxonomy.ASK
    assert "nested too deep" in result.stages[0].reason


def test_unknown_and_allowed_commands_stay_clean(in_project):
    _script(in_project, "build.sh", "#!/bin/sh\necho building\nfrobnicate --all\nls -la\n")
    result = classify_command("./build.sh")
    assert result.final_decision == taxonomy.ALLOW
    assert result.stages[0].reason.startswith("script clean:")


def test_python_script_is_not_read_as_shell(in_project):
    _script(in_project, "tool.sh", "#!/usr/bin/env python3\nprint('npm publish')\n")
    assert classify_command("./tool.sh").final_decision == taxonomy.ALLOW
