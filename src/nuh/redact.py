"""Secret redaction for LLM providers that opt in (``llm.<provider>.redact_secrets``).

``redact_secrets`` replaces each secret in place, never the line around it, so
the rest of a command chained with ``&&`` stays readable:

- a private key (PEM, PGP armor, PuTTY .ppk) becomes one placeholder when
  every line of it is key material; otherwise only its markers and base64
  lines go and every other line stays visible (key_blocks.py);
- each match of the secret table (secret_patterns.py) and of config-added
  ``secret`` patterns becomes ``[redacted: <description>]``; for a pattern
  with a ``secret`` group (keyword patterns such as
  ``aws_secret_access_key = …``), only that group's text is replaced.

The decision log is not redacted: by the time a tool call exists its secret
is already in the agent's transcript and on the model's server, and a raw
log is what replay needs.
"""

from __future__ import annotations

import re
from typing import Callable

from nuh.key_blocks import redact_key_blocks
from nuh.secret_patterns import SECRET_GROUP


def redact_secrets(text: str) -> str:
    """*text* with each private key block and secret replaced in place by ``[redacted: <description>]``."""
    from nuh.content import get_secret_patterns  # lazy: content loads config

    text = redact_key_blocks(text)
    for regex, desc in get_secret_patterns():
        text = regex.sub(_replacer(regex, f"[redacted: {desc}]"), text)
    return text


def _replacer(regex: re.Pattern[str], placeholder: str) -> Callable[[re.Match[str]], str]:
    """The ``sub`` callback putting *placeholder* over a match, or over just its ``secret`` group.

    A callback, not a template string, so a description from config is never
    read as a backreference.
    """
    if SECRET_GROUP not in regex.groupindex:
        return lambda match: placeholder

    def value_only(match: re.Match[str]) -> str:
        start, end = match.span(SECRET_GROUP)
        if start < 0:  # an optional secret group that did not take part: nothing to hide
            return match.group()
        whole = match.group()
        offset = match.start()
        return whole[:start - offset] + placeholder + whole[end - offset:]

    return value_only
