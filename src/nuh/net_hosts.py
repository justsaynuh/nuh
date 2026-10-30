"""Destination hosts of curl and wget invocations.

Every host the request's data can reach is returned: each URL, an explicit
``--url``, proxies, and connection overrides (``--resolve``,
``--connect-to``). Option values (output files, headers, user agents…) are
skipped using the option lists from the curl(1) and wget(1) man pages, in
separate (``-o file``), attached (``--output=file``, ``-ofile``) and short
cluster (``-sSo file``) forms.

Unknowable destinations (URLs read from a file or config) are reported as a
``<…>`` marker that never matches a trusted host, so the caller asks.
"""

from __future__ import annotations

import re
import urllib.parse

# --- curl ---------------------------------------------------------------

_CURL_SHORT_VALUE = set("AbcCdDeEFhHKmoPQrtTuUwxXyYz")
_CURL_LONG_VALUE = {
    "abstract-unix-socket", "alt-svc", "aws-sigv4", "cacert", "capath", "cert",
    "cert-type", "ciphers", "config", "connect-timeout", "connect-to",
    "continue-at", "cookie", "cookie-jar", "create-file-mode", "crlfile", "curves",
    "data", "data-ascii", "data-binary", "data-raw", "data-urlencode", "delegation",
    "dns-interface", "dns-ipv4-addr", "dns-ipv6-addr", "dns-servers", "doh-url",
    "dump-header", "ech", "egd-file", "engine", "etag-compare", "etag-save",
    "expect100-timeout", "form", "form-string", "ftp-account",
    "ftp-alternative-to-user", "ftp-method", "ftp-port", "ftp-ssl-ccc-mode",
    "happy-eyeballs-timeout-ms", "haproxy-clientip", "header", "help",
    "hostpubmd5", "hostpubsha256", "hsts", "interface", "ip-tos", "ipfs-gateway",
    "json", "keepalive-cnt", "keepalive-time", "key", "key-type", "krb", "libcurl",
    "limit-rate", "local-port", "login-options", "mail-auth", "mail-from",
    "mail-rcpt", "max-filesize", "max-redirs", "max-time", "netrc-file", "noproxy",
    "oauth2-bearer", "output", "output-dir", "parallel-max", "pass",
    "pinnedpubkey", "preproxy", "proto", "proto-default", "proto-redir", "proxy",
    "proxy-cacert", "proxy-capath", "proxy-cert", "proxy-cert-type",
    "proxy-ciphers", "proxy-crlfile", "proxy-header", "proxy-key",
    "proxy-key-type", "proxy-pass", "proxy-pinnedpubkey", "proxy-service-name",
    "proxy-tls13-ciphers", "proxy-tlsauthtype", "proxy-tlspassword",
    "proxy-tlsuser", "proxy-user", "proxy1.0", "pubkey", "quote", "random-file",
    "range", "rate", "referer", "request", "request-target", "resolve", "retry",
    "retry-delay", "retry-max-time", "sasl-authzid", "service-name", "socks4",
    "socks4a", "socks5", "socks5-gssapi-service", "socks5-hostname",
    "speed-limit", "speed-time", "stderr", "tftp-blksize", "time-cond",
    "tls-max", "tls13-ciphers", "tlsauthtype", "tlspassword", "tlsuser", "trace",
    "trace-ascii", "trace-config", "unix-socket", "upload-file", "url",
    "url-query", "user", "user-agent", "variable", "vlan-priority", "write-out",
}
_CURL_SHORT_LONG = {"x": "proxy", "K": "config"}
_CURL_PROXY = {"proxy", "preproxy", "socks4", "socks4a", "socks5", "socks5-hostname",
               "proxy1.0", "doh-url"}

# --- wget ---------------------------------------------------------------

# `-n` takes its v/c/d/H/p suffix as an attached argument in wget's getopt.
_WGET_SHORT_VALUE = set("aABCDeiIloOPQRtTUwXYn")
_WGET_LONG_VALUE = {
    "output-file", "append-output", "execute", "output-document",
    "directory-prefix", "tries", "timeout", "dns-timeout", "connect-timeout",
    "read-timeout", "wait", "waitretry", "quota", "user-agent", "header",
    "post-data", "post-file", "body-data", "body-file", "method", "user",
    "password", "http-user", "http-password", "proxy-user", "proxy-password",
    "ftp-user", "ftp-password", "load-cookies", "save-cookies", "referer",
    "input-file", "base", "config", "domains", "exclude-domains",
    "include-directories", "exclude-directories", "accept", "reject",
    "accept-regex", "reject-regex", "level", "limit-rate", "bind-address",
    "bind-dns-address", "dns-servers", "certificate", "certificate-type",
    "private-key", "private-key-type", "ca-certificate", "ca-directory",
    "crl-file", "pinnedpubkey", "random-file", "egd-file", "secure-protocol",
    "ciphers", "restrict-file-names", "default-page", "cut-dirs", "progress",
    "local-encoding", "remote-encoding", "rejected-log", "warc-file",
    "warc-header", "warc-max-size", "warc-cdx", "warc-dedup", "warc-tempdir",
    "use-askpass", "report-speed", "retry-on-http-error", "hsts-file",
    "prefer-family", "backups", "start-pos", "compression", "regex-type",
    "proxy", "cache", "max-redirect", "metalink-index", "preferred-location",
}
_WGET_SHORT_LONG = {"i": "input-file", "e": "execute", "B": "base"}
_WGET_PROXY_RE = re.compile(r"^\s*(https?|ftp)_proxy\s*=\s*(\S+)\s*$", re.IGNORECASE)

INPUT_FILE_MARKER = "<urls from input file>"
CONFIG_FILE_MARKER = "<urls from config file>"


def url_host(text: str) -> str | None:
    """Hostname of a URL or bare ``host[:port][/path]`` string, else None."""
    text = text.strip()
    if not text:
        return None
    candidate = text if "://" in text else f"http://{text.lstrip('/')}"
    try:
        host = urllib.parse.urlparse(candidate).hostname
    except ValueError:
        # Malformed authority (e.g. bad IPv6 brackets): no trustworthy host.
        return text
    return host or None


def curl_hosts(args: list[str]) -> list[str]:
    """Every destination host of a curl command."""
    hosts: list[str] = []
    for name, value in _iter_options(args, _CURL_SHORT_VALUE, _CURL_LONG_VALUE,
                                     _CURL_SHORT_LONG):
        if name is None or name == "url" or name in _CURL_PROXY:
            hosts.extend(_hosts_of(value))
        elif name in ("resolve", "connect-to"):
            hosts.extend(_override_targets(value))
        elif name == "config":
            hosts.append(CONFIG_FILE_MARKER)
    return hosts


def wget_hosts(args: list[str]) -> list[str]:
    """Every destination host of a wget command."""
    hosts: list[str] = []
    for name, value in _iter_options(args, _WGET_SHORT_VALUE, _WGET_LONG_VALUE,
                                     _WGET_SHORT_LONG):
        if name is None:
            hosts.extend(_hosts_of(value))
        elif name == "proxy":
            continue  # --proxy=on/off toggles use of the configured proxy
        elif name == "base":
            hosts.extend(_hosts_of(value))
        elif name == "input-file":
            hosts.append(INPUT_FILE_MARKER)
        elif name == "execute":
            match = _WGET_PROXY_RE.match(value)
            if match:
                hosts.extend(_hosts_of(match.group(2)))
    return hosts


def _hosts_of(value: str) -> list[str]:
    host = url_host(value)
    return [host] if host else []


def _override_targets(value: str) -> list[str]:
    """Addresses curl really connects to for ``--resolve`` / ``--connect-to``.

    ``--resolve [+]host:port:addr[,addr]…`` and ``--connect-to
    host:port:host2:port2`` both carry the destination in the third field
    (an empty one means "unchanged"). A value without that field is returned
    whole so it never matches a trusted host.
    """
    parts = _split_colons(value)
    if len(parts) < 3:
        return [value] if value else []
    return [addr.strip("[]") for addr in parts[2].split(",") if addr.strip("[]")]


def _split_colons(value: str) -> list[str]:
    """Split on ``:`` outside ``[...]`` (bracketed IPv6 addresses)."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in value:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(depth - 1, 0)
        if ch == ":" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _iter_options(
    args: list[str],
    short_value: set[str],
    long_value: set[str],
    short_long: dict[str, str],
) -> list[tuple[str | None, str]]:
    """Return (option name, value) pairs; name None marks a positional URL.

    Short options are reported by their long name when *short_long* maps
    them, otherwise by the letter itself.
    """
    out: list[tuple[str | None, str]] = []
    i = 0
    while i < len(args):
        tok = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if tok == "--":
            out.extend((None, rest) for rest in args[i + 1:])
            break
        if tok.startswith("--"):
            name, eq, attached = tok[2:].partition("=")
            if name in long_value:
                if eq:
                    out.append((name, attached))
                else:
                    out.append((name, nxt))
                    i += 1
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            consumed = _short_cluster(tok[1:], nxt, short_value, short_long, out)
            i += 1 + consumed
            continue
        out.append((None, tok))
        i += 1
    return out


def _short_cluster(
    letters: str,
    nxt: str,
    short_value: set[str],
    short_long: dict[str, str],
    out: list[tuple[str | None, str]],
) -> int:
    """Parse ``-sSo`` style clusters; returns how many following args were consumed."""
    for pos, letter in enumerate(letters):
        if letter not in short_value:
            continue
        name = short_long.get(letter, letter)
        attached = letters[pos + 1:]
        if attached:
            out.append((name, attached))
            return 0
        out.append((name, nxt))
        return 1
    return 0
