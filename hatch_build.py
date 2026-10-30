"""Hatchling build hook: render the man page into every wheel, editable ones included.

The page is generated from the argparse tree and the help text
(``nuh.manpage``), so it is built, not committed. The hook renders it to
``build/man/nuh.1`` (gitignored) and maps it into the wheel's shared data at
``share/man/man1/nuh.1``, which pip puts under the environment's prefix and
pipx links into ``~/.local/share/man/man1``.

The build runs in an isolated environment where nuh is not installed, so
``src/`` goes on ``sys.path`` to import it. Building the parser imports no
handler module and none of nuh's dependencies (``tests/test_cli_imports.py``),
so hatchling is the only build requirement; ``__version__`` is a literal in
``nuh/__init__.py``.
"""

import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ManPageHook(BuildHookInterface):
    """Adds the rendered man page to the wheel's shared data."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Render the page and add it to ``build_data["shared_data"]`` (``standard`` and ``editable`` builds)."""
        out = Path(self.root) / "build" / "man" / "nuh.1"
        src = str(Path(self.root) / "src")
        sys.path.insert(0, src)
        try:
            from nuh import manpage

            manpage.write(out)
            install_path = manpage.INSTALL_PATH
        finally:
            sys.path.remove(src)
        build_data["shared_data"][str(out)] = install_path
