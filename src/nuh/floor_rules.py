"""Floor rules that read a Bash stage's words, whatever its action type or policy.

Each check returns a ``floor.Verdict`` (ask or block, with its rule id) or
None. ``bash`` applies them to every simple command it classifies, nested
ones included, so a user's ``actions:`` policy for the command's type cannot
switch them off; only ``human_only: {<rule>: false}`` in the global config
lets the LLM decide the resulting ask.
"""

import os
from collections.abc import Iterator

from nuh import floor, git_exec, self_protect, ssh_args, taxonomy

# Builtins whose NAME=value arguments set the environment of later commands.
_ENV_SETTERS = frozenset({"export", "env", "declare", "typeset", "local", "readonly"})


def is_env_assignment(tok: str) -> bool:
    """Return True for env-style NAME=value assignments."""
    if "=" not in tok or tok.startswith("="):
        return False
    name, _ = tok.split("=", 1)
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        ch.isalnum() or ch == "_" for ch in name
    )


def _assignments(tokens: list[str]) -> Iterator[tuple[str, str]]:
    """(name, value) of leading assignments, and of the arguments of export/env/declare/…."""
    i = 0
    while i < len(tokens) and is_env_assignment(tokens[i]):
        name, value = tokens[i].split("=", 1)
        yield name, value
        i += 1
    if i < len(tokens) and os.path.basename(tokens[i]) in _ENV_SETTERS:
        for tok in tokens[i + 1:]:
            if is_env_assignment(tok):
                name, value = tok.split("=", 1)
                yield name, value


def env_floor(tokens: list[str]) -> floor.Verdict | None:
    """Floor verdict for environment a stage sets (before assignments are stripped)."""
    for name, value in _assignments(tokens):
        reason = self_protect.agent_home_reason(name, value)
        if reason:
            return floor.Verdict(taxonomy.ASK, reason, "unguarded_launch")
        reason = git_exec.env_reason(name, value)
        if reason:
            return floor.Verdict(taxonomy.ASK, reason, "git_exec")
    return None


_POWER_COMMANDS = frozenset({"shutdown", "reboot", "poweroff", "halt"})
_SYSTEMCTL_VERBS = frozenset({
    "poweroff", "reboot", "halt", "suspend", "hibernate", "hybrid-sleep", "suspend-then-hibernate",
    "kexec", "isolate", "rescue", "emergency", "default", "stop", "kill", "mask", "disable",
})
_LOGINCTL_VERBS = frozenset({"poweroff", "reboot", "halt", "suspend", "hibernate", "terminate-session",
                             "terminate-user", "kill-session", "kill-user"})
_INIT_LEVELS = frozenset({"0", "1", "6", "s", "S"})
_HELP = frozenset({"--help", "--version"})  # shutdown -h means halt, not help

_NETCATS = frozenset({"nc", "ncat", "netcat", "nc.traditional", "nc.openbsd"})
_NETCAT_EXEC_FLAGS = ("-e", "-c", "--exec", "--sh-exec", "--lua-exec")
_NETCAT_EXEC_ATTACHED = ("--exec=", "--sh-exec=", "--lua-exec=", "-e/", "-c/")
_SOCAT_EXEC_ADDRESSES = ("EXEC:", "SYSTEM:")
NETWORK_DEVICE_PREFIXES = ("/dev/tcp/", "/dev/udp/")


def _first_operand(args: list[str]) -> str:
    return next((a for a in args if not a.startswith("-")), "")


def power_reason(words: list[str]) -> str:
    """Why *words* power the machine off or down a service, or ""."""
    cmd, args = words[0], words[1:]
    if any(a in _HELP for a in args):
        return ""
    if cmd in _POWER_COMMANDS:
        return f"{cmd} powers the machine off or restarts it"
    if cmd in ("init", "telinit") and _first_operand(args) in _INIT_LEVELS:
        return f"{cmd} {_first_operand(args)} changes the runlevel"
    if cmd == "systemctl" and _first_operand(args) in _SYSTEMCTL_VERBS:
        return f"systemctl {_first_operand(args)} stops services or the machine"
    if cmd == "loginctl" and _first_operand(args) in _LOGINCTL_VERBS:
        return f"loginctl {_first_operand(args)} ends sessions or powers off"
    if cmd == "service" and len(args) >= 2 and args[1] == "stop":
        return f"service {args[0]} stop stops a service"
    return ""


def network_shell_reason(words: list[str]) -> str:
    """Why *words* wire a shell or program to a network socket, or ""."""
    cmd, args = words[0], words[1:]
    if cmd in _NETCATS and any(a in _NETCAT_EXEC_FLAGS or a.startswith(_NETCAT_EXEC_ATTACHED) for a in args):
        return f"{cmd} runs a program on a network connection"
    if cmd == "socat" and any(a.upper().startswith(_SOCAT_EXEC_ADDRESSES) for a in args):
        return "socat runs a program on a network connection"
    if any(prefix in w for w in words for prefix in NETWORK_DEVICE_PREFIXES):
        return "opens a raw network socket (/dev/tcp, /dev/udp)"
    return ""


# Privilege wrappers: their options that take a value, and those that take a command string.
_PRIVILEGE_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U", "--user", "--group",
                       "--close-from", "--chdir", "--host", "--prompt", "--role", "--type",
                       "--command-timeout", "--other-user"}),
    "doas": frozenset({"-u", "-C"}),
    "pkexec": frozenset({"--user"}),
    "runuser": frozenset({"-u", "-g", "-G", "-l", "-s", "-w", "--user", "--group", "--supp-group",
                          "--login", "--shell", "--whitelist-environment"}),
    "su": frozenset({"-g", "-G", "-s", "-w", "--group", "--supp-group", "--shell", "--whitelist-environment"}),
}
_PRIVILEGE_COMMAND_FLAGS = frozenset({"-c", "--command"})
# sudo options that do not run a command (edit files, list, validate, reset).
_SUDO_NO_COMMAND = frozenset({"-e", "--edit", "-l", "--list", "-v", "--validate", "-k", "-K",
                              "--reset-timestamp", "--remove-timestamp", "-V", "--version"})


def privilege_wrapper(tokens: list[str]) -> tuple[str, list[str] | None] | None:
    """(wrapper, inner command tokens or None) for a privilege wrapper; None for other commands.

    The inner command is None when the wrapper starts a shell or does
    something other than running one command nuh can read.
    """
    if not tokens:
        return None
    wrapper = os.path.basename(tokens[0])
    if wrapper == "sudoedit":
        return wrapper, None
    value_flags = _PRIVILEGE_VALUE_FLAGS.get(wrapper)
    if value_flags is None:
        return None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-") or tok == "-":
            if wrapper == "su":
                i += 1  # su's operands are the user (and `-` for a login shell), never a command
                continue
            break
        if wrapper == "sudo" and tok in _SUDO_NO_COMMAND:
            return wrapper, None
        if tok in _PRIVILEGE_COMMAND_FLAGS and wrapper in ("su", "runuser"):
            return wrapper, (["sh", "-c", tokens[i + 1]] if i + 1 < len(tokens) else None)
        i += 2 if tok in value_flags else 1
    inner = tokens[i:]
    return wrapper, (inner if inner and wrapper != "su" else None)


def token_floor(tokens: list[str]) -> list[floor.Verdict]:
    """Floor verdicts for a simple command's words (wrappers already peeled)."""
    if not tokens:
        return []
    words = [os.path.basename(tokens[0])] + tokens[1:]
    verdicts = []
    for check, rule in ((self_protect.launch_reason, "unguarded_launch"),
                        (power_reason, "power_service"),
                        (network_shell_reason, "network_shell"),
                        (ssh_exec_reason, "ssh_exec_options")):
        reason = check(words)
        if reason:
            verdicts.append(floor.Verdict(taxonomy.ASK, reason, rule))
    verdicts += [floor.Verdict(taxonomy.ASK, reason, "git_exec") for reason in git_exec.command_reasons(words)]
    return verdicts


_RSYNC_SHELL_FLAGS = ("-e", "--rsh")


def ssh_exec_reason(words: list[str]) -> str:
    """Why an ssh, scp, sftp or rsync command line runs a local command through ssh options, or ""."""
    parsed = ssh_args.parse_tokens(words)
    if parsed is not None:
        return ssh_args.exec_reason(parsed)
    if words[0] != "rsync":
        return ""
    for i, arg in enumerate(words[1:], 1):
        value = None
        if arg in _RSYNC_SHELL_FLAGS and i + 1 < len(words):
            value = words[i + 1]
        elif arg.startswith("--rsh="):
            value = arg[len("--rsh="):]
        if value is not None:
            shell = value.split()
            if not shell or os.path.basename(shell[0]) != "ssh":
                return f"rsync -e runs {value!r} as its remote shell"
            return ssh_args.exec_reason(ssh_args.parse("ssh", shell[1:]))
    return ""
