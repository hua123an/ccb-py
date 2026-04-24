"""Provider factory.

Routing rules (by priority):
1. Account explicitly sets ``provider`` + ``baseUrl``  →  honour that.
   An OpenAI-compatible relay (huaan.space, b.ai, …) that also serves
   Claude models must NOT be hijacked to the Anthropic SDK.
2. No custom base_url, provider is "openai"/"grok", but model looks like
   Claude  →  auto-switch to AnthropicProvider (direct Anthropic API).
3. Otherwise  →  use the provider type as-is.
"""
from __future__ import annotations

from ccb.api.base import Provider
from ccb.config import get_api_key, get_base_url, get_model, get_provider


def _is_claude_model(model: str) -> bool:
    """Check if a model name refers to a Claude/Anthropic model."""
    m = model.lower()
    return any(k in m for k in ("claude", "anthropic", "sonnet", "opus", "haiku"))


def _account_has_custom_base_url() -> bool:
    """Return True if the active account provides its own base URL.

    When an account explicitly sets a baseUrl it means the user configured
    a specific relay/proxy endpoint.  In that case we must respect the
    account's ``provider`` field and NOT auto-switch based on model name.
    """
    from ccb.config import get_active_account
    acct = get_active_account()
    if acct and acct.get("baseUrl"):
        return True
    return False


def create_provider(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Provider:
    model = model or get_model()
    api_key = api_key or get_api_key()
    base_url = base_url or get_base_url()
    provider_type = get_provider()

    # Auto-detect: Claude models → Anthropic SDK, BUT only when there is
    # no custom base_url on the active account.  Relays like huaan.space
    # and b.ai serve Claude models via the OpenAI-compatible protocol;
    # sending Anthropic-format requests to them would fail.
    if (
        provider_type in ("openai", "grok")
        and _is_claude_model(model)
        and not _account_has_custom_base_url()
    ):
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
