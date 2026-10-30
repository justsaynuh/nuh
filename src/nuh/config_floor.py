"""Config may tighten a floor rule (floor.RULES), never lower it.

The config keys that could lower a floor's verdict are checked as the config
loads; an entry that would lower one warns once, naming the key, and is
dropped. The others are closed where they act: ``sensitive_paths`` and
``sensitive_basenames`` allows (sensitive_paths.py), ``allow_paths``
(``config.is_path_allowed`` with ``sensitive_paths.user_entries``), and
``classify`` and ``profile`` for the floors that key on a command's type
(``bash_stage``'s built-in floor type). The global ``human_only: {<rule>:
false}`` stays the only loosening: it hands a floor's ask to the LLM, it
never lowers a verdict by itself.
"""

import sys

from nuh import taxonomy

# Action types a floor rule is named for: an ask from one is floored (bash_stage).
FLOOR_TYPES: dict[str, str] = {taxonomy.NUH_ADMIN: "nuh_admin", taxonomy.ENV_READ: "env_read"}


def lowers(action_type: str, policy: object) -> str:
    """The floor rule *policy* for *action_type* would lower, "" when it lowers none."""
    rule = FLOOR_TYPES.get(action_type, "")
    if not rule:
        return ""
    floor_policy = taxonomy.POLICIES.get(action_type, taxonomy.ASK)
    if taxonomy.STRICTNESS.get(policy, 2) < taxonomy.STRICTNESS[floor_policy]:
        return rule
    return ""


def actions(raw: dict) -> dict:
    """*raw* ``actions`` without the entries that would lower a floor (each warns once)."""
    kept: dict = {}
    for action_type, policy in raw.items():
        rule = lowers(action_type, policy)
        if rule:
            sys.stderr.write(f"nuh: config: actions.{action_type}: {policy} would lower floor {rule}; ignored "
                             f"(set human_only: {{{rule}: false}} to let the LLM decide its asks)\n")
            continue
        kept[action_type] = policy
    return kept


def protect_package(raw: object) -> None:
    """Warn when ``protect_package`` asks to switch the package_dir floor off; ``true`` is what nuh always does."""
    if raw is not None and raw is not True:
        sys.stderr.write(f"nuh: config: protect_package: {raw!r} is ignored: writes to nuh's own code always "
                         "ask (floor package_dir; set human_only: {package_dir: false} to let the LLM decide them)\n")
