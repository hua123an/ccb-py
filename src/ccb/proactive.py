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

        self._last_suggestions = suggestions
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
