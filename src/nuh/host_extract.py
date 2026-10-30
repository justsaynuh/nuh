"""The destination hosts a network command names: curl/wget URLs, ssh/scp/sftp, httpie, nc/telnet."""

import os
import urllib.parse

from nuh import net_hosts, ssh_args


def extract_hosts(tokens: list[str]) -> list[str]:
    """Every destination host of a network command, in argv order.

    curl and wget report all URLs, proxies and connection overrides; other
    commands report their single extracted host.
    """
    if not tokens:
        return []
    cmd = os.path.basename(tokens[0])
    if cmd == "curl":
        return net_hosts.curl_hosts(tokens[1:])
    if cmd == "wget":
        return net_hosts.wget_hosts(tokens[1:])
    ssh = ssh_args.parse_tokens(tokens)
    if ssh is not None:
        return ssh_args.destinations(ssh)
    host = extract_host(tokens)
    return [host] if host else []


def extract_host(tokens: list[str]) -> str | None:
    """Extract the first hostname from network command tokens.

    Handles curl/wget URLs, ssh user@host, nc/telnet host.
    """
    if not tokens:
        return None

    cmd = tokens[0]
    args = tokens[1:]

    if os.path.basename(cmd) in ("curl", "wget"):
        hosts = extract_hosts(tokens)
        return hosts[0] if hosts else None
    if cmd in ("http", "https", "xh", "xhs"):
        return _extract_httpie_host(args)
    if cmd in ("ssh", "scp", "sftp"):
        return _extract_ssh_host(cmd, args)
    if cmd in ("nc", "ncat", "telnet"):
        return _extract_positional_host(args, {"-p", "-w", "-s"})

    # Fallback: try URL extraction
    return _extract_url_host(args)


_HTTPIE_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _extract_httpie_host(args: list[str]) -> str | None:
    """Extract host from httpie args. Skips flags, METHOD tokens, data items."""
    for arg in args:
        if arg.startswith("-"):
            continue
        # Skip method tokens
        if arg.upper() in _HTTPIE_METHODS:
            continue
        # Skip data items (key=value, key:=value, key@file)
        if "=" in arg or ":=" in arg or ("@" in arg and "://" not in arg):
            continue
        # This is the URL/host
        if "://" in arg:
            parsed = urllib.parse.urlparse(arg)
            if parsed.hostname:
                return parsed.hostname
        # Bare hostname
        if "." in arg or ":" in arg:
            part = arg.split("/")[0]
            if part:
                return part.split(":")[0] if ":" in part else part
        return arg
    return None


def _extract_url_host(args: list[str]) -> str | None:
    """Find URL-like argument and parse hostname."""
    for arg in args:
        if arg.startswith("-"):
            continue
        # Try parsing as URL
        if "://" in arg or arg.startswith("//"):
            parsed = urllib.parse.urlparse(arg)
            if parsed.hostname:
                return parsed.hostname
        # Bare hostname:port or hostname/path
        if "." in arg or ":" in arg:
            # Could be host:port or host/path
            part = arg.split("/")[0]
            if part and not part.startswith("-"):
                return part.split(":")[0] if ":" in part else part
    return None


def _looks_like_local_path(arg: str) -> bool:
    """Check if an argument looks like a local file path rather than a hostname."""
    return arg.startswith(("/", "./", "../", "~"))


def _strip_host_from_colon_suffix(s: str) -> str:
    """Extract hostname from host:port or host:path, handling [IPv6] brackets."""
    if s.startswith("["):
        end = s.find("]")
        if end != -1:
            return s[1:end]
    return s.split(":")[0]


# ssh/scp/sftp valued flags — flags that consume the next argument.
# Comprehensive set to avoid misidentifying flag values as hostnames.
_SSH_VALUED_FLAGS = {
    "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L",
    "-l", "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w",
}


def _extract_ssh_host(cmd: str, args: list[str]) -> str | None:
    """Extract host from ssh/scp/sftp args.

    Two-pass approach:
    1. Prefer args with @ (user@host) — unambiguous.
    2. For scp, prefer args with : (host:path) — remote indicator.
    3. Fall back to first positional that doesn't look like a local path.
    """
    positionals = _collect_positionals(args, _SSH_VALUED_FLAGS)

    # Pass 1: look for user@host
    for arg in positionals:
        if "@" in arg:
            host_part = arg.split("@", 1)[1]
            return _strip_host_from_colon_suffix(host_part) if ":" in host_part else host_part

    # Pass 2 (scp/sftp): look for host:path (colon indicates remote)
    if cmd in ("scp", "sftp"):
        for arg in positionals:
            if ":" in arg:
                return _strip_host_from_colon_suffix(arg)

    # Pass 3: first positional that doesn't look like a local path
    for arg in positionals:
        if not _looks_like_local_path(arg):
            return arg

    return None


def _collect_positionals(args: list[str], valued_flags: set[str]) -> list[str]:
    """Collect positional (non-flag) args, skipping valued flags and their values."""
    positionals = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if arg in valued_flags:
                skip_next = True
            continue
        positionals.append(arg)
    return positionals


def _extract_positional_host(args: list[str], valued_flags: set[str]) -> str | None:
    """Extract host from positional args, skipping valued flags. Handles user@host."""
    positionals = _collect_positionals(args, valued_flags)
    for arg in positionals:
        if "@" in arg:
            host_part = arg.split("@", 1)[1]
            return _strip_host_from_colon_suffix(host_part) if ":" in host_part else host_part
    # First positional that doesn't look like a local path
    for arg in positionals:
        if not _looks_like_local_path(arg):
            return arg
    # Last resort: first positional
    return positionals[0] if positionals else None
