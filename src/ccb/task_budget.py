"""Task budget and thinking configuration.

Inspired by Anthropic Agent SDK's TaskBudget and ThinkingConfig.
Provides token budget enforcement and adaptive thinking control.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ThinkingMode(str, Enum):
    OFF = "off"
    ON = "on"
    ADAPTIVE = "adaptive"


@dataclass
class ThinkingConfig:
    """Configuration for model thinking/reasoning."""
    mode: ThinkingMode = ThinkingMode.OFF
    budget_tokens: int = 10000
    display: str = "summarized"  # summarized, omitted

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.mode.value,
            "budget_tokens": self.budget_tokens,
            "display": self.display,
        }

    @classmethod
    def disabled(cls) -> ThinkingConfig:
        return cls(mode=ThinkingMode.OFF)

    @classmethod
    def enabled(cls, budget: int = 10000, display: str = "summarized") -> ThinkingConfig:
        return cls(mode=ThinkingMode.ON, budget_tokens=budget, display=display)

    @classmethod
    def adaptive(cls, display: str = "summarized") -> ThinkingConfig:
        return cls(mode=ThinkingMode.ADAPTIVE, display=display)


@dataclass
class TaskBudget:
    """Token/cost budget for a task or session.

    Tracks usage and enforces limits to prevent runaway costs.
    """
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0
    max_turns: int = 0
    max_usd: float = 0.0

    # Tracking
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    used_turns: int = 0
    estimated_usd: float = 0.0

    @property
    def used_total_tokens(self) -> int:
        return self.used_input_tokens + self.used_output_tokens

    @property
    def is_exhausted(self) -> bool:
        if self.max_input_tokens and self.used_input_tokens >= self.max_input_tokens:
            return True
        if self.max_output_tokens and self.used_output_tokens >= self.max_output_tokens:
            return True
        if self.max_total_tokens and self.used_total_tokens >= self.max_total_tokens:
            return True
        if self.max_turns and self.used_turns >= self.max_turns:
            return True
        if self.max_usd and self.estimated_usd >= self.max_usd:
            return True
        return False

    @property
    def remaining_tokens(self) -> int | None:
        if self.max_total_tokens:
            return max(0, self.max_total_tokens - self.used_total_tokens)
        return None

    def add_usage(self, usage: dict[str, Any]) -> None:
        """Record token usage from an API response."""
        self.used_input_tokens += usage.get("input_tokens", 0)
        self.used_output_tokens += usage.get("output_tokens", 0)
        self.used_turns += 1

        # Estimate cost (rough: $3/M input, $15/M output for Sonnet)
        input_cost = self.used_input_tokens * 3 / 1_000_000
        output_cost = self.used_output_tokens * 15 / 1_000_000
        self.estimated_usd = input_cost + output_cost

    def check(self) -> tuple[bool, str]:
        """Check if budget allows continued execution.

        Returns (can_continue, reason).
        """
        if self.max_input_tokens and self.used_input_tokens >= self.max_input_tokens:
            return False, f"Input token budget exhausted ({self.used_input_tokens:,}/{self.max_input_tokens:,})"
        if self.max_output_tokens and self.used_output_tokens >= self.max_output_tokens:
            return False, f"Output token budget exhausted ({self.used_output_tokens:,}/{self.max_output_tokens:,})"
        if self.max_total_tokens and self.used_total_tokens >= self.max_total_tokens:
            return False, f"Total token budget exhausted ({self.used_total_tokens:,}/{self.max_total_tokens:,})"
        if self.max_turns and self.used_turns >= self.max_turns:
            return False, f"Turn budget exhausted ({self.used_turns}/{self.max_turns})"
        if self.max_usd and self.estimated_usd >= self.max_usd:
            return False, f"Cost budget exhausted (${self.estimated_usd:.2f}/${self.max_usd:.2f})"
        return True, ""

    def summary(self) -> dict[str, Any]:
        """Get a summary of budget usage."""
        can_continue, reason = self.check()
        return {
            "input_tokens": self.used_input_tokens,
            "output_tokens": self.used_output_tokens,
            "total_tokens": self.used_total_tokens,
            "turns": self.used_turns,
            "estimated_usd": round(self.estimated_usd, 4),
            "can_continue": can_continue,
            "reason": reason if not can_continue else "",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_total_tokens": self.max_total_tokens,
            "max_turns": self.max_turns,
            "max_usd": self.max_usd,
            "used": self.summary(),
        }
