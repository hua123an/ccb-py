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
    "api.mistral.ai",
    "api.groq.com",
    "api.minimaxi.com",
    "api.freemodel.dev",
    # Add more as needed
}

# Known Anthropic-compatible relays (native Anthropic format, NOT OpenAI)
ANTHROPIC_RELAYS = {
    "api.kimi.com",
    # Add more as needed
}


def _is_claude_model(model: str) -> bool:
    """Check if model name is a Claude model (for Anthropic-native image relay)."""
    return "claude" in model.lower()


def _detect_provider_from_url(base_url: str | None, fallback: str) -> str:
    """Detect provider type from base_url, or return fallback."""
    if not base_url:
        return fallback

    url_lower = base_url.lower()

    # Check URL path for /anthropic suffix — many relay services expose
    # both OpenAI and Anthropic format endpoints on the same domain
    # (e.g. api.minimaxi.com/anthropic).
    # This MUST be checked before the domain-based relay lists.
    from urllib.parse import urlparse
    path = urlparse(url_lower).path.rstrip("/")
    if path.endswith("/anthropic"):
        return "anthropic"

    # Check known Anthropic relays (always use Anthropic format)
    if any(relay in url_lower for relay in ANTHROPIC_RELAYS):
        return "anthropic"

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
    provider_type: str | None = None,
) -> Provider:
    model = model or get_model()
    api_key = api_key or get_api_key()
    base_url = base_url or get_base_url()
    account_provider = provider_type or get_provider()

    # Detect provider from base_url if provided, otherwise use account config
    resolved_provider_type = _detect_provider_from_url(base_url, account_provider)

    # Default: trust the detected provider type
    from ccb.capabilities import get_capabilities

    if resolved_provider_type == "anthropic":
        from ccb.api.anthropic_provider import AnthropicProvider
        p = AnthropicProvider(api_key=api_key, model=model, base_url=base_url)
    elif resolved_provider_type == "gemini":
        from ccb.api.gemini_provider import GeminiProvider
        p = GeminiProvider(api_key=api_key, model=model, base_url=base_url)
    elif resolved_provider_type == "bedrock":
        from ccb.api.bedrock_provider import BedrockProvider
        p = BedrockProvider(model=model, base_url=base_url)
    elif resolved_provider_type == "vertex":
        from ccb.api.vertex_provider import VertexProvider
        p = VertexProvider(model=model, base_url=base_url)
    elif resolved_provider_type in ("openai", "grok"):
        from ccb.api.openai_provider import OpenAIProvider
        p = OpenAIProvider(api_key=api_key, model=model, base_url=base_url)
    else:
        # Unknown provider type, default to Anthropic
        from ccb.api.anthropic_provider import AnthropicProvider
        p = AnthropicProvider(api_key=api_key, model=model, base_url=base_url)

    p._caps = get_capabilities(resolved_provider_type, model)
    return p


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
                provider_type=acct.get("provider"),
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
            provider_type=acct.get("provider"),
        ), acct_name

    # Fallback: use current active account
    return create_provider(model=model), None
