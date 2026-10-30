"""find -exec / xargs running the files they find: unknown (ask), not a missing script."""

import pytest

from nuh import bash_review, config, taxonomy
from nuh.bash import classify_command
from nuh.config import NuhConfig

FIND_REASON = "runs the files it finds (unknown until run)"
XARGS_REASON = "xargs runs the files it reads (unknown until run)"


class TestFindRunsFoundFiles:
    @pytest.mark.parametrize("command", [
        "find . -name '*.sh' -exec {} \\;",
        "find . -name '*.sh' -exec sh {} \\;",
        "find . -name '*.sh' -exec bash -x {} +",
        "find . -name '*.py' -exec python3 {} \\;",
        "find . -name '*.py' -exec python3 -u {} \\;",
        "find . -name '*.sh' -exec env FOO=1 {} \\;",
        "find . -type f -exec bash -c '\"$0\"' {} \\;",
        "find . -type f -exec sh -c 'chmod +x \"$1\" && \"$1\"' _ {} \\;",
        "find . -type f -exec sh -c '. \"$0\"' {} \\;",
        "find . -type f -exec bash -c 'for f; do \"$f\"; done; \"$@\"' _ {} +",
        "find . -name run -execdir ./{} \\;",
        "find . -name '*.sh' -ok {} \\;",
    ])
    def test_unknown(self, project_root, command):
        r = classify_command(command)
        assert r.final_decision == taxonomy.ASK
        assert FIND_REASON in r.reason
        assert "script not found" not in r.reason
        assert r.floor_rules() == []

    def test_reason_names_the_action(self, project_root):
        assert classify_command("find . -name run -execdir ./{} \\;").reason == f"find -execdir {FIND_REASON}"

    def test_sent_to_the_llm(self, project_root):
        config._cached_config = NuhConfig(llm={"enabled": True})
        assert bash_review.route(classify_command("find . -name '*.sh' -exec {} \\;"), "decide").kind == bash_review.ASK

    def test_user_unknown_policy_decides(self, project_root):
        config._cached_config = NuhConfig(actions={"unknown": "allow"})
        assert classify_command("find . -name '*.sh' -exec sh {} \\;").final_decision == taxonomy.ALLOW

    @pytest.mark.parametrize("command", [
        "find . -name '*.py' -exec grep -n def {} +",
        "find . -type f -exec bash -c 'echo \"$0\"' {} \\;",
        "find . -type f -exec sh -c 'wc -l \"$1\"' _ {} \\;",
        "find . -name '*.py' -exec python3 -m py_compile {} +",
        "find . -name '*.json' -exec python3 -c 'import sys; print(sys.argv[1])' {} \\;",
    ])
    def test_file_as_data_is_classified_as_before(self, project_root, command):
        assert FIND_REASON not in classify_command(command).reason


class TestXargsRunsInput:
    @pytest.mark.parametrize("command", [
        "find . -name '*.py' | xargs -I{} {}",
        "find . -name '*.py' | xargs -I % %",
        "find . -name '*.py' | xargs --replace ./{}",
        "find . -name '*.sh' | xargs sh",
        "find . -name '*.sh' | xargs -n1 bash",
        "find . -name '*.sh' | xargs -n 1 -I{} bash {}",
        "find . -name '*.sh' -print0 | xargs -0 -I{} sh -c '\"$1\"' _ {}",
        "ls *.py | xargs python3",
    ])
    def test_unknown(self, project_root, command):
        r = classify_command(command)
        assert r.final_decision == taxonomy.ASK
        assert XARGS_REASON in r.reason
        assert r.floor_rules() == []

    @pytest.mark.parametrize("command", [
        "find . -name '*.py' | xargs grep -n def",
        "find . -name '*.py' | xargs wc -l",
        "ls | xargs -I{} echo {}",
        "find . -name '*.sh' | xargs bash -c 'echo \"$1\"' _",
        "find . -name 'test_*.py' | xargs python3 -m pytest",
    ])
    def test_other_commands_unchanged(self, project_root, command):
        assert XARGS_REASON not in classify_command(command).reason
