"""A glob in a path position is checked by every path it can reach (glob_reach, glob_pattern).

The project sits in the suite's fake HOME (tests/hook_env.py), so ``..``
from it is ``~`` and the built-in ~/.ssh and ~/.aws rules apply to the
fixture files there. Each case was confirmed against real bash and zsh
(``cat ../.ss(h)/i*`` only in zsh).
"""

import os
import shutil
import tempfile

import pytest

from nuh import config, glob_options, glob_pattern, paths, sensitive_paths, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig
from tests.hook_env import fake_home

READS = [
    "cat ../.ss*/i*",
    "cat ../.s?h/i*",
    "cat ../.s*/id_*",
    "cat ../.ss*/id_?sa",
    "cat ../.ss[[:alpha:]]/i*",
    "cat ../.s*/**",
    "cat ../.[sa][sw][hs]/*",
    "cat ~+/../.ssh/i*",
    "cat ../.ss(h)/i*",
    "cat ../.ss*/i*; echo done",
    "cat ~/.ss*/i*",
    "cat ../.ss(h|x)/i*",         # the parser reads the | as a pipe: the cut-short group may hold anything
    "cat ../.ss?(h)/i*",          # bash extglob
    "cat ../.ss^x/i*",            # zsh extendedglob
    "cat ../.ss*/*(.)",           # zsh glob qualifier
    "python3 -c \"open('../.ss*/id_rsa')\"",
]


@pytest.fixture
def home_project(monkeypatch):
    """A project in the fake HOME, beside ~/.ssh (id_rsa, config) and ~/.aws (credentials); removed afterwards."""
    home = fake_home()
    made = [os.path.join(home, name) for name in (".ssh", ".aws") if not os.path.exists(os.path.join(home, name))]
    for folder, names in ((".ssh", ("id_rsa", "config")), (".aws", ("credentials",))):
        os.makedirs(os.path.join(home, folder), exist_ok=True)
        for name in names:
            with open(os.path.join(home, folder, name), "w") as f:
                f.write("secret\n")
    project = tempfile.mkdtemp(prefix="proj-", dir=home)
    for folder, name in (("src", "a.py"), ("build", "x.o"), (".", "README.md")):
        os.makedirs(os.path.join(project, folder), exist_ok=True)
        with open(os.path.join(project, folder, name), "w") as f:
            f.write("x\n")
    paths.set_project_root(project)
    monkeypatch.chdir(project)
    yield project
    os.chdir(home)  # out of the project before it goes (monkeypatch restores the directory after)
    shutil.rmtree(project)
    for folder in made:
        if os.path.exists(folder):  # a test may have removed it already
            shutil.rmtree(folder)


def _decide(command: str) -> tuple[str, list[str]]:
    result = classify_command(command)
    return result.final_decision, result.floor_rules()


@pytest.mark.parametrize("command", READS)
def test_a_glob_into_ssh_blocks(home_project, command):
    assert _decide(command) == (taxonomy.BLOCK, [sensitive_paths.CREDENTIAL_RULE])


@pytest.mark.parametrize("command", ["cat ../.ss*/i*", "cat ../.ss*/nothing*", "cat ~/.ss(h)/i*"])
def test_a_glob_that_matches_nothing_yet_still_blocks(home_project, command):
    shutil.rmtree(os.path.join(fake_home(), ".ssh"))
    assert _decide(command) == (taxonomy.BLOCK, [sensitive_paths.CREDENTIAL_RULE])


def test_aws_credentials_through_a_glob_ask(home_project):
    assert _decide("cat ../.a?s/cred*") == (taxonomy.ASK, [sensitive_paths.CREDENTIAL_RULE])


@pytest.mark.parametrize("command,decision,rule", [
    ("echo x > ../.ss*/config", taxonomy.BLOCK, sensitive_paths.CREDENTIAL_RULE),
    ("cp README.md ../.ss*/authorized_keys", taxonomy.BLOCK, sensitive_paths.CREDENTIAL_RULE),
    ("rm -rf ~/.cl*", taxonomy.ASK, "agent_hooks"),
])
def test_protected_writes_through_a_glob(home_project, command, decision, rule):
    found, rules = _decide(command)
    assert found == decision and rule in rules


def test_the_directory_before_a_cd(home_project, tmp_path):
    assert _decide(f"cd {tmp_path} && cat ~-/../.ssh/i*") == (taxonomy.BLOCK, [sensitive_paths.CREDENTIAL_RULE])
    found, rules = _decide("cat ~-/../.ssh/i*")
    assert found == taxonomy.ASK and "cd nuh did not see" in classify_command("cat ~-/../.ssh/i*").reason


@pytest.mark.parametrize("command", [
    "ls src/*.py",
    "cat *.md",
    "rm -rf build/*",
    "cat ../.ss*/config",            # ssh's config is readable, and that is all the glob can reach in ~/.ssh
    "cat ~/.ssh/*.pub",
    "cat '../.ss*/i*'",              # quoted: one literal name
    'cat "../.ss*"/i*',              # only the i* globs, inside a directory literally named .ss*
    "cat src/notes~",
    "ls ~/*",                        # dotglob is off: * reaches no dot-name, so not ~/.ssh
    "du -sh ~/*",
    "ls ../*",
])
def test_counterparts_allow(home_project, command):
    assert _decide(command)[0] == taxonomy.ALLOW


def test_a_user_entry_holds_through_a_glob(home_project):
    downloads = os.path.join(fake_home(), "Downloads")
    os.makedirs(downloads, exist_ok=True)
    try:
        with open(os.path.join(downloads, "a.pdf"), "w") as f:
            f.write("x\n")
        assert _decide("ls ~/Downloads/*.pdf")[0] == taxonomy.ALLOW
        config._cached_config = NuhConfig(sensitive_paths={"~/Downloads": "ask"})
        sensitive_paths.reset_sensitive_paths()
        assert _decide("ls ~/Downloads/*.pdf") == (taxonomy.ASK, [sensitive_paths.SENSITIVE_RULE])
    finally:
        shutil.rmtree(downloads)


def _names(component: str) -> list[str]:
    part = glob_pattern.matcher(component, qualifiers=True)
    assert part is not None and part.regex is not None
    return [name for name in (".ssh", ".ssx", "id_rsa", "notes", "notes~", "a1b", ".", "..")
            if part.regex.fullmatch(name)]


@pytest.mark.parametrize("component,names", [
    (".ss(h)", [".ssh"]),
    (".ss[[:alpha:]]", [".ssh", ".ssx"]),
    ("*(.)", [".ssh", ".ssx", "id_rsa", "notes", "notes~", "a1b", ".", ".."]),   # a qualifier filters
    ("notes~", ["notes", "notes~"]),                  # zsh: notes except nothing; bash: the letter ~
    ("a<1-5>b", ["a1b"]),
    ("!(x)", [".ssh", ".ssx", "id_rsa", "notes", "notes~", "a1b", ".", ".."]),  # bash: anything but x
])
def test_component_patterns(component, names):
    assert _names(component) == names


def test_dots_need_a_component_that_spells_them():
    assert not glob_pattern.matcher("*").dots
    assert glob_pattern.matcher(".*").dots


@pytest.mark.parametrize("command,decision", [
    ("cat ~/.bash*", taxonomy.ASK),           # the dot is spelled out: ~/.bashrc, as cat ~/.bashrc asks
    ("ls ~/.config/*", taxonomy.ASK),         # gh, gcloud, nuh: no dot needed below ~/.config
    ("cat ~/*/id_rsa", taxonomy.ASK),         # dotglob off: only the basename asks, ~/.ssh is not reached
    ("cat ~/*(D)/id_rsa", taxonomy.ASK),      # mid-word (D) is a zsh group, not a qualifier
])
def test_without_dotglob(home_project, command, decision):
    assert _decide(command)[0] == decision


@pytest.mark.parametrize("command", [
    "shopt -s dotglob; cat ~/*/id_rsa",
    "shopt -s nullglob dotglob; cat ~/*/id_rsa",
    "cat ~/*/id_rsa; shopt -s dotglob",       # order is not modelled
    "setopt GLOB_DOTS; cat ~/*/id_rsa",
    "setopt globdots && cat ~/*/id_rsa",
    "unsetopt NO_GLOB_DOTS; cat ~/*/id_rsa",
    "set -o glob_dots; cat ~/*/id_rsa",
    "cat ~/*/id_rsa(D)",                      # the qualifier, ending the word
    "GLOBIGNORE=x; cat ~/*/id_rsa",
    "setopt $O; cat ~/*/id_rsa",              # an option nuh cannot read
    "shopt -s $(cat opts); cat ~/*/id_rsa",
    "emulate sh; cat ~/*/id_rsa",
    "bash -O dotglob -c 'cat ~/*/id_rsa'",
    "zsh -o globdots -c 'cat ~/*/id_rsa'",
])
def test_with_dotglob(home_project, command):
    assert _decide(command) == (taxonomy.BLOCK, [sensitive_paths.CREDENTIAL_RULE])


@pytest.mark.parametrize("command,enables", [
    ("set -euo pipefail; ls", False),
    ("set -- $X; ls", False),                 # positional parameters only
    ("shopt -s nullglob; ls", False),
    ("echo dotglob", True),                   # a mention is enough: nuh does not model what it does
    ("NO_GLOB_DOTS=1", True),
    ("set $OPTS; ls", True),
])
def test_what_may_turn_dotglob_on(command, enables):
    assert glob_options.enables_dotglob(command) is enables


def test_glob_flags_are_unknown(home_project):
    assert glob_pattern.matcher("(#i)x") is None
    result = classify_command("cat ../(#i).SSH/id_rsa")
    assert result.final_decision == taxonomy.ASK
