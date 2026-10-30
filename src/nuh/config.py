"""Config loading — YAML config with global + project merge."""

import os
import sys
from dataclasses import dataclass, field

from nuh import config_floor, config_gating, config_keys, config_llm, hook_error
from nuh.deadline import DEFAULT_DEADLINE_S
from nuh.taxonomy import POLICIES as _POLICIES, PROFILES as _PROFILES, STRICTNESS as _STRICTNESS

class ConfigError(Exception):
    """Raised when a config file exists but fails to parse."""

_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "nuh")
_GLOBAL_CONFIG = os.path.join(_CONFIG_DIR, "config.yaml")
_PROJECT_CONFIG_NAME = ".nuh.yaml"


@dataclass
class NuhConfig:
    profile: str = "full"
    classify_global: dict[str, list[str]] = field(default_factory=dict)
    classify_project: dict[str, list[str]] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    sensitive_paths_default: str = "ask"
    sensitive_paths: dict[str, str] = field(default_factory=dict)
    allow_paths: dict[str, list[str]] = field(default_factory=dict)
    known_registries: list | dict = field(default_factory=list)
    exec_sinks: list | dict = field(default_factory=list)
    sensitive_basenames: dict = field(default_factory=dict)
    decode_commands: list | dict = field(default_factory=list)
    content_patterns_add: list = field(default_factory=list)
    content_patterns_suppress: list = field(default_factory=list)
    content_policies: dict = field(default_factory=dict)
    credential_patterns_add: list = field(default_factory=list)
    credential_patterns_suppress: list = field(default_factory=list)
    llm: dict = field(default_factory=dict)
    llm_can_block: bool = False  # default: an LLM block is capped to ask (llm_cap.py)
    llm_trace_dir: str = ""  # opt-in LLM trace directory (absolute, ~ expanded); "" = off
    llm_trace_keep_days: int = config_llm.TRACE_KEEP_DAYS  # older traces are pruned (trace_prune.py); 0 = never
    llm_on_uncertain: str = "ask"  # ask | static: what an uncertain review does (llm_fallback.py)
    llm_on_failure: str = "ask"    # ask | static: what a review nobody answered does
    llm_self_heal: bool = True  # a provider timeout starts a background model warm-up (llm_heal.py)
    trace_off: bool = False  # project .nuh.yaml `trace: false`: no tracing for this project
    trusted_paths: list[str] = field(default_factory=list)
    db_targets: list[dict] = field(default_factory=list)
    log: dict = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)  # configured tools words; the rest default (gating.py)
    human_only: dict[str, bool] = field(default_factory=dict)  # configured human_only keys (gating.py)
    hook_deadline_s: float = DEFAULT_DEADLINE_S  # static inspection budget per hook call
    hook_on_error: str = hook_error.DEFAULT  # ask | block | defer: the answer when the hook fails (hook_error.py)
    project_root: str = "git"  # git | git_or_launch_dir: where the project is without a git root
    project_lifts_sensitive: bool = False  # paths in the project skip the user's own sensitive_paths above it
    completion_hint: bool = True  # the CLI says when nuh's zsh completion is missing (completion_hint.py)
    sensitive_paths_global: dict[str, str] = field(default_factory=dict)  # the user's own entries (global config)
    unknown: list[str] = field(default_factory=list)  # keys no setting reads, ignored (config_keys)


_cached_config: NuhConfig | None = None
_cached_global: dict | None = None

PROJECT_ROOT_MODES = ("git", "git_or_launch_dir")
# Settings a project .nuh.yaml may not set: the first two widen what counts as the project or what it may
# touch; completion_hint is about the user's shell, not a repository.
_GLOBAL_ONLY_WARNED = ("project_root", "project_lifts_sensitive", "completion_hint")


def get_config() -> NuhConfig:
    """Load and return merged config. Cached for process lifetime."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    global_data = _load_global_data()

    # Find project config via project root
    project_data: dict = {}
    from nuh.paths import get_project_root  # lazy import to avoid circular
    project_root = get_project_root()
    if project_root:
        project_config_path = os.path.join(project_root, _PROJECT_CONFIG_NAME)
        project_data = _load_yaml_file(project_config_path)

    _cached_config = _merge_configs(global_data, project_data)
    return _cached_config


def reset_config() -> None:
    """Clear cached config (for testing)."""
    global _cached_config, _cached_global
    _cached_config = None
    _cached_global = None


def _load_global_data() -> dict:
    """The global config file's data, read once per process."""
    global _cached_global
    if _cached_global is None:
        _cached_global = _load_yaml_file(_GLOBAL_CONFIG)
    return _cached_global


def project_root_mode() -> str:
    """The global ``project_root`` setting, readable before the merged config exists.

    Finding the project root needs it, and the merged config needs the
    project root (for ``.nuh.yaml``), so it comes from the global file alone.
    A global config that does not parse means git only: the narrower root.
    """
    if _cached_config is not None:
        return _cached_config.project_root
    try:
        return _parse_project_root(_load_global_data().get("project_root"), warn=False)
    except ConfigError as exc:
        sys.stderr.write(f"nuh: config: project_root: {exc}; using git\n")
        return "git"


def completion_hint() -> bool:
    """The global ``completion_hint`` setting, read from the global file alone (``completion_hint.py``).

    The CLI hint runs before a command picks its project (``nuh test
    --cwd``), so it must not cache a merged config; the setting is global-only.
    """
    if _cached_config is not None:
        return _cached_config.completion_hint
    return _parse_global_bool(_load_global_data(), "completion_hint", True)


def _parse_project_root(raw: object, *, warn: bool = True) -> str:
    """Parse ``project_root``: git (default) or git_or_launch_dir."""
    if raw is None:
        return "git"
    if raw not in PROJECT_ROOT_MODES:
        if warn:
            sys.stderr.write(f"nuh: config: project_root must be one of {', '.join(PROJECT_ROOT_MODES)}, "
                             f"got {raw!r}; using git\n")
        return "git"
    return str(raw)


def _parse_global_bool(global_cfg: dict, key: str, default: bool) -> bool:
    """A true/false global setting; any other value warns and keeps *default*."""
    raw = global_cfg.get(key, default)
    if isinstance(raw, bool):
        return raw
    sys.stderr.write(f"nuh: config: {key} must be true or false, got {raw!r}; keeping {str(default).lower()}\n")
    return default


def apply_override(override_data: dict) -> None:
    """Apply inline config override for single-shot CLI use (nuh test --config).

    Merges override_data onto the current config. No cleanup needed —
    the override only lives for the process lifetime.
    """
    global _cached_config
    cfg = get_config()  # ensure base is loaded

    if "profile" in override_data:
        profile = override_data["profile"]
        if profile in _PROFILES:
            cfg.profile = profile
    if "classify" in override_data:
        cfg.classify_global.update(_validate_dict(override_data["classify"]))
    if "actions" in override_data:
        cfg.actions.update(config_floor.actions(_validate_dict(override_data["actions"])))
    if "sensitive_paths" in override_data:
        cfg.sensitive_paths.update(_validate_dict(override_data["sensitive_paths"]))
        cfg.sensitive_paths_global.update(_validate_dict(override_data["sensitive_paths"]))
    if "trusted_paths" in override_data:
        tp = override_data["trusted_paths"]
        if isinstance(tp, list):
            cfg.trusted_paths = [str(p) for p in tp]
    if "known_registries" in override_data:
        cfg.known_registries = override_data["known_registries"]
    if "exec_sinks" in override_data:
        cfg.exec_sinks = override_data["exec_sinks"]
    if "sensitive_basenames" in override_data:
        cfg.sensitive_basenames.update(_validate_dict(override_data["sensitive_basenames"]))
    if "decode_commands" in override_data:
        raw_dc = override_data["decode_commands"]
        if isinstance(raw_dc, (list, dict)):
            cfg.decode_commands = raw_dc
    if "db_targets" in override_data:
        raw_dt = override_data["db_targets"]
        if isinstance(raw_dt, list):
            cfg.db_targets = [t for t in raw_dt if isinstance(t, dict)]
    if "content_patterns" in override_data:
        cp = _validate_dict(override_data["content_patterns"])
        if "suppress" in cp:
            cfg.content_patterns_suppress = cp["suppress"]
        if "add" in cp:
            cfg.content_patterns_add = cp["add"]
        if "policies" in cp:
            cfg.content_policies.update(_validate_dict(cp["policies"]))
    if "credential_patterns" in override_data:
        cp = _validate_dict(override_data["credential_patterns"])
        if "suppress" in cp:
            cfg.credential_patterns_suppress = cp["suppress"]
        if "add" in cp:
            cfg.credential_patterns_add = cp["add"]
    config_gating.apply_override(cfg, override_data)

    _cached_config = cfg

    # Reset lazy-merge caches so they re-read from the updated config
    from nuh import sensitive_paths, content, context, taxonomy
    sensitive_paths.reset_sensitive_paths()
    content.reset_content_patterns()
    context.reset_known_hosts()
    taxonomy.reset_exec_sinks()
    taxonomy.reset_decode_commands()


def _load_yaml_file(path: str) -> dict:
    """Load YAML file. Returns {} if the file is missing; ConfigError when it does not parse."""
    if not os.path.isfile(path):
        return {}
    import yaml  # lazy: a hook call with no config file never pays for it
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        raise ConfigError(f"config parse error in {path}: {e}") from e


def _validate_dict(val) -> dict:
    """Return val if dict, else empty dict."""
    return val if isinstance(val, dict) else {}


def _merge_dict_tighten(global_d: dict, project_d: dict, defaults: dict | None = None) -> dict:
    """Merge two dicts — project can only tighten (stricter policy wins)."""
    merged = dict(global_d)
    for key, val in project_d.items():
        if key in merged:
            if _STRICTNESS.get(val, 2) >= _STRICTNESS.get(merged[key], 2):
                merged[key] = val
        else:
            # New key: only accept if at least as strict as the built-in default
            base = defaults.get(key, "ask") if defaults else "ask"
            if _STRICTNESS.get(val, 2) >= _STRICTNESS.get(base, 2):
                merged[key] = val
    return merged


def apply_llm_override(llm: dict) -> None:
    """Replace the merged ``llm`` section for this process (``nuh test``'s LLM flags).

    In memory only, like ``apply_override``: no config file is written. The
    settings parsed from the section are parsed again from *llm* (the config
    load already warned about it).
    """
    config_llm.apply(get_config(), llm, warn=False)


def _parse_add_remove(raw) -> tuple[list, list]:
    """Parse polymorphic config: list = add-only, dict = add/remove."""
    if isinstance(raw, list):
        return raw, []
    if isinstance(raw, dict):
        add = raw.get("add", [])
        remove = raw.get("remove", [])
        return (add if isinstance(add, list) else []), (remove if isinstance(remove, list) else [])
    return [], []


def _merge_configs(global_cfg: dict, project_cfg: dict) -> NuhConfig:
    """Merge global and project configs with security rules."""
    config = NuhConfig()

    # Keys no setting reads (config_keys): warned about, then ignored.
    config.unknown = config_keys.unknown(global_cfg, project_cfg)

    # profile: global config ONLY, validated
    profile = global_cfg.get("profile", "full")
    if profile not in _PROFILES:
        sys.stderr.write(f"nuh: unknown profile '{profile}', using 'full'\n")
        profile = "full"
    config.profile = profile

    # classify: keep global and project SEPARATE for three-table lookup
    config.classify_global = _validate_dict(global_cfg.get("classify", {}))
    config.classify_project = _validate_dict(project_cfg.get("classify", {}))

    # actions: tighten only, and never below a floor (config_floor)
    config.actions = config_floor.actions(_merge_dict_tighten(
        _validate_dict(global_cfg.get("actions", {})),
        _validate_dict(project_cfg.get("actions", {})),
        defaults=_POLICIES,
    ))

    # sensitive_paths_default: use project if stricter
    g_default = global_cfg.get("sensitive_paths_default", "ask")
    p_default = project_cfg.get("sensitive_paths_default", "")
    if p_default and _STRICTNESS.get(p_default, 2) >= _STRICTNESS.get(g_default, 2):
        config.sensitive_paths_default = p_default
    else:
        config.sensitive_paths_default = g_default if g_default in _STRICTNESS else "ask"

    # sensitive_paths: tighten only
    config.sensitive_paths_global = dict(_validate_dict(global_cfg.get("sensitive_paths", {})))
    config.sensitive_paths = _merge_dict_tighten(
        config.sensitive_paths_global,
        _validate_dict(project_cfg.get("sensitive_paths", {})),
    )

    # allow_paths: global config ONLY — project .nuh.yaml silently ignored
    g_allow = global_cfg.get("allow_paths", {})
    if isinstance(g_allow, dict):
        config.allow_paths = {k: v for k, v in g_allow.items() if isinstance(v, list)}

    # known_registries: global only, polymorphic (list = add-only, dict = add/remove)
    raw_kr = global_cfg.get("known_registries", [])
    if isinstance(raw_kr, (list, dict)):
        config.known_registries = raw_kr
    else:
        config.known_registries = []

    # exec_sinks: global only, polymorphic (list = add-only, dict = add/remove)
    raw_es = global_cfg.get("exec_sinks", [])
    if isinstance(raw_es, (list, dict)):
        config.exec_sinks = raw_es
    else:
        config.exec_sinks = []

    # sensitive_basenames: global only, flat dict (name → policy)
    config.sensitive_basenames = _validate_dict(global_cfg.get("sensitive_basenames", {}))

    # decode_commands: global only, polymorphic (list = add-only, dict = add/remove)
    raw_dc = global_cfg.get("decode_commands", [])
    if isinstance(raw_dc, (list, dict)):
        config.decode_commands = raw_dc
    else:
        config.decode_commands = []

    # content_patterns: add/suppress global-only, policies tighten-only
    g_content = _validate_dict(global_cfg.get("content_patterns", {}))
    p_content = _validate_dict(project_cfg.get("content_patterns", {}))
    raw_cp_add = g_content.get("add", [])
    config.content_patterns_add = raw_cp_add if isinstance(raw_cp_add, list) else []
    raw_cp_suppress = g_content.get("suppress", [])
    config.content_patterns_suppress = raw_cp_suppress if isinstance(raw_cp_suppress, list) else []
    g_policies = _validate_dict(g_content.get("policies", {}))
    p_policies = _validate_dict(p_content.get("policies", {}))
    config.content_policies = _merge_dict_tighten(g_policies, p_policies)

    # credential_patterns: entirely global-only
    g_cred = _validate_dict(global_cfg.get("credential_patterns", {}))
    raw_cred_add = g_cred.get("add", [])
    config.credential_patterns_add = raw_cred_add if isinstance(raw_cred_add, list) else []
    raw_cred_suppress = g_cred.get("suppress", [])
    config.credential_patterns_suppress = raw_cred_suppress if isinstance(raw_cred_suppress, list) else []

    # llm: global config ONLY — a project's .nuh.yaml llm is ignored with a
    # warning (config_gating), but a project may switch tracing off
    # (`trace: false`): that only reduces what is written.
    config.trace_off = config_llm.parse_project_trace(project_cfg)
    config_llm.apply(config, _validate_dict(global_cfg.get("llm", {})), warn=True)

    # trusted_paths: global config ONLY (project .nuh.yaml cannot set)
    # Default: /tmp (and /private/tmp on macOS) for profile: full — standard
    # scratch space, prompting on every temp file write is pure friction.
    _default_trusted = ["/tmp", "/private/tmp"] if config.profile == "full" else []
    g_trusted = global_cfg.get("trusted_paths", [])
    if isinstance(g_trusted, list):
        config.trusted_paths = [str(p) for p in g_trusted]
    # Merge defaults (user entries take priority, defaults just fill in)
    existing = set(config.trusted_paths)
    for p in _default_trusted:
        if p not in existing:
            config.trusted_paths.append(p)

    # db_targets: global config ONLY — project .nuh.yaml silently ignored
    g_targets = global_cfg.get("db_targets", [])
    if isinstance(g_targets, list):
        config.db_targets = [t for t in g_targets if isinstance(t, dict)]

    # log: global config ONLY — project .nuh.yaml silently ignored
    config.log = _validate_dict(global_cfg.get("log", {}))

    # tools, human_only: global config ONLY — how far nuh and its LLM handle each
    # tool, and which asks no LLM may lower (gating.py); a project's warns
    config_gating.apply(config, global_cfg, project_cfg)

    # hook.deadline_s, hook.on_error: global config ONLY — a repo must not stretch
    # or shrink the budget, nor choose what a crash on its own input answers
    hook_cfg = _validate_dict(global_cfg.get("hook", {}))
    config.hook_deadline_s = _parse_deadline(hook_cfg.get("deadline_s"))
    config.hook_on_error = _parse_on_error(hook_cfg.get("on_error"))
    for key in _validate_dict(project_cfg.get("hook", {})):
        sys.stderr.write(f"nuh: config: hook.{key} is global-only; ignored in {_PROJECT_CONFIG_NAME}\n")

    # protect_package: nuh's own code is always floored; anything but true warns
    config_floor.protect_package(global_cfg.get("protect_package"))

    # project_root, project_lifts_sensitive: global config ONLY — a repo must
    # never widen what counts as the project or lift the user's sensitive entries
    config.project_root = _parse_project_root(global_cfg.get("project_root"))
    config.project_lifts_sensitive = _parse_global_bool(global_cfg, "project_lifts_sensitive", False)
    config.completion_hint = _parse_global_bool(global_cfg, "completion_hint", True)
    for key in _GLOBAL_ONLY_WARNED:
        if key in project_cfg:
            sys.stderr.write(f"nuh: config: {key} is global-only; ignored in {_PROJECT_CONFIG_NAME}\n")

    return config


def _parse_deadline(raw: object) -> float:
    """Parse hook.deadline_s: a positive number of seconds; unset is the default."""
    if raw is None:
        return DEFAULT_DEADLINE_S
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 < raw < float("inf"):
        sys.stderr.write(f"nuh: config: hook.deadline_s must be a positive number: {raw!r}; "
                         f"using {DEFAULT_DEADLINE_S:g}\n")
        return DEFAULT_DEADLINE_S
    return float(raw)


def _parse_on_error(raw: object) -> str:
    """Parse hook.on_error: ask, block or defer; unset is the default, anything else warns and is the default."""
    if raw is None:
        return hook_error.DEFAULT
    if raw not in hook_error.MODES:
        sys.stderr.write(f"nuh: config: hook.on_error must be one of {', '.join(hook_error.MODES)}: {raw!r}; "
                         f"using {hook_error.DEFAULT}\n")
        return hook_error.DEFAULT
    return raw


def is_path_allowed(
    sensitive_path: str, project_root: str | None, *, literal: bool = False,
) -> bool:
    """Check if a sensitive path is exempted via allow_paths for the given project root."""
    if not project_root:
        return False

    cfg = get_config()
    if not cfg.allow_paths:
        return False

    from nuh.paths import resolve_path  # lazy import to avoid circular
    # Patch filenames and their actual project root are filesystem literals.
    # Config patterns retain expansion so existing allow-path rules still work.
    real_root = os.path.realpath(project_root) if literal else resolve_path(project_root)
    real_path = os.path.realpath(sensitive_path) if literal else resolve_path(sensitive_path)

    for pattern, roots in cfg.allow_paths.items():
        resolved_pattern = resolve_path(pattern)
        if real_path == resolved_pattern or real_path.startswith(resolved_pattern + os.sep):
            for root in roots:
                resolved_root = resolve_path(root)
                if real_root == resolved_root:
                    return True
    return False


def get_global_config_path() -> str:
    """Return the global config file path."""
    return _GLOBAL_CONFIG


def get_project_config_path() -> str | None:
    """Return the project config file path, or None if no project root."""
    from nuh.paths import get_project_root
    project_root = get_project_root()
    if project_root:
        return os.path.join(project_root, _PROJECT_CONFIG_NAME)
    return None
