"""The built-in secret patterns: what makes Write/Edit content ask, and what redaction replaces.

content.py files the asking ones under its ``secret`` category, so config can
suppress their ask by description. Redaction (redact.py) applies every
entry here in this order, after its private key and PuTTY block rules,
whatever config suppresses: suppressing a pattern stops the ask, never the
redaction.

Form rules, all measured (.scratch/research/secret-formats):

- Every pattern runs in linear time: content and commands are attacker
  controlled, and a hook that runs out of time does not block.
- A pattern starts with its literal prefix where it has one, and the word
  boundary before that prefix is a fixed-width lookbehind placed after it
  (``ghp_(?<!\\w.{4})``, not ``\\bghp_``). A leading ``\\b`` or lookbehind
  turns off CPython's literal-prefix scan and costs 30-40x on large content.
- Vendor patterns come before generic ones (the Anthropic, OpenAI and
  OpenRouter keys before the generic ``sk-``, GitHub's app tokens before JSON
  web tokens, every token before the headers that carry it), so redaction
  replaces a whole key before a broader pattern could catch part of it.
- A pattern with a named group ``secret`` (``SECRET_GROUP``) has only that
  group replaced: ``aws_secret_access_key = [redacted: AWS secret access
  key]`` keeps the setting's name readable. A value never holds whitespace or
  shell syntax, so redaction never extends past it into a pipeline.

An entry with ``ask=False`` is redacted but never makes a write ask: JSON web
tokens and passwords in URLs turn up in ordinary development config (a
Postgres URL in a compose file), and asking on each would be noise.

Not detected on purpose: bare 40-hex tokens (a Gitea token is 20 random bytes
in hex, the shape of a git commit hash), bare UUIDs, and entropy guesses. A
Gitea token makes a write ask only beside its name (the Gitea assignment), in
a Docker ``auth`` entry or an authorization header; in a URL it is redacted
but never asks, as all URL credentials. The sensitive paths ``~/.config/tea``
and ``~/.git-credentials`` cover the files that hold bare ones.
"""

import re
from dataclasses import dataclass

SECRET_GROUP = "secret"


@dataclass(frozen=True)
class SecretPattern:
    regex: re.Pattern[str]
    desc: str
    ask: bool = True  # False: redacted only, never a reason for a write to ask


def _S(pattern: str, desc: str, *, ask: bool = True) -> SecretPattern:
    return SecretPattern(re.compile(pattern), desc, ask)


# A key or header value: no whitespace, quotes or shell syntax.
_HEADER_VALUE = r"(?P<secret>[A-Za-z0-9._~+/=-]{16,})"
# Between a setting's name and its value: = or : (JSON, YAML, env), or a space (a CLI flag).
_ASSIGN = r"\b['\"]?(?:\s*[:=]\s*|\s+)['\"]?"
_AWS_SECRET = r"(?P<secret>[A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"
_AWS_SESSION = r"(?P<secret>[A-Za-z0-9/+=]{100,})"
# RFC 3986 userinfo characters, minus everything a shell reads as syntax.
_USERINFO = r"[A-Za-z0-9._~%!*+,=-]"

SECRET_PATTERNS: list[SecretPattern] = [
    # --- private keys (PEM: PKCS#1, PKCS#8, OpenSSH, EC, DSA, encrypted; PGP armor) ---
    _S(r"-----BEGIN\s[\sA-Z0-9]{0,40}PRIVATE\s+KEY(?:\s+BLOCK)?-----", "private key"),
    _S(r"PuTTY-User-Key-File-(?<!\w.{20})[23]: *[a-z0-9-]+", "PuTTY private key"),
    _S(r"AGE-SECRET-KEY-1(?<!\w.{16})[02-9AC-HJ-NP-Z]{58}\b", "age secret key"),
    # --- AWS (env and ini names, AWS CLI JSON, CLI flags) ---
    _S(r"A(?:KIA|SIA|BIA|CCA|3T[A-Z0-9])(?<!\w.{4})[A-Z0-9]{16}\b", "AWS access key ID"),
    # The AWS CLI's JSON names are separate patterns: a leading alternation costs 4x on large content.
    _S(r"(?i)aws[_-]?secret[_-]?(?:access[_-]?)?key" + _ASSIGN + _AWS_SECRET, "AWS secret access key"),
    _S(r"SecretAccessKey" + _ASSIGN + _AWS_SECRET, "AWS secret access key"),
    _S(r"(?i)aws[_-]?session[_-]?token" + _ASSIGN + _AWS_SESSION, "AWS session token"),
    _S(r"SessionToken" + _ASSIGN + _AWS_SESSION, "AWS session token"),
    # --- forges ---
    _S(r"gh[pousr]_(?<!\w.{4})(?:[A-Za-z0-9]{36,}|\d{1,12}_[A-Za-z0-9_.-]{36,})", "GitHub token"),
    _S(r"github_pat_(?<!\w.{11})[A-Za-z0-9_]{82,}", "GitHub fine-grained token"),
    _S(r"gl(?<!\wgl)(?:pat|oas|dt|rtr?|cbt|ptt|ft|imt|agent|soat|ffct|wt)-[A-Za-z0-9_.-]{20,}", "GitLab token"),
    _S(r"gt[oa]_(?<!\w.{4})[a-z2-7]{52}\b", "Gitea OAuth secret"),
    # GITEA_TOKEN=…, gitea_token: …, GITEA_RUNNER_REGISTRATION_TOKEN=…: a bare Gitea token is 40
    # hex, so only the name beside it tells it from a commit hash.
    _S(r"(?i)gitea(?<![a-z0-9]gitea)\w{0,24}?(?:token|pat|key|secret|password)['\"]?[ \t]*[:=][ \t]*['\"]?"
       r"(?P<secret>[0-9A-Za-z]{40})(?![0-9A-Za-z])", "Gitea token assignment"),
    # --- AI providers ---
    _S(r"sk-ant-(?<!\w.{7})[a-z]+\d{2}-[A-Za-z0-9_-]{20,}", "Anthropic key"),
    # Project, service account, admin and user (sk-None-) keys; legacy keys hit the generic sk-. A key
    # body holds a capital or a digit early on (bounded, so "sk-proj-" repeated stays linear), which
    # keeps sk-proj-settings-panel-component-wrapper a name.
    _S(r"sk-(?<!\wsk-)(?:proj|svcacct|admin|None)-(?=[a-z_-]{0,40}[A-Z0-9])[A-Za-z0-9_-]{32,}", "OpenAI key"),
    _S(r"sk-or-v1-(?<!\w.{9})[0-9a-f]{64}\b", "OpenRouter key"),
    # Runs to the token's end ("-" and "_" included), so redaction leaves no tail.
    _S(r"sk-(?<!\wsk-)[0-9a-zA-Z]{20,}[0-9a-zA-Z_-]*", "secret key token (sk-)"),
    _S(r"gsk_(?<!\w.{4})[A-Za-z0-9]{52,}", "Groq key"),
    _S(r"xai-(?<!\w.{4})[A-Za-z0-9]{80,}", "xAI key"),
    _S(r"hf_(?<!\whf_)[A-Za-z0-9]{34,}", "Hugging Face token"),
    _S(r"api_org_(?<!\w.{8})[A-Za-z0-9]{34,}", "Hugging Face token"),
    _S(r"r8_(?<!\wr8_)[A-Za-z0-9_-]{37,}", "Replicate token"),
    # --- packages and containers ---
    _S(r"npm_(?<!\w.{4})[A-Za-z0-9]{36,}", "npm token"),
    _S(r"pypi-AgE(?<!\w.{8})[IN][A-Za-z0-9_-]{50,}", "PyPI token"),
    _S(r"dckr_(?<!\w.{5})(?:pat|oat)_[A-Za-z0-9_-]{24,}", "Docker Hub token"),
    _S(r"ptr_(?<!\w.{4})[A-Za-z0-9+/]{43}=", "Portainer API key"),
    _S(r"\"auth\"\s*:\s*\"(?P<secret>[A-Za-z0-9+/]{16,}={0,2})\"", "Docker registry auth"),
    _S(r"/api/(?:stacks/)?webhooks/"
       r"(?P<secret>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b",
       "Portainer webhook URL"),
    # --- cloud ---
    _S(r"AIza(?<!\w.{4})[A-Za-z0-9_-]{35,}", "Google API key"),
    _S(r"GOCSPX-(?<!\w.{7})[A-Za-z0-9_-]{28,}", "Google OAuth client secret"),
    _S(r"ya29\.(?<!\w.{5})[A-Za-z0-9_-]{20,}", "Google OAuth access token"),
    # No literal prefix: the "Q~" sits four characters in.
    _S(r"(?<![A-Za-z0-9_~.-])[A-Za-z0-9_~.-]{3}\dQ~[A-Za-z0-9_~.-]{31,34}(?![A-Za-z0-9_~.-])",
       "Azure Entra client secret"),
    _S(r"AccountKey=(?<!\w.{11})(?P<secret>[A-Za-z0-9+/]{86}==)", "Azure storage account key"),
    _S(r"do[opr]_v1_(?<!\w.{7})[a-f0-9]{64}\b", "DigitalOcean token"),
    _S(r"hv[sb]\.(?<!\w.{4})[A-Za-z0-9_-]{90,}", "Vault token"),
    _S(r"tskey-(?<!\w.{6})[a-z]+-[A-Za-z0-9_-]{10,}", "Tailscale key"),
    # --- SaaS ---
    _S(r"sk_(?<!\wsk_)(?:live|test|prod)_[A-Za-z0-9]{10,}", "Stripe secret key"),
    _S(r"rk_(?<!\wrk_)(?:live|test|prod)_[A-Za-z0-9]{10,}", "Stripe secret key"),
    _S(r"whsec_(?<!\w.{6})[A-Za-z0-9+/=]{24,}", "Stripe webhook secret"),
    _S(r"xox(?<!\wxox)[abeoprs](?:\.x(?:ox[abp]|app))?-[A-Za-z0-9-]{10,}", "Slack token"),
    _S(r"xapp-(?<!\w.{5})\d-[A-Za-z0-9-]{10,}", "Slack token"),
    _S(r"https://hooks\.slack\.com/(?:services|workflows|triggers)/(?P<secret>[A-Za-z0-9/_+-]{20,})",
       "Slack webhook URL"),
    _S(r"SG\.(?<!\wSG\.)[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b", "SendGrid key"),
    # --- structural and context ---
    _S(r"eyJ(?<!\weyJ)[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*", "JSON web token", ask=False),
    # scheme://user:password@host. Both parts hold only userinfo characters without shell syntax, so a
    # $(…), backtick, ; | & or ${IFS} ends the match; a %VAR% password is a placeholder, not a password.
    _S(rf"(?<=://){_USERINFO}+:(?!%)(?P<secret>{_USERINFO}{{3,}})(?=@)", "credentials in URL", ask=False),
    # As a header line or as a JSON / dict entry ("Authorization": "Bearer …", .mcp.json headers).
    _S(r"(?i)authorization['\"]?\s*:\s*['\"]?(?:bearer|token|basic)\s+" + _HEADER_VALUE, "authorization header"),
    # x-api-key, api-key (Azure): starts at the literal "-" and looks back for "api", in any case.
    _S(r"-[Kk][Ee][Yy](?<=[Aa][Pp][Ii]-[Kk][Ee][Yy])['\"]?\s*:\s*['\"]?" + _HEADER_VALUE, "API key header"),
    # The value holds only characters a key can contain: stopping at whitespace and shell syntax
    # keeps redaction from swallowing code planted after an empty key.
    _S(r"""api(?:_?key|_secret)\s*[=:]\s*['"](?P<secret>[^'"\s;()&|<>`$]{8,})['"]""", "hardcoded API key"),
]
