"""PolicyLimits — organization-level policy restrictions from API.

Fetches org policy restrictions and uses them to disable CLI features.
Follows patterns: fail open, ETag caching, background polling, retry.

Eligibility:
- Console users (API key): All eligible
- OAuth users: Only Team and Enterprise subscribers
- API fails open — if fetch fails, continues without restrictions
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POLL_INTERVAL = 300  # 5 minutes
CACHE_FILE = Path.home() / ".claude" / "policy-limits-cache.json"


@dataclass
class PolicyRestriction:
    """A single policy restriction."""
    key: str
    allowed: bool = True


@dataclass
class PolicyLimitsState:
    """Current state of policy limits."""
    restrictions: dict[str, PolicyRestriction] = field(default_factory=dict)
    last_fetched: float = 0.0
    etag: str = ""
    fetch_errors: int = 0
    initialized: bool = False

    def is_allowed(self, feature: str) -> bool:
        """Check if a feature is allowed by policy. Fails open (allowed if unknown)."""
        r = self.restrictions.get(feature)
        if r is None:
            return True  # fail open
        return r.allowed

    def blocked_features(self) -> list[str]:
        """List all blocked features."""
        return [k for k, v in self.restrictions.items() if not v.allowed]


class PolicyLimitsService:
    """Manages organization policy limits."""

    def __init__(self, api_base: str = "https://api.anthropic.com") -> None:
        self._state = PolicyLimitsState()
        self._api_base = api_base
        self._load_cache()

    def _cache_path(self) -> Path:
        return CACHE_FILE

    def _load_cache(self) -> None:
        """Load cached policy limits from disk."""
        cp = self._cache_path()
        if cp.exists():
            try:
                data = json.loads(cp.read_text())
                for key, val in data.get("restrictions", {}).items():
                    self._state.restrictions[key] = PolicyRestriction(
                        key=key,
                        allowed=val.get("allowed", True),
                    )
                self._state.etag = data.get("etag", "")
                self._state.last_fetched = data.get("last_fetched", 0.0)
                self._state.initialized = True
            except Exception as e:
                logger.debug("Failed to load policy cache: %s", e)

    def _save_cache(self) -> None:
        """Persist policy limits to disk."""
        cp = self._cache_path()
        cp.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "restrictions": {
                k: {"allowed": v.allowed}
                for k, v in self._state.restrictions.items()
            },
            "etag": self._state.etag,
            "last_fetched": self._state.last_fetched,
        }
        cp.write_text(json.dumps(data, indent=2))

    async def fetch(self, api_key: str = "") -> bool:
        """Fetch policy limits from API. Returns True if updated."""
        try:
            import aiohttp
        except ImportError:
            logger.debug("aiohttp not available for policy fetch")
            return False

        url = f"{self._api_base}/v1/organizations/policy-limits"
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        if self._state.etag:
            headers["If-None-Match"] = self._state.etag

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 304:
                        # Not modified
                        self._state.last_fetched = time.time()
                        return False

                    if resp.status == 200:
                        data = await resp.json()
                        new_etag = resp.headers.get("ETag", "")

                        self._state.restrictions.clear()
                        for key, val in data.get("restrictions", {}).items():
                            self._state.restrictions[key] = PolicyRestriction(
                                key=key,
                                allowed=val.get("allowed", True),
                            )
                        self._state.etag = new_etag
                        self._state.last_fetched = time.time()
                        self._state.fetch_errors = 0
                        self._state.initialized = True
                        self._save_cache()
                        return True

                    # Non-200/304: fail open
                    logger.debug("Policy fetch returned %d", resp.status)
                    self._state.fetch_errors += 1
                    return False

        except Exception as e:
            # Fail open
            logger.debug("Policy fetch error: %s", e)
            self._state.fetch_errors += 1
            return False

    def should_poll(self) -> bool:
        """Check if it's time to poll for updates."""
        return time.time() - self._state.last_fetched >= POLL_INTERVAL

    def is_allowed(self, feature: str) -> bool:
        """Check if a feature is allowed. Fails open."""
        return self._state.is_allowed(feature)

    def blocked_features(self) -> list[str]:
        return self._state.blocked_features()

    def summary(self) -> dict[str, Any]:
        return {
            "initialized": self._state.initialized,
            "last_fetched": self._state.last_fetched,
            "restrictions_count": len(self._state.restrictions),
            "blocked": self.blocked_features(),
            "fetch_errors": self._state.fetch_errors,
        }


# ── Module singleton ───────────────────────────────────────────

_service: PolicyLimitsService | None = None


def get_policy_limits() -> PolicyLimitsService:
    global _service
    if _service is None:
        _service = PolicyLimitsService()
    return _service
