"""shell_braces.expand: brace expansion as bash and zsh do it, and the forms nuh will not read.

Pieces are in the order bash prints them (``bash -c 'echo …'``, bash 5.2;
zsh 5.9 agrees on every expanded case). How the pieces are checked is in
test_brace_paths.py.
"""

import pytest

from nuh import shell_braces


@pytest.mark.parametrize("word,pieces", [
    ("{a,b}{1,2}", ["a1", "a2", "b1", "b2"]),
    ("{x{1,2},y}", ["x1", "x2", "y"]),
    ("{a{b,c}}", ["{ab}", "{ac}"]),          # {x} is literal, the group inside still expands
    ("a{b{c,d}e}f", ["a{bce}f", "a{bde}f"]),
    ("x{,}y", ["xy", "xy"]),
    ("{,~/x}", ["", "~/x"]),
    ("{1..10..3}", ["1", "4", "7", "10"]),
    ("{10..1..3}", ["10", "7", "4", "1"]),
    ("{1..3..-1}", ["1", "2", "3"]),
    ("{001..3}", ["001", "002", "003"]),
    ("{-01..2}", ["-01", "000", "001", "002"]),
    ("{c..a}", ["c", "b", "a"]),
    ("HEAD@{1}", ["HEAD@{1}"]),
    ("{}", ["{}"]),
    ("${x}{a,b}", ["${x}a", "${x}b"]),
    ("{${x,,},b}", ["${x,,}", "b"]),
    ("$HOME{/x,}", ["$HOME/x", "$HOME"]),     # no piece extends the name: both shells agree
])
def test_as_the_shell_expands(word, pieces):
    assert shell_braces.expand(word) == pieces


@pytest.mark.parametrize("word", [
    "{a,b", "a,b}", "{a,b}}", "{{a,b}",       # unmatched: bash keeps it, zsh may not parse it
    "{a..e..2}", "{1..a}", "{+1..3}", "{1..3..0}", "{a..C}",  # bash and zsh expand these differently
    "{1.5..3}",                               # a range form nuh does not model
    "${x:-{a,b}}",                            # zsh expands it, bash does not
    "$X{1,2}",                                # bash: $X1 $X2; zsh: ${X}1 ${X}2
    "{1..300}", "{a,b,c,d}{a,b,c,d}{a,b,c,d}{a,b,c,d}{a,b}",  # more than 256 pieces
    "${x",
])
def test_unread(word):
    assert shell_braces.expand(word) is None
