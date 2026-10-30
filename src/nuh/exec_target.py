"""Whether text written to a file can run — the gate for runnable-text content patterns (want 8a).

A Write or Edit puts text on disk; destructive or exfiltration text only does
harm once something runs it. Running a file through nuh is inspected at run
time (``bash notes.md``, ``. notes.md``, ``./notes.md``, ``make`` and its
includes all scan the file's content), so text that nothing runs on its own
needs no ask when written. What runs on its own is listed here: an allowlist
of kinds whose content executes (scripts, build files, CI and tool config,
shell rc files, hooks, units, crontabs), a shebang, or an executable bit.
Anything not recognised is data. When the existing file cannot be examined,
it counts as runnable: when in doubt, inspect.
"""

import os
import re
import stat

# Source code, scripts and templates that render code.
_CODE_EXTENSIONS = frozenset({
    ".sh", ".bash", ".zsh", ".ksh", ".mksh", ".csh", ".tcsh", ".fish", ".nu", ".xsh", ".elv",
    ".command", ".rc", ".py", ".pyw", ".pyz", ".pth", ".ipynb", ".rb", ".rake", ".gemspec", ".ru",
    ".pl", ".pm", ".t", ".cgi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts",
    ".php", ".phtml", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs", ".vbe", ".wsf", ".ahk",
    ".applescript", ".scpt", ".lua", ".tcl", ".tk", ".r", ".jl", ".awk", ".sed", ".el", ".vim",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx", ".m", ".mm", ".go", ".rs", ".java",
    ".kt", ".kts", ".scala", ".sc", ".groovy", ".gradle", ".swift", ".cs", ".csx", ".fs", ".fsx",
    ".vb", ".dart", ".zig", ".nim", ".cr", ".ex", ".exs", ".erl", ".hrl", ".hs", ".ml", ".clj",
    ".cljs", ".cljc", ".lisp", ".scm", ".rkt", ".sql", ".psql",
    ".erb", ".ejs", ".j2", ".jinja", ".jinja2", ".tmpl", ".tpl",
})

# Build files and config that tools run commands from (CI, compose, Ansible,
# Taskfile, tox, systemd, launchd, terraform provisioners, desktop entries).
_CONFIG_EXTENSIONS = frozenset({
    ".mk", ".make", ".mak", ".just", ".bzl", ".star", ".bazel", ".cmake", ".ninja", ".gyp",
    ".gypi", ".nix", ".dockerfile", ".containerfile",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".plist", ".desktop", ".cron", ".hcl", ".tf",
    ".service", ".timer", ".socket", ".path", ".mount", ".automount", ".swap", ".target",
    ".slice", ".scope",
})

_EXTENSIONS = _CODE_EXTENSIONS | _CONFIG_EXTENSIONS

# Basenames (compared lowercased) whose content runs: build and task files,
# package manifests with scripts, editor/devcontainer task files, agent and
# MCP wiring, and shell, editor and session rc files.
_NAMES = frozenset({
    "makefile", "gnumakefile", "justfile", ".justfile", "taskfile", "rakefile", "gemfile",
    "guardfile", "vagrantfile", "brewfile", "podfile", "fastfile", "appfile", "dangerfile",
    "jenkinsfile", "earthfile", "tiltfile", "procfile", "dockerfile", "containerfile", "build",
    "workspace", "snakefile", "sconstruct", "sconscript", "meson.build", "cmakelists.txt",
    "configure", "build.xml", "pom.xml",
    "package.json", "composer.json", "deno.json", "deno.jsonc", "project.json", "tasks.json",
    "launch.json", "devcontainer.json", ".devcontainer.json", ".mcp.json", "mcp.json",
    "claude_desktop_config.json",
    "crontab", ".npmrc", ".yarnrc", ".envrc", ".env", ".gitconfig", "gitconfig", "rc.local",
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bash_aliases", ".profile",
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".kshrc", ".mkshrc", ".cshrc",
    ".tcshrc", ".login", ".logout", "bashrc", "bash.bashrc", "zshrc", "zshenv", "zprofile",
    "zlogin", "profile", "csh.cshrc", "csh.login", ".xinitrc", ".xsession", ".xsessionrc",
    ".xprofile", ".vimrc", ".gvimrc", ".exrc", ".screenrc", ".irbrc", ".pryrc",
})

# Basename prefixes (lowercased): Dockerfile.dev, Containerfile.prod, and
# .env.local (dotenv files get sourced by shells and included by Makefiles).
_NAME_PREFIXES = ("dockerfile.", "containerfile.", ".env.")

# Directories whose files run (hooks, CI, editor tasks, cron, init, profile
# fragments, executables on a PATH). Matched as a path component.
_DIRS = frozenset({
    ".githooks", ".husky", ".circleci", ".buildkite", ".woodpecker", ".devcontainer", ".vscode",
    ".idea", "cron.d", "cron.daily", "cron.hourly", "cron.weekly", "cron.monthly", "crontabs",
    "init.d", "rc.d", "profile.d", ".bashrc.d", ".zshrc.d", "bin", "sbin", "libexec",
})
_RC_LEVEL_DIR = re.compile(r"rc[0-6S]\.d")

# Path endings (component-wise) whose file runs.
_TAILS = (
    (".git", "config"), (".git", "config.worktree"), (".config", "git", "config"),
    (".claude", "settings.json"), (".claude", "settings.local.json"),
)

_SHEBANG_LINE = re.compile(r"^#!", re.MULTILINE)


def _named_kind(path: str) -> str:
    """Why *path*'s name or location makes it run, or ""."""
    parts = [p for p in path.split(os.sep) if p]
    if not parts:
        return ""
    base = parts[-1].lower()
    ext = os.path.splitext(base)[1]
    if ext in _EXTENSIONS:
        return f"{ext} file"
    if base in _NAMES or base.startswith(_NAME_PREFIXES):
        return f"{parts[-1]} runs its content"
    dirs = parts[:-1]
    for i, part in enumerate(dirs):
        if part in _DIRS or _RC_LEVEL_DIR.fullmatch(part):
            return f"file under {part}/"
        if part == ".git" and "hooks" in dirs[i + 1:]:
            return "git hook"
    for tail in _TAILS:
        if tuple(parts[-len(tail):]) == tail:
            return f"{os.path.join(*tail)} runs its content"
    return ""


def _existing_kind(resolved: str) -> str:
    """Why the file already at *resolved* runs (executable bit, shebang), or ""."""
    try:
        st = os.stat(resolved)
    except (FileNotFoundError, NotADirectoryError):
        return ""
    except OSError as exc:
        # The file is there but cannot be examined, so whether it runs is
        # unknown: inspect rather than call it data.
        return f"existing file cannot be examined ({type(exc).__name__})"
    if not stat.S_ISREG(st.st_mode):
        return "existing target is not a regular file"
    if st.st_mode & 0o111:
        return "existing file is executable"
    try:
        with open(resolved, "rb") as f:
            head = f.read(2)
    except OSError as exc:
        # As above: unreadable means its first line is unknown, so inspect.
        return f"existing file cannot be read ({type(exc).__name__})"
    return "existing file has a shebang" if head == b"#!" else ""


def runnable_reason(file_path: str, new_text: str, *, partial: bool) -> str:
    """Why text written to *file_path* can run, or "" when the target is data.

    *new_text* is what the call writes. With *partial* (an edit or patch hunk,
    whose position in the file is unknown) a shebang on any line counts;
    otherwise (whole new content) only at the start. Both the path as given
    and its resolved target are checked, so a symlink to a script counts.
    """
    if not file_path:
        return "target unknown"
    resolved = os.path.realpath(os.path.expanduser(file_path))
    for spelling in dict.fromkeys((os.path.abspath(os.path.expanduser(file_path)), resolved)):
        reason = _named_kind(spelling)
        if reason:
            return reason
    if new_text.startswith("#!") or (partial and _SHEBANG_LINE.search(new_text)):
        return "shebang in new content"
    return _existing_kind(resolved)
