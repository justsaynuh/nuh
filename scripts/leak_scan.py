#!/usr/bin/env python3
"""Scan files for private terms before a release.

Usage: python scripts/leak_scan.py [--terms FILE] PATH [PATH ...]

The terms file holds one case-insensitive regular expression per line (blank
lines and lines starting with ``#`` are skipped). Keep it outside the shipped
tree: the default is ``.scratch/release/private-terms.txt``. Every hit prints
as ``file:line: term`` and never the matched line itself, so the output is safe
to paste. Exit status: 0 with no hits, 1 on any hit, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_TERMS = Path(".scratch/release/private-terms.txt")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}


def load_terms(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    """Read the terms file into (source, compiled pattern) pairs."""
    terms = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            terms.append((line, re.compile(line, re.IGNORECASE)))
        except re.error as exc:
            raise ValueError(f"{path}:{number}: bad pattern: {exc}") from exc
    return terms


def iter_files(paths: list[Path]):
    """Yield every regular file under the given paths, skipping cache dirs below each one."""
    for root in paths:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(root).parts):
                yield path


def scan_file(path: Path, terms) -> list[str]:
    """Return one ``file:line: term`` report per (line, term) hit."""
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for source, pattern in terms:
            if pattern.search(line):
                hits.append(f"{path}:{number}: {source}")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan files for private terms.")
    parser.add_argument("--terms", type=Path, default=DEFAULT_TERMS,
                        help=f"terms file, one regex per line (default: {DEFAULT_TERMS})")
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories to scan")
    args = parser.parse_args(argv)
    try:
        terms = load_terms(args.terms)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"leak_scan: {exc}\n")
        return 2
    missing = [str(p) for p in args.paths if not p.exists()]
    if missing:
        sys.stderr.write(f"leak_scan: no such path: {', '.join(missing)}\n")
        return 2
    hits = [hit for path in iter_files(args.paths) for hit in scan_file(path, terms)]
    for hit in hits:
        print(hit)
    print(f"leak_scan: {len(hits)} hit(s) for {len(terms)} term(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
