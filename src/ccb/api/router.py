"""Provider factory."""
from __future__ import annotations

from ccb.api.base import Provider
from ccb.config import get_api_key, get_base_url, get_model, get_provider


def _is_claude_model(model: str) -> bool:
    """Check if a model name refers to a Claude/Anthropic model."""
    m = model.lower()
    return any(k in m for k in ("claude", "anthropic", "sonnet", "opus", "haiku"))


def create_provider(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Provider:
    model = model or get_model()
    api_key = api_key or get_api_key()
    base_url = base_url or get_base_url()
    provider_type = get_provider()

    # Auto-detect: Claude models should use Anthropic provider even through
    # OpenRouter or other OpenAI-compatible gateways
    if provider_type in ("openai", "grok") and _is_claude_model(model):
        from ccb.api.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url)

    if provider_type == "gemini":
        from ccb.api.gemini_provider import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model, base_url=base_url)
    elif provider_type in ("openai", "grok"):
        from ccb.api.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url)
    else:
        from ccb.api.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url)
