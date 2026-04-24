"""Auto-route models to the correct account/endpoint.

When the user picks a model, we check which configured account actually
serves it (via /v1/models) and cache the result. Switching model clears
the cache entry only if the model differs.

Usage:
    from ccb.model_router import resolve_provider_for_model
    provider = await resolve_provider_for_model("claude-opus-4.6")
"""
from __future__ import annotations

import asyncio
from typing import Any

# model → account_name cache (persists for the session lifetime)
_route_cache: dict[str, str] = {}


async def _account_has_model(
    base_url: str, api_key: str, model: str, timeout: float = 8.0,
) -> bool:
    """Check if an endpoint's /models list contains the given model."""
    import httpx

    base = base_url.rstrip("/")
    urls = [f"{base}/models"]
    if not base.endswith("/v1"):
        urls.append(f"{base}/v1/models")

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for url in urls:
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        ids = {m["id"] for m in data.get("data", []) if "id" in m}
                        return model in ids
                except Exception:
                    continue
    except Exception:
        pass
    return False


async def find_account_for_model(model: str) -> dict[str, Any] | None:
    """Find which account serves `model` by checking all accounts in parallel.

    Returns the full account profile dict (with '_name' injected) or None.
    """
    from ccb.config import load_accounts

    # Check cache first
    if model in _route_cache:
        store = load_accounts()
        acct = store.get("accounts", {}).get(_route_cache[model])
        if acct:
            acct = dict(acct)
            acct["_name"] = _route_cache[model]
            return acct
        # Stale cache entry
        del _route_cache[model]

    store = load_accounts()
    accounts = store.get("accounts", {})
    if not accounts:
        return None

    # Fire parallel checks
    async def _check(name: str, profile: dict) -> str | None:
        base = profile.get("baseUrl", "")
        key = profile.get("apiKey", "")
        if not base or not key:
            return None
        if await _account_has_model(base, key, model):
            return name
        return None

    tasks = [_check(name, prof) for name, prof in accounts.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, str):
            # Found — cache and return
            _route_cache[model] = result
            acct = dict(accounts[result])
            acct["_name"] = result
            return acct

    return None


def get_cached_account(model: str) -> str | None:
    """Return cached account name for model, or None."""
    return _route_cache.get(model)


def clear_cache(model: str | None = None) -> None:
    """Clear route cache for a specific model or all."""
    if model:
        _route_cache.pop(model, None)
    else:
        _route_cache.clear()


def set_cache(model: str, account_name: str) -> None:
    """Manually set a route cache entry."""
    _route_cache[model] = account_name
