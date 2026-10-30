"""The static floor: verdicts neither the LLM nor config may lower.

A floor rule yields ask or block, and every rule id is a ``human_only`` key,
true by default (gating.py). The hook does not call the LLM for a floored
decision, logs it with the rule id and marks it replayable, so a later replay can
show what the LLM would have said. Config may make a floor stricter, never
weaker (config_floor.py): the only loosening is the global
``human_only: {<rule id>: false}``, which lets the LLM decide that rule's
asks. Blocks stay blocks either way.

RULES below names every rule id and what it covers.
"""

from nuh import taxonomy

# Rule id -> what it covers (shown by docs and the config warning).
RULES: dict[str, str] = {
    "nuh_admin": "mutating nuh subcommands; uninstalling, reinstalling or pinning nuh",
    "project_config": "writes to a project's .nuh.yaml",
    "agent_hooks": "writes to agent hook wiring and settings, nuh's own config, or a parent of them",
    "unguarded_launch": "launching claude or codex with hooks or permissions turned off",
    "catastrophic": "deletes of /, home, system trees, trusted roots, .git or the project root; "
                    "raw storage; recursive permission changes; fork bombs",
    "privilege": "privilege wrappers (sudo, doas, su, pkexec, runuser)",
    "power_service": "power commands and stopping or disabling services",
    "network_shell": "shells or programs wired to a network socket",
    "credential_paths": "built-in credential files, private keys and .env files",
    "sensitive_paths": "every other sensitive path: user sensitive_paths and sensitive_basenames entries, "
                       "shell init files, agent settings and the rest of the built-in list",
    "git_exec": "git config keys and environment that run a program; .git/hooks and .git/config writes",
    "ssh_exec_options": "ssh options and config files that run a local command",
    "hidden_chars": "control, bidi, zero-width and other characters that hide text",
    "env_read": "environment dumps, printing credential variables, secret-manager reads",
    "package_dir": "writes to the directory nuh runs from",
}


# Rules any more specific rule outranks among equally strict verdicts (outranks).
_CATCH_ALL = frozenset({"sensitive_paths"})


class Verdict(tuple):
    """A ``(decision, reason)`` pair that may carry the floor rule that produced it.

    It unpacks and compares like the plain pair the path and context checks
    have always returned, so callers that only need the pair are unchanged;
    callers that keep the floor read ``rule_of(verdict)``.
    """

    floor: str

    def __new__(cls, decision: str, reason: str, floor: str = "") -> "Verdict":
        obj = super().__new__(cls, (decision, reason))
        obj.floor = floor
        return obj


def rule_of(verdict: object) -> str:
    """The floor rule a verdict carries, "" for a plain pair or None."""
    return getattr(verdict, "floor", "") or ""


def outranks(new_rule: str, old_rule: str) -> bool:
    """True when a verdict floored by *new_rule* should replace an equally strict one floored by *old_rule*.

    A floored verdict beats an unfloored one, and an enforced rule beats one
    the global config set to ``human_only: false``: the verdict kept is the
    one the LLM may not lower (``credential_paths`` over ``sensitive_paths:
    false``). Then
    a specific rule beats the catch-all ``sensitive_paths`` (``agent_hooks``
    for a write to ~/.claude/settings.json).
    """
    return rank(new_rule) > rank(old_rule)


def rank(rule: str) -> tuple[bool, bool, bool]:
    """Sort key for *rule* among equally strict verdicts (see outranks); higher wins."""
    return bool(rule), bool(rule) and is_enforced(rule), bool(rule) and rule not in _CATCH_ALL


def strictest(verdicts: list[tuple[str, str]]) -> tuple[str, str]:
    """The strictest verdict, the first highest-ranked floored one among equals; ``(allow, "")`` for none."""
    best: tuple[str, str] = (taxonomy.ALLOW, "")
    for verdict in verdicts:
        new = taxonomy.STRICTNESS.get(verdict[0], 2)
        old = taxonomy.STRICTNESS.get(best[0], 2)
        if new > old or (new == old and outranks(rule_of(verdict), rule_of(best))):
            best = verdict
    return best


def is_enforced(rule: str) -> bool:
    """True when the LLM may not decide *rule*'s asks: ``human_only`` keeps it true (the default for every rule)."""
    from nuh import gating  # lazy import: gating reads RULES
    return gating.is_human_only(rule)


def enforced(rules: list[str]) -> str:
    """The first enforced rule among *rules*, "" when none is."""
    for rule in rules:
        if is_enforced(rule):
            return rule
    return ""
