"""What a task runner would run, as shell lines, and the error for what nuh cannot read.

Shared by ``task_make`` and ``task_runners``; the Bash classifier classifies a
``Recipe`` like a script and asks on ``Unreadable``.
"""

from dataclasses import dataclass, field

MAX_FILE_BYTES = 1_000_000


class Unreadable(Exception):
    """The runner would run something nuh cannot read; the message says what."""


@dataclass
class Recipe:
    runner: str                                    # "make test"
    source: str                                    # the file the lines came from
    lines: list[str] = field(default_factory=list)  # each line runs in its own shell
    cwd: str = ""                                  # directory the lines run in
    one_shell: bool = False                        # .ONESHELL: the lines are one script
    opaque_reason: str = ""                       # generated recipe bytes nuh cannot resolve
