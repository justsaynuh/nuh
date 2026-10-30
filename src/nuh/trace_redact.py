"""What an LLM trace hides: auth header values and credential query parameters in provider URLs."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"

_AUTH_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key"})
_AUTH_WORDS = ("token", "secret", "api-key", "api_key", "apikey")
_AUTH_QUERY = frozenset({"key", "api_key", "apikey", "api-key", "token", "access_token", "auth"})


def redact_headers(items: list[tuple[str, str]]) -> dict:
    """Header dict with auth/key/token values replaced by ``<redacted>``."""
    out = {}
    for name, value in items:
        lower = name.lower()
        secret = lower in _AUTH_HEADERS or any(w in lower for w in _AUTH_WORDS) or lower.endswith("-key")
        out[name] = REDACTED if secret else value
    return out


def redact_url(url: str) -> str:
    """URL with credential-looking query parameter values replaced."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = [(k, REDACTED if k.lower() in _AUTH_QUERY else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(query, safe="<>")))
