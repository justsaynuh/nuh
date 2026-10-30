"""Floors in the commands a Bash command runs (runner, recipe, ``find -exec``), held whatever config says.

``profile: none`` turns nuh's own classification off, and a user's global
``classify:`` entry for a command decides what it is. Config may not lower a
floor (config_floor.py), though, and ``uv run rm -rf ~``, a recipe line or a
``find -exec sh -c '…'`` payload is where such a rule fires. So under either
config the unwrap still runs (``floors_only``) and its result counts only
when a floor stands behind it (``kept``).
"""

import os

from nuh import bash_type_floor, taxonomy
from nuh.bash_types import Stage, StageResult
from nuh.xargs_args import _DELETE_PROGRAMS


def floors_only(stage: Stage, kw: dict) -> bool:
    """Whether config set the unwrap of *stage* aside, all but its floors (*kw*: the stage classifier's tables)."""
    return kw.get("profile") == "none" or bool(
        kw.get("global_table") and taxonomy._prefix_match(stage.tokens, kw["global_table"]) != taxonomy.UNKNOWN)


def kept(sr: StageResult, only_floors: bool) -> bool:
    """Whether an unwrapped result stands: always, or with *only_floors* when a floor rule is behind it."""
    return not only_floors or any(s.floor and s.decision != taxonomy.ALLOW for s in sr.walk())


def deletes(sr: StageResult, command: list[str]) -> bool:
    """Whether a find payload deletes: its program, or any command in it by its type or nuh's own type for it.

    nuh's own type (bash_type_floor.builtin_type) counts so that a profile or
    classify entry cannot hide ``sh -c 'rm -rf "$1"'`` from the delete floors.
    """
    if os.path.basename(command[0]) in _DELETE_PROGRAMS:
        return True
    return any(s.tokens and taxonomy.FILESYSTEM_DELETE in (
        s.action_type, bash_type_floor.builtin_type(s.tokens, s.action_type, configured=True)) for s in sr.walk())
