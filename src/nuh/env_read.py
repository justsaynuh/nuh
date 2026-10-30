"""Reads of the environment and of secret stores (action type ``env_read``, floor rule ``env_read``).

Environment dumps (``env``, ``printenv``, bare ``set``/``export``/``declare``,
``ps e``, ``/proc/*/environ``), printing a credential variable
(``echo $AWS_SECRET_ACCESS_KEY``) and secret-manager reads put secrets in
the transcript. The plain-prefix secret-manager reads are in
``data/classify_full/env_read.json``; the forms that depend on flags are here.

Adapted from manuelschipper/nah (MIT), 0.x 43e84866 and 297b845b (env_read
type, flag-aware builtins and ``ps``); the credential variable names follow
crates/nah-proto/src/labels.rs ``CREDENTIAL_NAMES``.
"""

import os
import re

CREDENTIAL_NAMES = frozenset({
    "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AZURE_CLIENT_SECRET", "DATABASE_URL",
    "GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "NPM_TOKEN", "OPENAI_API_KEY", "PGPASSWORD", "TWINE_PASSWORD",
    "VAULT_TOKEN",
})
_CREDENTIAL_SUFFIX_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|SECRET_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIALS?)$")
_VAR_REF_RE = re.compile(r"\$(?:\{[#!]?([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|([A-Za-z_][A-Za-z0-9_]*))")
_PRINTERS = frozenset({"echo", "printf", "print"})
_FILE_READERS = frozenset({"cat", "less", "more", "head", "tail", "strings", "xxd", "od", "hexdump", "tr",
                           "grep", "rg", "sed", "awk", "cut", "sort"})
_PROC_ENVIRON_RE = re.compile(r"^/proc/[^/]+/(?:task/[^/]+/)?environ$")

_SHELL_VAR_BUILTINS = frozenset({"set", "export", "declare", "typeset", "readonly"})

_PS_VALUE_FLAGS = frozenset({"-p", "-u", "-U", "-g", "-G", "-t", "-o", "-O", "-C", "-s", "-N", "-q", "-k",
                             "--sort", "--ppid", "--format", "--pid", "--user", "--group", "--tty", "--sid"})
_PS_BSD_VALUE_LETTERS = frozenset("ptUGkoO")
_PS_BSD_CLUSTER_RE = re.compile(r"^[A-Za-z]+$")

_AWS_VALUE_FLAGS = frozenset({"--profile", "--region", "--output", "--endpoint-url", "--query", "--color",
                              "--ca-bundle", "--cli-read-timeout", "--cli-connect-timeout", "--cli-binary-format"})
_KUBECTL_VALUE_FLAGS = frozenset({"-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-l",
                                  "--selector", "-o", "--output", "-s", "--server", "--token", "--as",
                                  "--field-selector", "--sort-by", "--template", "-c", "--container"})
_PASS_COMMANDS = frozenset({"init", "ls", "list", "find", "search", "insert", "add", "edit", "generate", "rm",
                            "remove", "delete", "mv", "rename", "cp", "copy", "git", "help", "version"})


def is_credential_name(name: str) -> bool:
    """True for an environment variable name that holds a credential."""
    return name in CREDENTIAL_NAMES or bool(_CREDENTIAL_SUFFIX_RE.search(name))


def reason(tokens: list[str]) -> str:
    """Why *tokens* read the environment or a secret store, or "" (wrappers already peeled)."""
    if not tokens:
        return ""
    cmd, args = os.path.basename(tokens[0]), tokens[1:]
    check = _CHECKS.get(cmd)
    found = check(args) if check else ""
    if found:
        return found
    if cmd in _PRINTERS:
        names = [m.group(1) or m.group(2) for arg in args for m in _VAR_REF_RE.finditer(arg)]
        secret = next((n for n in names if is_credential_name(n)), "")
        return f"{cmd} prints credential variable ${secret}" if secret else ""
    if cmd in _FILE_READERS and any(_PROC_ENVIRON_RE.match(a) for a in args):
        return "reads a process environment (/proc/*/environ)"
    return ""


_ENV_NO_COMMAND_FLAGS = frozenset({"-0", "--null", "-i", "--ignore-environment", "-"})


def _env(args: list[str]) -> str:
    """env with only assignments and listing flags prints the environment; any other form may run a command."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-u", "--unset"):
            skip = True
        elif not (arg in _ENV_NO_COMMAND_FLAGS or arg.startswith("--unset=") or _is_assignment(arg)):
            return ""
    return "env with no command prints the environment"


def _is_assignment(arg: str) -> bool:
    name, eq, _value = arg.partition("=")
    return bool(eq) and bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _printenv(_args: list[str]) -> str:
    return "printenv prints the environment"


def _shell_builtin(name: str):
    def check(args: list[str]) -> str:
        if not args:
            return f"{name} with no arguments prints the variables"
        if name != "set" and args[0] == "-p" and not any("=" in a for a in args):
            return f"{name} -p prints the variables"
        return ""
    return check


def _ps(args: list[str]) -> str:
    skip = False
    for tok in args:
        if skip:
            skip = False
            continue
        if tok in _PS_VALUE_FLAGS:
            skip = True
            continue
        if tok.startswith("-"):
            if not tok.startswith("--") and "E" in tok[1:] and "=" not in tok:
                return "ps -E prints process environments"
            continue
        if _PS_BSD_CLUSTER_RE.match(tok):
            if "e" in tok:
                return "ps e prints process environments"
            skip = tok[-1] in _PS_BSD_VALUE_LETTERS
    return ""


def _operands(args: list[str], value_flags: frozenset[str]) -> list[str]:
    out, skip = [], False
    for tok in args:
        if skip:
            skip = False
        elif tok in value_flags:
            skip = True
        elif not tok.startswith("-"):
            out.append(tok)
    return out


def _aws(args: list[str]) -> str:
    words = _operands(args, _AWS_VALUE_FLAGS)
    if words[:2] in (["secretsmanager", "get-secret-value"], ["secretsmanager", "batch-get-secret-value"]):
        return f"aws {' '.join(words[:2])} reads a secret"
    if words[:1] == ["ssm"] and words[1:2] and words[1].startswith("get-parameter") and "--with-decryption" in args:
        return f"aws ssm {words[1]} --with-decryption reads a secret"
    return ""


def _gcloud(args: list[str]) -> str:
    words = [a for a in args if not a.startswith("-")]
    for i in range(len(words) - 2):
        if words[i:i + 3] == ["secrets", "versions", "access"]:
            return "gcloud secrets versions access reads a secret"
    return ""


def _kubectl(args: list[str]) -> str:
    words = _operands(args, _KUBECTL_VALUE_FLAGS)
    if words[:1] == ["view-secret"]:
        return "kubectl view-secret reads a secret"
    if words[:1] not in (["get"], ["describe"]) or len(words) < 2:
        return ""
    kinds = [part.split("/", 1)[0].split(".", 1)[0] for part in words[1].split(",")]
    if any(kind in ("secret", "secrets") for kind in kinds):
        return f"kubectl {words[0]} secret reads a secret"
    return ""


def _sops(args: list[str]) -> str:
    if any(a in ("-d", "--decrypt") for a in args) or [a for a in args if not a.startswith("-")][:1] in (
            ["decrypt"], ["exec-env"], ["exec-file"]):
        return "sops decrypts a secret"
    return ""


def _pass(args: list[str]) -> str:
    words = [a for a in args if not a.startswith("-")]
    if not words:
        return ""
    if words[0] in ("show", "otp", "grep") or words[0] not in _PASS_COMMANDS:
        return "pass reads a stored password"
    return ""


_CHECKS = {
    "env": _env,
    "printenv": _printenv,
    "ps": _ps,
    "aws": _aws,
    "gcloud": _gcloud,
    "kubectl": _kubectl,
    "sops": _sops,
    "pass": _pass,
    **{name: _shell_builtin(name) for name in _SHELL_VAR_BUILTINS},
}
