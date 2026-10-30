"""The secret table (secret_patterns.py): every format's label, in-place redaction, and ask vs redact-only.

The fakes come from tests/secret_fakes.py; assertion messages name the format, never the key.
"""

import pytest

from nuh.redact import redact_secrets
from nuh.secret_patterns import SECRET_PATTERNS
from tests.secret_fakes import AFTER, B64, BEFORE, CASES, HEX, REDACT_ONLY, URLSAFE, _IDS, _r, _secret_descs, _uuid


def test_every_table_description_has_a_case():
    assert {p.desc for p in SECRET_PATTERNS} == {case[4] for case in CASES}


@pytest.mark.parametrize(("name", "before", "secret", "after", "label"), CASES, ids=_IDS)
def test_redacted_in_place_with_its_label(name, before, secret, after, label):
    text = f"{BEFORE}{before}{secret}{after}{AFTER}"
    expected = f"{BEFORE}{before}[redacted: {label}]{after}{AFTER}"
    assert redact_secrets(text) == expected, f"{name}: redaction differs from the expected placeholder"


@pytest.mark.parametrize(("name", "before", "secret", "after", "label"), CASES, ids=_IDS)
def test_asks_unless_redact_only(name, before, secret, after, label):
    descs = _secret_descs(f"{before}{secret}{after}")
    if label in REDACT_ONLY:
        assert descs == set(), f"{name}: a redact-only format made the write ask ({sorted(descs)})"
    else:
        assert label in descs, f"{name}: the write did not ask with {label!r} (got {sorted(descs)})"


def test_redact_only_formats_are_jwt_and_url_credentials():
    assert REDACT_ONLY == {"JSON web token", "credentials in URL"}


class TestNearMisses:
    @pytest.mark.parametrize("text", [
        _r(40, HEX),
        "commit " + _r(40, HEX) + " Merge branch 'main'",
        "git checkout " + _r(40, HEX) + " && git log -1",
        _r(64, HEX),
        _uuid(),
        _r(200, B64) + "==",
        '"integrity": "sha512-' + _r(86, B64) + '=="',
        "--hash=sha256:" + _r(64, HEX),
        "image@sha256:" + _r(64, HEX),
        "gitea_commit_sha = " + _r(40, HEX),
        "sk-1.2.3",
        "sk-short",
        "pip install sk-learn scikit-learn",
        'class="flex items-center sk-button sk-modal-header"',
        "sk-proj-settings-panel-component-wrapper",
        "the ghp_ prefix marks a GitHub PAT, and sk-ant- an Anthropic key",
        "export AWS_SECRET_ACCESS_KEY=$AWS_SECRET",
        "postgres://user:${DB_PASSWORD}@localhost/db",
        "git@git.corp.example:alice/nuh.git",
        "https://git.corp.example/alice/nuh.git",
        'curl -H "Authorization: Bearer $TOKEN" https://api.example.com',
        "eyJustKidding.eyJokes.ok",
        "ptr_offset = ptr_base + 4",
        '{"auth": "required", "authType": "oauth"}',
        "POST /api/stacks/webhooks/{webhookID}",
        "-----BEGIN " + "PUBLIC KEY-----",
        "-----BEGIN " + "CERTIFICATE-----",
        "Use PuTTYgen to convert the key, then load it in Pageant.",
    ], ids=["sha1", "sha1-in-log", "sha1-in-command", "sha256", "uuid", "base64", "integrity", "pip-hash",
            "docker-digest", "gitea-var-sha", "sk-version", "sk-short", "sk-learn", "css", "kebab", "prose",
            "env-var-ref", "url-placeholder", "ssh-remote", "https-remote", "bearer-var", "jwt-ish-words",
            "ptr-word", "json-auth-flag", "webhook-route-doc", "pem-public", "pem-cert", "putty-prose"])
    def test_not_a_secret(self, text):
        assert redact_secrets(text) == text
        assert _secret_descs(text) == set()


class TestValueOnly:
    def test_keyword_pattern_keeps_the_name(self):
        secret = _r(40, B64)
        assert redact_secrets(f"aws_secret_access_key={secret}\nregion=us-east-1") == \
            "aws_secret_access_key=[redacted: AWS secret access key]\nregion=us-east-1"

    def test_one_line_pipeline_keeps_every_command(self):
        aws, pw, tok = _r(40, B64), _r(16), _r(40, URLSAFE)
        command = (f"AWS_SECRET_ACCESS_KEY={aws} aws s3 ls s3://b && "
                   f"psql postgres://app:{pw}@db/app -c 'select 1' | tee out && "
                   f"curl -H 'Authorization: Bearer {tok}' https://x.example/api && rm -rf ./build")
        assert redact_secrets(command) == (
            "AWS_SECRET_ACCESS_KEY=[redacted: AWS secret access key] aws s3 ls s3://b && "
            "psql postgres://app:[redacted: credentials in URL]@db/app -c 'select 1' | tee out && "
            "curl -H 'Authorization: Bearer [redacted: authorization header]' https://x.example/api && rm -rf ./build")

    def test_a_vendor_token_in_a_header_keeps_its_own_label(self):
        """The token pattern runs first, so the header pattern finds nothing left to hide."""
        token = "ghp_" + _r(36)
        assert redact_secrets(f"Authorization: token {token} && ls") == \
            "Authorization: token [redacted: GitHub token] && ls"

    def test_config_pattern_with_a_secret_group(self):
        from nuh import config, content
        from nuh.config import NuhConfig
        config._cached_config = NuhConfig(content_patterns_add=[{
            "category": "secret", "pattern": r"vault_pass:\s*(?P<secret>\S+)", "description": "vault password"}])
        content.reset_content_patterns()
        try:
            assert redact_secrets("vault_pass: hunter2 && ls") == "vault_pass: [redacted: vault password] && ls"
        finally:
            config._cached_config = None
            content.reset_content_patterns()
