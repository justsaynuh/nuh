"""The hint a static Bash ask carries: the nuh command that would stop it asking, or the floor rule that holds it."""

from nuh import paths, sensitive_paths, taxonomy


def _extract_target_from_tokens(tokens: list[str]) -> str | None:
    """Extract first path-like argument from tokens for hint generation."""
    for tok in tokens[1:]:  # skip command name
        if tok.startswith("-"):
            continue
        if "/" in tok or tok.startswith("~") or tok.startswith("."):
            return tok
    return None


def _floor_hint(rule: str) -> str:
    return (f"Static floor rule {rule}: only you can approve this; "
            f"human_only: {{{rule}: false}} in the global config lets the LLM decide it")


def build(result) -> str | None:
    """Build an actionable hint for bash ask decisions."""
    if result.composition_rule:
        return None
    if result.floor:
        return _floor_hint(result.floor)
    for sr in result.stages:
        if sr.decision != taxonomy.ASK:
            continue
        if sr.floor and sr.floor != sensitive_paths.SENSITIVE_RULE:
            # A classify, trust or allow-path would not lift a floor rule; say which one held it.
            # (allow-path still exempts a path from the user's own sensitive entries, below.)
            return _floor_hint(sr.floor)
        if sr.action_type == taxonomy.UNKNOWN:
            cmd = sr.tokens[0] if sr.tokens else "command"
            return f"To classify: nuh classify {cmd} <type>\n     See available types: nuh types"
        if sr.action_type == taxonomy.NETWORK_WRITE:
            return "To always allow: nuh allow network_write"
        if "unknown host: " in sr.reason:
            # Extract host from reason like "network_outbound → ask (unknown host: example.com)"
            idx = sr.reason.index("unknown host: ") + len("unknown host: ")
            host = sr.reason[idx:].rstrip(")")
            return f"To trust this host: nuh trust {host}"
        if "targets sensitive path:" in sr.reason:
            # Extract path from reason like "targets sensitive path: ~/.aws"
            idx = sr.reason.index("targets sensitive path:") + len("targets sensitive path: ")
            path = sr.reason[idx:].strip()
            if sensitive_paths.check_path_basic_raw(path, exempt=sensitive_paths.user_entries()):
                return _floor_hint(sr.floor or sensitive_paths.SENSITIVE_RULE)  # a built-in entry
            return f"To always allow: nuh allow-path {path}"
        if "outside project" in sr.reason:
            # The path that decided: a redirect's, else the one the context policy judged
            # (rm ./x /opt/n asks for /opt/n); a first path-like word only when neither is known.
            target = sr.redirect_target or sr.context_target or _extract_target_from_tokens(sr.tokens)
            if target:
                dir_hint = paths._suggest_trust_dir(target)
                if dir_hint != "/":  # Never suggest trusting root
                    return f"To always allow: nuh trust {dir_hint}"
        # Action policy ask
        return f"To always allow: nuh allow {sr.action_type}"
    return None
