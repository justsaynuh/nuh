"""Trusted network host entries (``known_registries`` / ``nuh trust``).

Entry forms:

- ``example.com`` — exactly that host name.
- ``*.example.com`` or ``.example.com`` — any subdomain on a dot boundary
  (``a.example.com``, ``a.b.example.com``). The apex ``example.com`` itself
  is *not* matched, the same as a wildcard certificate; list it separately.
- ``192.0.2.7`` or ``2001:db8::1`` — exactly that IP address.
- ``192.0.2.0/24`` or ``2001:db8::/48`` — any IP address in the range.

Host names compare case-insensitively.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from functools import lru_cache

NAME = "name"
SUFFIX = "suffix"
NETWORK = "network"

_LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
# Ranges larger than a /8 (IPv4) or /32 (IPv6) are almost certainly a mistake.
_MAX_V4_PREFIX = 8
_MAX_V6_PREFIX = 32

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class HostEntry:
    kind: str
    name: str = ""
    network: IPNetwork | None = None


@lru_cache(maxsize=1024)
def parse_entry(raw: str) -> HostEntry:
    """Parse a config entry. Raises ValueError for a malformed IP/CIDR entry.

    Anything that is neither a suffix nor an IP form is an exact name, so
    legacy entries keep matching exactly as before.
    """
    text = raw.strip().lower().rstrip(".")
    if text.startswith("*."):
        return HostEntry(SUFFIX, name=text[2:])
    if text.startswith(".") and len(text) > 1:
        return HostEntry(SUFFIX, name=text[1:])
    if "/" in text:
        return HostEntry(NETWORK, network=ipaddress.ip_network(text, strict=False))
    try:
        return HostEntry(NETWORK, network=ipaddress.ip_network(text))
    except ValueError:
        # Not an IP address: an exact host name entry.
        return HostEntry(NAME, name=text)


def validate_entry(raw: str) -> str:
    """Return the normalized entry, or raise ValueError explaining what is wrong."""
    text = raw.strip().lower().rstrip(".")
    if not text:
        raise ValueError("empty host entry")
    if "://" in text:
        raise ValueError(f"'{raw}' is a URL; trust the bare host name instead")
    if any(ch.isspace() for ch in text) or "@" in text:
        raise ValueError(f"'{raw}' is not a host name, IP address or CIDR range")
    try:
        entry = parse_entry(text)
    except ValueError as exc:
        raise ValueError(f"'{raw}' is not a valid CIDR range: {exc}") from exc
    if entry.kind == NETWORK:
        _check_network_size(raw, entry.network)
        return text
    if "/" in text or ":" in text:
        raise ValueError(f"'{raw}': host entries take no port or path")
    if "*" in entry.name:
        raise ValueError(f"'{raw}': '*' is only allowed as a leading '*.' label")
    labels = entry.name.split(".")
    if not all(_LABEL_RE.match(label) for label in labels):
        raise ValueError(f"'{raw}' is not a valid host name")
    if entry.kind == SUFFIX and len(labels) < 2:
        raise ValueError(f"'{raw}' would trust a whole top-level domain")
    return text


def _check_network_size(raw: str, network: IPNetwork | None) -> None:
    if network is None:
        raise ValueError(f"'{raw}' is not a valid IP address or CIDR range")
    limit = _MAX_V4_PREFIX if network.version == 4 else _MAX_V6_PREFIX
    if network.prefixlen < limit:
        raise ValueError(
            f"'{raw}' is too broad (shorter than /{limit}); trust a narrower range")


def host_matches(host: str, entry: HostEntry) -> bool:
    """True when *host* (no port) is covered by *entry*."""
    host = host.strip().lower().rstrip(".").strip("[]")
    if entry.kind == NAME:
        return host == entry.name
    if entry.kind == SUFFIX:
        return host.endswith("." + entry.name)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # a name never matches an IP entry
    return entry.network is not None and address in entry.network


def is_trusted(host: str, entries: set[str]) -> bool:
    """True when any entry in *entries* covers *host*. Malformed entries never match."""
    for raw in entries:
        try:
            entry = parse_entry(raw)
        except ValueError:
            continue  # reported once when the config is merged (context.py)
        if host_matches(host, entry):
            return True
    return False
