"""Unit tests for nuh.taxonomy — classify_tokens by action type, and get_policy.

classify_tokens continues in test_taxonomy_tokens.py.
"""

import pytest

from nuh.taxonomy import (
    get_policy,
)
from tests.taxonomy_helpers import _ct


# --- classify_tokens ---


class TestClassifyTokens:
    """Action type classification via prefix matching."""

    # filesystem_read
    @pytest.mark.parametrize("cmd", ["cat", "head", "tail", "less", "more", "file",
                                      "wc", "stat", "du", "df", "ls", "tree", "bat",
                                      "echo", "printf", "diff", "grep", "rg", "awk", "sed"])
    def test_filesystem_read(self, cmd):
        assert _ct([cmd, "file.txt"]) == "filesystem_read"

    # filesystem_write
    @pytest.mark.parametrize("cmd", ["tee", "cp", "mv", "mkdir", "touch", "chmod",
                                      "chown", "ln", "install"])
    def test_filesystem_write(self, cmd):
        assert _ct([cmd, "target"]) == "filesystem_write"

    # filesystem_delete
    @pytest.mark.parametrize("cmd", ["rm", "rmdir", "unlink", "shred", "truncate"])
    def test_filesystem_delete(self, cmd):
        assert _ct([cmd, "file"]) == "filesystem_delete"

    # git_safe
    @pytest.mark.parametrize("tokens", [
        ["git", "status"],
        ["git", "log", "--oneline"],
        ["git", "diff"],
        ["git", "show", "HEAD"],
        ["git", "branch"],
        ["git", "tag"],
        ["git", "remote", "-v"],
        ["git", "stash", "list"],
    ])
    def test_git_safe(self, tokens):
        assert _ct(tokens) == "git_safe"

    # git_write
    @pytest.mark.parametrize("tokens", [
        ["git", "add", "."],
        ["git", "commit", "-m", "msg"],
        ["git", "pull"],
        ["git", "fetch"],
        ["git", "merge", "main"],
        ["git", "stash"],
        ["git", "checkout", "branch"],
        ["git", "switch", "branch"],
    ])
    def test_git_write(self, tokens):
        assert _ct(tokens) == "git_write"

    # git_remote_write
    def test_git_push_remote_write(self):
        assert _ct(["git", "push"]) == "git_remote_write"

    # git_history_rewrite
    @pytest.mark.parametrize("tokens", [
        ["git", "push", "--force"],
        ["git", "push", "-f"],
        ["git", "rebase", "main"],
        ["git", "clean", "-fd"],
        ["git", "stash", "drop"],
        ["git", "stash", "clear"],
        ["git", "branch", "-D", "old"],
    ])
    def test_git_history_rewrite(self, tokens):
        assert _ct(tokens) == "git_history_rewrite"

    # Prefix priority: longer prefix wins
    def test_git_push_force_beats_git_push(self):
        assert _ct(["git", "push", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "push"]) == "git_remote_write"

    def test_git_branch_D_beats_git_branch(self):
        assert _ct(["git", "branch", "-D", "x"]) == "git_history_rewrite"
        assert _ct(["git", "branch"]) == "git_safe"

    def test_git_stash_drop_beats_git_stash(self):
        assert _ct(["git", "stash", "drop"]) == "git_history_rewrite"
        assert _ct(["git", "stash"]) == "git_write"

    # network_outbound
    @pytest.mark.parametrize("cmd", ["curl", "wget", "ssh", "scp", "rsync",
                                      "nc", "ncat", "telnet", "ftp", "sftp"])
    def test_network_outbound(self, cmd):
        assert _ct([cmd, "target"]) == "network_outbound"

    # package_install
    @pytest.mark.parametrize("tokens", [
        ["npm", "install"],
        ["pip", "install", "flask"],
        ["pip3", "install", "flask"],
        ["cargo", "build"],
        ["brew", "install", "jq"],
        ["apt", "install", "curl"],
        ["gem", "install", "rails"],
        ["go", "get", "pkg"],
        ["pnpm", "install"],
        ["yarn", "add", "react"],
        ["bun", "install"],
    ])
    def test_package_install(self, tokens):
        assert _ct(tokens) == "package_install"

    @pytest.mark.parametrize("tokens", [
        ["pip", "install", "--target=/tmp/lib", "flask"],
        ["pip", "install", "--root=/opt", "flask"],
        ["pip", "install", "-t", "/tmp/lib", "flask"],
        ["pip3", "install", "-t", "/tmp/lib", "flask"],
    ])
    def test_global_install_flag_variants_escalate_to_unknown(self, tokens):
        assert _ct(tokens) == "unknown"

    # package_run
    @pytest.mark.parametrize("tokens", [
        ["npx", "create-react-app"],
        ["pytest", "-v"],
        ["make", "build"],
        ["npm", "test"],
        ["npm", "run", "dev"],
        ["cargo", "test"],
        ["cargo", "run"],
        ["go", "test", "./..."],
        ["go", "run", "main.go"],
        ["python", "-m", "pytest"],
        ["pnpm", "run", "dev"],
        ["yarn", "run", "build"],
        ["bun", "run", "dev"],
        ["just", "build"],
        ["task", "lint"],
    ])
    def test_package_run(self, tokens):
        assert _ct(tokens) == "package_run"

    # lang_exec
    @pytest.mark.parametrize("tokens", [
        ["python", "-c", "print(1)"],
        ["python3", "-c", "print(1)"],
        ["node", "-e", "console.log(1)"],
        ["ruby", "-e", "puts 1"],
        ["perl", "-e", "print 1"],
        ["php", "-r", "echo 1;"],
    ])
    def test_lang_exec(self, tokens):
        assert _ct(tokens) == "lang_exec"

    # find — special case
    def test_find_read(self):
        assert _ct(["find", ".", "-name", "*.py"]) == "filesystem_read"

    def test_find_delete(self):
        assert _ct(["find", ".", "-delete"]) == "filesystem_delete"

    @pytest.mark.parametrize("flag", ["-fprint", "-fprint0", "-fprintf", "-fls"])
    def test_find_writing_its_list_to_a_file_is_a_write(self, flag):
        assert _ct(["find", ".", "-name", "*.py", flag, "out.txt"]) == "filesystem_write"

    def test_find_delete_outranks_a_write_flag(self):
        assert _ct(["find", ".", "-fprint", "out.txt", "-delete"]) == "filesystem_delete"

    def test_find_exec(self):
        assert _ct(["find", ".", "-type", "f", "-exec", "grep", "-l", "needle", "{}", "+"]) == "filesystem_read"

    def test_find_exec_delete_command(self):
        assert _ct(["find", ".", "-exec", "rm", "{}", ";"]) == "filesystem_delete"

    def test_find_execdir_unknown_payload_is_not_a_delete(self):
        assert _ct(["find", ".", "-execdir", "cmd", "{}", ";"]) == "unknown"

    # git_discard
    @pytest.mark.parametrize("tokens", [
        ["git", "checkout", "."],
        ["git", "checkout", "--", "file.txt"],
        ["git", "checkout", "HEAD", "file.txt"],
        ["git", "checkout", "-f"],
        ["git", "checkout", "--force"],
        ["git", "checkout", "--ours", "file.txt"],
        ["git", "checkout", "--theirs", "file.txt"],
        ["git", "checkout", "-B", "branch"],
        ["git", "switch", "-f", "branch"],
        ["git", "switch", "--force", "branch"],
        ["git", "switch", "--discard-changes", "branch"],
        ["git", "restore", "file.txt"],
        ["git", "rm", "file.txt"],
    ])
    def test_git_discard(self, tokens):
        assert _ct(tokens) == "git_discard"

    # git_discard vs git_write priority
    def test_git_checkout_dot_discard_not_write(self):
        assert _ct(["git", "checkout", "."]) == "git_discard"
        assert _ct(["git", "checkout", "branch"]) == "git_write"

    def test_git_switch_force_discard_not_write(self):
        assert _ct(["git", "switch", "--force", "b"]) == "git_discard"
        assert _ct(["git", "switch", "branch"]) == "git_write"

    # process_signal
    @pytest.mark.parametrize("tokens", [
        ["kill", "-9", "1234"],
        ["kill", "-KILL", "1234"],
        ["kill", "-SIGKILL", "1234"],
        ["pkill", "nginx"],
        ["killall", "node"],
    ])
    def test_process_signal(self, tokens):
        assert _ct(tokens) == "process_signal"

    # container_destructive
    @pytest.mark.parametrize("tokens", [
        ["docker", "rm", "abc"],
        ["docker", "rmi", "img"],
        ["docker", "system", "prune"],
        ["docker", "container", "prune"],
        ["docker", "image", "prune"],
        ["docker", "volume", "prune"],
        ["docker", "network", "prune"],
        ["docker", "builder", "prune"],
        ["docker", "buildx", "prune"],
        ["docker", "compose", "down"],
        ["docker", "compose", "rm"],
        ["docker", "buildx", "rm", "builder0"],
        ["docker", "volume", "rm", "vol"],
        ["docker", "container", "rm", "abc"],
        ["docker", "image", "rm", "img"],
        ["docker", "network", "rm", "net"],
        ["podman", "rm", "abc"],
        ["podman", "rmi", "img"],
        ["podman", "system", "prune"],
        ["podman", "container", "prune"],
        ["podman", "image", "prune"],
        ["podman", "volume", "prune"],
        ["podman", "network", "prune"],
        ["podman", "pod", "prune"],
        ["podman", "compose", "down"],
        ["podman", "compose", "rm"],
        ["podman", "volume", "rm", "vol"],
        ["podman", "container", "rm", "abc"],
        ["podman", "image", "rm", "img"],
        ["podman", "network", "rm", "net"],
        ["podman", "pod", "rm", "dev"],
        ["podman", "machine", "rm", "devvm"],
        ["podman", "secret", "rm", "db-pass"],
    ])
    def test_container_destructive(self, tokens):
        assert _ct(tokens) == "container_destructive"


    # docker/podman read-only listing ops
    @pytest.mark.parametrize("tokens", [
        ["docker", "ps"],
        ["docker", "images"],
        ["docker", "image", "ls"],
        ["docker", "container", "ls"],
        ["docker", "compose", "ps"],
        ["docker", "compose", "ls"],
        ["docker", "volume", "ls"],
        ["docker", "network", "ls"],
        ["podman", "ps"],
        ["podman", "images"],
        ["podman", "image", "ls"],
        ["podman", "container", "ls"],
        ["podman", "compose", "ps"],
        ["podman", "pod", "ps"],
        ["podman", "volume", "ls"],
        ["podman", "network", "ls"],
    ])
    def test_container_listing_read_ops(self, tokens):
        assert _ct(tokens) == "filesystem_read"

    # package_uninstall
    @pytest.mark.parametrize("tokens", [
        ["pip", "uninstall", "flask"],
        ["pip3", "uninstall", "flask"],
        ["npm", "uninstall", "react"],
        ["brew", "uninstall", "jq"],
        ["brew", "remove", "jq"],
        ["cargo", "uninstall", "ripgrep"],
        ["gem", "uninstall", "rails"],
        ["pnpm", "remove", "react"],
        ["yarn", "remove", "react"],
        ["bun", "remove", "react"],
        ["apt", "remove", "curl"],
        ["apt", "purge", "curl"],
    ])
    def test_package_uninstall(self, tokens):
        assert _ct(tokens) == "package_uninstall"

    # db_write
    @pytest.mark.parametrize("tokens", [
        ["psql"],
        ["psql", "-c", "SELECT 1"],
        ["psql", "-f", "script.sql"],
        ["psql", "--command", "SELECT 1"],
        ["psql", "--file", "script.sql"],
        ["mysql"],
        ["mysql", "-e", "SHOW TABLES"],
        ["mysql", "--execute", "SHOW TABLES"],
        ["sqlite3", "db.sqlite", "SELECT 1"],
        ["snow", "sql", "-q", "SELECT 1"],
        ["snow", "sql", "-f", "file.sql"],
        ["snow", "sql", "--query", "SELECT 1"],
        ["snow", "sql", "--filename", "file.sql"],
        ["snowsql", "-q", "SELECT 1"],
        ["snowsql", "-f", "file.sql"],
        ["snowsql", "--query", "SELECT 1"],
        ["pg_restore", "dump.sql"],
    ])
    def test_db_write(self, tokens):
        assert _ct(tokens) == "db_write"

    # beads_safe
    @pytest.mark.parametrize("tokens", [
        ["bd", "list"],
        ["bd", "show", "nah-123"],
        ["bd", "ready"],
        ["bd", "doctor"],
        ["bd", "info"],
        ["bd", "status"],
        ["bd", "label", "list"],
        ["bd", "duplicates"],
    ])
    def test_beads_safe(self, tokens):
        assert _ct(tokens) == "beads_safe"

    # beads_write
    @pytest.mark.parametrize("tokens", [
        ["bd", "create", "foo"],
        ["bd", "update", "nah-123"],
        ["bd", "close", "nah-123"],
        ["bd", "label", "add", "nah-123", "design"],
        ["bd", "dolt", "push"],
        ["bd", "gate", "check"],
        ["bd", "doctor", "--fix"],
        ["bd", "duplicates", "--auto-merge"],
        ["bd", "backup"],
    ])
    def test_beads_write(self, tokens):
        assert _ct(tokens) == "beads_write"

    # beads_destructive
    @pytest.mark.parametrize("tokens", [
        ["bd", "delete", "nah-123"],
        ["bd", "sql", "SELECT 1"],
        ["bd", "init"],
        ["bd", "purge"],
        ["bd", "admin", "reset"],
        ["bd", "backup", "restore"],
    ])
    def test_beads_destructive(self, tokens):
        assert _ct(tokens) == "beads_destructive"

    def test_bd_dolt_start_stays_process_signal(self):
        assert _ct(["bd", "dolt", "start"]) == "process_signal"

    def test_bare_dolt_stays_db(self):
        assert _ct(["dolt", "sql"]) == "db_write"
        assert _ct(["dolt", "status"]) == "db_read"

    # db companion tools → filesystem_write
    @pytest.mark.parametrize("tokens", [
        ["pg_dump", "mydb"],
        ["pg_dumpall"],
        ["mysqldump", "mydb"],
    ])
    def test_db_companion_filesystem_write(self, tokens):
        assert _ct(tokens) == "filesystem_write"

    # git push origin --force → git_history_rewrite (not git_write)
    def test_git_push_origin_force(self):
        assert _ct(["git", "push", "origin", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "push", "origin", "-f"]) == "git_history_rewrite"
        assert _ct(["git", "push", "origin", "main", "--force"]) == "git_history_rewrite"
        assert _ct(["git", "push", "origin", "main", "-f"]) == "git_history_rewrite"


# --- get_policy ---


class TestGetPolicy:
    """Policy defaults per action type."""

    @pytest.mark.parametrize("action_type, expected", [
        ("filesystem_read", "allow"),
        ("filesystem_write", "context"),
        ("filesystem_delete", "context"),
        ("git_safe", "allow"),
        ("git_write", "allow"),
        ("git_remote_write", "ask"),
        ("git_discard", "ask"),
        ("git_history_rewrite", "ask"),
        ("network_outbound", "context"),
        ("package_install", "allow"),
        ("package_run", "allow"),
        ("package_uninstall", "ask"),
        ("lang_exec", "context"),
        ("process_signal", "ask"),
        ("container_destructive", "ask"),
        ("db_read", "allow"),
        ("db_write", "context"),
        ("beads_safe", "allow"),
        ("beads_write", "allow"),
        ("beads_destructive", "ask"),
        ("obfuscated", "block"),
        ("unknown", "ask"),
    ])
    def test_all_defaults(self, action_type, expected):
        assert get_policy(action_type) == expected

    def test_unknown_type_falls_back_to_ask(self):
        assert get_policy("totally_made_up") == "ask"
