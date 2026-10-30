"""Read ssh, scp and sftp command lines: their destinations and the options that run commands.

Used by the network context (every remote is a destination; some options
take the localhost shortcut away) and by the floor (options that run a local
command: rule ``ssh_exec_options``). The option split follows
manuelschipper/nah (MIT), crates/effinterp-engine/src/models/subprocess.rs
(``ssh_loopback``, ``client_command_option``); the code is nuh's.
"""

import os
import re
import urllib.parse
from dataclasses import dataclass, field

SSH_COMMANDS = frozenset({"ssh", "scp", "sftp"})
# Single-letter options that take a value, across ssh, scp and sftp.
_VALUED = frozenset("bcDEeFIiJLlmOoPpQRSWwB")
# -o keywords (lowercase) that run a local command, and the values that do not.
_EXEC_OPTIONS = {
    "proxycommand": frozenset({"none"}),
    "localcommand": frozenset(),
    "permitlocalcommand": frozenset({"no"}),
    "knownhostscommand": frozenset({"none"}),
    "include": frozenset(),
}
# -o keywords that change where the connection really goes.
_ROUTE_OPTIONS = frozenset({"proxyjump", "hostname"})
_NO_CONFIG = frozenset({"/dev/null", "none"})
_IPV6_RE = re.compile(r"^\[([^\]]+)\]")


@dataclass
class SshArgs:
    command: str
    flags: list[tuple[str, str]] = field(default_factory=list)  # (letter, value or "")
    options: list[tuple[str, str]] = field(default_factory=list)  # -o (keyword lowercase, value)
    positionals: list[str] = field(default_factory=list)


def parse(command: str, args: list[str]) -> SshArgs:
    """Split *args*; ssh reads options until its remote command starts (after the destination)."""
    parsed = SshArgs(command)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            parsed.positionals += args[i + 1:]
            break
        if command == "ssh" and len(parsed.positionals) == 1 and not arg.startswith("-"):
            parsed.positionals += args[i:]  # the remote command
            break
        if not arg.startswith("-") or arg == "-":
            parsed.positionals.append(arg)
            i += 1
            continue
        i += _parse_cluster(arg, args[i + 1] if i + 1 < len(args) else None, parsed)
    return parsed


def _parse_cluster(arg: str, following: str | None, parsed: SshArgs) -> int:
    """Record the flags in one ``-xyz`` word; return how many words it used (1 or 2)."""
    for j in range(1, len(arg)):
        letter = arg[j]
        if letter not in _VALUED:
            parsed.flags.append((letter, ""))
            continue
        attached = arg[j + 1:]
        value = attached if attached else (following or "")
        parsed.flags.append((letter, value))
        if letter == "o":
            parsed.options.append(_split_option(value))
        return 1 if attached else 2
    return 1


def _split_option(value: str) -> tuple[str, str]:
    """``Keyword=value`` or ``Keyword value`` → (keyword lowercase, value)."""
    parts = re.split(r"\s*=\s*|\s+", value.strip(), maxsplit=1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def _strip_user_port(spec: str) -> str:
    host = spec.rsplit("@", 1)[-1]
    match = _IPV6_RE.match(host)
    if match:
        return match.group(1)
    return host.split(":", 1)[0]


def _url_host(arg: str) -> str | None:
    if "://" not in arg:
        return None
    return urllib.parse.urlparse(arg).hostname


def destinations(parsed: SshArgs) -> list[str]:
    """Every remote host the command connects to or through, in order."""
    hosts: list[str] = []
    if parsed.command in ("ssh", "sftp"):
        candidates = parsed.positionals[:1]
    else:
        candidates = [a for a in parsed.positionals if "://" in a or _is_remote_operand(a)]
    for arg in candidates:
        hosts.append(_url_host(arg) or _strip_user_port(arg))
    for letter, value in parsed.flags:
        if letter == "J":
            hosts += [_strip_user_port(part) for part in value.split(",") if part]
    for key, value in parsed.options:
        if key == "proxyjump" and value.lower() != "none":
            hosts += [_strip_user_port(part) for part in value.split(",") if part]
        elif key == "hostname" and value:
            hosts.append(value)
    return [h for h in hosts if h]


def _is_remote_operand(arg: str) -> bool:
    """scp operand ``[user@]host:path`` (not a local path that merely holds a colon)."""
    if arg.startswith(("/", "./", "../", "~")):
        return False
    head = arg.split("/", 1)[0]
    return ":" in head or (arg.startswith("[") and "]:" in arg)


def exec_reason(parsed: SshArgs) -> str:
    """Why the command line runs a local command (ProxyCommand, -F config, scp -S, …), or ""."""
    for key, value in parsed.options:
        harmless = _EXEC_OPTIONS.get(key)
        if harmless is not None and value.lower() not in harmless:
            return f"{parsed.command} -o {key} runs a local command"
    for letter, value in parsed.flags:
        if letter == "F" and value not in _NO_CONFIG:
            return f"{parsed.command} -F {value}: a config file can run local commands"
        if letter == "S" and parsed.command in ("scp", "sftp"):
            return f"{parsed.command} -S runs {value} as its ssh program"
        if letter == "D" and parsed.command == "sftp":
            return f"sftp -D runs {value} as its server"
    return ""


def shortcut_blocker(parsed: SshArgs) -> str:
    """Why a localhost destination is not just localhost here (jump, proxy, -W, -F), or ""."""
    reason = exec_reason(parsed)
    if reason:
        return reason
    for letter, _value in parsed.flags:
        if letter in ("J", "W"):
            return f"{parsed.command} -{letter} routes the connection elsewhere"
    for key, _value in parsed.options:
        if key in _ROUTE_OPTIONS:
            return f"{parsed.command} -o {key} routes the connection elsewhere"
    return ""


def parse_tokens(tokens: list[str]) -> SshArgs | None:
    """Parse a command line whose program is ssh, scp or sftp; None for anything else."""
    if not tokens:
        return None
    command = os.path.basename(tokens[0])
    return parse(command, tokens[1:]) if command in SSH_COMMANDS else None
