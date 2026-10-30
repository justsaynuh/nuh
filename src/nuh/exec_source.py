"""Where does an interpreter get its program from?

The pipe-into-interpreter composition rules (``network | exec``,
``decode | exec``, ``read | exec``) exist because an interpreter that reads
its program from stdin executes whatever the left side of the pipe produces.
When nuh can tell the program comes from somewhere else (``python -c CODE``,
``node script.js``, ``bash -c CMD``), the piped stdin is only data and the
stages keep their normal classification.

This module is an explicit allowlist. An interpreter missing from the table,
a flag the table does not know, or an ambiguous argv returns ``None``, which
callers must treat exactly like "program from stdin".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from nuh import taxonomy, word_quoting

INLINE = "inline"
MODULE = "module"
SCRIPT = "script"
STDIN = "stdin"


@dataclass(frozen=True)
class ProgramSource:
    """How an interpreter invocation receives its program.

    ``kind`` is one of INLINE, MODULE, SCRIPT, STDIN. ``value`` is the code,
    module name or script path; for STDIN it is the explicit stdin operand
    (here-string, heredoc body or ``<`` redirect target), empty when stdin is
    inherited from the pipe.
    """

    interpreter: str
    kind: str
    value: str = ""


# Where an InlineProgram's text came from, as the LLM reviewer is told.
FROM_ARGUMENT = "inline argument"                       # python3 -c CODE, node -e CODE
FROM_STDIN = "stdin (heredoc or here-string)"           # python3 - <<'EOF', python3 <<< CODE
FROM_WRITTEN_SCRIPT = "script written earlier in this command"  # cat > x.py <<'EOF' … && python3 x.py


@dataclass(frozen=True)
class InlineProgram:
    """Program text a lang_exec stage runs that is not a file on disk.

    Extracted once by the Bash classifier, content-scanned there, and carried
    on the stage result so the LLM reviewer sees the same text.
    """

    interpreter: str  # the command word as written (python3.12, node)
    code: str
    source: str       # one of the FROM_* labels


@dataclass(frozen=True)
class _Spec:
    """Per-interpreter argv grammar. Anything not listed is unrecognized."""

    inline: frozenset[str] = frozenset()
    module: frozenset[str] = frozenset()
    script_flags: frozenset[str] = frozenset()
    bool_flags: frozenset[str] = frozenset()
    value_flags: frozenset[str] = frozenset()
    attached_prefixes: tuple[str, ...] = ()
    cluster_letters: str = ""
    cluster_tail: dict[str, str] | None = None  # last letter -> INLINE/MODULE
    # The cluster_tail letter is a plain flag that may sit anywhere in a
    # cluster (sh -cl CMD), its value the next word, not the rest of the cluster.
    tail_anywhere: bool = False
    plus_options: bool = False  # +x and +o NAME are options too (a shell turning them off)
    stdin_flags: frozenset[str] = frozenset({"-"})
    inline_ends_options: bool = True
    eval_re: re.Pattern | None = None


_PY_EVAL = re.compile(
    r"\b(exec|eval|compile|execfile|__import__|runpy|system|popen|subprocess|"
    r"spawn\w*|exec[lv]\w*|pickle|marshal|dill|shelve|code\.interact|"
    r"InteractiveConsole|unsafe_load|yaml\.load)\b")
_NODE_EVAL = re.compile(
    r"\b(eval|Function|child_process|exec\w*|spawn\w*|fork|vm|"
    r"runIn\w*Context|import)\b")
_RUBY_EVAL = re.compile(
    r"(`|%x|\b(eval|instance_eval|class_eval|module_eval|instance_exec|system|"
    r"exec|spawn|popen\w*|Open3|load|require|Marshal|send|public_send)\b)")
_PERL_EVAL = re.compile(r"(`|\b(eval|system|exec|qx|do|require|open)\b)")
_PHP_EVAL = re.compile(
    r"(`|\b(eval|assert|system|exec|shell_exec|passthru|popen|proc_open|"
    r"include\w*|require\w*|create_function|unserialize|call_user_func\w*)\b)")
# A shell -c command that runs another interpreter, sources, evals, or puts a
# parameter expansion in command position hands its (network) stdin to code.
_SHELL_EVAL = re.compile(
    r"(`|/dev/stdin|/dev/fd/0|(^|[;&|({!]|\b(do|then|else|exec|command|"
    r"builtin|noglob|nocorrect)\b)\s*\$|\b(eval|exec|source|xargs|"
    r"bash|sh|dash|zsh|ksh|fish|pwsh|python[0-9.]*|node|ruby|perl|php|bun|"
    r"deno|env)\b|(^|[;&|({]|\b(do|then|else)\b)\s*\.\s)")

_PYTHON = _Spec(
    inline=frozenset({"-c"}),
    module=frozenset({"-m"}),
    # -i is deliberately absent: it drops into a REPL reading stdin.
    bool_flags=frozenset({"-b", "-bb", "-B", "-d", "-E", "-I", "-O", "-OO",
                          "-P", "-q", "-R", "-s", "-S", "-u", "-v", "-vv", "-x"}),
    value_flags=frozenset({"-W", "-X"}),
    attached_prefixes=("-W", "-X"),
    cluster_letters="bBdEIOPqRsSuvx",
    cluster_tail={"c": INLINE, "m": MODULE},
    eval_re=_PY_EVAL,
)
_NODE = _Spec(
    inline=frozenset({"-e", "--eval", "-p", "--print"}),
    bool_flags=frozenset({"--no-warnings", "--no-deprecation", "--trace-warnings",
                          "--enable-source-maps", "--trace-uncaught"}),
    value_flags=frozenset({"-r", "--require", "--import", "--loader", "--input-type"}),
    inline_ends_options=False,
    eval_re=_NODE_EVAL,
)
_RUBY = _Spec(
    inline=frozenset({"-e"}),
    bool_flags=frozenset({"-w", "-n", "-p", "-a", "-l"}),
    value_flags=frozenset({"-I", "-r"}),
    attached_prefixes=("-I", "-r"),
    cluster_letters="wnpal",
    cluster_tail={"e": INLINE},
    inline_ends_options=False,
    eval_re=_RUBY_EVAL,
)
_PERL = _Spec(
    inline=frozenset({"-e", "-E"}),
    # -x (program embedded in input) and -i (in-place edit) are not listed.
    bool_flags=frozenset({"-w", "-W", "-X", "-n", "-p", "-a", "-l", "-s", "-T", "-t"}),
    value_flags=frozenset({"-I", "-M", "-m"}),
    attached_prefixes=("-I", "-M", "-m", "-0"),
    cluster_letters="wWXnpalsTt",
    cluster_tail={"e": INLINE, "E": INLINE},
    inline_ends_options=False,
    eval_re=_PERL_EVAL,
)
_PHP = _Spec(
    inline=frozenset({"-r"}),
    script_flags=frozenset({"-f"}),
    bool_flags=frozenset({"-n", "-q"}),
    value_flags=frozenset({"-c", "-d"}),
    inline_ends_options=False,
    eval_re=_PHP_EVAL,
)
_SHELL = _Spec(
    inline=frozenset({"-c"}),
    # -i (interactive) reads commands from stdin and is deliberately absent.
    bool_flags=frozenset({"-e", "-u", "-x", "-v", "-l", "-f", "--norc",
                          "--noprofile", "--posix", "--login"}),
    value_flags=frozenset({"-o", "-O", "--rcfile", "--init-file"}),
    cluster_letters="euxvlf",
    cluster_tail={"c": INLINE},
    tail_anywhere=True,
    plus_options=True,
    stdin_flags=frozenset({"-", "-s"}),
    eval_re=_SHELL_EVAL,
)
_SOURCE = _Spec(stdin_flags=frozenset())

_SPECS: dict[str, _Spec] = {
    "python": _PYTHON, "python3": _PYTHON,
    "node": _NODE, "ruby": _RUBY, "perl": _PERL, "php": _PHP,
    "bash": _SHELL, "sh": _SHELL, "dash": _SHELL, "zsh": _SHELL,
    "source": _SOURCE, ".": _SOURCE,
}

_INPUT_REDIRECT_RE = re.compile(r"^(\d*)(<<<|<<-?|<>|<)(.*)$")
_STDIN_PATHS = frozenset({"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})


def interpreter_name(token: str) -> str:
    """Canonical interpreter name for a command token (basename, version-stripped)."""
    return taxonomy._normalize_interpreter(os.path.basename(token))


def program_source(tokens: list[str]) -> ProgramSource | None:
    """Return how *tokens* supply the interpreter's program, or None if unknown.

    None covers interpreters outside the table (``eval``, ``fish``, ``env``…),
    unknown flags, and argv shapes the table cannot read with certainty.
    """
    if not tokens:
        return None
    name = interpreter_name(tokens[0])
    spec = _SPECS.get(name)
    if spec is None:
        return None
    source = _parse(name, spec, tokens[1:])
    if source is not None and source.kind == SCRIPT and source.value in _STDIN_PATHS:
        return ProgramSource(name, STDIN)
    return source


def _parse(name: str, spec: _Spec, args: list[str]) -> ProgramSource | None:
    found: ProgramSource | None = None
    stdin_operand = ""
    explicit_stdin = False
    i = 0
    while i < len(args):
        tok = args[i]
        redirect = _INPUT_REDIRECT_RE.match(tok)
        if redirect:
            operand, step = _redirect_operand(redirect.group(2), redirect.group(3), args, i)
            if redirect.group(1) in ("", "0"):
                stdin_operand = f"{stdin_operand} {operand}".strip()
            i += step
            continue
        if found is not None:
            # Program already identified; later tokens are argv or (for
            # interpreters that keep parsing options) further switches.
            if spec.inline_ends_options or found.kind != INLINE or not tok.startswith("-"):
                i += 1
                continue
        step, result = _parse_option(name, spec, args, i)
        if step == 0:
            return None
        if result == STDIN:
            explicit_stdin = True
            i += step
            continue
        if isinstance(result, ProgramSource) and found is None:
            found = result
        i += step
    if explicit_stdin or found is None:
        return ProgramSource(name, STDIN, stdin_operand)
    return found


def stdin_program(tokens: list[str], heredoc_text: str) -> tuple[str | None, str | None] | None:
    """``(script path, inline code)`` of an interpreter fed its program on stdin.

    ``python3 - <<'EOF'`` and ``node <<EOF`` run the heredoc body
    (*heredoc_text*, the stage's heredoc text), ``python3 <<< CODE`` runs the
    here-string and ``python3 < x.py`` the file. None when the program comes
    from argv or inherited stdin. ``(None, None)`` is a program nuh cannot
    read: a heredoc without a body, a file opened read-write (``<>``), or
    several stdin redirects (the last one wins in the shell, and only one can
    be inspected).
    """
    source = program_source(tokens)
    if source is None or source.kind != STDIN:
        return None
    tokens = _without_body_words(tokens, heredoc_text)
    programs: list[tuple[str | None, str | None]] = []
    i = 1
    while i < len(tokens):
        redirect = _INPUT_REDIRECT_RE.match(tokens[i])
        i += 1
        if redirect is None or redirect.group(1) not in ("", "0"):
            continue
        op, operand = redirect.group(2), redirect.group(3)
        if not operand and op in ("<", "<>", "<<<") and i < len(tokens):
            operand = tokens[i]
            i += 1
        if op == "<<<":
            programs.append((None, operand))
        elif op == "<>":
            # A program on a stdin open for writing can rewrite its own unread
            # text as it runs: the file's content is not what runs.
            programs.append((None, None))
        elif op == "<":
            path = os.path.join(os.getcwd(), word_quoting.expand_path(operand)) if operand else None
            programs.append((path, None))
        else:
            programs.append((None, heredoc_text or None))
    if not programs:
        return None
    # Multiple inputs are inspected separately by the stage classifier;
    # this single-program API cannot choose a body valid in both shells.
    return programs[0] if len(programs) == 1 else (None, None)


def _without_body_words(tokens: list[str], heredoc_text: str) -> list[str]:
    """*tokens* minus the heredoc body words at their tail.

    The body is data: an ``if a < b`` line in it is no stdin redirect. When
    the words do not match (escape processing in an unquoted body), all
    tokens stay; a stray redirect-like word then makes the program ambiguous,
    which asks.
    """
    body = heredoc_text.split()
    if body and len(tokens) > len(body) and tokens[-len(body):] == body:
        return tokens[:-len(body)]
    return tokens


def _redirect_operand(op: str, rest: str, args: list[str], i: int) -> tuple[str, int]:
    """Return (operand text, tokens consumed) for an input redirect at args[i]."""
    if op.startswith("<<") and op != "<<<":
        # Heredoc: shlex leaves the delimiter and body as the following
        # tokens, so everything after the operator is stdin content.
        return " ".join(args[i + 1:]), len(args) - i
    if rest:
        return rest, 1
    if i + 1 < len(args):
        return args[i + 1], 2
    return "", 1


def _parse_option(
    name: str, spec: _Spec, args: list[str], i: int,
) -> tuple[int, ProgramSource | str | None]:
    """Parse args[i]. Returns (tokens consumed, outcome); 0 consumed = unrecognized.

    Outcome is a ProgramSource when the token identifies the program, STDIN for
    an explicit stdin marker, or None for a plain option.
    """
    tok = args[i]
    nxt = args[i + 1] if i + 1 < len(args) else None
    if tok in spec.stdin_flags:
        return 1, STDIN
    if tok == "--" and name not in ("source", "."):
        if nxt is None:
            return 1, STDIN
        return 2, ProgramSource(name, SCRIPT, nxt)
    if spec.plus_options and tok.startswith("+") and len(tok) > 1:
        return _parse_plus_option(spec, tok, nxt)
    if not tok.startswith("-") or tok == "-":
        return 1, ProgramSource(name, SCRIPT, tok)
    for flags, kind in ((spec.inline, INLINE), (spec.module, MODULE), (spec.script_flags, SCRIPT)):
        if tok in flags:
            return (2, ProgramSource(name, kind, nxt)) if nxt is not None else (0, None)
        long_value = _long_attached(tok, flags)
        if long_value is not None:
            return 1, ProgramSource(name, kind, long_value)
    if tok in spec.bool_flags:
        return 1, None
    if tok in spec.value_flags:
        return (2, None) if nxt is not None else (0, None)
    if _long_attached(tok, spec.value_flags) is not None:
        return 1, None
    if any(tok.startswith(p) and len(tok) > len(p) for p in spec.attached_prefixes):
        return 1, None
    return _parse_cluster(name, spec, tok, nxt)


def _long_attached(tok: str, flags: frozenset[str]) -> str | None:
    """Value of ``--flag=value`` when ``--flag`` is in *flags*."""
    if not tok.startswith("--") or "=" not in tok:
        return None
    flag, value = tok.split("=", 1)
    return value if flag in flags else None


def _parse_cluster(
    name: str, spec: _Spec, tok: str, nxt: str | None,
) -> tuple[int, ProgramSource | None]:
    """Parse a short-flag cluster like ``-uB``, ``-lne`` or ``-ec``."""
    if not spec.cluster_letters or tok.startswith("--") or len(tok) < 3:
        return 0, None
    letters = tok[1:]
    tails = spec.cluster_tail or {}
    at = len(letters) - 1 if letters[-1] in tails else -1
    if at < 0 and spec.tail_anywhere:
        at = next((k for k, ch in enumerate(letters) if ch in tails), -1)
    tail_kind = tails[letters[at]] if at >= 0 else None
    body = letters[:at] + letters[at + 1:] if at >= 0 else letters
    if not all(ch in spec.cluster_letters for ch in body):
        return 0, None
    if tail_kind is None:
        return 1, None
    if nxt is None:
        return 0, None
    return 2, ProgramSource(name, tail_kind, nxt)


def _parse_plus_option(spec: _Spec, tok: str, nxt: str | None) -> tuple[int, None]:
    """Parse a shell's ``+o NAME``, ``+O NAME`` or ``+eu`` (an option turned off, never an operand)."""
    if "-" + tok[1:] in spec.value_flags:
        return (2, None) if nxt is not None else (0, None)
    if all(ch in spec.cluster_letters for ch in tok[1:]):
        return 1, None
    return 0, None


def evaluates_input(source: ProgramSource) -> bool:
    """True when inline program text looks like it executes what it reads.

    ``python -c 'exec(sys.stdin.read())'`` takes its program from -c, yet still
    runs the piped bytes. Inline code mentioning an eval/exec/deserialize
    primitive is treated like a stdin program by the pipe rules. Coarse on
    purpose: a false positive keeps today's block, a false negative is RCE.
    """
    if source.kind != INLINE:
        return False
    spec = _SPECS.get(source.interpreter)
    if spec is None or spec.eval_re is None:
        return True
    return bool(spec.eval_re.search(source.value))


def reads_program_from_pipe(tokens: list[str], placeholder_marker: str) -> bool:
    """True unless *tokens* provably take their program from somewhere other than stdin.

    A program argument that contains a substitution placeholder
    (*placeholder_marker*) is ambiguous, so it counts as stdin too.
    """
    source = program_source(tokens)
    if source is None or source.kind == STDIN:
        return True
    if placeholder_marker in source.value:
        return True
    return evaluates_input(source)
