"""Hooks system - run user-defined scripts on events."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ccb.config import claude_dir


HOOK_EVENTS = [
    "pre_tool_call",
    "post_tool_call",
    "pre_message",
    "post_message",
    "session_start",
    "session_end",
]


def load_hooks(cwd: str) -> dict[str, list[dict[str, Any]]]:
    """Load hooks from user, project, and plugin sources.

    Merge order (later wins for duplicates, but we collect all):
      1. ~/.claude/hooks.json                      — global user
      2. <cwd>/.claude/hooks.json                  — project
      3. Each installed+enabled plugin's hooks/*.json
    """
    hooks: dict[str, list[dict[str, Any]]] = {e: [] for e in HOOK_EVENTS}

    def _ingest(path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for event, entries in data.items():
            if event in hooks and isinstance(entries, list):
                hooks[event].extend(entries)

    _ingest(claude_dir() / "hooks.json")
    _ingest(Path(cwd) / ".claude" / "hooks.json")

    # Plugin-contributed hooks. Each installed+enabled plugin may drop hooks
    # in its <plugin_root>/hooks/*.json — they're loaded with the same
    # schema as user hooks.
    try:
        from ccb.plugins import load_installed_plugins
        for info in load_installed_plugins().values():
            if not info.get("enabled", True):
                continue
            hooks_dir = Path(info.get("path", "")) / "hooks"
            if not hooks_dir.is_dir():
                continue
            for f in hooks_dir.glob("*.json"):
                _ingest(f)
    except Exception:
        pass

    return hooks


async def run_hooks(
    event: str,
    hooks: dict[str, list[dict[str, Any]]],
    context: dict[str, Any] | None = None,
    cwd: str = ".",
) -> list[str]:
    """Run all hooks for an event. Returns list of outputs."""
    entries = hooks.get(event, [])
    outputs = []

    for entry in entries:
        command = entry.get("command")
        if not command:
            continue

        env = {**os.environ}
        if context:
            env["CCB_HOOK_EVENT"] = event
            env["CCB_HOOK_CONTEXT"] = json.dumps(context, ensure_ascii=False)

        # Optional matcher
        matcher = entry.get("match")
        if matcher and context:
            tool_name = context.get("tool_name", "")
            if matcher != tool_name and matcher != "*":
                continue

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace").strip()
            if output:
                outputs.append(output)
        except (asyncio.TimeoutError, Exception):
            pass

    return outputs
