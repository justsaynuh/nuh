"""Context items a system 1 definition may ask for: ``project``, ``cwd_rel``, ``git.branch``, ``git.dirty``.

They go to the model as JSON in the state's context block (system1_state),
never into preamble or instruction text. Each is computed only when a
definition asks for it, once per hook call (cached on the ToolCall). An item
nuh cannot compute is null, and ``omitted`` says why.

The git calls read a repository the agent may control, so each runs with
what would make git run a program switched off: no fsmonitor, no index
write (so no post-index-change hook), no submodules, and attributes read
from the empty tree (so no clean filter). Each call may take at most
``GIT_TIMEOUT_S``.
"""

from __future__ import annotations

import os
import subprocess

from nuh.system1_def import Context
from nuh.tool_call import ToolCall

GIT_TIMEOUT_S = 0.2
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_ATTR_SOURCE": _EMPTY_TREE, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
_GIT_FLAGS = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false")


def wanted(ctx: Context) -> bool:
    """True when the definition asks for any item here."""
    return ctx.project or ctx.cwd_rel or ctx.git_branch or ctx.git_dirty


def gather(ctx: Context, call: ToolCall) -> dict:
    """The items *ctx* asks for, about *call*; ``omitted`` names each one nuh could not compute, and why."""
    items: dict = {}
    omitted: dict[str, str] = {}
    root = _project_root() if ctx.project or ctx.cwd_rel else None
    if ctx.project:
        items["project"] = root
        if root is None:
            omitted["project"] = "no project root"
    if ctx.cwd_rel:
        items["cwd_rel"], why = _cwd_rel(call.cwd, root)
        if why:
            omitted["cwd_rel"] = why
    if ctx.git_branch or ctx.git_dirty:
        items["git"] = {}
    for name, wanted_item, fetch in (("branch", ctx.git_branch, _branch), ("dirty", ctx.git_dirty, _dirty)):
        if wanted_item:
            items["git"][name], why = _cached(call, f"git.{name}", lambda f=fetch: f(call.cwd))
            if why:
                omitted[f"git.{name}"] = why
    if omitted:
        items["omitted"] = omitted
    return items


def _project_root() -> str | None:
    from nuh.paths import get_project_root  # lazy import: paths reads the config
    return get_project_root()


def _cwd_rel(cwd: str, root: str | None) -> tuple[str | None, str]:
    if root is None:
        return None, "no project root"
    real_cwd, real_root = os.path.realpath(cwd or os.getcwd()), os.path.realpath(root)
    if real_cwd != real_root and not real_cwd.startswith(real_root + os.sep):
        return None, "the working directory is outside the project"
    return os.path.relpath(real_cwd, real_root), ""


def _cached(call: ToolCall, key: str, fetch) -> tuple[object, str]:
    """*fetch()*'s (value, why omitted), once per hook call."""
    if key not in call.facts:
        call.facts[key] = fetch()
    return call.facts[key]


def _git(cwd: str, *args: str) -> tuple[int, str, str]:
    """(returncode, stdout, why it failed) of one hardened git call in *cwd*; returncode -1 when it did not run."""
    try:
        done = subprocess.run(["git", *_GIT_FLAGS, "-C", cwd or os.getcwd(), *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_S, env={**os.environ, **_GIT_ENV},
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", f"git took over {int(GIT_TIMEOUT_S * 1000)} ms"
    except OSError as exc:
        return -1, "", f"git did not run: {type(exc).__name__}"
    return done.returncode, done.stdout, ""


def _branch(cwd: str) -> tuple[str | None, str]:
    code, out, why = _git(cwd, "symbolic-ref", "--short", "-q", "HEAD")
    if why:
        return None, why
    if code == 0 and out.strip():
        return out.strip(), ""
    inside, _out, why = _git(cwd, "rev-parse", "--is-inside-work-tree")
    if why:
        return None, why
    return (None, "detached HEAD") if inside == 0 else (None, "not a git repository")


def _dirty(cwd: str) -> tuple[int | None, str]:
    """How many paths ``git status`` lists as changed or untracked (a count only, never the paths)."""
    code, out, why = _git(cwd, "--no-optional-locks", "status", "--porcelain=v1", "--ignore-submodules=all")
    if why:
        return None, why
    if code != 0:
        return None, "not a git repository"
    return sum(1 for line in out.splitlines() if line.strip()), ""
