"""Floors that key on a Bash stage's action type, held whatever ``classify``, ``actions`` or ``profile`` say.

nuh_admin and env_read floor their command at ask; the catastrophic and
protected-write path floors depend on whether a stage deletes, writes or only
reads; a script's hidden characters are read under lang_exec's context policy.
A global or project ``classify`` entry or a non-full ``profile`` can give a
command another type, and ``actions`` can skip the context read, so each of
these floors also runs on the type nuh's own tables give the command
(``builtin_type``), and the script read runs whatever the policy.
"""

from nuh import config_floor, env_read, floor, lang_exec_context, self_protect, taxonomy
from nuh.bash_policy import _lang_exec_program, _StageProgram
from nuh.bash_types import _escalate, StageResult

_REASONS = {taxonomy.NUH_ADMIN: self_protect.nuh_admin_reason, taxonomy.ENV_READ: env_read.reason}


def builtin_type(tokens: list[str], action_type: str, *, configured: bool) -> str:
    """The action type the built-in tables of profile full give *tokens*.

    *configured*: a user or project classify table or a non-full profile took
    part in *action_type*; without one the two are the same.
    """
    if not configured:
        return action_type
    return taxonomy.classify_tokens(tokens, None, taxonomy.get_builtin_table("full"), None, profile="full")


def apply(sr: StageResult, tokens: list[str], floor_type: str) -> None:
    """Floor *sr* by the nuh_admin and env_read rules for its type and for *floor_type* (``builtin_type``)."""
    for action_type in dict.fromkeys((sr.action_type, floor_type)):
        rule = config_floor.FLOOR_TYPES.get(action_type)
        if rule is None:
            continue
        detail = _REASONS[action_type](tokens)
        if action_type == sr.action_type and sr.decision != taxonomy.ALLOW:
            sr.reason = f"{sr.reason} ({detail})" if detail else sr.reason
            sr.floor = rule
        else:
            reason = f"{action_type} → {taxonomy.ASK}" + (f" ({detail})" if detail else "")
            _escalate(sr, floor.Verdict(taxonomy.ASK, reason, rule))


def script_hidden_chars(sr: StageResult, tokens: list[str], program: _StageProgram | None,
                        floor_type: str, profile: str) -> None:
    """Floor *sr* at ask when the script it runs hides characters, unless the context read already checked.

    *program* is the stage's program when it does not come from argv
    (``bash_policy._stage_program``, or a ``source``d file).
    """
    if taxonomy.LANG_EXEC not in (sr.action_type, floor_type):
        return
    if sr.action_type == taxonomy.LANG_EXEC and sr.default_policy == taxonomy.CONTEXT and profile != "none":
        return  # resolve_lang_exec_context read the script and applied the floor
    verdict = lang_exec_context.hidden_chars_verdict(_lang_exec_program(tokens, program)[0])
    if verdict is not None:
        _escalate(sr, verdict)
