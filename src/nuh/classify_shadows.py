"""User ``classify:`` entries that shadow a built-in table entry or a flag classifier (config diagnostics)."""



# Commands with Phase 2 flag classifiers (flag-dependent classification).
_FLAG_CLASSIFIER_CMDS = {"find", "sed", "awk", "gawk", "mawk", "nawk",
                          "tar", "git", "curl", "wget",
                          "http", "https", "xh", "xhs",
                          "npm", "pnpm", "bun", "pip", "pip3", "cargo", "gem",
                          "python", "python3", "node", "ruby", "perl",
                          "bash", "sh", "dash", "zsh", "php", "tsx"}


def find_table_shadows(
    user_table: list[tuple[tuple[str, ...], str]],
    builtin_table: list[tuple[tuple[str, ...], str]],
) -> dict[tuple[str, ...], list[tuple[str, ...]]]:
    """Return {user_prefix: [shadowed_builtin_prefixes]}.

    A user prefix u shadows builtin prefix b when:
    - b == u (exact override), OR
    - len(b) > len(u) AND b[:len(u)] == u (user is a proper prefix of builtin)
    """
    shadows: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for u_prefix, _ in user_table:
        matched = []
        for b_prefix, _ in builtin_table:
            if b_prefix == u_prefix:
                matched.append(b_prefix)
            elif len(b_prefix) > len(u_prefix) and b_prefix[:len(u_prefix)] == u_prefix:
                matched.append(b_prefix)
        if matched:
            shadows[u_prefix] = matched
    return shadows


def find_flag_classifier_shadows(
    user_table: list[tuple[tuple[str, ...], str]],
) -> list[tuple[str, ...]]:
    """Return user prefixes that shadow a Phase 2 flag classifier."""
    return [u_prefix for u_prefix, _ in user_table
            if len(u_prefix) == 1 and u_prefix[0] in _FLAG_CLASSIFIER_CMDS]
