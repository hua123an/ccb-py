"""Token cost estimation — per-model USD pricing."""
from __future__ import annotations

# Prices per 1M tokens (input, output) in USD
# Updated periodically; add new models as needed.
_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-sonnet-4": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-haiku-3.5": (0.80, 4.0),
    "claude-3.5-sonnet": (3.0, 15.0),
    "claude-3.5-haiku": (0.80, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-sonnet": (3.0, 15.0),
    "claude-3-haiku": (0.25, 1.25),
    # OpenAI
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o1-pro": (150.0, 600.0),
    "o3": (10.0, 40.0),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-1.5-flash": (0.075, 0.30),
    # Grok
    "grok-3": (3.0, 15.0),
    "grok-3-mini": (0.30, 0.50),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


def _find_pricing(model: str) -> tuple[float, float] | None:
    """Find pricing for a model, trying exact match then prefix match."""
    m = model.lower()
    # Exact match
    if m in _PRICING:
        return _PRICING[m]
    # Prefix match (e.g. "claude-sonnet-4-20250514" matches "claude-sonnet-4")
    for key, price in sorted(_PRICING.items(), key=lambda x: -len(x[0])):
        if m.startswith(key):
            return price
    # Partial match (e.g. "anthropic/claude-sonnet-4" contains "claude-sonnet-4")
    for key, price in sorted(_PRICING.items(), key=lambda x: -len(x[0])):
        if key in m:
            return price
    return None


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Estimate cost in USD. Returns None if model pricing is unknown."""
    pricing = _find_pricing(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def format_cost(cost_usd: float | None) -> str:
    """Format cost for display."""
    if cost_usd is None:
        return "unknown"
    if cost_usd < 0.01:
        return f"${cost_usd:.4f}"
    return f"${cost_usd:.2f}"
