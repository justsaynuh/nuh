"""Task runners: the shell lines ``make T``, ``npm run T``, ``just T`` or ``task T`` would run.

``resolve`` reads the target's recipe or script from the Makefile,
package.json, justfile or Taskfile and returns it as a ``task_recipe.Recipe``
for the Bash classifier to classify line by line. None means the command is
not a task runner or is a read-only form (``make -n``, ``npm run`` with no
script, ``just --list``); ``task_recipe.Unreadable`` means it runs something nuh
cannot read, and the caller asks.
"""

import json
import os
import re
import shlex

from nuh import shell_continuations, task_make
from nuh.task_recipe import MAX_FILE_BYTES, Recipe, Unreadable

_MAX_BYTES = MAX_FILE_BYTES


def resolve(tokens: list[str]) -> Recipe | None:
    """The recipe *tokens* run, None when they are not a task runner run, or Unreadable."""
    if not tokens:
        return None
    cmd, args = os.path.basename(tokens[0]), tokens[1:]
    if cmd in ("make", "gmake"):
        return task_make.resolve(cmd, args)
    if cmd in ("npm", "pnpm", "yarn"):
        return _package_script(cmd, args)
    if cmd == "just":
        return _just(args)
    if cmd in ("task", "go-task"):
        return _taskfile(cmd, args)
    return None


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(_MAX_BYTES + 1)
    except OSError as exc:
        raise Unreadable(f"cannot read {path}: {exc.strerror}") from exc
    if len(text) > _MAX_BYTES:
        raise Unreadable(f"{path} is too large to read")
    return text


def _find_up(start: str, names: tuple[str, ...]) -> str | None:
    """The first of *names* in *start* or its ancestors (case-insensitive when *names* says so)."""
    directory = os.path.abspath(start)
    while True:
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                return path
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


# -- npm, pnpm, yarn: package.json scripts --

_NPM_SCRIPT_VERBS = {"run": None, "run-script": None, "rum": None, "urn": None, "test": "test", "t": "test",
                     "tst": "test", "start": "start", "stop": "stop", "restart": "restart"}
_NPM_VALUE = frozenset({"--prefix", "-w", "--workspace", "--userconfig", "--registry", "--cache", "--loglevel",
                        "-C", "--dir", "--cwd", "-F", "--filter", "--script-shell"})
_WORKSPACE_FLAGS = frozenset({"-w", "--workspace", "-ws", "--workspaces", "-r", "--recursive", "-F", "--filter"})
_PNPM_BUILTINS = frozenset({
    "add", "audit", "bin", "cache", "config", "c", "create", "dedupe", "deploy", "dlx", "doctor", "env", "exec",
    "fetch", "import", "init", "install", "i", "install-test", "it", "licenses", "link", "ln", "list", "ls", "ll",
    "la", "outdated", "pack", "patch", "patch-commit", "patch-remove", "prune", "publish", "rebuild", "rb",
    "recursive", "remove", "rm", "uninstall", "un", "root", "server", "setup", "store", "unlink", "update", "up",
    "upgrade", "why", "help", "approve-builds", "self-update", "catalog", "x",
})
_YARN_BUILTINS = frozenset({
    "add", "audit", "autoclean", "bin", "cache", "check", "config", "constraints", "create", "dedupe", "dlx",
    "exec", "explain", "generate-lock-entry", "global", "help", "import", "info", "init", "install", "licenses",
    "link", "list", "login", "logout", "node", "npm", "outdated", "owner", "pack", "patch", "patch-commit",
    "plugin", "policies", "publish", "rebuild", "remove", "search", "set", "stage", "tag", "team", "unlink",
    "unplug", "up", "upgrade", "upgrade-interactive", "version", "versions", "why", "workspace", "workspaces",
})


def _package_script(cmd: str, args: list[str]) -> Recipe | None:
    directory, workspace = ".", False
    operands: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        name, eq, value = tok.partition("=")
        if name in _WORKSPACE_FLAGS:
            workspace = True
        if operands or not tok.startswith("-") or tok == "--":
            operands.append(tok)
            i += 1
            continue
        if name in _NPM_VALUE and not eq:
            value = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        if name in ("--prefix", "-C", "--dir", "--cwd"):
            directory = os.path.join(directory, os.path.expanduser(value))
        i += 1
    found = _script_and_args(cmd, operands)
    if found is None:
        return None
    script, extra, implicit = found
    if workspace:
        raise Unreadable(f"{cmd} runs {script} across workspaces")
    path = _find_up(directory, ("package.json",))
    if path is None:
        # `pnpm build`: maybe a binary, classified as before. `npm test`: npm
        # stops with no package.json, so nothing runs.
        return None if implicit else Recipe(f"{cmd} run {script}", "", [], os.path.abspath(directory))
    scripts = _scripts(path)
    if implicit and script not in scripts:
        return None  # `pnpm eslint`: a binary, not a script; classified as before
    lines = []
    for name in (f"pre{script}", script, f"post{script}"):
        body = scripts.get(name)
        if isinstance(body, str) and body.strip():
            lines.append(body + (" " + shlex.join(extra) if name == script and extra else ""))
    package_dir = os.path.dirname(path)
    if script not in scripts:
        if cmd == "yarn":
            lines.append(shlex.join([script, *extra]))  # yarn run X runs the package binary X
        elif script == "start" and os.path.isfile(os.path.join(package_dir, "server.js")):
            lines.append("node server.js")
    return Recipe(f"{cmd} run {script}", path, lines, package_dir)


def _script_and_args(cmd: str, operands: list[str]) -> tuple[str, list[str], bool] | None:
    """(script, its extra arguments, named without `run`) for a package-manager command line.

    None when it runs no script: another verb, or a bare `run` that lists them.
    """
    if not operands:
        return None
    verb, rest = operands[0], operands[1:]
    if verb in ("run", "run-script", "rum", "urn"):
        names = [n for n, a in enumerate(rest) if not a.startswith("-")]
        if not names or ("--" in rest and rest.index("--") < names[0]):
            return None
        script, rest = rest[names[0]], rest[names[0] + 1:]
        implicit = False
    elif verb in _NPM_SCRIPT_VERBS:
        script, implicit = _NPM_SCRIPT_VERBS[verb] or "", False
    else:
        builtins = _PNPM_BUILTINS if cmd == "pnpm" else _YARN_BUILTINS if cmd == "yarn" else None
        if builtins is None or verb in builtins or verb.startswith("-"):
            return None
        script, implicit = verb, True
    if cmd == "npm":
        extra = rest[rest.index("--") + 1:] if "--" in rest else []
    else:
        extra = [a for a in rest if a != "--"]
    return script, extra, implicit


def _scripts(path: str) -> dict:
    try:
        manifest = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise Unreadable(f"{path} is not valid JSON: {exc.msg}") from exc
    scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
    return scripts if isinstance(scripts, dict) else {}


# -- just --

_JUST_FILES = ("justfile", "Justfile", ".justfile", "JUSTFILE")
_JUST_READ_ONLY = frozenset({"-l", "--list", "--summary", "-s", "--show", "--dump", "--evaluate", "--variables",
                             "-h", "--help", "-V", "--version", "-n", "--dry-run", "--groups", "--usage",
                             "--completions", "--man", "--check"})
_JUST_VALUE = frozenset({"-f", "--justfile", "-d", "--working-directory", "--dotenv-filename", "--dotenv-path",
                         "-E", "--color", "--command-color", "--chooser", "--list-heading", "--list-prefix",
                         "--shell-arg", "--timestamp-format", "--list-submodules", "--alias-style"})
_JUST_RECIPE_RE = re.compile(r"^(@?)([A-Za-z_][A-Za-z0-9_-]*)((?:\s+[^:=]*?)?)\s*:(?!=)(.*)$")
_JUST_ASSIGN_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*:=\s*(.*)$")
_JUST_INTERP_RE = re.compile(r"\{\{(.*?)\}\}")
_BACKTICK_RE = re.compile(r"`([^`]*)`")
_STRING_RE = re.compile(r"""^(?:'([^']*)'|"([^"\\]*)")$""")


def _just(args: list[str]) -> Recipe | None:
    justfile, directory, positional, command = None, ".", [], None
    i = 0
    while i < len(args):
        tok = args[i]
        name, eq, value = tok.partition("=")
        if tok in _JUST_READ_ONLY or name in _JUST_READ_ONLY:
            return None
        if tok in ("--choose", "--edit", "--fmt", "--init", "--set", "--shell", "-c", "--command"):
            if tok in ("-c", "--command") and i + 1 < len(args):
                command = args[i + 1]
                break
            raise Unreadable(f"just {tok} is not something nuh reads")
        if tok.startswith("-"):
            if tok in _JUST_VALUE:
                value = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            if name in ("-f", "--justfile"):
                justfile = value
            elif name in ("-d", "--working-directory"):
                directory = value
            i += 1
            continue
        positional = args[i:]
        break
    path = justfile or _find_up(directory, _JUST_FILES)
    if path is None:
        raise Unreadable("no justfile")
    book = _JustBook(path)
    if command is not None:
        return Recipe("just --command", path, book.load_lines + [command], os.path.dirname(os.path.abspath(path)))
    lines = book.load_lines + book.run(positional)
    label = "just " + " ".join(positional) if positional else "just"
    opaque = "just recipe uses interpolation nuh cannot resolve" if any("__just_" in line for line in lines) else ""
    return Recipe(label.strip(), path, lines, os.path.dirname(os.path.abspath(path)), opaque_reason=opaque)


class _JustBook:
    def __init__(self, path: str):
        self.recipes: dict[str, tuple[list[str], list[str], list[str]]] = {}  # name -> (params, deps, body)
        self.order: list[str] = []
        self.values: dict[str, str] = {}
        self.load_lines: list[str] = []
        self._read(path, 0)

    def _read(self, path: str, depth: int) -> None:
        if depth > 5:
            raise Unreadable("justfile imports nest too deeply")
        lines = _read(path).replace("\r\n", "\n").split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("["):
                continue
            if line[0] in " \t":
                continue
            first = stripped.split(None, 1)[0]
            if first in ("import", "import?"):
                target = stripped.split(None, 1)[1].strip().strip("'\"") if " " in stripped else ""
                full = os.path.join(os.path.dirname(path), os.path.expanduser(target))
                if os.path.isfile(full):
                    self._read(full, depth + 1)
                elif first == "import":
                    raise Unreadable(f"justfile imports missing {target}")
                continue
            if first in ("mod", "mod?", "alias"):
                if first == "alias":
                    parts = stripped.split(":=")
                    if len(parts) == 2:
                        self.recipes.setdefault(parts[0].split()[1], ([], [parts[1].strip()], []))
                continue
            if first == "set":
                self._setting(stripped)
                continue
            assign = _JUST_ASSIGN_RE.match(stripped)
            if assign:
                self._assign(assign.group(1), assign.group(2))
                continue
            header = _JUST_RECIPE_RE.match(stripped)
            if not header:
                raise Unreadable(f"cannot read justfile line: {stripped[:80]}")
            body = []
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                body.append(lines[i])
                i += 1
            name = header.group(2)
            params = header.group(3).split()
            deps = [d for d in re.sub(r"[()]", " ", header.group(4)).replace("&&", " ").split()
                    if re.match(r"^[A-Za-z_][\w-]*$", d)]
            self.recipes[name] = (params, deps, _dedent(body))
            self.order.append(name)

    def _setting(self, line: str) -> None:
        if re.match(r"^set\s+(shell|windows-shell|script-interpreter)\b", line):
            if not re.search(r"""['"](?:/bin/|/usr/bin/)?(?:ba|z|da|k)?sh['"]""", line):
                raise Unreadable(f"justfile {line.strip()} runs recipes in something other than a shell")

    def _assign(self, name: str, expr: str) -> None:
        self.load_lines += _BACKTICK_RE.findall(expr)
        literal = _STRING_RE.match(expr.strip())
        self.values[name] = (literal.group(1) or literal.group(2) or "") if literal else f"__just_{name}__"

    def run(self, positional: list[str]) -> list[str]:
        overrides = {}
        while positional and re.match(r"^[A-Za-z_][\w-]*=", positional[0]):
            key, _sep, value = positional.pop(0).partition("=")
            overrides[key] = value
        self.values.update(overrides)
        if not positional:
            if not self.order:
                raise Unreadable("the justfile has no recipes")
            positional = [self.order[0]]
        lines: list[str] = []
        done: set[str] = set()
        while positional:
            name = positional.pop(0)
            if name not in self.recipes:
                raise Unreadable(f"no just recipe {name}")
            params = self.recipes[name][0]
            variadic = any(p.startswith(("*", "+", "$*", "$+")) for p in params)
            count = len(positional) if variadic else min(len(params), len(positional))
            arguments, positional = positional[:count], positional[count:]
            self._visit(name, arguments, lines, done, 0)
        return lines

    def _visit(self, name: str, arguments: list[str], lines: list[str], done: set[str], depth: int) -> None:
        if name in done or depth > 50:
            return
        done.add(name)
        if name not in self.recipes:
            raise Unreadable(f"no just recipe {name}")
        params, deps, body = self.recipes[name]
        for dep in deps:
            self._visit(dep, [], lines, done, depth + 1)
        if not body:
            return
        values = dict(self.values)
        for n, param in enumerate(params):
            key = param.lstrip("*+$").split("=", 1)[0]
            values[key] = arguments[n] if n < len(arguments) else f"__just_{key}__"
        text = [_JUST_INTERP_RE.sub(lambda m: values.get(m.group(1).strip(), "__just_expr__"), line) for line in body]
        if text[0].startswith("#!"):
            interpreter = os.path.basename(text[0][2:].split()[-1] if text[0][2:].split() else "")
            if interpreter not in ("sh", "bash", "zsh", "dash"):
                raise Unreadable(f"just recipe {name} is a {interpreter or 'shebang'} script")
            lines.append("\n".join(text[1:]))
            return
        # Just joins continued recipe lines before passing them to the shell.
        joined = shell_continuations.join("\n".join(text)).split("\n")
        lines += [line.lstrip("@-").strip() for line in joined if line.strip() and not line.lstrip().startswith("#")]


def _dedent(body: list[str]) -> list[str]:
    while body and not body[-1].strip():
        body.pop()
    indents = [len(line) - len(line.lstrip()) for line in body if line.strip()]
    cut = min(indents) if indents else 0
    return [line[cut:] for line in body]


# -- task (go-task) --

_TASKFILES = ("Taskfile.yml", "taskfile.yml", "Taskfile.yaml", "taskfile.yaml",
              "Taskfile.dist.yml", "taskfile.dist.yml", "Taskfile.dist.yaml", "taskfile.dist.yaml")
_TASK_READ_ONLY = frozenset({"-l", "--list", "-a", "--list-all", "--summary", "-n", "--dry", "-h", "--help",
                             "--version", "--status"})
_TASK_VALUE = frozenset({"-t", "--taskfile", "-d", "--dir", "-o", "--output", "-C", "--concurrency",
                         "--interval", "--sort", "--output-group-begin", "--output-group-end"})
_TASK_TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}")


def _taskfile(cmd: str, args: list[str]) -> Recipe | None:
    taskfile, directory, names, cli_args = None, ".", [], []
    i = 0
    while i < len(args):
        tok = args[i]
        name, eq, value = tok.partition("=")
        if tok == "--":
            cli_args = args[i + 1:]
            break
        if name in _TASK_READ_ONLY:
            return None
        if tok in ("-i", "--init", "-w", "--watch", "-I"):
            raise Unreadable(f"{cmd} {tok} is not something nuh reads")
        if tok.startswith("-"):
            if name in _TASK_VALUE and not eq:
                value = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            if name in ("-t", "--taskfile"):
                taskfile = value
            elif name in ("-d", "--dir"):
                directory = value
            i += 1
            continue
        names.append(tok)
        i += 1
    path = taskfile or _find_up(directory, _TASKFILES)
    if path is None:
        raise Unreadable("no Taskfile")
    import yaml  # lazy: only a Taskfile needs it
    try:
        data = yaml.safe_load(_read(path))
    except yaml.YAMLError as exc:
        raise Unreadable(f"{path} is not valid YAML") from exc
    if not isinstance(data, dict):
        raise Unreadable(f"{path} is not a Taskfile")
    if data.get("includes"):
        raise Unreadable("the Taskfile includes other Taskfiles")
    book = _TaskBook(data, os.path.dirname(os.path.abspath(path)), " ".join(shlex.quote(a) for a in cli_args))
    overrides = {n.split("=", 1)[0]: n.split("=", 1)[1] for n in names if re.match(r"^[A-Za-z_]\w*=", n)}
    book.values.update(overrides)
    goals = [n for n in names if n.split("=", 1)[0] not in overrides] or ["default"]
    lines: list[str] = []
    for goal in goals:
        book.visit(goal, lines, set(), 0)
    opaque = "task recipe uses interpolation nuh cannot resolve" if any("__task_" in line for line in lines) else ""
    return Recipe(f"{cmd} {' '.join(goals)}", path, book.load_lines + lines, book.directory, opaque_reason=opaque)


class _TaskBook:
    def __init__(self, data: dict, directory: str, cli_args: str):
        self.tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
        self.directory = directory
        self.values: dict[str, str] = {"CLI_ARGS": cli_args}
        self.load_lines: list[str] = []
        self._vars(data.get("vars"))

    def _vars(self, raw: object) -> None:
        if not isinstance(raw, dict):
            return
        for name, value in raw.items():
            if isinstance(value, dict) and isinstance(value.get("sh"), str):
                self.load_lines.append(value["sh"])
                self.values[str(name)] = f"__task_{name}__"
            elif isinstance(value, (str, int, float, bool)):
                self.values[str(name)] = str(value)

    def _render(self, text: str) -> str:
        def sub(match: re.Match) -> str:
            expr = match.group(1).strip()
            return self.values.get(expr[1:], f"__task_{expr}__") if expr.startswith(".") else "__task_expr__"
        return _TASK_TEMPLATE_RE.sub(sub, text)

    def visit(self, name: str, lines: list[str], done: set[str], depth: int) -> None:
        if name in done or depth > 50:
            return
        done.add(name)
        task = self.tasks.get(name)
        if task is None:
            raise Unreadable(f"no task {name}")
        if isinstance(task, str):
            task = {"cmds": [task]}
        elif isinstance(task, list):
            task = {"cmds": task}
        if not isinstance(task, dict):
            raise Unreadable(f"task {name} is not something nuh reads")
        if task.get("dir") not in (None, "", "."):
            raise Unreadable(f"task {name} runs in another directory ({task['dir']})")
        self._vars(task.get("vars"))
        for dep in task.get("deps") or []:
            dep_name = dep.get("task") if isinstance(dep, dict) else dep
            self.visit(str(dep_name), lines, done, depth + 1)
        for check in (task.get("preconditions") or []) + (task.get("status") or []):
            text = check.get("sh") if isinstance(check, dict) else check
            if isinstance(text, str):
                lines.append(self._render(text))
        for item in task.get("cmds") or []:
            self._command(item, lines, done, depth)

    def _command(self, item: object, lines: list[str], done: set[str], depth: int) -> None:
        if isinstance(item, str):
            lines.append(self._render(item))
        elif isinstance(item, dict) and isinstance(item.get("cmd"), str) and "for" not in item:
            lines.append(self._render(item["cmd"]))
        elif isinstance(item, dict) and isinstance(item.get("task"), str):
            self.visit(item["task"], lines, done, depth + 1)
        elif isinstance(item, dict) and isinstance(item.get("defer"), str):
            lines.append(self._render(item["defer"]))
        else:
            raise Unreadable("a task command nuh cannot read (for loop or unknown form)")
