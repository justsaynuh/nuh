"""The docs' generated blocks (scripts/gen_cli_docs.py, `make docs`) are current, and their Markdown shows text as
written."""

import importlib.util
import re
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_cli_docs.py"
HOSTILE = "a | b `co|de` <img src=x> &amp; *x* _y_ [l](u) \\ `"
"""Help text with every character Markdown or a table would act on, an unmatched backtick last."""
HOSTILE_SHOWN = "a | b co|de <img src=x> &amp; *x* _y_ [l](u) \\ `"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_cli_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load()


@pytest.mark.parametrize("page", sorted(gen.PAGES))
def test_committed_blocks_match_the_render(page: str):
    text = (gen.ROOT / page).read_text(encoding="utf-8")
    assert text == gen.render(text, gen.PAGES[page]()), f"{page}'s generated blocks are stale: run `make docs`"


def test_the_cli_page_has_a_block_for_every_command():
    blocks = gen.cli_blocks()
    assert {"nuh trust", "nuh config show", "nuh system1 export", "nuh completion"} <= set(blocks)
    assert blocks["nuh trust"][0] == "### nuh trust"


def _page(*names: str) -> str:
    return "".join(f"x\n<!-- BEGIN GENERATED: {n} -->\nold\n<!-- END GENERATED: {n} -->\n" for n in names)


def test_render_replaces_only_the_block_bodies():
    out = gen.render("intro\n" + _page("one"), {"one": ["new"]})
    assert out == "intro\nx\n<!-- BEGIN GENERATED: one -->\n\nnew\n\n<!-- END GENERATED: one -->\n"


@pytest.mark.parametrize(("page", "message"), [
    (_page("one"), "no generated block 'two'"),
    (_page("one", "two", "two"), "'two' appears twice"),
    (_page("one", "two", "gone"), "'gone' matches nothing"),
])
def test_render_refuses_a_page_whose_blocks_do_not_match(page: str, message: str):
    with pytest.raises(ValueError, match=message):
        gen.render(page, {"one": ["1"], "two": ["2"]})


def _cells(row: str) -> list[str]:
    """A table row's cells as a GFM renderer splits them: on every pipe not escaped by a backslash."""
    return re.split(r"(?<!\\)\|", row.strip())[1:-1]


def test_table_cells_keep_pipes_markup_and_html_inert():
    cell = gen.inline(HOSTILE, cell=True)
    assert len(_cells(gen.table(("a", "b"), [("`x`", cell)])[-1])) == 2
    assert "<img" not in cell and "&amp;amp;" in cell
    assert "<code>co&#124;de</code>" in cell
    for raw in "*_[]`":
        assert re.search(rf"(?<!\\){re.escape(raw)}", cell.replace("<code>co&#124;de</code>", "")) is None


@pytest.mark.parametrize(("text", "shown"), [("- not a list", "\\- not"), ("1. not a list", "1\\. not"),
                                             ("2) nor this", "2\\) nor"), ("# not a heading", "\\# not")])
def test_a_paragraph_never_starts_a_list_or_heading(text: str, shown: str):
    assert gen.inline(text).startswith(shown)


def test_code_spans_and_fences_outlast_backticks_inside():
    assert gen.code("a`b") == "``a`b``"
    assert gen.code("`x") == "`` `x ``"
    assert gen.fence("bash", ["echo ```"]) == ["````bash", "echo ```", "````"]


class _Cells(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "td":
            self.cells.append("")
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.cells[-1] += data


def test_python_markdown_shows_the_hostile_text_as_written():
    """mkdocs renders the site with Python-Markdown; the docs extra installs it."""
    markdown = pytest.importorskip("markdown")
    rendered = markdown.markdown("\n".join(gen.table(("Option", "Description"),
                                                     [(gen.code("--x", cell=True), gen.inline(HOSTILE, cell=True))])),
                                 extensions=["tables"])
    cells = _Cells()
    cells.feed(rendered)
    assert cells.cells == ["--x", HOSTILE_SHOWN]
