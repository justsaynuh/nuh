"""Fake secrets for the secret-pattern tests (test_secret_*.py), and the case table.

Every fake key here is built at runtime from a seeded random body and its
documented prefix. None is a real key, and none is written out whole in this
file: nuh scans Write content, and a forge's push protection scans pushes.
Assertion messages name the format, never the key.
"""

import random
import string

from nuh.content import scan_content
from nuh.secret_patterns import SECRET_PATTERNS


REDACT_ONLY = {p.desc for p in SECRET_PATTERNS if not p.ask}

_RNG = random.Random(20261018)
ALNUM = string.ascii_letters + string.digits
URLSAFE = ALNUM + "_-"
HEX = "0123456789abcdef"
B64 = ALNUM + "+/"
B32L = "abcdefghijklmnopqrstuvwxyz234567"
UPNUM = string.ascii_uppercase + string.digits
BECH32 = "QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"
DIGITS = string.digits


def _r(n: int, charset: str = ALNUM) -> str:
    return "".join(_RNG.choice(charset) for _ in range(n))


def _uuid() -> str:
    return f"{_r(8, HEX)}-{_r(4, HEX)}-4{_r(3, HEX)}-a{_r(3, HEX)}-{_r(12, HEX)}"


def _pem(kind: str, body: str = "") -> str:
    label = f"{kind} PRIVATE KEY".strip()
    body = body or "\n".join(_r(64, B64) for _ in range(3))
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----"


def _pgp(*headers: str) -> str:
    label = "PGP PRIVATE KEY BLOCK"
    armor = "".join(f"{h}\n" for h in headers)
    body = "\n".join(_r(64, B64) for _ in range(3)) + "\n" + _r(20, B64)
    return f"-----BEGIN {label}-----\n{armor}\n{body}\n={_r(4, B64)}\n-----END {label}-----"


def _ppk(newline: str = "\n", comment: str = "eddsa-key-20261018 work laptop", fields: tuple[str, ...] = ()) -> str:
    lines = ["PuTTY" + "-User-Key-File-3: ssh-ed25519", "Encryption: aes256-cbc" if fields else "Encryption: none",
             f"Comment: {comment}", "Public-Lines: 2", _r(64, B64), _r(4, B64) + "==", *fields,
             "Private-Lines: 1", _r(44, B64), "Private-MAC: " + _r(64, HEX)]
    return newline.join(lines)


# (id, kept before, secret, kept after, label): the fake is before + secret + after, and redaction
# must leave exactly before + "[redacted: label]" + after.
CASES = [
    ("pem-rsa", "", _pem("RSA"), "", "private key"),
    ("pem-pkcs8", "", _pem(""), "", "private key"),
    ("pem-openssh", "", _pem("OPENSSH"), "", "private key"),
    ("pem-ec", "", _pem("EC"), "", "private key"),
    ("pem-dsa", "", _pem("DSA"), "", "private key"),
    ("pem-encrypted", "", _pem("ENCRYPTED"), "", "private key"),
    ("pem-encrypted-headers", "",
     _pem("RSA", "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC," + _r(16, HEX).upper() + "\n\n" + _r(64, B64)),
     "", "private key"),
    ("pgp", "", _pgp(), "", "private key"),
    ("pgp-gnupg-v1", "", _pgp("Version: GnuPG v1.4.11 (GNU/Linux)"), "", "private key"),
    ("pgp-keybase", "", _pgp("Version: Keybase OpenPGP v2.0.76", "Comment: https://keybase.io/crypto"), "",
     "private key"),
    ("pem-indented-yaml", "key: |\n  ", _pem("EC").replace("\n", "\n  "), "", "private key"),
    ("pem-json-escaped", '{"private_key": "', _pem("").replace("\n", "\\n"), '\\n"}', "private key"),
    ("putty", "", _ppk(), "", "PuTTY private key"),
    ("putty-apostrophe-comment", "", _ppk(comment="Alice's laptop"), "", "PuTTY private key"),
    ("putty-parens-comment", "", _ppk(comment="alice@host (work)"), "", "PuTTY private key"),
    ("putty-hash-comment", "", _ppk(comment="key #2"), "", "PuTTY private key"),
    ("putty-argon2", "", _ppk(fields=("Key-Derivation: Argon2id", "Argon2-Memory: 8192", "Argon2-Passes: 13",
                                      "Argon2-Parallelism: 1", "Argon2-Salt: " + _r(32, HEX))), "",
     "PuTTY private key"),
    ("putty-escaped", '{"ppk": "', _ppk(newline="\\n"), '"}', "PuTTY private key"),
    ("age", "", "AGE-SECRET-KEY-1" + _r(58, BECH32), "", "age secret key"),
    ("aws-akia", "", "AKIA" + _r(16, UPNUM), "", "AWS access key ID"),
    ("aws-asia", "", "ASIA" + _r(16, UPNUM), "", "AWS access key ID"),
    ("aws-secret-ini", "aws_secret_access_key = ", _r(40, B64), "", "AWS secret access key"),
    ("aws-secret-env", 'AWS_SECRET_ACCESS_KEY="', _r(40, B64), '"', "AWS secret access key"),
    ("aws-secret-short-name", "AWS_SECRET_KEY=", _r(40, B64), "", "AWS secret access key"),
    ("aws-secret-cli-json", '{"AccessKeyId": "x", "SecretAccessKey": "', _r(40, B64), '", "Expiration": 1}',
     "AWS secret access key"),
    ("aws-secret-cli-flag", "aws configure set --aws-secret-access-key ", _r(40, B64), " --profile p",
     "AWS secret access key"),
    ("aws-session", "aws_session_token = ", "FwoGZXIvYXdzE" + _r(300, B64), "", "AWS session token"),
    ("aws-session-cli-json", '"SessionToken": "', "IQoJb3JpZ2lu" + _r(300, B64), '"', "AWS session token"),
    ("github-ghp", "", "ghp_" + _r(36), "", "GitHub token"),
    ("github-gho", "", "gho_" + _r(36), "", "GitHub token"),
    ("github-ghs-stateless", "",
     "ghs_" + _r(7, DIGITS) + "_eyJ" + _r(40, URLSAFE) + ".eyJ" + _r(60, URLSAFE) + "." + _r(43, URLSAFE),
     "", "GitHub token"),
    ("github-fine-grained", "", "github_pat_" + _r(22) + "_" + _r(59), "", "GitHub fine-grained token"),
    ("gitlab-pat", "", "glpat-" + _r(20, URLSAFE), "", "GitLab token"),
    ("gitlab-runner", "", "glrt-t1_" + _r(27, URLSAFE), "", "GitLab token"),
    ("gitea-oauth", "", "gto_" + _r(52, B32L), "", "Gitea OAuth secret"),
    ("gitea-env", "GITEA_TOKEN=", _r(40, HEX), "", "Gitea token assignment"),
    ("gitea-yaml", "gitea_token: ", _r(40, HEX), "", "Gitea token assignment"),
    ("gitea-runner", "GITEA_RUNNER_REGISTRATION_TOKEN=", _r(40), "", "Gitea token assignment"),
    ("anthropic-api03", "", "sk-ant-api03-" + _r(93, URLSAFE) + "AA", "", "Anthropic key"),
    ("anthropic-oat01", "", "sk-ant-oat01-" + _r(95, URLSAFE), "", "Anthropic key"),
    ("openai-proj", "", "sk-proj-" + _r(74, URLSAFE) + "T3BlbkFJ" + _r(74, URLSAFE), "", "OpenAI key"),
    ("openai-svcacct", "", "sk-svcacct-" + _r(58, URLSAFE) + "T3BlbkFJ" + _r(58, URLSAFE), "", "OpenAI key"),
    ("openai-admin", "", "sk-admin-" + _r(58, URLSAFE) + "T3BlbkFJ" + _r(58, URLSAFE), "", "OpenAI key"),
    ("openai-proj-short", "", "sk-proj-" + _r(34, URLSAFE), "", "OpenAI key"),
    ("openai-user", "", "sk-None-" + _r(58, URLSAFE) + "T3BlbkFJ" + _r(58, URLSAFE), "", "OpenAI key"),
    ("openrouter", "", "sk-or-v1-" + _r(64, HEX), "", "OpenRouter key"),
    ("sk-generic", "", "sk-" + _r(32, string.ascii_lowercase + DIGITS), "", "secret key token (sk-)"),
    ("sk-legacy-openai", "", "sk-" + _r(20) + "T3BlbkFJ" + _r(20), "", "secret key token (sk-)"),
    ("groq", "", "gsk_" + _r(52), "", "Groq key"),
    ("xai", "", "xai-" + _r(80), "", "xAI key"),
    ("huggingface", "", "hf_" + _r(34, string.ascii_letters), "", "Hugging Face token"),
    ("huggingface-org", "", "api_org_" + _r(34), "", "Hugging Face token"),
    ("replicate", "", "r8_" + _r(37), "", "Replicate token"),
    ("npm", "", "npm_" + _r(36), "", "npm token"),
    ("pypi", "", "pypi-AgEIcHlwaS5vcmc" + _r(160, URLSAFE), "", "PyPI token"),
    ("docker-pat", "", "dckr_pat_" + _r(27, URLSAFE), "", "Docker Hub token"),
    ("docker-oat", "", "dckr_oat_" + _r(32, URLSAFE), "", "Docker Hub token"),
    ("portainer-key", "", "ptr_" + _r(43, B64) + "=", "", "Portainer API key"),
    ("portainer-key-header", "X-API-Key: ", "ptr_" + _r(43, B64) + "=", "", "Portainer API key"),
    ("docker-auth", '{"auths": {"gitea.example": {"auth": "', _r(60, B64) + "=", '"}}}', "Docker registry auth"),
    ("portainer-webhook", "curl -X POST https://portainer.example:9443/api/stacks/webhooks/", _uuid(), "",
     "Portainer webhook URL"),
    ("google-api", "", "AIza" + _r(35, URLSAFE), "", "Google API key"),
    ("google-oauth-secret", "", "GOCSPX-" + _r(28, URLSAFE), "", "Google OAuth client secret"),
    ("google-access-token", "", "ya29." + _r(120, URLSAFE), "", "Google OAuth access token"),
    ("azure-entra", "", _r(3) + "8Q~" + _r(34, ALNUM + "_~.-"), "", "Azure Entra client secret"),
    ("azure-storage", "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=", _r(86, B64) + "==", ";",
     "Azure storage account key"),
    ("digitalocean", "", "dop_v1_" + _r(64, HEX), "", "DigitalOcean token"),
    ("vault", "", "hvs." + _r(95, URLSAFE), "", "Vault token"),
    ("tailscale", "", "tskey-auth-" + _r(17) + "-" + _r(33), "", "Tailscale key"),
    ("stripe-live", "", "sk_live_" + _r(99), "", "Stripe secret key"),
    ("stripe-restricted", "", "rk_test_" + _r(99), "", "Stripe secret key"),
    ("stripe-webhook", "", "whsec_" + _r(32), "", "Stripe webhook secret"),
    ("slack-bot", "", "xoxb-" + _r(12, DIGITS) + "-" + _r(13, DIGITS) + "-" + _r(24), "", "Slack token"),
    ("slack-app", "", "xapp-1-A" + _r(10, UPNUM) + "-" + _r(13, DIGITS) + "-" + _r(64, HEX), "", "Slack token"),
    ("slack-webhook", "https://hooks.slack.com/services/", "T" + _r(10, UPNUM) + "/B" + _r(10, UPNUM) + "/" + _r(24),
     "", "Slack webhook URL"),
    ("sendgrid", "", "SG." + _r(22, URLSAFE) + "." + _r(43, URLSAFE), "", "SendGrid key"),
    ("jwt", "", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ" + _r(60, URLSAFE) + "." + _r(43, URLSAFE), "",
     "JSON web token"),
    ("url-postgres", "postgres://app:", _r(20), "@db.internal:5432/app", "credentials in URL"),
    ("url-git-remote", "https://alice:", _r(40, HEX), "@gitea.example/alice/repo.git", "credentials in URL"),
    ("bearer-header", "Authorization: Bearer ", _r(48, URLSAFE), "", "authorization header"),
    ("gitea-token-header", 'curl -H "Authorization: token ', _r(40, HEX), '" https://gitea.example/api/v1/user',
     "authorization header"),
    ("bearer-json", '{"Authorization": "Bearer ', _r(48, URLSAFE), '"}', "authorization header"),
    ("token-python-dict", "headers = {'Authorization': 'token ", _r(40, HEX), "'}", "authorization header"),
    ("api-key-header", "x-api-key: ", _r(32, URLSAFE), "", "API key header"),
    ("api-key-json", '{"x-api-key": "', _r(32, URLSAFE), '"}', "API key header"),
    ("api-key-mcp-json", '"headers": {"X-API-Key": "', _r(40, URLSAFE), '"}', "API key header"),
    ("azure-api-key-header", "api-key: ", _r(32, HEX), "", "API key header"),
    ("hardcoded-api-key", 'api_key = "', _r(24, URLSAFE), '"', "hardcoded API key"),
]
_IDS = [case[0] for case in CASES]

# Shell text on the same line before and after the fake: it must come through byte for byte.
BEFORE = "cd /srv && echo "
AFTER = " | tee -a out.log && ls -la"


def _secret_descs(text: str) -> set[str]:
    return {m.pattern_desc for m in scan_content(text) if m.category == "secret"}


KEY = "[redacted: private key]"
PPK = "[redacted: PuTTY private key]"
BEGIN, END = "-----BEGIN ", "-----END "
PPK_HEADER = "PuTTY" + "-User-Key-File-2: ssh-rsa"


def _marker(kind: str, label: str = "PRIVATE KEY") -> tuple[str, str]:
    full = f"{kind} {label}".strip()
    return f"{BEGIN}{full}-----", f"{END}{full}-----"
