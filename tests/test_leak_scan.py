"""scripts/leak_scan.py: case-insensitive hits by file and line, never the matched line itself."""

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "leak_scan.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leak_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load()


def _terms(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "terms.txt"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_hits_print_file_line_and_term_but_not_the_line(tmp_path, capsys):
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "a.md").write_text("fine\nhost is Secret.Example here, keep quiet\n")
    (tree / "b.txt").write_text("nothing\n")
    terms = _terms(tmp_path, "# a comment", "", r"secret\.example")
    assert scan.main(["--terms", terms, str(tree)]) == 1
    out = capsys.readouterr().out
    assert out == f"{tree / 'sub' / 'a.md'}:2: secret\\.example\n"
    assert "keep quiet" not in out


def test_clean_tree_exits_zero(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("nothing to see\n")
    assert scan.main(["--terms", _terms(tmp_path, "secret"), str(tmp_path / "a.txt")]) == 0
    assert capsys.readouterr().out == ""


def test_cache_dirs_are_skipped_below_a_root_but_not_as_one(tmp_path):
    cache = tmp_path / "tree" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"\x00secret\xff")
    terms = _terms(tmp_path, "secret")
    assert scan.main(["--terms", terms, str(tmp_path / "tree")]) == 0
    assert scan.main(["--terms", terms, str(cache)]) == 1


def test_usage_errors_exit_two(tmp_path, capsys):
    assert scan.main(["--terms", str(tmp_path / "missing.txt"), str(tmp_path)]) == 2
    assert scan.main(["--terms", _terms(tmp_path, "("), str(tmp_path)]) == 2
    assert scan.main(["--terms", _terms(tmp_path, "x"), str(tmp_path / "nope")]) == 2
    assert "bad pattern" in capsys.readouterr().err
