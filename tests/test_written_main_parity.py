"""Main's adjacent-heredoc contract, with both written-body expansion readings.

Expected main answers were checked against 80efb47c in the same project and environment.
Commands are classified only; destructive programs are never executed.
"""

import time
from pathlib import Path

import pytest

from nuh import deadline, taxonomy
from nuh.bash import classify_command
from tests.test_single_quote_literal import quote_project as quote_project


ROWS = [
    ("bash ok.sh && bash ok.sh", "allow"),
    ("python3 -m pytest && bash ok.sh", "allow"),
    ("bash -c 'echo hello'; bash ok.sh", "allow"),
    ("cat > fresh.sh <<'EOF' && bash fresh.sh\necho safe\nEOF", "allow"),
    ("cat > fresh.sh <<'EOF'\necho safe\nEOF\nbash -e fresh.sh", "allow"),
    ("cat > fresh.sh <<'EOF'\necho safe\nEOF\nbash fresh.sh arg", "allow"),
    ("sh -c 'echo safe'; bash ~/.ssh/id_rsa", "block"),
    ("source ~/.ssh/id_rsa", "block"),
    ("cat > w.sh <<EOF\n# $UNSET\nrm -rf /\nEOF\nbash w.sh", "block"),
    ("cat > s.sh <<EOF\nrm -r -f '$HOME'\nEOF\nbash s.sh", "block"),
    ("cat > fresh.sh <<'EOF'\nEOF\nbash fresh.sh", "ask"),
]


@pytest.mark.parametrize("command,expected", ROWS)
def test_restored_main_answers(quote_project: Path, command: str, expected: str) -> None:
    (quote_project / "ok.sh").write_text("echo harmless\n")
    result = classify_command(command)
    assert result.final_decision == expected, result.reason


@pytest.mark.parametrize("delimiter", ["EOF", "'EOF'"])
@pytest.mark.parametrize("body,expected", [
    ("rm -r -f '$HOME'", "block"),
    ("rm -rf '~'", "block"),
    ("cat '$HOME/.ssh/id_rsa'", "block"),
    # Main asks for the expanding heredoc and allows the literal heredoc;
    # the additional active-backtick reading conservatively asks for both.
    ("cat '`printf /`etc/shadow'", "ask"),
    ("echo safe", "allow"),
])
def test_written_body_keeps_stricter_reading(
    quote_project: Path, delimiter: str, body: str, expected: str,
) -> None:
    result = classify_command(f"cat > w.sh <<{delimiter}\n{body}\nEOF\nbash w.sh")
    assert result.final_decision == expected, result.reason


def test_written_reading_does_not_leak_to_later_classification(quote_project: Path) -> None:
    assert classify_command("cat > w.sh <<EOF\nrm -rf '$HOME'\nEOF\nbash w.sh").final_decision == "block"
    assert classify_command("cat '$HOME/.aws/credentials'").final_decision == "allow"


def test_large_printf_stays_within_classification_deadline(quote_project: Path) -> None:
    command = "printf '%s' " + "a " * 300_000 + "> w.sh; bash w.sh"
    started = time.monotonic()
    deadline.start(deadline.DEFAULT_DEADLINE_S)
    try:
        result = classify_command(command)
        assert result.final_decision == taxonomy.ASK  # main: missing script, no reconstruction
        assert time.monotonic() - started < deadline.DEFAULT_DEADLINE_S
    finally:
        deadline.clear()
