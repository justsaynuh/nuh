"""env_read: environment dumps, credential variables and secret-manager reads are a floored ask."""

import pytest

from nuh import bash_review, config, env_read, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig


@pytest.mark.parametrize("command", [
    "env",
    "env -0",
    "env | grep TOKEN",
    "printenv",
    "printenv HOME",
    "set",
    "export",
    "export -p",
    "declare -p",
    "declare -p FOO",
    "typeset",
    "readonly -p",
    "ps eww",
    "ps auxe",
    "ps -E",
    "ps -p 1 e",
    "echo $AWS_SECRET_ACCESS_KEY",
    "echo ${GITHUB_TOKEN}",
    "printf '%s' \"$MY_SERVICE_API_KEY\"",
    "echo $DEEPSEEK_AUTH_TOKEN",
    "cat /proc/self/environ",
    "vault read secret/db",
    "vault kv get -field=password secret/x",
    "aws --profile prod secretsmanager get-secret-value --secret-id x",
    "aws ssm get-parameter --name x --with-decryption",
    "gcloud --project p secrets versions access latest --secret=s",
    "az keyvault secret show --name x --vault-name v",
    "kubectl get secret db -o yaml",
    "kubectl -n prod get secrets",
    "kubectl describe secret/tls",
    "kubectl get configmap,secret",
    "pass show email",
    "pass email/work",
    "op read op://vault/item/field",
    "op item get Login",
    "bw get password github",
    "doppler secrets download --no-file",
    "infisical secrets",
    "chamber read app key",
    "sops -d secrets.enc.yaml",
    "sops decrypt secrets.enc.yaml",
    "heroku config",
    "heroku config:get DATABASE_URL",
])
def test_floored_ask(command):
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert "env_read" in result.floor_rules()
    assert bash_review.route(result, "decide").kind != bash_review.ASK


@pytest.mark.parametrize("command", [
    "env FOO=bar ls",
    "env -i PATH=/usr/bin ls",
    "set -e",
    "set -o pipefail",
    "export FOO=bar",
    "declare -a arr",
    "ps aux",
    "ps -ef",
    "ps -o user,pid,comm",
    "ps -u alice",
    "ps U alice",
    "echo $HOME",
    "echo $PATH",
    "echo TOKEN",
    "kubectl get pods",
    "aws ssm get-parameter --name x",
    "aws s3 ls",
    "pass",
    "pass ls",
    "pass generate site 20",
    "sops -e x.yaml",
    "cat /proc/self/status",
])
def test_not_env_read(command):
    result = classify_command(command)
    assert all(sr.action_type != taxonomy.ENV_READ for sr in result.all_stages())


@pytest.mark.parametrize("policy", ["allow", "context"])
def test_config_cannot_lower_it(capsys, policy):
    """actions.env_read below ask warns and is dropped; the floor holds (C2)."""
    config._cached_config = config._merge_configs({"actions": {"env_read": policy}}, {})
    assert "actions.env_read" in capsys.readouterr().err
    result = classify_command("printenv")
    assert (result.final_decision, result.floor_rules()) == (taxonomy.ASK, ["env_read"])


def test_floor_holds_even_if_a_lowering_reaches_the_classifier():
    config._cached_config = NuhConfig(actions={"env_read": "allow"})  # bypasses the config load's check
    result = classify_command("printenv")
    assert (result.final_decision, result.floor_rules()) == (taxonomy.ASK, ["env_read"])


def test_user_can_tighten_it():
    config._cached_config = config._merge_configs({"actions": {"env_read": "block"}}, {})
    assert classify_command("printenv").final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("cfg", [
    {"classify": {"filesystem_read": ["printenv", "echo", "vault read"]}},
    {"profile": "none"},
    {"profile": "minimal", "actions": {"unknown": "allow"}},
])
@pytest.mark.parametrize("command", ["printenv", "echo $GITHUB_TOKEN", "vault read secret/x"])
def test_classify_or_profile_cannot_lower_it(cfg, command):
    config._cached_config = config._merge_configs(cfg, {})
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert "env_read" in result.floor_rules()


def test_human_only_false_lets_the_llm_see_it():
    config._cached_config = NuhConfig(human_only={"env_read": False})
    assert bash_review.route(classify_command("printenv"), "decide").kind == bash_review.ASK


def test_listed_as_a_type():
    assert taxonomy.validate_action_type("env_read") == (True, [])
    assert taxonomy.get_policy("env_read") == taxonomy.ASK


@pytest.mark.parametrize("name,secret", [("GITHUB_TOKEN", True), ("DATABASE_URL", True), ("PGPASSWORD", True),
                                         ("FOO_SECRET_KEY", True), ("HOME", False), ("SSH_AUTH_SOCK", False),
                                         ("TOKENIZER_PATH", False)])
def test_credential_names(name, secret):
    assert env_read.is_credential_name(name) == secret
