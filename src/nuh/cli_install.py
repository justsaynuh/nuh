"""``nuh install``, ``update``, ``uninstall`` and ``claude``: the guard script and the agents' hook settings.

Install, update and uninstall also write, refresh and remove the user's zsh
completion (``completion_install``): once per user, whichever agents are named.
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from nuh import __version__, agents, completion_install, deadline


_HOOK_SCRIPT = Path.home() / ".claude" / "hooks" / "nuh_guard.py"

# The hook timeout the settings entry is written with rides in the script, so the hook can finish
# inside it (cascade_budget); install writes both from the same config.
_SHIM_TEMPLATE = ("#!{interpreter}\n_NUH_HOOK_TIMEOUT_S = {hook_timeout_s}\n"
                  + Path(__file__).with_name("guard.py").read_text().replace("{", "{{").replace("}", "}}"))


def _hook_script(agent_key: str = agents.CLAUDE) -> Path:
    """Keep the historical Claude path; give Codex its own installed guard."""
    if agent_key == agents.CLAUDE:
        return _HOOK_SCRIPT
    return agents.AGENT_SETTINGS[agent_key].parent / "hooks" / "nuh_guard.py"


def _hook_command(agent_key: str = agents.CLAUDE) -> str:
    """Build the command string for settings.json hook entries."""
    # Use POSIX forward-slash paths: safe in both bash and cmd.exe on Windows.
    # shlex.quote() produces POSIX single-quoting which only works when the
    # command is interpreted by a POSIX shell. Claude Code may invoke hooks
    # via cmd.exe or direct OS spawn, where single quotes are literal chars.
    # as_posix() eliminates backslashes at the source, sidestepping the issue.
    exe = Path(sys.executable).as_posix()
    script = _hook_script(agent_key).as_posix()
    return f'"{exe}" "{script}"'


def _hook_timeouts() -> tuple[float, float]:
    """(hook.deadline_s, LLM cascade seconds) from the global config."""
    from nuh.config import ConfigError, get_config
    from nuh.llm import cascade_timeout_s
    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"  Warning: {exc}; hook timeout uses defaults")
        return deadline.DEFAULT_DEADLINE_S, 0.0
    return cfg.hook_deadline_s, cascade_timeout_s(cfg.llm)


def _hook_timeout_s() -> int:
    """Hook ``timeout`` (seconds): static deadline + LLM cascade + margin, at least the floor."""
    return deadline.install_timeout_s(*_hook_timeouts())


def _hook_entry(agent_key: str, command: str, timeout_s: int) -> dict:
    """One nuh command hook. Claude Code blocks the call when the hook fails or times out.

    Codex's hooks.json has a ``timeout`` but no failure setting: a Codex hook
    that fails or times out lets the call through.
    """
    hook: dict = {"type": "command", "command": command, "timeout": timeout_s}
    if agent_key == agents.CLAUDE:
        hook["onFailure"] = "block"
    return hook


def _build_hooks_settings() -> dict:
    """Build a settings dict containing nuh PreToolUse hooks for Claude Code."""
    hook = _hook_entry(agents.CLAUDE, _hook_command(), _hook_timeout_s())
    pre_tool_use = []
    for tool_name in agents.AGENT_TOOL_MATCHERS[agents.CLAUDE]:
        pre_tool_use.append({
            "matcher": tool_name,
            "hooks": [dict(hook)],
        })
    return {"hooks": {"PreToolUse": pre_tool_use}}


def _read_settings(settings_file: Path) -> dict:
    """Read a settings.json file, return empty structure if missing."""
    if settings_file.exists():
        with open(settings_file) as f:
            return json.load(f)
    return {}


def _write_settings(settings_file: Path, data: dict) -> None:
    """Write settings.json with backup."""
    backup = settings_file.with_suffix(".json.bak")
    if settings_file.exists():
        with open(settings_file) as f:
            backup_content = f.read()
        with open(backup, "w") as f:
            f.write(backup_content)

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _is_nuh_hook(hook_entry: dict) -> bool:
    """Check if a hook entry belongs to nuh."""
    for hook in hook_entry.get("hooks", []):
        if "nuh_guard.py" in hook.get("command", ""):
            return True
    return False


def _without_nuh_hooks(entries: list[dict]) -> list[dict]:
    """Remove our commands without discarding other commands in shared entries."""
    result = []
    for entry in entries:
        if not _is_nuh_hook(entry):
            result.append(entry)
            continue
        remaining = [hook for hook in entry.get("hooks", [])
                     if "nuh_guard.py" not in hook.get("command", "")]
        if remaining:
            result.append({**entry, "hooks": remaining})
    return result


def _resolve_agents(args: argparse.Namespace) -> list[str]:
    """Resolve --agent flag to list of agent keys."""
    agent_arg = getattr(args, "agent", None) or agents.CLAUDE
    if agent_arg == "all":
        # Only install for agents whose settings dir exists
        result = []
        for key in sorted(agents.INSTALLABLE_AGENTS):
            settings_file = agents.AGENT_SETTINGS[key]
            if settings_file.parent.exists():
                result.append(key)
        if not result:
            print("No supported agent directories found.")
        return result
    if agent_arg not in agents.INSTALLABLE_AGENTS:
        print(f"Agent '{agent_arg}' is not installable. Supported: {', '.join(sorted(agents.INSTALLABLE_AGENTS))}")
        return []
    return [agent_arg]


def _write_hook_script(agent_key: str = agents.CLAUDE) -> None:
    """Write the shared hook shim script (used by all agents)."""
    script = _hook_script(agent_key)
    script.parent.mkdir(parents=True, exist_ok=True)

    shim_content = _SHIM_TEMPLATE.format(interpreter=sys.executable, hook_timeout_s=_hook_timeout_s())

    # Skip write if content is identical
    if script.exists():
        try:
            if script.read_text() == shim_content:
                return
        except OSError:
            # Read is best-effort optimization; if it fails (race with
            # deletion, permissions, disk), the safe default is to fall
            # through to the write path which will surface real errors.
            pass

    if script.exists():
        os.chmod(script, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

    with open(script, "w") as f:
        f.write(shim_content)

    os.chmod(script, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # 444


def _install_for_agent(agent_key: str) -> None:
    """Patch a single agent's settings.json with nuh hook entries."""
    settings_file = agents.AGENT_SETTINGS[agent_key]
    tool_names = list(agents.AGENT_TOOL_MATCHERS[agent_key])
    agent_name = agents.AGENT_NAMES[agent_key]

    settings = _read_settings(settings_file)
    hooks = settings.setdefault("hooks", {})
    # Existing matchers may deliberately connect custom policy to additional
    # tools. Updating the guard must not silently remove that coverage.
    for entry in hooks.get("PreToolUse", []):
        matcher = entry.get("matcher")
        if _is_nuh_hook(entry) and isinstance(matcher, str) and matcher not in tool_names:
            tool_names.append(matcher)
    pre_tool_use = _without_nuh_hooks(hooks.setdefault("PreToolUse", []))
    hooks["PreToolUse"] = pre_tool_use

    deadline_s, llm_s = _hook_timeouts()
    timeout_s = deadline.install_timeout_s(deadline_s, llm_s)
    hook = _hook_entry(agent_key, _hook_command(agent_key), timeout_s)

    for tool_name in tool_names:
        pre_tool_use.append({
            "matcher": tool_name,
            "hooks": [dict(hook)],
        })

    _write_settings(settings_file, settings)
    backup = settings_file.with_suffix(".json.bak")

    print(f"  {agent_name}:")
    print(f"    Settings:  {settings_file} ({len(tool_names)} PreToolUse matchers)")
    print(f"    Timeout:   {timeout_s}s (hook.deadline_s {deadline_s:g}s + LLM providers {llm_s:g}s"
          f" + margin; rerun `nuh update` after changing either)")
    if agent_key == agents.CLAUDE:
        print("    On failure: block (a crashed or timed-out hook denies the call)")
    if backup.exists():
        print(f"    Backup:    {backup}")


def cmd_install(args: argparse.Namespace) -> None:
    if getattr(args, "completion_only", False):
        if not completion_install.install(update=False):
            raise SystemExit(1)
        return
    agent_keys = _resolve_agents(args)
    if not agent_keys:
        return

    print(f"nuh {__version__} installed:")
    print(f"  Interpreter: {sys.executable}")

    for key in agent_keys:
        _write_hook_script(key)
        print(f"  Hook script: {_hook_script(key)} (read-only)")
        _install_for_agent(key)
    completion_ok = getattr(args, "no_completion", False) or completion_install.install(update=False)

    print()
    print("Ready. Safe commands go through silently, dangerous ones are")
    print("blocked. Claude requests confirmation for ambiguous actions;")
    print("Codex blocks them for review. Codex hooks must be enabled/trusted in Codex.")
    if not completion_ok:
        raise SystemExit(1)


def cmd_update(args: argparse.Namespace) -> None:
    """Update hook script: unlock → overwrite → re-lock. Update settings for targeted agents."""
    agent_keys = _resolve_agents(args)
    updated = False
    for key in agent_keys:
        script = _hook_script(key)
        if not script.exists():
            print(f"Hook script not found: {script}")
            print("Run `nuh install` first.")
            continue
        _write_hook_script(key)
        if agents.AGENT_SETTINGS[key].exists():
            _install_for_agent(key)
        print(f"  Hook script: {script} (re-locked read-only)")
        updated = True
    completion_ok = not updated or getattr(args, "no_completion", False) or completion_install.install(update=True)

    print(f"nuh {__version__} updated:")
    print(f"  Interpreter: {sys.executable}")
    if "codex" in agent_keys:
        print("Enable/trust the updated hooks in Codex; trust state was not changed.")
    if not completion_ok:
        raise SystemExit(1)


def cmd_uninstall(args: argparse.Namespace) -> None:
    agent_keys = _resolve_agents(args)
    if not agent_keys:
        return

    # 1. Remove nuh entries from each agent's config
    for key in agent_keys:
        settings_file = agents.AGENT_SETTINGS[key]
        agent_name = agents.AGENT_NAMES[key]
        if settings_file.exists():
            settings = _read_settings(settings_file)
            hooks = settings.get("hooks", {})
            pre_tool_use = hooks.get("PreToolUse", [])

            filtered = _without_nuh_hooks(pre_tool_use)

            if filtered:
                hooks["PreToolUse"] = filtered
            else:
                hooks.pop("PreToolUse", None)

            _write_settings(settings_file, settings)
            print(f"  {agent_name}: {settings_file} (nuh hooks removed)")
        else:
            print(f"  {agent_name}: settings not found (nothing to clean)")

    # 2. Remove dedicated Codex scripts; keep legacy shared Claude scripts if used.
    for key in agent_keys:
        if key != agents.CLAUDE:
            script = _hook_script(key)
            if script.exists():
                script.chmod(stat.S_IRUSR | stat.S_IWUSR)
                script.unlink()

    any_remaining = _installed_elsewhere(agent_keys)
    if agents.CLAUDE in agent_keys:
        if any_remaining:
            print(f"  Hook script: {_HOOK_SCRIPT} (kept — other agents still use it)")
        elif _HOOK_SCRIPT.exists():
            os.chmod(_HOOK_SCRIPT, stat.S_IRUSR | stat.S_IWUSR)
            _HOOK_SCRIPT.unlink()
            print(f"  Hook script: {_HOOK_SCRIPT} (deleted)")
        else:
            print(f"  Hook script: {_HOOK_SCRIPT} (not found)")

    # The completion is the user's, not an agent's: it goes with the last agent's hooks.
    completion_ok = True
    if any_remaining:
        print(f"{completion_install.LABEL}kept (nuh is still installed for another agent)")
    else:
        completion_ok = completion_install.remove()
    print("nuh uninstalled.")
    if not completion_ok:
        raise SystemExit(1)


def _installed_elsewhere(agent_keys: list[str]) -> bool:
    """True when an agent outside *agent_keys* still has nuh hook entries (legacy ones may use the Claude guard)."""
    for key in agents.INSTALLABLE_AGENTS:
        if key in agent_keys:
            continue
        sf = agents.AGENT_SETTINGS[key]
        if sf.exists():
            try:
                data = _read_settings(sf)
                if any(_is_nuh_hook(entry) for entry in data.get("hooks", {}).get("PreToolUse", [])):
                    return True
            except Exception as exc:
                sys.stderr.write(f"nuh: uninstall: {exc}\n")
    return False


def cmd_claude(user_args: list[str]) -> None:
    """Launch Claude Code with nuh hooks active for this session."""
    import shutil

    for arg in user_args:
        if arg == "--settings" or arg.startswith("--settings="):
            print("nuh claude: --settings is managed by nuh; pass other flags directly",
                  file=sys.stderr)
            raise SystemExit(1)

    claude_path = shutil.which("claude")
    if claude_path is None:
        print("nuh claude: 'claude' not found on PATH", file=sys.stderr)
        raise SystemExit(1)

    settings_file = agents.AGENT_SETTINGS[agents.CLAUDE]
    already_installed = False
    if settings_file.exists():
        settings = _read_settings(settings_file)
        for entry in settings.get("hooks", {}).get("PreToolUse", []):
            if _is_nuh_hook(entry):
                already_installed = True
                break

    if already_installed:
        os.execvp(claude_path, ["claude"] + user_args)
    else:
        _write_hook_script()
        settings_json = json.dumps(_build_hooks_settings())
        os.execvp(claude_path, ["claude", "--settings", settings_json] + user_args)
