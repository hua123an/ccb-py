"""Account/auth-related slash commands."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info

if TYPE_CHECKING:
    from ccb.api.base import Provider
    from ccb.session import Session


async def cmd_account(args: str, provider: Provider, session: Session) -> Provider | None:
    """Handle /account command."""
    return await _account(args, provider, session)


async def cmd_login(args: str = "") -> bool:
    """Handle /login command."""
    from ccb.select_ui import ask_text, select_one

    methods = [
        {"label": "API Key", "description": "Enter an API key directly"},
        {"label": "OAuth", "description": "Authenticate via browser (Anthropic)"},
        {"label": "Environment", "description": "Use ANTHROPIC_API_KEY env var"},
    ]
    choice = await select_one(methods, title="Login Method")
    if choice == 0:
        key = await ask_text("API Key", placeholder="sk-...", mask=True, title="Login — API Key")
        if key:
            from ccb.config import load_accounts, accounts_path
            import json as _json
            store = load_accounts()
            name = await ask_text("Account name", default="default", title="Login — Account Name")
            if not name:
                print_info("Cancelled.")
                return True
            store[name] = {"_name": name, "api_key": key, "provider": "anthropic"}
            store["active"] = name
            accounts_path().write_text(_json.dumps(store, indent=2))
            print_info(f"Logged in as '{name}'")
    elif choice == 1:
        try:
            from ccb.oauth.flow import OAuthFlow, OAuthConfig
            flow = OAuthFlow(OAuthConfig(provider="anthropic", client_id="ccb-py-cli"))
            token = await flow.authorization_code_flow(timeout=120)
            if token:
                print_info(f"OAuth login successful (expires: {token.expires_at})")
            else:
                print_error("OAuth flow cancelled or failed.")
        except Exception as e:
            print_error(f"OAuth error: {e}")
    elif choice == 2:
        import os
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            print_info(f"Using ANTHROPIC_API_KEY from environment ({key[:8]}...)")
        else:
            print_error("ANTHROPIC_API_KEY not set in environment.")
    return True


async def cmd_logout() -> bool:
    """Handle /logout command."""
    from ccb.config import load_accounts, accounts_path
    store = load_accounts()
    store.pop("active", None)
    accounts_path().write_text(__import__("json").dumps(store, indent=2))
    print_info("Logged out (cleared active account)")
    return True


def cmd_config() -> bool:
    """Handle /config command."""
    from ccb.config import get_api_key, get_base_url, get_model, get_provider, get_active_account
    acct = get_active_account()
    console.print(f"  Provider: {get_provider()}")
    console.print(f"  Model:    {get_model()}")
    console.print(f"  Base URL: {get_base_url()}")
    console.print(f"  Account:  {acct.get('_name') if acct else 'none'}")
    key = get_api_key()
    console.print(f"  API Key:  {key[:8]}...{key[-4:]}" if len(key) > 12 else f"  API Key:  {key}")
    return True


async def _account(args: str, provider: Provider, session: Session) -> Provider | None:
    """Switch, add, remove, or list accounts.

    Subcommands (checked first):
      /account add              → interactive wizard (name, baseUrl, apiKey, pick model)
      /account remove <name>    → remove an account
      /account list             → show all accounts (no switch)

    Default:
      /account                  → interactive picker + model chooser
      /account <name> [model]   → direct switch

    Returns a new Provider if the active account/model changed, else None.
    """
    from ccb.config import get_active_account, switch_account, load_accounts
    from ccb.api.router import create_provider
    from ccb.select_ui import select_one

    # ── Subcommand dispatch ─────────────────────────────────────────────
    sub_parts = args.strip().split(maxsplit=1)
    sub = sub_parts[0] if sub_parts else ""
    sub_args = sub_parts[1] if len(sub_parts) > 1 else ""

    if sub == "add":
        return await _account_add(provider, session)
    if sub == "remove":
        await _account_remove(sub_args)
        return None
    if sub == "list":
        _account_list()
        return None

    store = load_accounts()
    accounts = store.get("accounts", {})
    acct = get_active_account()
    active_name = acct.get("_name", "") if acct else ""
    active_model_name = store.get("activeModel") if active_name else None

    async def _fetch_remote_models(profile: dict) -> list[str]:
        """Fetch model list from provider's /models endpoint."""
        import httpx
        base = profile.get("baseUrl", "").rstrip("/")
        api_key = profile.get("apiKey", "")
        if not base or not api_key:
            return profile.get("models", [])

        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        # Try multiple URL patterns: base/models, then base/v1/models
        urls = [f"{base}/models"]
        if not base.endswith("/v1"):
            urls.append(f"{base}/v1/models")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for url in urls:
                    try:
                        resp = await client.get(url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            ids = [m["id"] for m in data.get("data", []) if "id" in m]
                            if ids:
                                return sorted(dict.fromkeys(ids))
                    except Exception:
                        continue
        except Exception:
            pass
        local_models = profile.get("models", [])
        default_model = profile.get("defaultModel", "")
        if default_model and default_model not in local_models:
            return [default_model, *local_models]
        return local_models

    async def _pick_model(account_name: str, profile: dict) -> tuple[str | None, bool]:
        """Step 2: interactive model picker for the chosen account."""
        print_info(f"Loading models from {profile.get('baseUrl', '') or account_name} ...")
        models = await _fetch_remote_models(profile)
        default_model = profile.get("defaultModel", "")
        preferred_model = active_model_name if account_name == active_name and active_model_name else default_model
        if not models:
            if default_model:
                return default_model, False
            print_error("No models available for this account.")
            return None, False
        items = []
        active_idx = 0
        for i, m in enumerate(models):
            is_default = m == default_model
            if m == preferred_model:
                active_idx = i
            items.append({
                "label": m,
                "description": "(default)" if is_default else "",
            })
        choice = await select_one(
            items,
            title=f"{account_name} — {len(models)} models",
            active=active_idx,
            searchable=True,
            search_placeholder="Search models",
            visible_count=15,
            cancel_label="go back",
        )
        if choice is None:
            return None, True
        return models[choice], False

    def _apply_switch(name: str, model: str) -> Provider:
        switch_account(name, model)
        from ccb.config import get_model, get_base_url
        new_model = get_model()
        new_provider = create_provider(model=new_model)
        session.model = new_model
        print_info(f"Switched account → {name} · {new_model}")
        print_info(f"URL: {get_base_url() or 'default'}")
        return new_provider

    if not args:
        # First-time user: no accounts → jump straight into the add wizard
        if not accounts:
            print_info("No accounts configured. Starting add-account wizard…")
            return await _account_add(provider, session)
        while True:
            # Reload each iteration so newly-added accounts show up immediately
            store = load_accounts()
            accounts = store.get("accounts", {})
            acct = get_active_account()
            active_name = acct.get("_name", "") if acct else ""
            names = list(accounts.keys())

            items = []
            active_idx = 0
            for i, name in enumerate(names):
                profile = accounts[name]
                is_active = name == active_name
                if is_active:
                    active_idx = i
                try:
                    host = profile.get("baseUrl", "").split("//")[1].split("/")[0]
                except (IndexError, AttributeError):
                    host = profile.get("baseUrl", "")
                items.append({
                    "label": f"{'✓ ' if is_active else ''}{name}",
                    "description": f"{profile.get('provider', '')} · {profile.get('defaultModel', '')}",
                    "hint": f"({host})",
                })
            # Special entries (indexed beyond the real account list)
            add_idx = len(items)
            items.append({
                "label": "+ Add new account…",
                "description": "Configure a new service provider",
                "hint": "",
            })
            remove_idx = len(items) if names else -1
            if names:
                items.append({
                    "label": "- Remove account…",
                    "description": "Delete an existing account",
                    "hint": "",
                })
            acct_choice = await select_one(
                items, title="Select Account", active=active_idx, visible_count=12
            )
            if acct_choice is None:
                print_info("Cancelled.")
                return None
            # Special-entry dispatch
            if acct_choice == add_idx:
                new_provider = await _account_add(provider, session)
                if new_provider is not None:
                    return new_provider
                continue  # back to picker, reflect the newly-added account
            if acct_choice == remove_idx:
                await _account_remove("")
                continue  # back to picker, reflect the removal
            # Regular account → model picker → switch
            picked_name = names[acct_choice]
            picked_profile = accounts[picked_name]
            model, go_back = await _pick_model(picked_name, picked_profile)
            if go_back:
                continue
            if model is None:
                print_info("Cancelled.")
                return None
            return _apply_switch(picked_name, model)

    # Direct argument: /account nvidia or /account 2 [model]
    parts = args.split(maxsplit=1)
    pick = parts[0]
    override_model = parts[1] if len(parts) > 1 else None

    names = list(accounts.keys())
    try:
        idx = int(pick) - 1
        if 0 <= idx < len(names):
            pick = names[idx]
    except ValueError:
        pass

    if pick not in accounts:
        print_error(f"Account '{pick}' not found. Available: {', '.join(names)}")
        return None

    profile = accounts[pick]
    if not override_model:
        model, _go_back = await _pick_model(pick, profile)
        if model is None:
            print_info("Cancelled.")
            return None
    else:
        model = override_model

    return _apply_switch(pick, model)


async def _account_add(provider: Provider, session: Session) -> Provider | None:
    """Interactive wizard for adding a new service provider / account."""
    import json
    from ccb.config import (
        accounts_path, load_accounts, switch_account,
    )
    from ccb.api.router import create_provider
    from ccb.select_ui import ask_text, select_one

    store = load_accounts()
    existing = store.get("accounts", {})

    # ── Step 1: name ─────────────────────────────────────────────────
    name = await ask_text(
        "Account name (short identifier, e.g. openrouter / b.ai)",
        placeholder="my-account",
        title="Add Account — Step 1/4",
    )
    if not name or not (name := name.strip()):
        print_info("Cancelled.")
        return None
    if name in existing:
        print_error(f"Account '{name}' already exists. Use /account remove first.")
        return None

    # ── Step 2: base URL ─────────────────────────────────────────────
    base_url = await ask_text(
        "Base URL (full URL up to /v1, e.g. https://api.example.com/v1)",
        placeholder="https://api.example.com/v1",
        title="Add Account — Step 2/5",
    )
    if not base_url or not (base_url := base_url.strip().rstrip("/")):
        print_info("Cancelled.")
        return None

    # ── Step 2.5: provider type ──────────────────────────────────────
    provider_options = [
        {"label": "OpenAI / OpenAI-compatible", "description": "Most relays (openrouter, huaan.space, etc.)"},
        {"label": "Anthropic (native)", "description": "api.anthropic.com or compatible endpoints"},
        {"label": "Google Gemini", "description": "Google's Gemini API"},
        {"label": "AWS Bedrock", "description": "Amazon Bedrock (uses AWS credentials)"},
        {"label": "Google Vertex", "description": "Google Cloud Vertex AI"},
    ]
    provider_idx = await select_one(
        provider_options,
        title="Select Protocol Type",
    )
    if provider_idx is None:
        print_info("Cancelled.")
        return None
    provider_type = ["openai", "anthropic", "gemini", "bedrock", "vertex"][provider_idx]

    # ── Step 3: API key (masked) ─────────────────────────────────────
    api_key = await ask_text(
        "API key (will be stored in ~/.ccb/accounts.json)",
        placeholder="sk-...",
        mask=True,
        title="Add Account — Step 3/5",
    )
    if not api_key or not (api_key := api_key.strip()):
        print_info("Cancelled.")
        return None

    # ── Step 4: probe & pick default model ──────────────────────────
    console.print(f"  [dim]Probing {base_url}/models ...[/dim]")
    models: list[str] = []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                models = sorted({m["id"] for m in data.get("data", []) if "id" in m})
            else:
                print_info(f"  /models returned {resp.status_code} — you'll need to enter the model manually.")
    except Exception as e:
        print_info(f"  /models probe failed: {e}")

    default_model: str | None = None
    if models:
        items = [{"label": m} for m in models]
        choice = await select_one(
            items,
            title=f"Add Account — Step 4/4 · pick default model ({len(models)} available)",
            searchable=True,
            search_placeholder="Search models",
            visible_count=15,
        )
        if choice is not None:
            default_model = models[choice]
    if default_model is None:
        default_model = await ask_text(
            "Default model id",
            placeholder="claude-sonnet-4-20250514",
            title="Add Account — Step 4/4",
        )
        if default_model:
            default_model = default_model.strip()
    if not default_model:
        print_info("Cancelled.")
        return None

    # ── Save ────────────────────────────────────────────────────────
    profile = {
        "provider": provider_type,
        "apiKey": api_key,
        "baseUrl": base_url,
        "models": models,
        "defaultModel": default_model,
    }
    store.setdefault("accounts", {})[name] = profile
    accounts_path().write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    )
    console.print(
        f"  [bold green]✓[/bold green] Added account [bold]{name}[/bold] · {len(models)} models · default: {default_model}"
    )

    # ── Offer to switch ─────────────────────────────────────────────
    should_switch_idx = await select_one(
        [{"label": f"Yes — switch to {name}"}, {"label": "No — stay on current"}],
        title="Switch to this account now?",
    )
    if should_switch_idx == 0:
        switch_account(name, default_model)
        new_provider = create_provider(model=default_model)
        session.model = default_model
        console.print(f"  [bold green]→[/bold green] Switched to [bold]{name}[/bold] → {default_model}")
        return new_provider
    return None


async def _account_remove(name: str) -> None:
    """Remove an account from ~/.ccb/accounts.json."""
    import json
    from ccb.config import accounts_path, load_accounts
    from ccb.select_ui import select_one

    store = load_accounts()
    accounts = store.get("accounts", {})

    name = name.strip()
    if not name:
        # Interactive picker
        if not accounts:
            print_info("No accounts to remove.")
            return
        names = list(accounts.keys())
        items = [{"label": n, "description": accounts[n].get("baseUrl", "")} for n in names]
        choice = await select_one(items, title="Remove which account?")
        if choice is None:
            return
        name = names[choice]

    if name not in accounts:
        print_error(f"Account '{name}' not found.")
        return

    # Confirm
    confirm_idx = await select_one(
        [{"label": f"Yes — delete {name}"}, {"label": "No — keep it"}],
        title=f"Remove account '{name}'?",
    )
    if confirm_idx != 0:
        print_info("Kept.")
        return

    del accounts[name]
    if store.get("active") == name:
        store.pop("active", None)
        store.pop("activeModel", None)
    accounts_path().write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    )
    print_info(f"Removed account {name}")


def _account_list() -> None:
    """List all configured accounts."""
    from ccb.config import load_accounts, get_active_account
    store = load_accounts()
    accounts = store.get("accounts", {})
    if not accounts:
        print_info("No accounts configured. Use /account add.")
        return
    acct = get_active_account()
    active_name = acct.get("_name", "") if acct else ""
    active_model = store.get("activeModel", "")
    print_info("Accounts:")
    for name, profile in accounts.items():
        is_active = name == active_name
        marker = "●" if is_active else " "
        model_info = f" · {active_model}" if is_active and active_model else f" · {profile.get('defaultModel', '')}"
        print_info(f"{marker} {name}{model_info}")
        print_info(f"  {profile.get('baseUrl', 'default')}")
    print_info("Commands: /account add · /account remove <name> · /account <name>")
