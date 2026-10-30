"""``nuh trace``: browse and render the LLM traces under ``llm.trace_dir``. Read-only, except ``--prune``.

With no argument on a terminal with fzf on PATH, an interactive picker: one
line per trace, newest first, the highlighted trace rendered beside it, keys
to switch views and Enter for the full rendering in ``$PAGER``. Without fzf
or a terminal it prints the list. ``nuh trace '#abc123'`` (a call reference,
as ``nuh log --call`` takes it) or ``nuh trace <dir>`` renders one trace.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

from nuh import trace_names, trace_prune
from nuh.log_view import BOLD, CYAN, DECISION_STYLE, DIM, RED, truncate, use_color
from nuh.notice import CALL_CHARS
from nuh.trace_read import ListFilter, Summary, find, load, newest, root_dir, summarize_safely, system1_answers
from nuh.trace_render import Renderer, final_steps
from nuh.trace_views import ALL

_DEFAULT_WIDTH = 120
_INPUT_WIDTH = 40
_KEYS = {"alt-a": "answers", "alt-p": "prompt", "alt-d": "path", "alt-r": "raw"}
_LEGEND = "enter: full view in $PAGER · alt-a answers · alt-p prompt · alt-d decision path · alt-r raw files"


def cmd_trace(args: argparse.Namespace) -> None:
    try:
        raise SystemExit(_run(args))
    except BrokenPipeError:
        # The reader went away (``nuh trace | head``): stop quietly, as nuh log does.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def _run(args: argparse.Namespace) -> int:
    from nuh.config import get_config  # lazy import: config loading is the slow part of a CLI start
    cfg = get_config()
    root = os.path.expanduser(args.dir) if args.dir else root_dir(cfg)
    target = (args.render or args.target or "").strip()  # fzf's {1} field may keep surrounding blanks
    if args.dry_run and not args.prune:
        return _fail("--dry-run goes with --prune")
    if args.prune:
        if target:
            return _fail("--prune takes no trace argument")
        return trace_prune.run_cli(root, cfg.llm_trace_keep_days, dry_run=args.dry_run)
    if not root and not os.path.isdir(target):
        return _fail("llm.trace_dir is not set (tracing is off); pass --dir")
    color = _color(args.color)
    if target:
        return _show_one(root, target, args, Renderer(color=color, width=_width()), by_ref=not args.render)
    flt = ListFilter(since=args.since, tool=args.tool, decision=args.decision, provider=args.provider)
    if not args.json and not args.list and sys.stdout.isatty() and shutil.which("fzf"):
        return _pick(root, flt, args)
    return _print_list(root, flt, args, color)


def _fail(message: str, code: int = 2) -> int:
    print(f"nuh trace: {message}", file=sys.stderr)
    return code


def _color(mode: str) -> bool:
    if mode == "never" or os.environ.get("NO_COLOR"):
        return False
    return mode == "always" or use_color(sys.stdout)


def _width() -> int:
    preview = os.environ.get("FZF_PREVIEW_COLUMNS", "")
    if preview.isdigit():
        return int(preview)
    return shutil.get_terminal_size((_DEFAULT_WIDTH, 24)).columns


# -- One trace --


def _resolve(root: str, target: str, by_ref: bool) -> str:
    """The trace directory *target* names: a directory path, a trace name under *root*, or a call reference."""
    if os.path.isdir(target):
        return target
    if root and trace_names.parse(target) is not None and os.path.lexists(os.path.join(root, target)):
        return os.path.join(root, target)  # rendered even when it is not a directory: as unreadable
    if by_ref and root:
        try:
            name = find(root, target)
        except OSError as exc:
            print(f"nuh trace: cannot list {root}: {exc.strerror or exc}", file=sys.stderr)
            return ""
        return os.path.join(root, name) if name else ""
    return ""


def _show_one(root: str, target: str, args: argparse.Namespace, renderer: Renderer, *, by_ref: bool) -> int:
    path = _resolve(root, target, by_ref)
    if not path:
        return _fail(f"no trace for {target!r}" + (f" under {root}" if root else ""), 1)
    trace = load(path)
    if args.json:
        record = summarize_safely(path).as_json()
        record.update(answers=system1_answers(trace) or None, steps=final_steps(trace), files=trace.listing)
        print(json.dumps(record, ensure_ascii=False))
        return 0
    try:
        print(renderer.render(trace, args.view))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # A trace whose JSON has a shape this version does not expect: say
        # so, and show the files as they are rather than nothing.
        print(renderer.paint(RED, f"nuh trace: cannot render {args.view}: {type(exc).__name__}: {exc}"))
        print(renderer.render(trace, "raw"))
    return 0


# -- The list --


def list_line(summary: Summary, *, color: bool) -> str:
    """One list line: local time, call, tool, input, decision, provider, system 1 verdict and risk."""
    def paint(style: str, text: str) -> str:
        return f"\033[{style}m{text}\033[0m" if color and style and text.strip() else text
    when = summary.stamp.astimezone().strftime("%m-%d %H:%M:%S") if summary.stamp else "??-?? --:--:--"
    call = f"#{summary.call[-CALL_CHARS:]}" if summary.call else "#-"
    head = f"{paint(DIM, when)}  {paint(CYAN, call.ljust(7))}"
    if summary.error and not summary.tool and not summary.decision:
        return f"{head}  {paint(RED, '(unreadable: ' + summary.error + ')')}"
    inp = truncate(summary.input.replace("\r", "").replace("\n", "⏎"), _INPUT_WIDTH).ljust(_INPUT_WIDTH)
    decision = summary.decision or "?"
    verdict = summary.verdict
    said = f"{verdict.get('choice') or '-'} {_p(verdict.get('p'))}" if verdict else ""
    risk = f"risk {summary.risk['score']:.2f}" if summary.risk.get("score") is not None else ""
    parts = [head, paint(BOLD, (summary.tool or "?")[:8].ljust(8)), inp,
             paint(DECISION_STYLE.get(decision, ""), decision.ljust(5)), (summary.provider or "-").ljust(8),
             paint(DECISION_STYLE.get(str(verdict.get("choice")), ""), said.ljust(10)), risk]
    line = "  ".join(parts).rstrip()
    return line + (paint(RED, "  (partial)") if summary.error else "")


def _p(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, float) else "-"


def _summaries(root: str, flt: ListFilter, limit: int) -> list[Summary] | None:
    try:
        return list(newest(root, flt, limit))
    except OSError as exc:
        print(f"nuh trace: cannot list {root}: {exc.strerror or exc}", file=sys.stderr)
        return None


def _print_list(root: str, flt: ListFilter, args: argparse.Namespace, color: bool) -> int:
    found = _summaries(root, flt, args.limit)
    if found is None:
        return 1
    if not found and not args.json:
        print("No traces found.")
    for summary in found:
        print(json.dumps(summary.as_json(), ensure_ascii=False) if args.json else list_line(summary, color=color))
    return 0


def _self_command(root: str) -> str:
    """How fzf's preview and Enter run this nuh, quoted for sh."""
    argv0 = sys.argv[0] if sys.argv else ""
    if os.path.basename(argv0) == "nuh" and os.path.isfile(argv0):
        base = [os.path.abspath(argv0)]
    else:
        base = [sys.executable, "-m", "nuh.cli"]
    return shlex.join([*base, "trace", "--dir", root, "--color", "always"])


def _pick(root: str, flt: ListFilter, args: argparse.Namespace) -> int:
    found = _summaries(root, flt, args.limit)
    if found is None:
        return 1
    if not found:
        print("No traces found.")
        return 0
    color = _color(args.color)
    lines = "".join(f"{s.name}\t{list_line(s, color=color)}\n" for s in found)
    me = _self_command(root)

    def render(view: str) -> str:
        return f"{me} --view {view} --render {{1}}"

    binds = [f"{key}:change-preview({render(view)})" for key, view in _KEYS.items()]
    binds.append(f"enter:execute({render(ALL)} | ${{PAGER:-less -R}})")
    cmd = ["fzf", "--ansi", "--no-sort", "--delimiter=\t", "--with-nth=2..", "--header", _LEGEND,
           "--preview", render("answers"), "--preview-window", "right,60%,wrap,<160(down,60%,wrap)"]
    for bind in binds:
        cmd += ["--bind", bind]
    # fzf runs preview and execute commands with $SHELL; the commands above are sh syntax
    # (zsh, for one, does not split an unquoted $PAGER). less shows colour with -R.
    env = {**os.environ, "SHELL": "/bin/sh"}
    env.setdefault("LESS", "-R")
    done = subprocess.run(cmd, input=lines, text=True, env=env, check=False)
    # 0: picked, 1: no match, 130: Esc or Ctrl-C. Anything else is fzf's own error, already shown.
    return 0 if done.returncode in (0, 1, 130) else done.returncode
