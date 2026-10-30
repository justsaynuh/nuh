"""The catastrophic floor: deletes, storage wipes and permission changes no prompt should allow.

Adapted from manuelschipper/nah (MIT), 0.x commit b4f467c7 "Block catastrophic
system actions" (context.py check_catastrophic_delete_target and friends,
bash.py _catastrophic_storage_reason, _recursive_permission_reason and
_FORK_BOMB_RE). Changes for nuh: targets are also checked with unresolved
variables and substitutions emptied (``rm -rf "$UNSET/"``), any ``.git``
directory counts, not only the project's, a wildcard over a trusted root
asks, ``find`` roots never block (find filters what it deletes), and
non-recursive permission changes of those roots ask.

Every verdict carries floor rule ``catastrophic``: blocks override the
user's ``actions:`` policies, and asks never go to the LLM unless the global
config sets ``human_only: {catastrophic: false}``.
"""

import fnmatch
import os
import re

from nuh import file_operands, find_args, floor, path_names, paths, taxonomy, word_quoting

RULE = "catastrophic"

_HOME_PARAMETER_RE = re.compile(r"\$\{HOME(?:(?::?[-=?])[^}]*)?\}")
_DYNAMIC_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]|__nuh_psub_\d+__|`[^`]*`")
_TREE_WIDE_SUFFIXES = ("*", ".*", "**", "{*,.*}", "{.*,*}", "{.,}*")
_CRITICAL_SYSTEM_TREES = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/root", "/sbin", "/sys",
    "/usr", "/var", "/Library", "/System",
)
_GIT_CORE_METADATA = frozenset({"logs", "objects", "packed-refs", "refs", "worktrees"})
_BUILTIN_TRUSTED_ROOTS = ("/tmp", "/private/tmp")
_RAW_STORAGE_TARGET_RE = re.compile(
    r"^/dev/(?:"
    r"(?:x?v|s|h)d[a-z][0-9]*|"
    r"nvme[0-9]+n[0-9]+(?:p[0-9]+)?|"
    r"mmcblk[0-9]+(?:p[0-9]+)?|"
    r"(?:r?disk|ada|da|md|dm-)[0-9]+(?:s[0-9]+)?|"
    r"mapper/.+|disk/.+|zvol/.+"
    r")$"
)
_FORK_BOMB_RE = re.compile(
    r"(?P<name>:|[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*"
    r"(?P=name)\s*\|\s*(?P=name)\s*&\s*;?\s*\}\s*;?\s*(?P=name)"
)
_PERMISSION_CMDS = frozenset({"chmod", "chown", "chgrp", "setfacl"})


def _block(reason: str) -> floor.Verdict:
    return floor.Verdict(taxonomy.BLOCK, reason, RULE)


def _ask(reason: str) -> floor.Verdict:
    return floor.Verdict(taxonomy.ASK, reason, RULE)


# -- path selection ---------------------------------------------------------


def _has_pattern(path: str) -> bool:
    return any(ch in path for ch in "*?[{")


def _tree_aliases(tree_path: str) -> set[str]:
    normalized = os.path.normpath(tree_path)
    return {normalized, os.path.realpath(normalized)}


def _glob_selects_path(pattern: str, path: str) -> bool:
    brace = re.search(r"\{([^{}]+)\}", pattern)
    if brace:
        return any(
            _glob_selects_path(pattern[:brace.start()] + option + pattern[brace.end():], path)
            for option in brace.group(1).split(",")
        )
    if "**" not in pattern and pattern.count(os.sep) != path.count(os.sep):
        return False
    for pattern_part, path_part in zip(pattern.split(os.sep), path.split(os.sep)):
        if path_part.startswith(".") and not pattern_part.startswith("."):
            return False
    return fnmatch.fnmatchcase(path, pattern)


def _selects_tree(normalized: str, tree_path: str, *, include_contents: bool) -> bool:
    """True when *normalized* names *tree_path* itself, or (with *include_contents*) all of it."""
    aliases = _tree_aliases(tree_path)
    if not _has_pattern(normalized):
        return os.path.realpath(normalized) in aliases
    for alias in aliases:
        if _glob_selects_path(normalized, alias):
            return True
        if include_contents and normalized in {os.path.join(alias, s) for s in _TREE_WIDE_SUFFIXES}:
            return True
    return False


def _selects_tree_contents(normalized: str, tree_path: str) -> bool:
    """True when *normalized* is a wildcard over everything in *tree_path*."""
    return any(normalized in {os.path.join(alias, s) for s in _TREE_WIDE_SUFFIXES}
               for alias in _tree_aliases(tree_path))


def _normalize(raw: str) -> str:
    """*raw* with ~ and variables expanded, made absolute against the cwd, normalized."""
    home = os.path.realpath(os.path.expanduser("~"))
    active = word_quoting.expansion(raw)
    expanded = word_quoting.decode(os.path.expanduser(os.path.expandvars(_HOME_PARAMETER_RE.sub(home, active))))
    return os.path.normpath(os.path.join(os.getcwd(), expanded))


def _trusted_roots() -> list[str]:
    from nuh.config import get_config  # lazy import to avoid circular
    return [paths.resolve_path(p) for p in {*_BUILTIN_TRUSTED_ROOTS, *get_config().trusted_paths}]


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _is_home_or_above(normalized: str, home: str) -> bool:
    if _has_pattern(normalized):
        return False
    resolved = os.path.realpath(normalized)
    return resolved == home or home.startswith(resolved + os.sep)


def _git_verdict(normalized: str) -> floor.Verdict | None:
    """Block a ``.git`` directory or its history; ask for anything else inside one."""
    parts = normalized.split(os.sep)
    if ".git" not in parts:
        return None
    after = parts[parts.index(".git") + 1:]
    if not after or after[0] in ("*", "**") or after[0] in _GIT_CORE_METADATA:
        return _block(f"catastrophic delete targets Git metadata: {path_names.friendly_path(normalized)}")
    return _ask(f"delete targets Git metadata: {path_names.friendly_path(normalized)}")


def _system_tree_verdict(normalized: str, what: str) -> floor.Verdict | None:
    home = _home()
    if _selects_tree(normalized, os.sep, include_contents=True):
        return _block(f"catastrophic {what} targets filesystem root: {normalized}")
    if _is_home_or_above(normalized, home) or _selects_tree(normalized, home, include_contents=True):
        return _block(f"catastrophic {what} targets home directory: {path_names.friendly_path(normalized)}")
    for tree in _CRITICAL_SYSTEM_TREES:
        if _selects_tree(normalized, tree, include_contents=True):
            return _block(f"catastrophic {what} targets critical system tree: {normalized}")
    for root in _trusted_roots():
        if _selects_tree(normalized, root, include_contents=False):
            return _block(f"catastrophic {what} targets trusted path root: {path_names.friendly_path(normalized)}")
    return None


def _project_root_verdict(normalized: str) -> floor.Verdict | None:
    """Ask for deleting the project root, everything in it, or a directory above it."""
    project_root = paths.get_project_root()
    if not project_root:
        return None
    if _selects_tree(normalized, project_root, include_contents=True):
        return _ask(f"delete targets project root: {path_names.friendly_path(normalized)}")
    if not _has_pattern(normalized) and os.path.realpath(project_root).startswith(
            os.path.realpath(normalized) + os.sep):
        return _ask(f"delete targets a directory holding the project: {path_names.friendly_path(normalized)}")
    return None


def _delete_verdict(raw: str, *, find_root: bool) -> floor.Verdict | None:
    normalized = _normalize(raw)
    verdict = _system_tree_verdict(normalized, "delete") or _git_verdict(normalized)
    if verdict is None:
        for root in _trusted_roots():
            if _selects_tree_contents(normalized, root):
                verdict = _ask(f"delete targets everything in trusted path: {path_names.friendly_path(root)}")
                break
    if verdict is None and not find_root:
        verdict = _project_root_verdict(normalized)
    if verdict is not None and find_root and verdict[0] == taxonomy.BLOCK:
        # find filters what it deletes under its root; a person decides.
        return _ask(f"find deletes under {path_names.friendly_path(normalized)}: {verdict[1]}")
    return verdict


def check_delete_target(raw: str, *, find_root: bool = False) -> floor.Verdict | None:
    """Floor verdict for deleting *raw*: block, ask, or None.

    A target holding variables or substitutions nuh cannot resolve is also
    checked with them emptied; if that selects something catastrophic, ask.
    """
    if not raw:
        return None
    verdict = _delete_verdict(raw, find_root=find_root)
    if verdict is not None:
        return verdict
    active = word_quoting.expansion(raw)
    emptied = _DYNAMIC_RE.sub("", os.path.expandvars(active))
    if emptied != active and emptied.startswith("/"):
        if _delete_verdict(emptied, find_root=find_root) is not None:
            return _ask(f"delete target may expand to {os.path.normpath(emptied)}: {raw}")
    return None


def _permission_verdict(raw: str, recursive: bool) -> floor.Verdict | None:
    normalized = _normalize(raw)
    if recursive:
        verdict = _system_tree_verdict(normalized, "recursive permission change")
        if verdict is not None:
            return verdict
    exact_roots = [os.sep, _home(), *_CRITICAL_SYSTEM_TREES, *_trusted_roots()]
    if not _has_pattern(normalized) and os.path.realpath(normalized) in {
            os.path.realpath(r) for r in exact_roots}:
        return _ask(f"permission change of {path_names.friendly_path(normalized)} itself")
    return None


def is_raw_storage_target(target: str) -> bool:
    return bool(_RAW_STORAGE_TARGET_RE.fullmatch(word_quoting.expand_path(target)))


def check_write_target(raw: str) -> floor.Verdict | None:
    """Block writes that directly erase storage or crash the machine."""
    if not raw:
        return None
    if is_raw_storage_target(raw):
        return _block(f"catastrophic write targets raw storage device: {raw}")
    if os.path.realpath(word_quoting.expand_path(raw)) == "/proc/sysrq-trigger":
        return _block("catastrophic write targets kernel crash trigger")
    return None


# -- commands ---------------------------------------------------------------


def _has_short_flag(tokens: list[str], flag: str) -> bool:
    return any(t.startswith("-") and not t.startswith("--") and flag in t[1:] for t in tokens)


def storage_reason(tokens: list[str]) -> str:
    """Why *tokens* explicitly destroy raw storage, or ""."""
    if not tokens:
        return ""
    command = os.path.basename(tokens[0])
    args = tokens[1:]
    if any(t in {"-h", "--help", "--version"} for t in args):
        return ""
    raw_target = any(is_raw_storage_target(t) for t in args)
    if command == "dd":
        overwrites = any(t.startswith("of=") and is_raw_storage_target(t.split("=", 1)[1]) for t in args)
        return "catastrophic raw storage overwrite" if overwrites else ""
    if command in {"shred", "truncate"}:
        return "catastrophic raw storage overwrite" if raw_target else ""
    if (command.startswith(("mkfs", "newfs")) or command in {"mke2fs", "mkswap"}) and raw_target:
        return "catastrophic raw storage format"
    if command == "wipefs" and raw_target and ("--all" in args or _has_short_flag(args, "a")) \
            and "--no-act" not in args and not _has_short_flag(args, "n"):
        return "catastrophic raw storage signature erase"
    if command in {"blkdiscard", "pvremove"} and raw_target:
        return "catastrophic raw storage removal"
    if command in {"lvremove", "vgremove"} and args and not ("--test" in args or _has_short_flag(args, "t")):
        return "catastrophic logical storage removal"
    if command == "cryptsetup" and raw_target and "luksFormat" in args:
        return "catastrophic raw storage encryption format"
    if command == "zpool" and "destroy" in args:
        return "catastrophic storage pool destruction"
    return _partition_reason(command, args, raw_target)


def _partition_reason(command: str, args: list[str], raw_target: bool) -> str:
    if not raw_target:
        return ""
    if command == "sgdisk" and any(t in {"-o", "-z", "-Z", "--clear", "--zap", "--zap-all"} for t in args):
        return "catastrophic partition table destruction"
    if command == "sfdisk" and "--delete" in args:
        return "catastrophic partition table destruction"
    if command == "parted" and any(t in {"mklabel", "mktable", "rm"} for t in args):
        return "catastrophic partition table destruction"
    if command == "mdadm" and "--zero-superblock" in args:
        return "catastrophic storage superblock destruction"
    if command == "nvme" and any(t in {"format", "sanitize"} for t in args):
        return "catastrophic raw storage erase"
    if command == "hdparm" and any("security-erase" in t for t in args):
        return "catastrophic raw storage erase"
    if command == "diskutil" and any(t in {"eraseDisk", "secureErase", "zeroDisk"} for t in args):
        return "catastrophic raw storage erase"
    if command == "badblocks" and _has_short_flag(args, "w"):
        return "catastrophic destructive block-device test"
    return ""


def stage_verdicts(tokens: list[str], action_type: str) -> list[floor.Verdict]:
    """Catastrophic verdicts for one simple command (wrappers peeled), whatever its policy."""
    if not tokens:
        return []
    command = os.path.basename(tokens[0])
    operands = [t for t in tokens[1:] if not t.startswith("-")]
    verdicts: list[floor.Verdict] = []
    reason = storage_reason(tokens)
    if reason:
        verdicts.append(_block(reason))
    if command in _PERMISSION_CMDS:
        recursive = "--recursive" in tokens or _has_short_flag(tokens[1:], "R")
        verdicts += [v for v in (_permission_verdict(t, recursive) for t in operands) if v]
    if action_type == taxonomy.FILESYSTEM_DELETE:
        # A find with a test deletes what it selects under its roots; one
        # without deletes everything there, like rm -rf. Only its roots are
        # targets: `-path ./.git` is a pattern, not a delete of .git.
        find_root = command == "find" and find_args.has_test(tokens)
        targets = find_args.roots(tokens) if command == "find" else operands
        verdicts += [v for v in (check_delete_target(t, find_root=find_root) for t in targets) if v]
    elif action_type == taxonomy.FILESYSTEM_WRITE:
        modified = file_operands.modified_files(tokens)
        # A selector only proves its selected writes. Preserve the old check
        # for other operands unless they are positively attributed as reads;
        # an uncertain grammar cannot establish even that exemption.
        inputs = (set(file_operands.input_files(tokens))
                  if file_operands.write_mode(tokens) != taxonomy.UNKNOWN else set())
        targets = (modified or []) + [t.split("=", 1)[-1] for t in operands
                                    if t.split("=", 1)[-1] not in inputs]
        verdicts += [v for v in (check_write_target(t) for t in targets) if v]
    return verdicts


def _unquoted_shell_text(command: str) -> str:
    """Shell text outside quotes and comments, so literal examples do not trigger the fork-bomb rule."""
    output: list[str] = []
    quote = ""
    escaped = False
    comment = False
    for char in command:
        if comment:
            output.append("\n" if char == "\n" else " ")
            comment = char != "\n"
            continue
        if escaped:
            output.append(" " if quote else char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            output.append(" ")
            continue
        if quote:
            if char == quote:
                quote = ""
            output.append(" ")
            continue
        if char in {"'", '"'}:
            quote = char
            output.append(" ")
            continue
        if char == "#" and (not output or output[-1].isspace()):
            comment = True
            output.append(" ")
            continue
        output.append(char)
    return "".join(output)


def contains_fork_bomb(command: str) -> bool:
    return bool(_FORK_BOMB_RE.search(_unquoted_shell_text(command)))
