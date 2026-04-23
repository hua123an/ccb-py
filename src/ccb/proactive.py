"""Proactive suggestions for ccb-py.

Analyzes context and suggests relevant actions, commands, or
code patterns to the user.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Suggestion:
    text: str
    category: str  # "command", "code", "tip", "fix"
    confidence: float = 0.5  # 0.0 - 1.0
    command: str = ""  # Suggested slash command
    context: str = ""  # Why this was suggested


class ProactiveEngine:
    """Generate contextual suggestions."""

    def __init__(self) -> None:
        self._enabled = True
        self._history: list[str] = []
        self._last_suggestions: list[Suggestion] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    def record_action(self, action: str) -> None:
        self._history.append(action)
        if len(self._history) > 100:
            self._history = self._history[-50:]

    def suggest(self, context: dict[str, Any] | None = None) -> list[Suggestion]:
        """Generate suggestions based on current context."""
        if not self._enabled:
            return []

        suggestions: list[Suggestion] = []
        ctx = context or {}

        # Check git status
        cwd = ctx.get("cwd", os.getcwd())
        if _has_uncommitted_changes(cwd):
            suggestions.append(Suggestion(
                text="You have uncommitted changes. Consider committing.",
                category="command",
                command="/commit",
                confidence=0.7,
                context="Uncommitted git changes detected",
            ))

        # Check for common patterns
        last_error = ctx.get("last_error", "")
        if last_error:
            suggestions.append(Suggestion(
                text=f"Error detected: {last_error[:50]}. Try /doctor for diagnostics.",
                category="fix",
                command="/doctor",
                confidence=0.6,
                context="Recent error in session",
            ))

        # Time-based suggestions
        msg_count = ctx.get("message_count", 0)
        if msg_count > 20:
            suggestions.append(Suggestion(
                text="Long conversation — consider /compact to free context.",
                category="tip",
                command="/compact",
                confidence=0.8,
                context=f"{msg_count} messages in session",
            ))

        # Check for missing config
        if not Path.home().joinpath(".claude", "CLAUDE.md").exists():
            suggestions.append(Suggestion(
                text="No CLAUDE.md found. Create one with /init for project-specific instructions.",
                category="tip",
                command="/init",
                confidence=0.5,
                context="Missing CLAUDE.md",
            ))

        # Repeated actions
        if len(self._history) >= 3:
            last3 = self._history[-3:]
            if len(set(last3)) == 1:
                suggestions.append(Suggestion(
                    text=f"Repeated action: {last3[0]}. Consider a different approach.",
                    category="tip",
                    confidence=0.4,
                    context="Same action repeated 3+ times",
                ))

        # Context window nearing limit
        ctx_usage = ctx.get("context_window_usage", 0.0)
        if ctx_usage > 0.8:
            suggestions.append(Suggestion(
                text=f"Context window {ctx_usage:.0%} full. Use /compact soon.",
                category="command",
                command="/compact",
                confidence=0.9,
                context=f"Context usage: {ctx_usage:.0%}",
            ))

        # Large number of files changed
        files_written = ctx.get("files_written", 0)
        if files_written >= 5:
            suggestions.append(Suggestion(
                text=f"{files_written} files changed. Good time for /diff and /commit.",
                category="command",
                command="/commit",
                confidence=0.7,
                context=f"{files_written} files written this session",
            ))

        # Long idle time
        last_activity = ctx.get("last_activity", 0)
        if last_activity and (time.time() - last_activity) > 600:
            suggestions.append(Suggestion(
                text="Inactive for 10+ minutes. Use /resume or start a new topic.",
                category="tip",
                confidence=0.3,
                context="Long idle period",
            ))

        # Tools usage hints
        tools_called = ctx.get("tools_called", 0)
        if tools_called == 0 and msg_count > 5:
            suggestions.append(Suggestion(
                text="No tools used yet. Try asking me to read, write, or run code.",
                category="tip",
                confidence=0.5,
                context="No tool usage in multi-turn session",
            ))

        # Memory suggestion
        if msg_count > 10 and not ctx.get("has_memories", False):
            suggestions.append(Suggestion(
                text="Consider saving key decisions with /memory for future sessions.",
                category="command",
                command="/memory",
                confidence=0.4,
                context="Active session with no memories saved",
            ))

        # Test file detection
        recent_files = ctx.get("recent_files", [])
        if any("test" not in f.lower() for f in recent_files) and files_written >= 3:
            has_tests = any("test" in f.lower() for f in recent_files)
            if not has_tests:
                suggestions.append(Suggestion(
                    text="Consider writing tests for your changes.",
                    category="code",
                    confidence=0.5,
                    context="Multiple files changed without test files",
                ))

        self._last_suggestions = suggestions
        # Sort by confidence (highest first)
        self._last_suggestions.sort(key=lambda s: s.confidence, reverse=True)
        return suggestions

    @property
    def last_suggestions(self) -> list[Suggestion]:
        return self._last_suggestions


def _has_uncommitted_changes(cwd: str) -> bool:
    try:
        from ccb.git_ops import status
        s = status(cwd=cwd)
        return bool(s.strip())
    except Exception:
        return False


# Module singleton
_engine: ProactiveEngine | None = None


def get_proactive_engine() -> ProactiveEngine:
    global _engine
    if _engine is None:
        _engine = ProactiveEngine()
    return _engine
