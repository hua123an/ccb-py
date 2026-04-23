"""Context window sizes for LLM models.

Data sources (as of 2026-04): official provider docs + release notes.
Where providers publish a range (e.g. "up to 1M with beta header"), we use
the default enabled value, not the maximum opt-in value.

Lookup order in ``get_context_limit``:
  1. User override from ``~/.claude/settings.json`` → ``modelContextLimits``
     (per-account override in ``accounts.json`` → ``contextLimit`` also works)
  2. Exact match in ``MODEL_CONTEXT_LIMITS``
  3. Longest matching prefix in ``_PREFIX_FALLBACKS``
  4. ``DEFAULT_CONTEXT_LIMIT`` (128k — safe middle-ground)
"""
from __future__ import annotations

import json
from pathlib import Path


DEFAULT_CONTEXT_LIMIT = 128_000

# ── Curated per-model limits ─────────────────────────────────────────
# Keys are normalized to lowercase for case-insensitive lookup.
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # ── Anthropic Claude ─────────────────────────────────────────────
    "claude-3-haiku":              200_000,
    "claude-3-sonnet":             200_000,
    "claude-3-opus":               200_000,
    "claude-3-5-haiku":            200_000,
    "claude-3-5-sonnet":           200_000,
    "claude-3-7-sonnet":           200_000,
    "claude-sonnet-4":             200_000,
    "claude-sonnet-4-5":           200_000,
    "claude-opus-4":               200_000,
    "claude-opus-4-1":             200_000,
    "claude-opus-4-5":             200_000,
    "claude-opus-4-6":             200_000,
    "claude-haiku-4":              200_000,
    "claude-haiku-4-5":            200_000,

    # ── OpenAI GPT / chat ────────────────────────────────────────────
    "gpt-3.5-turbo":                16_385,
    "gpt-3.5-turbo-16k":            16_385,
    "gpt-4":                         8_192,
    "gpt-4-32k":                    32_768,
    "gpt-4-turbo":                 128_000,
    "gpt-4-turbo-preview":         128_000,
    "gpt-4o":                      128_000,
    "gpt-4o-mini":                 128_000,
    "gpt-4.1":                   1_000_000,
    "gpt-4.1-mini":              1_000_000,
    "gpt-4.1-nano":              1_000_000,
    "gpt-5":                       400_000,
    "gpt-5-mini":                  400_000,
    "gpt-5-nano":                  400_000,
    "gpt-5.4":                     400_000,
    "gpt-5.4-mini":                400_000,

    # ── OpenAI reasoning (o-series) ──────────────────────────────────
    "o1":                          200_000,
    "o1-preview":                  128_000,
    "o1-mini":                     128_000,
    "o3":                          200_000,
    "o3-mini":                     200_000,
    "o4-mini":                     200_000,

    # ── Google Gemini ────────────────────────────────────────────────
    "gemini-1.5-pro":            2_000_000,
    "gemini-1.5-flash":          1_000_000,
    "gemini-2.0-pro":            2_000_000,
    "gemini-2.0-flash":          1_000_000,
    "gemini-2.5-pro":            1_000_000,
    "gemini-2.5-flash":          1_000_000,

    # ── xAI Grok ─────────────────────────────────────────────────────
    "grok-1":                        8_192,
    "grok-2":                      128_000,
    "grok-2-vision":               128_000,
    "grok-3":                    1_000_000,
    "grok-4":                      256_000,
    "grok-beta":                   128_000,

    # ── Moonshot / Kimi ──────────────────────────────────────────────
    "moonshot-v1-8k":                8_192,
    "moonshot-v1-32k":              32_768,
    "moonshot-v1-128k":            128_000,
    "kimi-k1.5":                   128_000,
    "kimi-k2":                     200_000,
    "kimi-k2.5":                   200_000,

    # ── DeepSeek ─────────────────────────────────────────────────────
    "deepseek-chat":                64_000,
    "deepseek-coder":               64_000,
    "deepseek-v3":                  64_000,
    "deepseek-r1":                  64_000,

    # ── Qwen / Tongyi ────────────────────────────────────────────────
    "qwen-max":                     32_768,
    "qwen-plus":                   128_000,
    "qwen-turbo":                1_000_000,
    "qwen2.5-72b":                 128_000,
    "qwen3":                       128_000,
    "qwen3-coder":                 256_000,

    # ── Mistral ──────────────────────────────────────────────────────
    "mistral-large":               128_000,
    "mistral-medium":              128_000,
    "mistral-small":                32_768,
    "mixtral-8x7b":                 32_768,
    "mixtral-8x22b":                64_000,
    "codestral":                    32_768,
    "codestral-mamba":             256_000,
    "devstral":                    128_000,

    # ── Meta Llama (via providers) ───────────────────────────────────
    "llama-3":                       8_192,
    "llama-3-70b":                   8_192,
    "llama-3.1":                   128_000,
    "llama-3.2":                   128_000,
    "llama-3.3":                   128_000,
    "llama-4":                   1_000_000,
}

# ── Prefix fallbacks ─────────────────────────────────────────────────
# Ordered from most-specific to least-specific. Used when the exact model
# string isn't in MODEL_CONTEXT_LIMITS (e.g. dated variants, relay aliases).
_PREFIX_FALLBACKS: list[tuple[str, int]] = [
    # OpenAI specific families first (so "gpt-4.1-" doesn't match "gpt-4")
    ("gpt-4.1",        1_000_000),
    ("gpt-5.4",          400_000),
    ("gpt-5",            400_000),
    ("gpt-4o",           128_000),
    ("gpt-4-turbo",      128_000),
    ("gpt-4-32k",         32_768),
    ("gpt-4",              8_192),
    ("gpt-3.5",           16_385),
    # Reasoning
    ("o4",               200_000),
    ("o3",               200_000),
    ("o1-mini",          128_000),
    ("o1",               200_000),
    # Anthropic
    ("claude",           200_000),
    ("anthropic",        200_000),
    # Gemini
    ("gemini-2.5",     1_000_000),
    ("gemini-2",       1_000_000),
    ("gemini-1.5",     1_000_000),
    ("gemini",         1_000_000),
    # Grok
    ("grok-3",         1_000_000),
    ("grok-4",           256_000),
    ("grok",             128_000),
    # Kimi / moonshot
    ("kimi",             200_000),
    ("moonshot",         128_000),
    # DeepSeek
    ("deepseek",          64_000),
    # Qwen
    ("qwen3-coder",      256_000),
    ("qwen",             128_000),
    # Mistral
    ("mistral-large",    128_000),
    ("mistral",          128_000),
    ("mixtral",           64_000),
    ("codestral",         32_768),
    ("devstral",         128_000),
    # Llama
    ("llama-4",        1_000_000),
    ("llama-3.1",        128_000),
    ("llama-3.2",        128_000),
    ("llama-3.3",        128_000),
    ("llama",              8_192),
]


def _load_user_overrides() -> dict[str, int]:
    """Read per-model limit overrides from ~/.claude/settings.json.

    Schema:
        {"modelContextLimits": {"gpt-5.4-2026-03-05": 400000}}
    """
    try:
        path = Path.home() / ".claude" / "settings.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        overrides = data.get("modelContextLimits") or {}
        return {str(k).lower(): int(v) for k, v in overrides.items() if isinstance(v, (int, float))}
    except Exception:
        return {}


def _load_account_override(model: str) -> int | None:
    """Check if the currently-active account's profile sets contextLimit.

    Schema in accounts.json:
        {"accounts": {"name": {..., "contextLimit": 400000}}}
    """
    try:
        from ccb.config import get_active_account
        acct = get_active_account()
        if acct and isinstance(acct.get("contextLimit"), (int, float)):
            return int(acct["contextLimit"])
        # Also support per-model override inside the account
        model_limits = acct.get("modelContextLimits") if acct else None
        if isinstance(model_limits, dict):
            v = model_limits.get(model) or model_limits.get(model.lower())
            if isinstance(v, (int, float)):
                return int(v)
    except Exception:
        pass
    return None


def get_context_limit(model: str) -> int:
    """Return the effective context-window size (in tokens) for ``model``.

    Lookup order: account override → settings.json override → exact table →
    longest prefix → DEFAULT_CONTEXT_LIMIT.
    """
    if not model:
        return DEFAULT_CONTEXT_LIMIT

    m = model.strip().lower()

    # 1. Per-account override (highest priority — most specific)
    acct_override = _load_account_override(model)
    if acct_override:
        return acct_override

    # 2. settings.json override
    user_overrides = _load_user_overrides()
    if m in user_overrides:
        return user_overrides[m]

    # 3. Exact match
    if m in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[m]

    # 4. Longest matching prefix (iterate in order — more-specific first)
    for prefix, limit in _PREFIX_FALLBACKS:
        if m.startswith(prefix):
            return limit

    # 5. Substring fallback (catches e.g. "claude" appearing mid-string from
    # weird relay naming like "openrouter/anthropic/claude-sonnet-4")
    for prefix, limit in _PREFIX_FALLBACKS:
        if prefix in m:
            return limit

    return DEFAULT_CONTEXT_LIMIT
