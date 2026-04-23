"""Cost and token tracking — estimates USD spend and formats token counts.

Mirrors the official Claude Code cost tracker. Provides:
  - Per-model cost-per-token lookup
  - Cumulative cost/token/duration tracking (session-level singleton)
  - Token formatting helpers (50k, 1.2M, etc.)
  - Context window percentage calculation
"""
from __future__ import annotations

import time
from typing import Any


# ── Per-model pricing (USD per 1M tokens, input / output) ──────────────
# Sources: official pricing pages as of 2026-04
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic  (input_per_1M, output_per_1M)
    "claude-3-haiku":          (0.25,   1.25),
    "claude-3-sonnet":         (3.00,  15.00),
    "claude-3-opus":          (15.00,  75.00),
    "claude-3-5-haiku":        (0.80,   4.00),
    "claude-3-5-sonnet":       (3.00,  15.00),
    "claude-3-7-sonnet":       (3.00,  15.00),
    "claude-sonnet-4":         (3.00,  15.00),
    "claude-sonnet-4-5":       (3.00,  15.00),
    "claude-opus-4":          (15.00,  75.00),
    "claude-opus-4-1":        (15.00,  75.00),
    "claude-opus-4-5":        (15.00,  75.00),
    "claude-opus-4-6":        (15.00,  75.00),
    "claude-haiku-4":          (0.80,   4.00),
    "claude-haiku-4-5":        (0.80,   4.00),
    # OpenAI
    "gpt-4o":                  (2.50,  10.00),
    "gpt-4o-mini":             (0.15,   0.60),
    "gpt-4-turbo":            (10.00,  30.00),
    "gpt-4":                  (30.00,  60.00),
    "gpt-3.5-turbo":           (0.50,   1.50),
    "gpt-4.1":                 (2.00,   8.00),
    "gpt-4.1-mini":            (0.40,   1.60),
    "gpt-4.1-nano":            (0.10,   0.40),
    "gpt-5":                   (5.00,  20.00),
    "gpt-5-mini":              (1.00,   4.00),
    "o1":                     (15.00,  60.00),
    "o1-mini":                 (3.00,  12.00),
    "o3":                     (10.00,  40.00),
    "o3-mini":                 (1.10,   4.40),
    "o4-mini":                 (1.10,   4.40),
    # Google Gemini
    "gemini-2.5-pro":          (1.25,  10.00),
    "gemini-2.5-flash":        (0.15,   0.60),
    "gemini-2.0-pro":          (1.25,  10.00),
    "gemini-2.0-flash":        (0.10,   0.40),
    "gemini-1.5-pro":          (1.25,   5.00),
    "gemini-1.5-flash":        (0.075,  0.30),
    # DeepSeek
    "deepseek-chat":           (0.27,   1.10),
    "deepseek-coder":          (0.14,   0.28),
    "deepseek-r1":             (0.55,   2.19),
    # Grok
    "grok-3":                  (3.00,  15.00),
    "grok-4":                  (3.00,  15.00),
}

# Prefix fallbacks for pricing
_PRICING_PREFIX: list[tuple[str, tuple[float, float]]] = [
    ("claude-opus",    (15.00, 75.00)),
    ("claude-sonnet",  (3.00, 15.00)),
    ("claude-haiku",   (0.80,  4.00)),
    ("claude",         (3.00, 15.00)),
    ("gpt-5",          (5.00, 20.00)),
    ("gpt-4.1",        (2.00,  8.00)),
    ("gpt-4o",         (2.50, 10.00)),
    ("gpt-4",         (10.00, 30.00)),
    ("gpt-3",          (0.50,  1.50)),
    ("o3",            (10.00, 40.00)),
    ("o4",             (1.10,  4.40)),
    ("o1",            (15.00, 60.00)),
    ("gemini-2.5",     (1.25, 10.00)),
    ("gemini-2",       (0.10,  0.40)),
    ("gemini",         (1.25,  5.00)),
    ("deepseek",       (0.27,  1.10)),
    ("grok",           (3.00, 15.00)),
]


def get_model_pricing(model: str) -> tuple[float, float]:
    """Return (input_cost_per_1M, output_cost_per_1M) for a model.

    Falls back to a generic estimate if the model isn't recognized.
    """
    m = model.strip().lower()
    if m in MODEL_PRICING:
        return MODEL_PRICING[m]
    for prefix, price in _PRICING_PREFIX:
        if m.startswith(prefix) or prefix in m:
            return price
    # Unknown model — return a safe middle-ground
    return (3.00, 15.00)


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a single API call."""
    inp_price, out_price = get_model_pricing(model)
    return (input_tokens * inp_price + output_tokens * out_price) / 1_000_000


# ── Formatting helpers ────────────────────────────────────────────────

def format_tokens(n: int) -> str:
    """Format token count as compact string: 1.2k, 50k, 1.2M, etc."""
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n / 1_000:.1f}k"
    if n < 1_000_000:
        return f"{n // 1_000}k"
    if n < 10_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n // 1_000_000}M"


def format_cost(usd: float) -> str:
    """Format USD cost: $0.00, $0.12, $1.23, $12.34."""
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1.0:
        return f"${usd:.2f}"
    return f"${usd:.2f}"


def format_duration(ms: float) -> str:
    """Format milliseconds to human-readable: 1.2s, 45s, 2m 30s."""
    secs = ms / 1000
    if secs < 1:
        return f"{ms:.0f}ms"
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    remaining = int(secs % 60)
    return f"{mins}m {remaining}s"


def context_percentage(used_tokens: int, context_limit: int) -> int:
    """Calculate context window usage as integer percentage."""
    if context_limit <= 0:
        return 0
    return min(100, round(used_tokens * 100 / context_limit))


# ── Session-level cost state ──────────────────────────────────────────

class CostState:
    """Accumulates cost and timing for one REPL session."""

    def __init__(self) -> None:
        self.total_cost_usd: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_creation_tokens: int = 0
        self.session_start: float = time.time()
        self.turn_start: float = 0.0
        self.last_turn_duration_ms: float = 0.0
        self._model: str = ""

    def set_model(self, model: str) -> None:
        self._model = model

    def start_turn(self) -> None:
        self.turn_start = time.time()

    def end_turn(self) -> None:
        if self.turn_start > 0:
            self.last_turn_duration_ms = (time.time() - self.turn_start) * 1000
            self.turn_start = 0.0

    def add_usage(self, usage: dict[str, int], model: str = "") -> None:
        model = model or self._model
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        self.total_input_tokens += inp
        self.total_output_tokens += out
        self.total_cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        self.total_cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
        if model:
            self.total_cost_usd += calculate_cost(model, inp, out)

    @property
    def total_duration_ms(self) -> float:
        return (time.time() - self.session_start) * 1000

    @property
    def elapsed_turn_ms(self) -> float:
        if self.turn_start > 0:
            return (time.time() - self.turn_start) * 1000
        return 0.0


# Global singleton
_cost_state = CostState()


def get_cost_state() -> CostState:
    return _cost_state


def reset_cost_state() -> None:
    global _cost_state
    _cost_state = CostState()
