"""Shell state through a command: cd tracking and variables (parser bug (e))."""

import os

import pytest

from nuh import config, sensitive_paths, shell_state, shell_walk
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages


@pytest.fixture
def in_project(project_root, monkeypatch):
    """Run in the project root, with a `sub` directory and the CDPATH lookup off."""
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("CDPATH", raising=False)
    os.makedirs(os.path.join(project_root, "sub"))
    return project_root


@pytest.fixture
def home_documents(in_project, tmp_path, monkeypatch):
    """A temporary HOME whose ~/Documents is sensitive (ask) in the global config."""
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    with open(config._GLOBAL_CONFIG, "w") as f:
        f.write("sensitive_paths:\n  ~/Documents: ask\n")
    config.reset_config()
    sensitive_paths.reset_sensitive_paths()
    sensitive_paths._sensitive_paths_merged = False
    yield str(home)
    os.remove(config._GLOBAL_CONFIG)


def decision(cmd: str) -> str:
    return classify_command(cmd).final_decision


def cwd_of(cmd: str, index: int) -> str | None:
    stages = _parse_stages(cmd)
    return shell_walk.walk(stages, shell_state.State(cwd=os.getcwd()))[index].state.cwd


class TestCdTracking:
    def test_home_then_relative_delete_targets_home(self, home_documents):
        assert decision("rm -rf Documents") == "allow"
        r = classify_command("cd ~ && rm -rf Documents")
        assert r.final_decision == "ask"
        assert "Documents" in r.reason

    def test_relative_cd_resolves_later_targets(self, in_project):
        r = classify_command("cd sub && rm -rf build")
        assert r.final_decision == "allow"
        assert os.path.join("sub", "build") in r.stages[1].reason

    def test_classification_restores_the_working_directory(self, in_project):
        classify_command("cd sub && rm -rf build")
        assert os.getcwd() == in_project

    @pytest.mark.parametrize("cmd", [
        'cd "$X" && rm -rf build',
        "cd - && rm -rf build",
        "cd *ub && rm -rf build",
        "cd a b && rm -rf build",
        "popd && rm -rf build",
        "cd nowhere-yet && touch a",
    ])
    def test_relative_path_after_unfollowable_cd_asks(self, in_project, cmd):
        r = classify_command(cmd)
        assert r.final_decision == "ask"
        assert "cd nuh cannot follow" in r.reason

    def test_unfollowable_cd_leaves_commands_without_paths_alone(self, in_project):
        assert decision('cd "$X" && git status') == "allow"
        assert decision(f'cd "$X" && rm -rf {in_project}/build') == "allow"

    def test_cd_back_with_dash(self, in_project):
        assert decision("cd sub && cd - && rm -rf build") == "allow"
        assert cwd_of("cd sub && cd - && ls", 2) == in_project

    def test_cd_home_forms(self, in_project, monkeypatch):
        home = os.path.expanduser("~")
        for cmd in ("cd && ls", "cd ~ && ls", "cd $HOME && ls", "cd -L -- ~ && ls"):
            assert cwd_of(cmd, 1) == home, cmd

    def test_pushd_popd(self, home_documents):
        assert decision("pushd ~ && popd && rm -rf Documents") == "allow"
        assert decision("pushd ~ && rm -rf Documents") == "ask"

    def test_cdpath_makes_a_bare_name_unknown(self, in_project, monkeypatch):
        monkeypatch.setenv("CDPATH", "/somewhere")
        assert decision("cd sub && rm -rf build") == "ask"
        assert decision("cd ./sub && rm -rf build") == "allow"


class TestCdScope:
    def test_new_shell_keeps_its_cd(self, home_documents):
        assert decision("bash -c 'cd ~ && rm -rf Documents'") == "ask"
        assert decision("bash -c 'cd ~'; rm -rf Documents") == "allow"

    def test_substitution_keeps_its_cd(self, home_documents):
        assert decision("cd ~ && echo $(rm -rf Documents)") == "ask"
        assert decision("echo $(cd ~); rm -rf Documents") == "allow"

    def test_pipeline_element_keeps_its_cd(self, home_documents):
        assert decision("cd ~ | cat; rm -rf Documents") == "allow"

    def test_cd_as_last_pipeline_element_may_leak(self, in_project):
        # zsh runs the last element in the current shell; bash does not.
        assert cwd_of("echo | cd sub; ls", 2) is None

    def test_background_keeps_its_cd(self, home_documents):
        assert decision("cd ~ & rm -rf Documents") == "allow"

    def test_conditional_cd_is_unknown_after_the_list(self, in_project):
        assert cwd_of("test -d sub && cd sub; ls", 2) is None
        assert cwd_of("test -d sub && cd sub && ls", 2) == os.path.join(in_project, "sub")
        assert cwd_of("cd sub || exit 1; ls", 2) == os.path.join(in_project, "sub")

    def test_eval_or_source_with_cd_makes_the_cwd_unknown(self, in_project):
        with open("go.sh", "w") as f:
            f.write("cd /\n")
        with open("quiet.sh", "w") as f:
            f.write("export GREETING=hi\n")
        assert cwd_of("eval 'cd sub'; ls", 1) is None
        assert cwd_of("source go.sh; ls", 1) is None
        assert cwd_of("source quiet.sh; ls", 1) == in_project
        assert cwd_of("source missing.sh; ls", 1) is None

    def test_new_shell_has_no_directory_stack(self, in_project):
        r = classify_command("pushd sub && bash -c 'popd; rm -rf build'")
        assert r.final_decision == "ask"
        assert "cd nuh cannot follow" in r.reason

    def test_cd_in_compound_syntax_is_uncertain(self, in_project):
        assert cwd_of("if true; then cd sub; fi; ls", 2) is None

    def test_cd_before_compound_syntax_is_followed(self, in_project):
        sub = os.path.join(in_project, "sub")
        assert cwd_of("cd sub && ls; for p in A; do echo $p; done", 1) == sub
        assert cwd_of("cd sub && ls; for p in A; do echo $p; done", 2) == sub
        assert decision("cd sub && rm -rf build; for p in A; do echo $p; done") == "allow"

    def test_cd_after_compound_syntax_closes_is_followed(self, in_project):
        sub = os.path.join(in_project, "sub")
        assert cwd_of("for p in A; do echo $p; done; cd sub; ls", 2) == sub
        assert cwd_of("if true; then echo x; fi\ncd sub\nls", 3) == sub

    @pytest.mark.parametrize("cmd", [
        "while false; do echo done; cd sub; done; ls",   # `done` as an argument closes nothing
        "for p (A) cd sub; ls",                           # zsh short loop: no closing word
        "f() { cd sub; }; ls",                            # a function body stays open
    ])
    def test_compound_syntax_without_a_seen_close_stays_uncertain(self, in_project, cmd):
        assert cwd_of(cmd, len(_parse_stages(cmd)) - 1) is None

    def test_assignment_after_a_loop_resolves(self, in_project):
        assert decision("for p in A; do echo $p; done; F=sub/x; rm -rf $F") == "allow"

    def test_cd_after_a_loop_header_line_is_uncertain(self, in_project):
        # The header `for p in A` makes no stage; the bare `cd sub` in the body is still in the loop.
        assert cwd_of("for p in A\ndo\ncd sub\ndone\nls", 1) is None


class TestAssignments:
    @pytest.mark.parametrize("cmd", ["FOO=bar", "A=1 B=2", "export FOO=bar", "export FOO", "local X=1"])
    def test_pure_assignment_is_shell_state(self, in_project, cmd):
        r = classify_command(cmd)
        assert r.final_decision == "allow"
        assert r.reason == "shell variable assignment"

    @pytest.mark.parametrize("cmd", [
        "export PATH=/opt/bin:$PATH", "LD_PRELOAD=/opt/x.so", "DYLD_INSERT_LIBRARIES=/x", "BASH_ENV=/x",
        "PROMPT_COMMAND=date", "IFS=:", "PAGER=less", "export EDITOR=vim", "HOME=/srv",
        "GIT_FOO_COMMAND=x",
    ])
    def test_hijack_variable_asks(self, in_project, cmd):
        r = classify_command(cmd)
        assert r.final_decision == "ask"
        assert "changes what later commands run" in r.reason

    def test_git_exec_variable_keeps_its_floor(self, in_project):
        r = classify_command("GIT_SSH_COMMAND=x")
        assert r.final_decision == "ask"
        assert "git_exec" in r.floor_rules()

    def test_substitution_in_value_is_classified(self, in_project):
        assert decision("X=$(rm -rf /)") == "block"


class TestIntraChainVariables:
    @pytest.mark.parametrize("cmd", [
        "F=/etc/shadow; cat $F",
        'F=/etc/shadow && cat "$F"',
        "export F=/etc/shadow; cat ${F}",
        "D=/etc; F=$D/shadow; cat $F",
    ])
    def test_assigned_path_is_checked_where_used(self, in_project, cmd):
        assert decision(cmd) == "block"

    def test_unresolvable_value_asks_where_used(self, in_project):
        r = classify_command("F=$(printf x); cat $F")
        assert r.final_decision == "ask"
        assert "$F" in r.reason
        assert decision("F=$(printf x); echo done") == "allow"

    def test_resolved_value_allows(self, in_project):
        r = classify_command("X=1; echo $X")
        assert r.final_decision == "allow"
        assert r.stages[1].tokens == ["echo", "1"]

    def test_prefix_assignment_does_not_expand_its_own_command(self, in_project):
        assert classify_command("F=x echo $F").stages[0].tokens == ["echo", "$F"]

    def test_pipeline_assignment_does_not_leak(self, in_project):
        assert classify_command("F=/etc/shadow | true; cat $F").stages[2].tokens == ["cat", "$F"]

    def test_assigned_directory_is_followed_by_cd(self, home_documents):
        assert decision("D=~; cd $D && rm -rf Documents") == "ask"

    @pytest.mark.parametrize("cmd", [
        "test -n x && F=/etc/shadow; cat $F",
        "if true; then F=/etc/shadow; fi; cat $F",
        "F=/etc/hostname; eval F=x; cat $F",
    ])
    def test_assignment_that_may_not_hold_is_unresolved(self, in_project, cmd):
        r = classify_command(cmd)
        assert r.final_decision == "ask"
        assert "$F" in r.reason

    def test_sourced_file_may_reassign(self, in_project):
        with open("env.sh", "w") as f:
            f.write("F=/etc/hostname\n")
        assert "$F" in classify_command("F=x; source env.sh; cat $F").reason

    def test_value_with_spaces_splits_as_a_whole_word(self, in_project):
        assert decision('F="-rf /"; rm $F') == "block"


class TestSubstitutedRedirect:
    def test_substitution_in_redirect_target_is_classified(self, in_project):
        assert decision("echo hi > $(rm -rf /)") == "block"

    def test_target_from_command_output_asks(self, in_project):
        r = classify_command('echo hi > "$(printf out)"')
        assert r.final_decision == "ask"
        assert "command output" in r.reason


class TestCdPrecommands:
    @pytest.mark.parametrize("cmd", ["builtin cd ~ && rm -rf Documents", "time cd ~ && rm -rf Documents",
                                     "command cd ~ && rm -rf Documents", "C=cd; $C ~; rm -rf Documents"])
    def test_cd_behind_a_precommand_or_variable_is_followed(self, home_documents, cmd):
        assert decision(cmd) == "ask"
