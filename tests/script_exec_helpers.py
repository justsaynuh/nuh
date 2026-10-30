"""Shared by the FD-079 script execution tests (test_fd079_*.py)."""

import os

from nuh import taxonomy


# Helper: classify tokens via taxonomy (Phase 2 flag classifier path)
def _ct(tokens):
    return taxonomy.classify_tokens(tokens)


def _write(path, content="print('hello')\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
