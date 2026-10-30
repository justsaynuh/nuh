"""An input redirect is a read of its file however it is spelled: glued, fd-prefixed, after a word, read-write.

``wc -l <f``, ``sort 3<f``, ``cat 0<f``, ``<f cat``, ``echo ok<f`` and
``cat <>f`` must get the checks their spaced spellings get. Here-strings,
heredocs and descriptor duplications name no file.
"""

import os

import pytest

from nuh import file_operands, taxonomy, word_quoting
from nuh.bash import classify_command
from nuh.bash_parse import _parse_stages

# (glued spelling, spaced spelling) of one input redirect from {f}.
_PAIRS = [
    ("wc -l <{f}", "wc -l < {f}"),
    ("sort 3<{f}", "sort 3< {f}"),
    ("cat 0<{f}", "cat 0< {f}"),
    ("<{f} cat", "< {f} cat"),
    ("<{f}", "< {f}"),                     # zsh runs $READNULLCMD on it; bash opens it
    ("cat <>{f}", "cat <> {f}"),
    ("cat 3<>{f}", "cat 3<> {f}"),
    ("base64 -w0<{f}", "base64 -w0 < {f}"),
    ("echo ok<{f}", "echo ok < {f}"),      # `ok` is a word, `<f` the redirect
]


def _pair(pair: tuple[str, str], name: str) -> tuple[str, str]:
    glued, spaced = pair
    return glued.format(f=name), spaced.format(f=name)


@pytest.fixture
def in_other(project, monkeypatch):
    """The cwd is the sibling Other: sensitive, outside the project, not lifted (as test_project_lift_bash)."""
    monkeypatch.chdir(project.parent.parent / "Other")
    return project


@pytest.mark.parametrize("pair", _PAIRS)
def test_blocked_secret(secrets_home, pair):
    glued, spaced = _pair(pair, "~/.ssh/id_rsa")
    assert classify_command(spaced).final_decision == taxonomy.BLOCK
    assert classify_command(glued).final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("pair", _PAIRS)
def test_bare_name_in_sensitive_cwd(in_other, pair):
    glued, spaced = _pair(pair, "notes.txt")
    result = classify_command(glued)
    assert result.final_decision == classify_command(spaced).final_decision == taxonomy.ASK
    assert "sensitive path" in result.reason


@pytest.mark.parametrize("pair", _PAIRS)
def test_path_outside_project(project, pair):
    glued, spaced = _pair(pair, str(project.parent.parent / "Other" / "notes.txt"))
    result = classify_command(glued)
    assert result.final_decision == classify_command(spaced).final_decision == taxonomy.ASK
    assert "sensitive_paths" in result.floor_rules()


@pytest.mark.parametrize("command", [
    "wc -l <notes.txt", "sort 3<notes.txt", "cat 0<notes.txt", "cat <>notes.txt", "cat 3<>notes.txt",
    "echo ok<notes.txt", "sort <notes.txt >out.txt",
])
def test_file_inside_project_allowed(project, command):
    assert classify_command(command).final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("command", [
    "cat <<< notes.txt", "cat <<<notes.txt", "cat -n<<<notes.txt", "cat <<EOF\nnotes.txt\nEOF",
    "cat <<-EOF\n\tnotes.txt\n\tEOF", "cat <<'EOF'\n<notes.txt\nEOF", "cat <&3", "cat <& 3", "cat 0<&-",
    "echo $(( 1 <2 ))",
])
def test_no_file_named(in_other, command):
    """A here-string or heredoc is text, ``<&`` duplicates a descriptor (bash and zsh refuse a file name), ``$((`` compares."""
    assert classify_command(command).final_decision == taxonomy.ALLOW


def _stage(command: str):
    with word_quoting.scope():
        stages = _parse_stages(command)
    assert stages is not None
    return stages[0]


def test_read_write_keeps_the_pipe():
    """``<>`` is on stdin: the pipe still carries cat's output to bash, which runs the file."""
    stage = _stage("cat <>f | bash")
    assert stage.operator == "|"
    assert [(r.fd, r.target) for r in stage.redirects] == [("0", "f")]
    assert "receives file input" in classify_command("cat <>f | bash").reason


def test_read_write_is_a_read_and_a_write():
    stage = _stage("cat 3<>f")
    assert [(r.fd, r.target) for r in stage.redirects] == [("3", "f")]
    assert file_operands.files(stage.tokens) == ["f"]


def test_program_on_read_write_stdin_asks(project_root, monkeypatch):
    """A program read from a stdin open for writing can rewrite its unread text: its content is not what runs."""
    monkeypatch.chdir(project_root)
    with open(os.path.join(project_root, "ok.py"), "w") as f:
        f.write("print('ok')\n")
    assert classify_command("python3 <ok.py").final_decision == taxonomy.ALLOW
    assert classify_command("python3 <>ok.py").final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", [
    "awk -v <~/.ssh/id_rsa x=1 '{print}'",
    "awk -v < ~/.ssh/id_rsa x=1 '{print}'",
    "awk -F <~/.ssh/id_rsa : '{print $1}'",
    "awk -F < ~/.ssh/id_rsa : '{print $1}'",
])
def test_option_never_takes_a_redirect(secrets_home, command):
    """``awk -v <f x=1 p`` runs ``awk -v x=1 p`` with f on stdin: the option's value is ``x=1``, not ``<``."""
    assert classify_command(command).final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("command", [
    "cat <{notes,~/.ssh/id_rsa}",
    "echo <{notes,~/.ssh/id_rsa}",
    "cat <>{notes,~/.ssh/id_rsa}",
    "cat 3<>{notes,~/.ssh/id_rsa}",
    "echo <>{notes,~/.ssh/authorized_keys}",
])
def test_every_brace_target_is_opened(secrets_home, command):
    """zsh opens every file a brace target expands to (bash refuses: ambiguous redirect)."""
    assert classify_command(command).final_decision == taxonomy.BLOCK


def test_every_brace_target_is_a_read_write():
    stage = _stage("cat <>{a,b}")
    assert [(r.fd, r.target) for r in stage.redirects] == [("0", "a"), ("0", "b")]


@pytest.mark.parametrize("command,decision", [
    ("op='<'; printf '%s %s\\n' $op .env", taxonomy.ALLOW),     # prints "< .env": a value is never syntax
    ("op='<'; printf '%s %s\\n' \"$op\" .env", taxonomy.ALLOW),
    ("printf '%s\\n' <.env", taxonomy.ASK),                     # a literal redirect reads .env
])
def test_only_a_literal_less_than_redirects(project_root, monkeypatch, command, decision):
    monkeypatch.chdir(project_root)
    assert classify_command(command).final_decision == decision


@pytest.mark.parametrize("command", [
    "echo $((1+((2)+3))) ok<~/.ssh/id_rsa",
    "awk -v n=$((1+((2)+3)))<~/.ssh/id_rsa '{print n, $0}'",
])
def test_redirect_after_nested_arithmetic(secrets_home, command):
    assert classify_command(command).final_decision == taxonomy.BLOCK


def test_operator_for_a_file_word(secrets_home):
    """``<>2<>f`` is a syntax error in both shells; the second operator still names f."""
    assert classify_command("echo hi <>2<>~/.ssh/id_rsa").final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("command", ["cat ~/.ssh/id_rsa | bash", "cat <~/.ssh/id_rsa | bash",
                                     "cat <>~/.ssh/id_rsa | bash"])
def test_pipe_composition_never_lowers_a_block(secrets_home, command):
    """``read | exec`` asks; it must not lower the block the read of ~/.ssh already earned."""
    assert classify_command(command).final_decision == taxonomy.BLOCK
