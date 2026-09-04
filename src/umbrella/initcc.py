"""Project settings that make Claude use this program for worktrees.

Hooks do not obey settings precedence. They merge across every settings file
and all matching ones run in parallel, so a project cannot override a hook the
user configured: both would run, both would build a worktree, and only one of
the two printed paths could win.

What does obey precedence is `enabledPlugins`. It is an ordinary setting, so
`false` in a project file beats `true` in the user's. That is the lever: turn
the competing plugin off for this project, then declare the hooks here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MARKETPLACE = "skills-dir"

DEFAULT_COMMAND = "umbrella"

HOOK_ARGS = {
    "WorktreeCreate": "hook worktree-create",
    "WorktreeRemove": "hook worktree-remove",
}


def commands(command: str = DEFAULT_COMMAND) -> dict[str, str]:
    """What to put in each hook entry.

    A bare name needs the program on PATH, and Claude runs a hook with the
    environment it was started in, not a dev shell. An absolute path in
    settings.local.json is the way to be sure.
    """
    return {event: f"{command} {args}" for event, args in HOOK_ARGS.items()}


HOOKS = commands()


@dataclass
class Result:
    path: Path
    added: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)


def skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def discovered_plugins(base: Path | None = None) -> list[str]:
    """Plugins Claude enables by finding them, rather than by a settings key.

    A plugin under the skills directory is on by default, under the marketplace
    name skills-dir, and never appears in enabledPlugins. They have to be named
    when writing that key, in case it is ever read as an allowlist: naming one
    plugin would otherwise turn these off.
    """
    root = base if base is not None else skills_dir()
    found = []
    for manifest in sorted(root.glob("*/.claude-plugin/plugin.json")):
        try:
            name = json.loads(manifest.read_text()).get("name")
        except (OSError, ValueError):
            continue
        if name:
            found.append(f"{name}@{MARKETPLACE}")
    return found


def _has_command(entries: list, command: str) -> bool:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def apply(
    path: Path,
    disable: list[str],
    keep: list[str] | None = None,
    command: str = DEFAULT_COMMAND,
) -> Result:
    """Add the hooks to a settings file, keeping everything already in it.

    Read, change only what is missing, write back. Anything else in the file is
    somebody's decision and none of this program's business.
    """
    data: dict = {}
    if path.exists():
        text = path.read_text().strip()
        if text:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError(f"{path} is not a json object")

    result = Result(path=path)

    hooks = data.setdefault("hooks", {})
    for event, line in commands(command).items():
        entries = hooks.setdefault(event, [])
        if _has_command(entries, line):
            result.already.append(event)
            continue
        entries.append({"hooks": [{"type": "command", "command": line}]})
        result.added.append(event)

    for plugin in disable:
        enabled = data.setdefault("enabledPlugins", {})
        if enabled.get(plugin) is False:
            result.already.append(plugin)
            continue
        enabled[plugin] = False
        result.disabled.append(plugin)

    if disable:
        # Name every other discovered plugin, so writing this key cannot be
        # what turns them off. An explicit choice already in the file wins.
        enabled = data.setdefault("enabledPlugins", {})
        for plugin in keep or []:
            if plugin in disable or plugin in enabled:
                continue
            enabled[plugin] = True
            result.kept.append(plugin)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return result
