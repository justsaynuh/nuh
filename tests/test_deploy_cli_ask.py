"""Deploy and publish commands of platform CLIs are network writes: an ordinary ask, also through a runner.

``npx --yes wrangler@4.151.0 pages deploy …`` stayed package_run (allow)
because the fetched name classified as unknown; the classify table now knows
the deploy subcommands, and a fetched package whose name classifies stricter
than the runner is judged by it. Read-only subcommands keep the runner's allow.
"""

import os

import pytest

from nuh import taxonomy
from nuh.bash import classify_command


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    return project_root


@pytest.mark.parametrize("command", [
    "wrangler deploy",
    "wrangler pages deploy dist --project-name x",
    "wrangler versions upload",
    "wrangler versions deploy",
    "wrangler secret put API_KEY",
    "vercel deploy",
    "vercel --prod",
    "netlify deploy --prod",
    "fly deploy",
    "fly secrets set A=1",
    "firebase deploy",
    "gcloud app deploy",
    "gcloud run deploy svc --source .",
    "heroku releases:rollback v12",
    "railway up",
    "aws s3 sync dist s3://bucket/",
    "aws s3 cp out.zip s3://bucket/out.zip",
    "docker push registry.example/app:1",
])
def test_deploy_is_an_ordinary_network_write_ask(in_project, command):
    stage = classify_command(command).stages[0]
    assert (stage.action_type, stage.decision, stage.floor) == (taxonomy.NETWORK_WRITE, taxonomy.ASK, "")


@pytest.mark.parametrize("command", [
    "npx --yes wrangler@4.151.0 pages deploy dist --project-name x --branch preview",
    "npx wrangler deploy",
    "pnpm dlx wrangler deploy",
    "bunx wrangler deploy",
    "bunx vercel --prod",
    "npx netlify-cli deploy",
    "npx firebase-tools deploy",
    "env CI=1 npx --yes wrangler@4.151.0 deploy",
])
def test_deploy_through_a_runner_asks(in_project, command):
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert result.stages[0].action_type == taxonomy.NETWORK_WRITE


@pytest.mark.parametrize("command", [
    "npx wrangler whoami",
    "npx wrangler pages project list",
    "npx vercel ls",
    "pnpm dlx wrangler whoami",
    "gh release list",
])
def test_read_only_subcommands_do_not_ask(in_project, command):
    assert classify_command(command).final_decision == taxonomy.ALLOW


def test_script_that_deploys_asks(in_project):
    """The incident's script: its body's deploy is judged by the same table."""
    path = os.path.join(in_project, "deploy.sh")
    with open(path, "w") as f:
        f.write("#!/bin/bash\nset -euo pipefail\nnpx --yes wrangler@4.151.0 deploy\n")
    os.chmod(path, 0o755)
    for command in ("./deploy.sh", "bash deploy.sh"):
        stage = classify_command(command).stages[0]
        assert (stage.action_type, stage.decision) == (taxonomy.NETWORK_WRITE, taxonomy.ASK)
