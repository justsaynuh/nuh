"""Content inspection — regex-scan Write/Edit content for dangerous patterns.

Every built-in pattern runs in linear time: the content is attacker-controlled
and a hook that runs out of time does not block. ``ANCHOR\\s+.*TAIL`` patterns
are ``GapPattern``s (see gap_pattern.py), and the ``rm -rf`` flag-run patterns
use a lookahead so they never retry every split of a long letter run.
"""

import re
import sys

from dataclasses import dataclass

from nuh import deadline
from nuh.gap_pattern import GapPattern
from nuh.secret_patterns import SECRET_PATTERNS


@dataclass
class ContentMatch:
    category: str
    pattern_desc: str
    matched_text: str
    policy: str = "ask"


# Content longer than this is not scanned whole, so it asks (never scans
# clean). Scanning costs about 0.1 s per MiB, so the cap bounds hook latency.
MAX_SCAN_CHARS = 4 * 1024 * 1024
OVERSIZE_CATEGORY = "oversize"
TIMEOUT_CATEGORY = "timeout"

# Categories of text that only does harm when it runs. A write to a target
# that cannot run (see exec_target.py) drops their matches; running the file
# later is inspected then. Secrets, config-added categories, and the oversize
# and timeout markers apply to every file.
RUNNABLE_CATEGORIES = frozenset({"destructive", "exfiltration", "credential_access", "obfuscation"})

# A compiled regex or a GapPattern: both have .pattern and .search(text).
Searcher = re.Pattern[str] | GapPattern

# Compiled regexes by category. Each entry: (searcher, description).
_CONTENT_PATTERNS: dict[str, list[tuple[Searcher, str]]] = {
    "destructive": [
        # Same matches as \brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b: the \b makes the
        # letter run end in f, and the lookahead finds the r in that run.
        (re.compile(r"\brm\s+-(?=[a-zA-Z]*r)[a-zA-Z]*f\b"), "rm -rf"),
        (re.compile(r"\brm\s+-(?=[a-zA-Z]*f)[a-zA-Z]*r\b"), "rm -rf"),
        (re.compile(r"\bshutil\.rmtree\b"), "shutil.rmtree"),
        (re.compile(r"\bos\.remove\b"), "os.remove"),
        (re.compile(r"\bos\.unlink\b"), "os.unlink"),
    ],
    "exfiltration": [
        (GapPattern(r"\bcurl\s+.*-[a-zA-Z]*X\s+POST\b", r"\bcurl(\s+)", r"-[a-zA-Z]*X\s+POST\b"),
         "curl -X POST"),
        (GapPattern(r"\bcurl\s+.*--data\b", r"\bcurl(\s+)", r"--data\b"), "curl --data"),
        (GapPattern(r"\bcurl\s+.*-d\s", r"\bcurl(\s+)", r"-d\s"), "curl -d"),
        (re.compile(r"\brequests\.post\b"), "requests.post"),
        (GapPattern(r"\burllib\.request\.urlopen\b.*data\s*=", r"\burllib\.request\.urlopen\b()", r"data\s*="),
         "urllib POST"),
    ],
    "credential_access": [
        (re.compile(r"~/\.ssh/"), "~/.ssh/ access"),
        (re.compile(r"~/\.aws/"), "~/.aws/ access"),
        (re.compile(r"~/\.gnupg/"), "~/.gnupg/ access"),
    ],
    "obfuscation": [
        (GapPattern(r"\bbase64\s+.*-d\s*\|\s*bash\b", r"\bbase64(\s+)", r"-d\s*\|\s*bash\b"),
         "base64 -d | bash"),
        (re.compile(r"\beval\s*\(\s*base64\.b64decode\b"), "eval(base64.b64decode"),
        (re.compile(r"\bexec\s*\(\s*compile\b"), "exec(compile"),
    ],
    # The secret table and its form rules: secret_patterns.py. Only the asking entries are
    # here; redaction (get_secret_patterns) takes the whole table, suppressed or not.
    "secret": [(p.regex, p.desc) for p in SECRET_PATTERNS if p.ask],
}

# Patterns for detecting credential-searching Grep queries.
_CREDENTIAL_SEARCH_PATTERNS: list[Searcher] = [
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
    re.compile(r"\bapi_key\b", re.IGNORECASE),
    re.compile(r"\bprivate_key\b", re.IGNORECASE),
    re.compile(r"\bAWS_SECRET"),
    GapPattern(r"BEGIN.*PRIVATE", r"BEGIN()", r"PRIVATE", re.IGNORECASE),
]

# Snapshot of hardcoded defaults for reset (testing).
_CONTENT_PATTERNS_DEFAULTS: dict[str, list[tuple[Searcher, str]]] = {
    cat: list(patterns) for cat, patterns in _CONTENT_PATTERNS.items()
}
_CREDENTIAL_SEARCH_DEFAULTS: list[Searcher] = list(_CREDENTIAL_SEARCH_PATTERNS)

# Config-added secret patterns, which redaction applies after the built-in table.
_added_secret_patterns: list[tuple[re.Pattern[str], str]] = []

# Merge state
_content_patterns_merged: bool = False
_content_policies: dict[str, str] = {}  # category → effective policy after merge


def _ensure_content_patterns_merged() -> None:
    """Lazy one-time merge of config into content patterns and policies."""
    global _content_patterns_merged
    if _content_patterns_merged:
        return
    _content_patterns_merged = True
    try:
        from nuh.config import get_config
        cfg = get_config()

        # Profile: none clears all built-in patterns
        if cfg.profile == "none":
            _CONTENT_PATTERNS.clear()
            _CREDENTIAL_SEARCH_PATTERNS.clear()

        # --- Content pattern suppression (by description string) ---
        if cfg.content_patterns_suppress:
            suppress_set = {str(s) for s in cfg.content_patterns_suppress}
            matched = set()
            for cat in list(_CONTENT_PATTERNS):
                before = list(_CONTENT_PATTERNS[cat])
                _CONTENT_PATTERNS[cat] = [
                    (r, d) for r, d in before if d not in suppress_set
                ]
                matched.update(d for _, d in before if d in suppress_set)
            unmatched = suppress_set - matched
            for desc in sorted(unmatched):
                sys.stderr.write(
                    f"nuh: unmatched content_patterns.suppress: '{desc}'\n"
                )

        # --- Add custom content patterns ---
        for entry in cfg.content_patterns_add:
            if not isinstance(entry, dict):
                continue
            category = str(entry.get("category", "")).strip()
            pattern = str(entry.get("pattern", "")).strip()
            description = str(entry.get("description", "")).strip()
            if not category or not pattern or not description:
                sys.stderr.write(
                    "nuh: content_patterns.add: missing category/pattern/description, skipping\n"
                )
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as e:
                sys.stderr.write(
                    f"nuh: invalid regex in content_patterns.add: '{pattern}' — {e}\n"
                )
                continue
            if category not in _CONTENT_PATTERNS:
                _CONTENT_PATTERNS[category] = []
            _CONTENT_PATTERNS[category].append((compiled, description))
            if category == "secret":
                _added_secret_patterns.append((compiled, description))

        # --- Build effective policies map ---
        _content_policies.clear()
        for cat in _CONTENT_PATTERNS:
            _content_policies[cat] = "ask"  # default for all categories
        for cat, policy in cfg.content_policies.items():
            if policy in ("ask", "block"):
                _content_policies[cat] = policy

        # --- Credential pattern suppression (by regex .pattern string) ---
        if cfg.credential_patterns_suppress:
            suppress_regexes = {str(s) for s in cfg.credential_patterns_suppress}
            matched_cred = set()
            original = list(_CREDENTIAL_SEARCH_PATTERNS)
            _CREDENTIAL_SEARCH_PATTERNS.clear()
            for rx in original:
                if rx.pattern in suppress_regexes:
                    matched_cred.add(rx.pattern)
                else:
                    _CREDENTIAL_SEARCH_PATTERNS.append(rx)
            unmatched_cred = suppress_regexes - matched_cred
            for pat in sorted(unmatched_cred):
                sys.stderr.write(
                    f"nuh: unmatched credential_patterns.suppress: '{pat}'\n"
                )

        # --- Add custom credential patterns ---
        for entry in cfg.credential_patterns_add:
            pat = str(entry).strip()
            if not pat:
                continue
            try:
                compiled = re.compile(pat)
            except re.error as e:
                sys.stderr.write(
                    f"nuh: invalid regex in credential_patterns.add: '{pat}' — {e}\n"
                )
                continue
            _CREDENTIAL_SEARCH_PATTERNS.append(compiled)

    except Exception as exc:
        sys.stderr.write(f"nuh: config: content_patterns: {exc}\n")


def reset_content_patterns() -> None:
    """Restore defaults and clear merge flag (for testing)."""
    global _content_patterns_merged
    _content_patterns_merged = False
    _CONTENT_PATTERNS.clear()
    for cat, patterns in _CONTENT_PATTERNS_DEFAULTS.items():
        _CONTENT_PATTERNS[cat] = list(patterns)
    _CREDENTIAL_SEARCH_PATTERNS.clear()
    _CREDENTIAL_SEARCH_PATTERNS.extend(_CREDENTIAL_SEARCH_DEFAULTS)
    _content_policies.clear()
    _added_secret_patterns.clear()


def scan_content(content: str) -> list[ContentMatch]:
    """Scan content for dangerous patterns. Returns matches (empty = safe)."""
    _ensure_content_patterns_merged()
    if not content:
        return []

    oversize = None
    if len(content) > MAX_SCAN_CHARS:
        # Text past the limit is never inspected, so the content can never
        # scan clean: the head is still scanned (a block pattern there still
        # blocks) and the size itself asks.
        oversize = ContentMatch(
            category=OVERSIZE_CATEGORY,
            pattern_desc=f"too large to inspect ({len(content)} characters, limit {MAX_SCAN_CHARS})",
            matched_text="",
        )
        content = content[:MAX_SCAN_CHARS]

    matches = _scan_patterns(content)
    if oversize is not None:
        matches.append(oversize)
    return matches


def _scan_patterns(content: str) -> list[ContentMatch]:
    """Every pattern's match in *content*, cut short by the hook's static deadline.

    The deadline is checked before each pattern (a running regex cannot be
    interrupted, so each pattern must itself be linear). Running out never
    scans clean: the matches so far are kept, so a block found already still
    blocks, and a timeout match asks.
    """
    matches = []
    for category, patterns in _CONTENT_PATTERNS.items():
        policy = _content_policies.get(category, "ask")
        for regex, desc in patterns:
            if deadline.expired():
                matches.append(ContentMatch(
                    category=TIMEOUT_CATEGORY,
                    pattern_desc=deadline.TIMEOUT_REASON,
                    matched_text="",
                ))
                return matches
            m = regex.search(content)
            if m:
                matches.append(ContentMatch(
                    category=category,
                    pattern_desc=desc,
                    matched_text=m.group()[:80],
                    policy=policy,
                ))
    return matches


def without_runnable(matches: list[ContentMatch]) -> list[ContentMatch]:
    """*matches* minus those in RUNNABLE_CATEGORIES (for text written where it cannot run)."""
    return [m for m in matches if m.category not in RUNNABLE_CATEGORIES]


def format_content_message(tool_name: str, matches: list[ContentMatch], runs: str = "") -> str:
    """Format content matches into a human-readable ask message.

    *runs* says why the written file can run, when that made runnable-text
    patterns apply.
    """
    if not matches:
        return ""

    categories = sorted({m.category for m in matches})
    details = ", ".join(m.pattern_desc for m in matches)
    message = f"{tool_name} content inspection [{', '.join(categories)}]: {details}"
    if runs and any(m.category in RUNNABLE_CATEGORIES for m in matches):
        message += f" (target runs: {runs})"
    return message


def is_credential_search(pattern: str) -> bool:
    """Check if a Grep pattern looks like a credential search."""
    _ensure_content_patterns_merged()
    if not pattern:
        return False
    return any(regex.search(pattern) for regex in _CREDENTIAL_SEARCH_PATTERNS)


def get_secret_patterns() -> list[tuple[re.Pattern[str], str]]:
    """The patterns redaction applies (LLM providers with ``redact_secrets``), in order.

    The whole built-in table, then the config-added ``secret`` patterns.
    Unlike the ask, redaction ignores ``content_patterns.suppress`` and
    ``profile: none``, and includes the redact-only entries: keeping a
    secret from a provider never depends on whether its write asks.
    """
    _ensure_content_patterns_merged()
    return [(p.regex, p.desc) for p in SECRET_PATTERNS] + list(_added_secret_patterns)
