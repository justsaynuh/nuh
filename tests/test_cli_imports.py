"""What each entry point imports: the hook never loads the CLI's doc modules, and the parser loads no handler.

The hook runs on every tool call, so every module it imports is latency on
every action; ``--help``, the man page build (in an isolated environment
without nuh's dependencies) and ``nuh completion`` build the parser, so it
must not reach the classifier, the config or the LLM layer.
"""

import subprocess
import sys

import pytest

_DOC_MODULES = {"nuh.cli_parser", "nuh.cli_help", "nuh.cli_text", "nuh.cli_tree", "nuh.cli_completion",
                "nuh.completion", "nuh.completion_zsh", "nuh.manual", "nuh.manpage"}
_PARSER_MODULES = {"nuh", "nuh.cli", "nuh.cli_parser", "nuh.cli_help", "nuh.cli_text", "nuh.cli_tree",
                   "nuh.cli_completion", "nuh.trace_views"}
_HEAVY = ("yaml", "celpy", "lark", "ruamel")


def _loaded(code: str) -> set[str]:
    probe = f"{code}\nimport sys\nprint('\\n'.join(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_the_hook_imports_none_of_the_cli_doc_modules():
    loaded = _loaded("import nuh.hook")
    assert "nuh.hook" in loaded
    assert not loaded & _DOC_MODULES


@pytest.mark.parametrize("code", [
    "from nuh.cli_parser import build_parser; build_parser()",
    "import nuh.cli",
], ids=["build_parser", "import nuh.cli"])
def test_building_the_parser_loads_no_handler_and_no_dependency(code: str):
    loaded = _loaded(code)
    assert {m for m in loaded if m == "nuh" or m.startswith("nuh.")} <= _PARSER_MODULES
    assert not [m for m in loaded if m.split(".")[0] in _HEAVY]


def test_the_man_page_renders_without_nuhs_dependencies():
    """hatch_build.py renders it in an isolated build environment where only hatchling is installed."""
    loaded = _loaded("from nuh.manpage import render; render()")
    assert {m for m in loaded if m.startswith("nuh.")} <= _PARSER_MODULES | {"nuh.manpage", "nuh.manual"}
    assert not [m for m in loaded if m.split(".")[0] in _HEAVY]
