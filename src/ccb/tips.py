"""Tips — contextual tip registry, scheduler, and history.

Shows relevant tips to the user based on their usage patterns and context.
Tips are shown sparingly and never repeated within a cooldown period.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Tip:
    """A single tip entry."""
    id: str
    text: str
    category: str = "general"
    condition: Callable[..., bool] | None = None  # Optional display condition
    priority: int = 0  # Higher = shown earlier
    max_shows: int = 3  # Max times to show this tip


@dataclass
class TipHistory:
    """Track which tips have been shown and when."""
    shown: dict[str, list[float]] = field(default_factory=dict)  # tip_id → list of timestamps

    def record(self, tip_id: str) -> None:
        self.shown.setdefault(tip_id, []).append(time.time())

    def show_count(self, tip_id: str) -> int:
        return len(self.shown.get(tip_id, []))

    def last_shown(self, tip_id: str) -> float:
        entries = self.shown.get(tip_id, [])
        return entries[-1] if entries else 0.0

    def last_any_tip(self) -> float:
        """When was any tip last shown?"""
        all_times = [t for ts in self.shown.values() for t in ts]
        return max(all_times) if all_times else 0.0


class TipRegistry:
    """Registry of all available tips."""

    def __init__(self) -> None:
        self._tips: dict[str, Tip] = {}
        self._register_builtin_tips()

    def register(self, tip: Tip) -> None:
        self._tips[tip.id] = tip

    def get(self, tip_id: str) -> Tip | None:
        return self._tips.get(tip_id)

    @property
    def all_tips(self) -> list[Tip]:
        return list(self._tips.values())

    def tips_for_category(self, category: str) -> list[Tip]:
        return [t for t in self._tips.values() if t.category == category]

    def _register_builtin_tips(self) -> None:
        """Register built-in tips."""
        tips = [
            Tip("compact", "Use /compact to free context when the conversation gets long.", "context", priority=5),
            Tip("effort", "Use /effort low|medium|high to control response quality vs speed.", "model"),
            Tip("at_mention", "Use @filename to include file contents in your message.", "input", priority=3),
            Tip("multiline", "Press Esc+Enter for multi-line input.", "input"),
            Tip("image_paste", "Press Ctrl+V to paste an image from clipboard.", "input"),
            Tip("vim_mode", "Use /vim to enable Vim-style editing keybindings.", "editor"),
            Tip("copy_response", "Press Ctrl+Y to copy the last response to clipboard.", "output"),
            Tip("sessions", "Use /sessions to list and resume previous conversations.", "session"),
            Tip("permissions", "Use /permissions to configure auto-approve for trusted tools.", "security"),
            Tip("model_switch", "Use /model to switch between different AI models mid-conversation.", "model"),
            Tip("diff_review", "Use /diff to see uncommitted changes before asking for help.", "git"),
            Tip("commit_auto", "Use /commit to auto-generate a commit message from staged changes.", "git"),
            Tip("agent_tool", "The 'agent' tool can spawn subagents for parallel tasks.", "tools"),
            Tip("memory", "Use /memory to view and manage cross-session memories.", "memory"),
            Tip("plugins", "Use /plugins to browse and install community plugins.", "extensibility"),
            Tip("hooks", "Use /hooks to set up pre/post tool-call automation.", "extensibility"),
            Tip("buddy", "Use /buddy to meet your virtual coding companion!", "fun"),
            Tip("scroll", "Use PageUp/PageDown to scroll through message history.", "navigation"),
            Tip("theme", "Use /theme to switch between different color themes.", "appearance"),
            Tip("context_viz", "Use /ctx_viz to visualize how your context window is being used.", "context"),
            Tip("pipe_mode", "Use 'ccb -p \"prompt\"' for non-interactive piped queries.", "advanced"),
            Tip("doctor", "Use /doctor to run system diagnostics if something seems wrong.", "debug"),
        ]
        for t in tips:
            self.register(t)


class TipScheduler:
    """Decides when and which tips to show."""

    def __init__(
        self,
        registry: TipRegistry | None = None,
        history: TipHistory | None = None,
        cooldown_seconds: float = 300.0,  # 5 minutes between tips
        show_probability: float = 0.3,    # 30% chance to show a tip
    ) -> None:
        self.registry = registry or TipRegistry()
        self.history = history or TipHistory()
        self.cooldown = cooldown_seconds
        self.show_probability = show_probability
        self._persistence_path = Path.home() / ".claude" / "tip-history.json"

    def should_show_tip(self) -> bool:
        """Check if we should show a tip now."""
        elapsed = time.time() - self.history.last_any_tip()
        if elapsed < self.cooldown:
            return False
        return random.random() < self.show_probability

    def pick_tip(self, context: dict[str, Any] | None = None) -> Tip | None:
        """Pick the best tip to show right now."""
        if not self.should_show_tip():
            return None

        context = context or {}
        candidates = []

        for tip in self.registry.all_tips:
            # Skip if shown too many times
            if self.history.show_count(tip.id) >= tip.max_shows:
                continue
            # Skip if shown recently (per-tip cooldown = 2x global cooldown)
            if time.time() - self.history.last_shown(tip.id) < self.cooldown * 2:
                continue
            # Check condition
            if tip.condition and not tip.condition(context):
                continue
            candidates.append(tip)

        if not candidates:
            return None

        # Sort by priority (high first), then by least-shown
        candidates.sort(
            key=lambda t: (-t.priority, self.history.show_count(t.id))
        )

        # Pick from top 3 randomly for variety
        pool = candidates[:3]
        tip = random.choice(pool)
        self.history.record(tip.id)
        return tip

    def get_contextual_tip(self, context: dict[str, Any]) -> Tip | None:
        """Get a tip relevant to the current context (ignoring cooldown/probability)."""
        category = None

        # Determine relevant category from context
        if context.get("context_pct", 0) > 60:
            category = "context"
        elif context.get("has_git_changes"):
            category = "git"
        elif context.get("turn_count", 0) == 1:
            category = "input"
        elif context.get("tool_count", 0) > 5:
            category = "tools"

        if not category:
            return None

        tips = self.registry.tips_for_category(category)
        eligible = [
            t for t in tips
            if self.history.show_count(t.id) < t.max_shows
        ]

        if not eligible:
            return None

        tip = eligible[0]
        self.history.record(tip.id)
        return tip

    def load_history(self) -> None:
        """Load tip history from disk."""
        if self._persistence_path.exists():
            try:
                data = json.loads(self._persistence_path.read_text())
                self.history.shown = data.get("shown", {})
            except Exception:
                pass

    def save_history(self) -> None:
        """Save tip history to disk."""
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"shown": self.history.shown}
        self._persistence_path.write_text(json.dumps(data, indent=2))

    def format_tip(self, tip: Tip) -> str:
        """Format a tip for display."""
        return f"💡 Tip: {tip.text}"


# ── Module-level singleton ─────────────────────────────────────

_scheduler: TipScheduler | None = None


def get_tip_scheduler() -> TipScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TipScheduler()
        _scheduler.load_history()
    return _scheduler
