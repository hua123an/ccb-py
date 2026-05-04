"""Provider factory.

Routing rules:
1. If base_url is provided, auto-detect from URL:
   - Known OpenAI relays (openrouter.ai, huaan.space, etc.) → OpenAI
   - anthropic.com direct → Anthropic
   - Other URLs → trust account config
2. If no base_url, use provider type from account config
"""
from __future__ import annotations

from ccb.api.base import Provider
from ccb.config import get_api_key, get_base_url, get_model, get_provider


# Known OpenAI-compatible relays
OPENAI_RELAYS = {
    "openrouter.ai",
    "huaan.space",
    "b.ai",
    "api.openai.com",
    "openai.com",
    # Add more as needed
}


def _detect_provider_from_url(base_url: str | None, fallback: str) -> str:
    """Detect provider type from base_url, or return fallback."""
    if not base_url:
        return fallback

    url_lower = base_url.lower()

    # Check known OpenAI relays
    if any(relay in url_lower for relay in OPENAI_RELAYS):
        return "openai"

    # Check Anthropic direct API
    if "anthropic" in url_lower and "api.anthropic.com" in url_lower:
        return "anthropic"

    # Check Bedrock
    if "bedrock" in url_lower:
        return "bedrock"

    # Check Vertex
    if "vertex" in url_lower or "aiplatform" in url_lower:
        return "vertex"

    # Fallback to account config
    return fallback


def create_provider(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Provider:
    model = model or get_model()
    api_key = api_key or get_api_key()
    base_url = base_url or get_base_url()
    account_provider = get_provider()

    # Detect provider from base_url if provided, otherwise use account config
    provider_type = _detect_provider_from_url(base_url, account_provider)

    # Default: trust the detected provider type
    if provider_type == "anthropic":
        from ccb.api.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url)
    elif provider_type == "gemini":
        from ccb.api.gemini_provider import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model, base_url=base_url)
    elif provider_type == "bedrock":
        from ccb.api.bedrock_provider import BedrockProvider
        return BedrockProvider(model=model, base_url=base_url)
    elif provider_type == "vertex":
        from ccb.api.vertex_provider import VertexProvider
        return VertexProvider(model=model, base_url=base_url)
    elif provider_type in ("openai", "grok"):
        from ccb.api.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model, base_url=base_url)
    else:
        # Unknown provider type, default to Anthropic
        from ccb.api.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url)


async def resolve_and_create_provider(
    model: str,
    silent: bool = False,
) -> tuple[Provider, str | None]:
    """Auto-route: find which account serves `model`, create provider.

    Returns (provider, account_name).  account_name is None if we fell
    back to the currently-active account (no routing happened).
    """
    from ccb.model_router import find_account_for_model, get_cached_account

    # Fast path: already cached
    cached = get_cached_account(model)
    if cached:
        from ccb.config import load_accounts
        store = load_accounts()
        acct = store.get("accounts", {}).get(cached)
        if acct:
            return create_provider(
                model=model,
                api_key=acct.get("apiKey"),
                base_url=acct.get("baseUrl"),
            ), cached

    # Slow path: probe all accounts in parallel
    if not silent:
        from ccb.display import print_info
        print_info(f"Auto-routing model {model}...")

    acct = await find_account_for_model(model)
    if acct:
        acct_name = acct.get("_name", "")
        if not silent:
            from ccb.display import print_info
            print_info(f"  → Found {model} on [{acct_name}]")
        return create_provider(
            model=model,
            api_key=acct.get("apiKey"),
            base_url=acct.get("baseUrl"),
        ), acct_name

    # Fallback: use current active account
    return create_provider(model=model), None
