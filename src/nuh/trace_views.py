"""The names of ``nuh trace``'s views.

Shared by the renderer (``trace_render``) and the CLI parser (``cli_parser``,
the ``--view`` choices), which must not import the renderer: building the
parser for ``--help``, the man page or completion stays cheap.
"""

ANSWERS, PROMPT, PATH, RAW, ALL = "answers", "prompt", "path", "raw", "all"
VIEWS = (ANSWERS, PROMPT, PATH, RAW)
