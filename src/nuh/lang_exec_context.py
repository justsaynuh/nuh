"""Context for lang_exec: the script a command runs (path, place, contents) or its inline code, scanned."""

import os

from nuh import floor, hidden_chars, path_names, paths, sensitive_paths, taxonomy


# Reason for inline code the content scan found nothing in: an ask the LLM may lower.
INLINE_CLEAN = "lang_exec: inline code, content scan clean (needs an LLM verdict)"


def resolve_lang_exec_context(
    target_path: str | None,
    *,
    inline_code: str | None = None,
) -> tuple[str, str]:
    """Resolve lang_exec context by checking script path and contents.

    For inline code (python -c) with extractable code string: scans content;
    a clean scan still asks (the LLM may allow it).
    For inline code without extractable string: ask.
    For script files: checks path sensitivity, reads file, inspects contents.
    Content inspection runs even for trusted/in-project paths — being inside
    the project is necessary but not sufficient for lang_exec.
    """
    if not target_path:
        if inline_code:
            from nuh.content import scan_content, format_content_message
            matches = scan_content(inline_code)
            if matches:
                worst = "ask"
                for m in matches:
                    if m.policy == "block":
                        worst = "block"
                        break
                return worst, format_content_message("inline", matches)
            # The scan only knows a few shapes; clean is not safe. Without an
            # LLM verdict this asks; an LLM review may allow it (static floor §8).
            return taxonomy.ASK, INLINE_CLEAN
        return taxonomy.ASK, "lang_exec: inline execution"

    from nuh.config import get_config
    if get_config().profile == "none":
        return taxonomy.ALLOW, "profile: none (no script inspection)"

    resolved = paths.resolve_path(target_path)

    # Core path check (hook + sensitive)
    basic = sensitive_paths.check_path_basic_raw(target_path)
    if basic:
        return basic

    # Project boundary check — outside project always asks
    project_root = paths.get_project_root()
    inside_project = False
    if project_root:
        real_root = os.path.realpath(project_root)
        inside_project = resolved == real_root or resolved.startswith(real_root + os.sep)

    if not inside_project and not paths.is_trusted_path(resolved):
        return taxonomy.ASK, f"script outside project: {path_names.friendly_path(resolved)}"

    # Inside project or trusted — read and inspect contents
    if not os.path.isfile(resolved):
        return taxonomy.ASK, f"script not found: {path_names.friendly_path(resolved)}"

    # Note: the LLM layer (llm.py) reads the file independently for prompt
    # enrichment. The dual read is intentional — this read is the authoritative
    # deterministic gate; the LLM read is for prompt context. TOCTOU between
    # the two reads is accepted as a known limitation.
    from nuh.content import MAX_SCAN_CHARS, scan_content, format_content_message
    content = _read_script_content(resolved, MAX_SCAN_CHARS + 1)
    if content is None:
        return taxonomy.ASK, f"script not readable: {path_names.friendly_path(resolved)}"
    if len(content) > MAX_SCAN_CHARS:
        # The rest is never read, so the script cannot be called clean.
        return taxonomy.ASK, (f"script too large to inspect: {path_names.friendly_path(resolved)}"
                              f" (over {MAX_SCAN_CHARS} characters)")
    if "\x00" in content:
        # A compiled program: the text scan cannot read what it does.
        return taxonomy.ASK, f"program is not a script nuh can inspect: {path_names.friendly_path(resolved)}"

    matches = scan_content(content)
    worst = taxonomy.BLOCK if any(m.policy == taxonomy.BLOCK for m in matches) else taxonomy.ASK
    if matches and worst == taxonomy.BLOCK:
        return worst, format_content_message("script", matches)
    if hidden_chars.find(content) >= 0:
        return floor.Verdict(taxonomy.ASK, hidden_chars.reason(content, f"script {path_names.friendly_path(resolved)}"),
                             hidden_chars.RULE)
    if matches:
        return worst, format_content_message("script", matches)

    return taxonomy.ALLOW, f"script clean: {path_names.friendly_path(resolved)}"


def hidden_chars_verdict(target_path: str | None) -> floor.Verdict | None:
    """The hidden_chars floor for the script *target_path* runs, or None.

    ``resolve_lang_exec_context`` checks for hidden characters as it reads
    the script under lang_exec's context policy; an allow or ask policy, a
    type given by ``classify``, or profile: none skip that read, and the floor
    with it. This read has the same reach: a script inside the project or a
    trusted path, small enough to scan, and text.
    """
    if not target_path:
        return None
    resolved = paths.resolve_path(target_path)
    project_root = paths.get_project_root()
    inside = bool(project_root) and path_names._is_under(resolved, os.path.realpath(project_root))
    if not (inside or paths.is_trusted_path(resolved)) or not os.path.isfile(resolved):
        return None
    from nuh.content import MAX_SCAN_CHARS
    content = _read_script_content(resolved, MAX_SCAN_CHARS + 1)
    if content is None or len(content) > MAX_SCAN_CHARS or "\x00" in content or hidden_chars.find(content) < 0:
        return None
    return floor.Verdict(taxonomy.ASK, hidden_chars.reason(content, f"script {path_names.friendly_path(resolved)}"),
                         hidden_chars.RULE)


def script_text(target_path: str) -> str | None:
    """The text of the script *target_path* names, as the content scan reads it; None when it cannot.

    None for a file that is unreadable, too large to scan or not text, so a
    caller that found the script clean asks when the file changed since.
    """
    from nuh.content import MAX_SCAN_CHARS
    content = _read_script_content(paths.resolve_path(target_path), MAX_SCAN_CHARS + 1)
    if content is None or len(content) > MAX_SCAN_CHARS or "\x00" in content:
        return None
    return content


def _read_script_content(path: str, max_chars: int) -> str | None:
    """Read up to *max_chars* characters of a script for content inspection; None on error.

    Callers pass one more than they inspect, so a longer script is seen as
    too large rather than scanned cut.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except OSError:
        # Read is best-effort; if it fails (race, permissions, disk),
        # the caller falls through to ask — never silently allows.
        return None
