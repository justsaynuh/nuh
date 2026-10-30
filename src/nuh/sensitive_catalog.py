"""The built-in sensitive-path catalogue beyond the original defaults in ``paths``.

Credential entries are floored (rule ``credential_paths``): their asks never
go to the LLM unless the global config sets that rule to review. The other
entries (shell init files, service units, container sockets) are ordinary
sensitive asks.

The 0.x entries are adapted from manuelschipper/nah (MIT), 0.x src/nah/paths.py
(c8b81b2e, 94909046); the filename patterns and the ~/.ssh split follow
crates/nah-proto/src/labels/sensitivity.rs and labels/host_integrity.rs.
"""

import fnmatch
import os

_HOME = os.path.expanduser("~")


def _home(*parts: str) -> str:
    return os.path.realpath(os.path.join(_HOME, *parts))


# (resolved, display, policy): credential stores, floored.
CREDENTIAL_DIRS: list[tuple[str, str, str]] = [
    (_home(".pgpass"), "~/.pgpass", "ask"),
    (_home(".cargo", "credentials"), "~/.cargo/credentials", "ask"),
    (_home(".cargo", "credentials.toml"), "~/.cargo/credentials.toml", "ask"),
    (_home(".docker"), "~/.docker", "ask"),
    (_home(".kube"), "~/.kube", "ask"),
    (_home(".config", "az"), "~/.config/az", "ask"),
    (_home(".config", "heroku"), "~/.config/heroku", "ask"),
    (_home(".config", "glab-cli"), "~/.config/glab-cli", "ask"),
    (_home(".config", "pypoetry", "auth.toml"), "~/.config/pypoetry/auth.toml", "ask"),
    (_home(".config", "containers", "auth.json"), "~/.config/containers/auth.json", "ask"),
    (_home(".gem", "credentials"), "~/.gem/credentials", "ask"),
    # Deploy CLIs' login tokens (wrangler, fly, vercel, netlify, railway, firebase).
    (_home(".wrangler"), "~/.wrangler", "ask"),
    (_home(".config", ".wrangler"), "~/.config/.wrangler", "ask"),
    (_home(".fly"), "~/.fly", "ask"),
    (_home(".local", "share", "com.vercel.cli"), "~/.local/share/com.vercel.cli", "ask"),
    (_home(".netlify"), "~/.netlify", "ask"),
    (_home(".config", "netlify"), "~/.config/netlify", "ask"),
    (_home(".railway"), "~/.railway", "ask"),
    (_home(".config", "configstore", "firebase-tools.json"), "~/.config/configstore/firebase-tools.json", "ask"),
]

_SHELL_INIT = (".bashrc", ".bash_profile", ".bash_aliases", ".bash_login", ".bash_logout", ".profile",
               ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".bashrc.d", ".zshrc.d")

# (resolved, display, policy): persistence and control surfaces, not floored.
OTHER_DIRS: list[tuple[str, str, str]] = [
    (os.path.realpath("/etc/docker"), "/etc/docker", "ask"),
    (os.path.realpath("/var/run/docker.sock"), "/var/run/docker.sock", "ask"),
    (os.path.realpath("/run/podman/podman.sock"), "/run/podman/podman.sock", "ask"),
    (os.path.realpath("/etc/systemd"), "/etc/systemd", "ask"),
    (_home(".config", "systemd", "user"), "~/.config/systemd/user", "ask"),
    (os.path.realpath("/lib/systemd"), "/lib/systemd", "ask"),
    *((_home(name), f"~/{name}", "ask") for name in _SHELL_INIT),
]

ENV_PATTERN = ".env.*"
_ENV_TEMPLATE_SUFFIXES = ("example", "sample", "template", "dist")

# (name or glob, display, policy): credential file names matched anywhere, floored.
CREDENTIAL_BASENAMES: list[tuple[str, str, str]] = [
    (".pgpass", ".pgpass", "ask"),
    (".boto", ".boto", "ask"),
    ("terraform.tfvars", "terraform.tfvars", "ask"),
    ("terraform.tfstate", "terraform.tfstate", "ask"),
    ("kubeconfig", "kubeconfig", "ask"),
    ("*.pem", "*.pem", "ask"),
    ("*.key", "*.key", "ask"),
    ("*.p12", "*.p12", "ask"),
    ("*.pfx", "*.pfx", "ask"),
    ("id_rsa*", "id_rsa*", "ask"),
    ("id_ed25519*", "id_ed25519*", "ask"),
    ("id_ecdsa*", "id_ecdsa*", "ask"),
    ("id_dsa*", "id_dsa*", "ask"),
    (ENV_PATTERN, ENV_PATTERN, "ask"),
    # Env files by another name (cloudflare.env, prod.env) and direnv's: what `set -a; . f` loads.
    ("*.env", "*.env", "ask"),
    (".envrc", ".envrc", "ask"),
]


def basename_matches(basename: str, name: str) -> bool:
    """True when *basename* matches catalogue entry *name* (an exact name or a glob).

    ``.env.*`` skips templates (``.env.example``, ``.env.local.sample``, …);
    key globs skip public keys (``id_rsa.pub``).
    """
    if not fnmatch.fnmatchcase(basename, name):
        return False
    if name == ENV_PATTERN:
        return basename.rsplit(".", 1)[-1] not in _ENV_TEMPLATE_SUFFIXES
    if name.endswith("*") and basename.endswith(".pub"):
        return False
    return True


# -- ~/.ssh: the directory and key material block; host files are writes only --

SSH_DIR = _home(".ssh")
SSH_DISPLAY = "~/.ssh"
_SSH_PUBLIC = ("known_hosts", "known_hosts.old", "*.pub")
# Reading these is fine; writing them changes who can log in or what ssh runs.
_SSH_EXEC_FILES = ("config", "authorized_keys", "authorized_keys2", "rc", "environment")
_SSH_EXEC_DIRS = ("config.d", "authorized_keys.d")


def _ssh_relative(resolved: str) -> list[str] | None:
    if resolved == SSH_DIR:
        return []
    if resolved.startswith(SSH_DIR + os.sep):
        return resolved[len(SSH_DIR) + 1:].split(os.sep)
    return None


def _is_ssh_exec_file(rel: list[str]) -> bool:
    return (len(rel) == 1 and rel[0] in _SSH_EXEC_FILES) or (len(rel) > 1 and rel[0] in _SSH_EXEC_DIRS)


def ssh_read_match(resolved: str) -> bool:
    """True when *resolved* is ``~/.ssh`` itself or key material in it.

    Anything in ``~/.ssh`` that is not a known public or host file counts as
    a key, whatever its name.
    """
    rel = _ssh_relative(resolved)
    if rel is None:
        return False
    if not rel:
        return True
    if len(rel) == 1 and any(fnmatch.fnmatchcase(rel[0], p) for p in _SSH_PUBLIC):
        return False
    return not _is_ssh_exec_file(rel)


def ssh_write_reason(resolved: str) -> str:
    """Why writing *resolved* changes ssh logins or the commands ssh runs (a block), or ""."""
    rel = _ssh_relative(resolved)
    if rel and _is_ssh_exec_file(rel):
        return f"targets ssh config: {SSH_DISPLAY}/{'/'.join(rel)}"
    return ""


def git_exec_write_reason(path: str) -> str:
    """Why writing *path* changes what the next git command runs (.git/hooks, .git/config), or ""."""
    parts = path.split(os.sep)
    if ".git" not in parts:
        return ""
    after = parts[parts.index(".git") + 1:]
    if "hooks" in after and after[0] not in ("refs", "logs"):
        return f"targets git hooks: {path}"
    if after and after[-1] in ("config", "config.worktree"):
        return f"targets git config: {path}"
    return ""
