"""Context resolution — filesystem and network context for 'context' policy decisions.

Database and lang_exec context are in ``db_context`` and ``lang_exec_context``;
the hosts a network command names in ``host_extract``.
"""

import os
import sys

from nuh import host_trust, path_names, paths, sensitive_paths, ssh_args, taxonomy
from nuh.db_context import resolve_database_context
from nuh.host_extract import extract_hosts
from nuh.lang_exec_context import resolve_lang_exec_context


# Known safe registries / hosts for network context.
_KNOWN_HOSTS_DEFAULTS: set[str] = {
    "npmjs.org", "www.npmjs.org", "registry.npmjs.org",
    "pypi.org", "files.pythonhosted.org",
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "crates.io",
    "rubygems.org",
    "packagist.org",
    "registry.yarnpkg.com",
    "registry.npmmirror.com",
    "dl.google.com",
    "repo.maven.apache.org",
    "pkg.go.dev", "proxy.golang.org",
    "hub.docker.com", "registry.hub.docker.com", "ghcr.io",
}
_known_hosts: set[str] = set(_KNOWN_HOSTS_DEFAULTS)
_known_hosts_merged = False


def _ensure_known_hosts_merged():
    """Lazy one-time merge of config known_registries into _known_hosts."""
    global _known_hosts_merged, _known_hosts
    if _known_hosts_merged:
        return
    _known_hosts_merged = True
    try:
        from nuh.config import get_config, _parse_add_remove
        cfg = get_config()
        if cfg.profile == "none":
            _known_hosts.clear()
        add, remove = _parse_add_remove(cfg.known_registries)
        for raw in (str(h) for h in add):
            try:
                host_trust.parse_entry(raw)
            except ValueError as exc:
                sys.stderr.write(f"nuh: config: known_registries: ignoring '{raw}': {exc}\n")
                continue
            _known_hosts.add(raw)
        _known_hosts.difference_update(str(h) for h in remove)
    except Exception as exc:
        sys.stderr.write(f"nuh: config: known_registries: {exc}\n")


def reset_known_hosts():
    """Restore defaults and clear merge flag (for testing)."""
    global _known_hosts_merged, _known_hosts
    _known_hosts_merged = False
    _known_hosts = set(_KNOWN_HOSTS_DEFAULTS)


# Localhost addresses.
_LOCALHOST: set[str] = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def resolve_context(
    action_type: str,
    *,
    tokens: list[str] | None = None,
    tool_input: dict | None = None,
    target_path: str | None = None,
    inline_code: str | None = None,
) -> tuple[str, str]:
    """Dispatch context resolution by action type.

    Callers provide what they have:
    - bash_policy.py: tokens + target_path (pre-extracted)
    - hook.py: tool_input (MCP structured input)
    """
    if action_type in (taxonomy.NETWORK_OUTBOUND, taxonomy.NETWORK_WRITE):
        if tokens:
            return resolve_network_context(tokens, action_type)
        return taxonomy.ASK, "unknown host"

    if action_type == taxonomy.DB_WRITE:
        return resolve_database_context(tokens, tool_input)

    if action_type in (taxonomy.FILESYSTEM_READ, taxonomy.FILESYSTEM_WRITE,
                       taxonomy.FILESYSTEM_DELETE):
        if target_path:
            return resolve_filesystem_context(target_path)
        if action_type in (taxonomy.FILESYSTEM_DELETE, taxonomy.FILESYSTEM_WRITE):
            return taxonomy.ASK, f"{action_type}: no target path extracted"
        return taxonomy.ALLOW, f"{action_type}: no target path"

    if action_type == taxonomy.LANG_EXEC:
        return resolve_lang_exec_context(target_path, inline_code=inline_code)

    return taxonomy.ASK, f"{action_type}: no context resolver"


def resolve_filesystem_context(target_path: str) -> tuple[str, str]:
    """Resolve filesystem context for a target path.

    Returns (decision, reason).
    """
    if not target_path:
        return taxonomy.ALLOW, "no target path"

    # profile: none disables boundary check (defense in depth)
    from nuh.config import get_config
    if get_config().profile == "none":
        return taxonomy.ALLOW, "profile: none (no boundary check)"

    resolved = paths.resolve_path(target_path)

    # Core path check (hook + sensitive)
    basic = sensitive_paths.check_path_basic_raw(target_path)
    if basic:
        return basic

    # Trusted paths check — before project root so it works with no git root (FD-107)
    if paths.is_trusted_path(resolved):
        return taxonomy.ALLOW, f"trusted path: {path_names.friendly_path(resolved)}"

    # Project root check
    project_root = paths.get_project_root()
    if project_root is None:
        return taxonomy.ASK, f"outside project (no git root): {path_names.friendly_path(resolved)}"

    real_root = os.path.realpath(project_root)
    if resolved == real_root or resolved.startswith(real_root + os.sep):
        return taxonomy.ALLOW, f"inside project: {path_names.friendly_path(resolved)}"

    return taxonomy.ASK, f"outside project: {path_names.friendly_path(resolved)}"


def resolve_network_context(tokens: list[str], action_type: str = taxonomy.NETWORK_OUTBOUND) -> tuple[str, str]:
    """Resolve network context for outbound/write commands.

    Every destination host is checked; the strictest decision wins.
    Returns (decision, reason).
    """
    _ensure_known_hosts_merged()
    hosts = extract_hosts(tokens)
    if not hosts:
        return taxonomy.ASK, "unknown host"
    ssh = ssh_args.parse_tokens(tokens)
    no_localhost = ssh_args.shortcut_blocker(ssh) if ssh is not None else ""

    worst: tuple[str, str] | None = None
    for host in dict.fromkeys(hosts):
        decision, reason = _host_decision(host, action_type, no_localhost)
        if worst is None or taxonomy.STRICTNESS.get(decision, 2) > taxonomy.STRICTNESS.get(worst[0], 2):
            worst = (decision, reason)
    return worst


def _host_decision(host: str, action_type: str, no_localhost: str = "") -> tuple[str, str]:
    """Network decision for a single destination host.

    *no_localhost* says why the localhost shortcut does not apply (an ssh
    jump host, proxy or config that sends the connection elsewhere).
    """
    # Strip a port from host:port (bare IPv6 addresses have several colons)
    host_no_port = host.split(":")[0] if host.count(":") == 1 else host

    if host_no_port in _LOCALHOST and no_localhost:
        return taxonomy.ASK, f"localhost: {host} ({no_localhost})"

    # Localhost — allowed for reads, ask for writes (exfiltration risk)
    if host_no_port in _LOCALHOST:
        if action_type == taxonomy.NETWORK_WRITE:
            return taxonomy.ASK, f"network_write to localhost: {host}"
        return taxonomy.ALLOW, f"localhost: {host}"

    # Network writes always ask (known hosts only trusted for reads)
    if action_type == taxonomy.NETWORK_WRITE:
        return taxonomy.ASK, f"network_write → ask (host: {host_no_port})"

    # Known hosts (defaults + user config, merged): names, *.suffix, IP, CIDR
    if host_trust.is_trusted(host_no_port, _known_hosts):
        return taxonomy.ALLOW, f"known host: {host_no_port}"

    return taxonomy.ASK, f"unknown host: {host_no_port}"
