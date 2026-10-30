"""``nuh allow``, ``deny``, ``allow-path``, ``classify``, ``trust``, ``status``, ``forget`` and ``types``.

The commands that write, list and remove the user's rules (``remember``).
"""

import argparse
import os
import sys


def _confirm(message: str) -> bool:
    """Prompt y/N. Non-interactive (piped) → False."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{message} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _warn_comments(project: bool) -> None:
    """Warn and confirm if config has comments. Exits on deny."""
    from nuh.remember import has_comments
    from nuh.config import get_global_config_path, get_project_config_path
    path = get_project_config_path() if project else get_global_config_path()
    if path and has_comments(path):
        if not _confirm(
            f"\u26a0 {os.path.basename(path)} has comments that will be removed by this write.\nProceed?"
        ):
            sys.exit(1)


def cmd_allow(args: argparse.Namespace) -> None:
    """Allow an action type."""
    from nuh.remember import write_action, CustomTypeError
    _warn_comments(args.project)
    try:
        msg = write_action(args.action_type, "allow", project=args.project)
        print(msg)
    except CustomTypeError:
        if not _confirm(f"\u26a0 '{args.action_type}' is not a built-in type. Create it?"):
            sys.exit(1)
        msg = write_action(args.action_type, "allow", project=args.project, allow_custom=True)
        print(msg)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_deny(args: argparse.Namespace) -> None:
    """Deny an action type."""
    from nuh.remember import write_action, CustomTypeError
    _warn_comments(args.project)
    try:
        msg = write_action(args.action_type, "block", project=args.project)
        print(msg)
    except CustomTypeError:
        if not _confirm(f"\u26a0 '{args.action_type}' is not a built-in type. Create it?"):
            sys.exit(1)
        msg = write_action(args.action_type, "block", project=args.project, allow_custom=True)
        print(msg)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_allow_path(args: argparse.Namespace) -> None:
    """Allow a sensitive path for the current project."""
    from nuh.remember import write_allow_path
    _warn_comments(project=False)
    try:
        msg = write_allow_path(args.path)
        print(msg)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_classify(args: argparse.Namespace) -> None:
    """Classify a command prefix as an action type."""
    from nuh.remember import write_classify, CustomTypeError
    _warn_comments(args.project)
    try:
        msg = write_classify(args.command_prefix, args.type, project=args.project)
        print(msg)
    except CustomTypeError:
        if not _confirm(f"\u26a0 '{args.type}' is not a built-in type. Create it?"):
            sys.exit(1)
        msg = write_classify(args.command_prefix, args.type, project=args.project, allow_custom=True)
        print(msg)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def _is_host_suffix(target: str) -> bool:
    """``.example.com`` is a domain suffix, not a dotfile, unless it exists on disk."""
    return (target.startswith(".") and "/" not in target and target.count(".") >= 2
            and not os.path.exists(target))


def cmd_trust(args: argparse.Namespace) -> None:
    """Trust a path or network host (global config only)."""
    target = args.target
    is_path = target.startswith(("/", "~", ".")) and not _is_host_suffix(target)

    if is_path and getattr(args, "project", False):
        print("trusted_paths is global-only — cannot use --project", file=sys.stderr)
        sys.exit(1)

    _warn_comments(project=False)

    if is_path:
        from nuh.remember import write_trust_path
        from nuh.paths import resolve_path
        from nuh.sensitive_paths import is_sensitive
        resolved = resolve_path(target)
        if resolved == "/":
            print("nuh: refusing to trust filesystem root", file=sys.stderr)
            sys.exit(1)
        home = os.path.realpath(os.path.expanduser("~"))
        if resolved == home:
            print("\u26a0 Trusting ~ allows writes to your entire home directory.")
            print("  Sensitive paths (~/.ssh, ~/.aws, ...) are still protected.")
            if not _confirm("Continue?"):
                sys.exit(1)
        # Informational warning if path overlaps a sensitive path
        matched, pattern, _policy = is_sensitive(resolved)
        if matched:
            print(f"\u26a0 {pattern} is a sensitive path \u2014 still blocked/asked regardless.")
        try:
            msg = write_trust_path(target)
            print(msg)
        except (ValueError, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    else:
        from nuh.remember import write_trust_host
        try:
            msg = write_trust_host(target)
            print(msg)
        except (ValueError, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    """Show all custom rules."""
    from nuh.remember import list_rules
    try:
        rules = list_rules()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    any_rules = False
    for scope in ("global", "project"):
        scope_rules = rules.get(scope, {})
        if not scope_rules:
            continue
        any_rules = True
        print(f"{scope.upper()} config:")
        if "actions" in scope_rules:
            for action_type, policy in scope_rules["actions"].items():
                print(f"  action: {action_type} → {policy}")
        if "allow_paths" in scope_rules:
            for path, roots in scope_rules["allow_paths"].items():
                print(f"  allow-path: {path} → {', '.join(roots)}")
        if "classify" in scope_rules:
            # Shadow warnings only for global scope — project entries are
            # Phase 3 (checked after builtin), so they can't shadow.
            table_shadows: dict = {}
            flag_shadows: set = set()
            if scope == "global":
                from nuh.classify_shadows import find_flag_classifier_shadows, find_table_shadows
                from nuh.taxonomy import build_user_table, get_builtin_table
                from nuh.config import get_config
                cfg = get_config()
                user_classify = scope_rules["classify"]
                user_table = build_user_table(user_classify)
                builtin_table = get_builtin_table(cfg.profile) if cfg.profile != "none" else []
                table_shadows = find_table_shadows(user_table, builtin_table)
                flag_shadows = set(find_flag_classifier_shadows(user_table))
            for action_type, prefixes in scope_rules["classify"].items():
                for prefix in prefixes:
                    prefix_tuple = tuple(prefix.split())
                    annotations = []
                    if prefix_tuple in table_shadows:
                        count = len(table_shadows[prefix_tuple])
                        annotations.append(f"shadows {count} built-in rule{'s' if count != 1 else ''}")
                    if prefix_tuple in flag_shadows:
                        annotations.append("shadows flag classifier")
                    suffix = f"  ({'; '.join(annotations)})" if annotations else ""
                    print(f"  classify: '{prefix}' → {action_type}{suffix}")
        if "known_registries" in scope_rules:
            kr = scope_rules["known_registries"]
            if isinstance(kr, list):
                for host in kr:
                    print(f"  trust: {host}")
            elif isinstance(kr, dict):
                from nuh.config import _parse_add_remove
                add, remove = _parse_add_remove(kr)
                for host in add:
                    print(f"  trust: {host}")
                for host in remove:
                    print(f"  trust: !{host}")
        if "exec_sinks" in scope_rules:
            from nuh.config import _parse_add_remove
            add, remove = _parse_add_remove(scope_rules["exec_sinks"])
            for s in add:
                print(f"  exec-sink: {s}")
            for s in remove:
                print(f"  exec-sink: !{s}")
        if "sensitive_basenames" in scope_rules:
            for name, policy in scope_rules["sensitive_basenames"].items():
                print(f"  sensitive-basename: {name} → {policy}")
        if "trusted_paths" in scope_rules:
            for p in scope_rules["trusted_paths"]:
                print(f"  trust-path: {p}")
        if "decode_commands" in scope_rules:
            from nuh.config import _parse_add_remove
            add, remove = _parse_add_remove(scope_rules["decode_commands"])
            for d in add:
                print(f"  decode-command: {d}")
            for d in remove:
                print(f"  decode-command: !{d}")

    if not any_rules:
        print("No custom rules configured.")


def cmd_forget(args: argparse.Namespace) -> None:
    """Remove a rule."""
    from nuh.remember import forget_rule
    try:
        msg = forget_rule(args.arg, project=args.project, global_only=args.global_flag)
        print(msg)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_types(args: argparse.Namespace) -> None:
    """List all action types."""
    from nuh.classify_shadows import find_flag_classifier_shadows, find_table_shadows
    from nuh.taxonomy import load_type_descriptions, get_policy, build_user_table, get_builtin_table
    from nuh.remember import list_rules
    from nuh.config import get_config

    descriptions = load_type_descriptions()
    cfg = get_config()

    # Gather shadow data from global classify entries only — project entries
    # are Phase 3 (checked after builtin), so they can't shadow.
    override_notes: dict[str, list[str]] = {}
    try:
        rules = list_rules()
    except RuntimeError:
        rules = {}
    user_classify = rules.get("global", {}).get("classify", {})
    if user_classify:
        builtin_table = get_builtin_table(cfg.profile) if cfg.profile != "none" else []
        user_table = build_user_table(user_classify)
        table_shadows = find_table_shadows(user_table, builtin_table)
        flag_shadows = set(find_flag_classifier_shadows(user_table))
        for action_type, prefixes in user_classify.items():
            for prefix in prefixes:
                prefix_tuple = tuple(prefix.split())
                parts = []
                if prefix_tuple in table_shadows:
                    count = len(table_shadows[prefix_tuple])
                    parts.append(f"overrides {count} built-in rule{'s' if count != 1 else ''}")
                if prefix_tuple in flag_shadows:
                    parts.append("overrides flag classifier")
                if parts:
                    note = f"'{prefix}' {'; '.join(parts)} (nuh forget {prefix} to use built-in)"
                    override_notes.setdefault(action_type, []).append(note)

    for name, desc in descriptions.items():
        policy = get_policy(name)
        print(f"  {name:<25} {policy:<8} {desc}")
        for note in override_notes.get(name, []):
            print(f"    \u21b3 {note}")
