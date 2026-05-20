"""GrowthBook-compatible feature flag system for ccb-py.

Supports remote flag evaluation via GrowthBook API, local overrides via
environment variables (``CCB_FLAG_<NAME>=1``) and a JSON file at
``~/.ccb/feature_flags.json``.

Auto-refreshes remote flags every 5 minutes in the background.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ccb.json_store import read_json, write_json

log = logging.getLogger(__name__)

_HAS_HTTPX = True
try:
    import httpx
except ImportError:
    _HAS_HTTPX = False

_FLAGS_PATH = Path.home() / ".ccb" / "feature_flags.json"
_REFRESH_INTERVAL = 300.0  # 5 minutes

DEFAULT_FEATURE_FLAGS: dict[str, Any] = {
    "tengu_kairos_enabled": False,
    "tengu_kairos_cron": True,
    "tengu_onyx_plover": {"enabled": True, "minHours": 24, "minSessions": 5},
    "tengu_ultraplan_enabled": True,
    "tengu_buddy_enabled": True,
    "tengu_auto_memory_enabled": True,
    "tengu_magic_docs_enabled": True,
    "tengu_context_collapse_enabled": True,
    "tengu_tool_use_summary_enabled": True,
    "tengu_policy_limits_enabled": True,
    "tengu_tips_enabled": True,
    "tengu_agent_summary_enabled": True,
    "tengu_jobs_enabled": True,
    "tengu_daemon_enabled": True,
    "tengu_plugins_enabled": True,
    "tengu_mcp_enabled": True,
    "tengu_hooks_enabled": True,
    "tengu_sandbox_enabled": True,
    "tengu_voice_enabled": True,
    "tengu_bridge_enabled": True,
    "tengu_remote_enabled": True,
    "tengu_lsp_enabled": True,
    "tengu_github_enabled": True,
    "tengu_oauth_enabled": True,
    "tengu_memory_decay_enabled": True,
    "tengu_session_transcript_enabled": True,
    "tengu_upstream_proxy_enabled": False,
    "tengu_assistant_panel_enabled": True,
    "tengu_background_forked_commands": True,
    "tengu_scheduled_task_missed_prompt": True,
    "tengu_cron_lock_enabled": True,
    "tengu_cron_recurring_expiry_hours": 168,
    "tengu_pipe_chain_enabled": True,
    "tengu_web_tools_enabled": True,
    "tengu_computer_tools_enabled": True,
    "tengu_notebook_tools_enabled": True,
    "tengu_image_tools_enabled": True,
    "tengu_agent_tool_enabled": True,
    "tengu_task_budget_enabled": True,
    "tengu_guardrails_enabled": True,
    "tengu_settings_sync_enabled": True,
    "tengu_proactive_enabled": True,
    "tengu_fast_mode_enabled": True,
    "tengu_bedrock_enabled": True,
    "tengu_vertex_enabled": True,
    "tengu_gemini_enabled": True,
    "tengu_openai_compat_enabled": True,
}

FLAG_ALIASES: dict[str, str] = {
    "kairos": "tengu_kairos_enabled",
    "kairos_cron": "tengu_kairos_cron",
    "auto_dream": "tengu_onyx_plover",
    "ultraplan": "tengu_ultraplan_enabled",
    "buddy": "tengu_buddy_enabled",
    "scheduled_tasks": "tengu_kairos_cron",
}


class FeatureFlags:
    """Feature flag client with local + remote evaluation.

    Priority (highest wins):
      1. Environment variables ``CCB_FLAG_<UPPER_NAME>=1|0|<json_value>``
      2. Local overrides file ``~/.ccb/feature_flags.json``
      3. Remote flags fetched from GrowthBook (or compatible API)

    Remote flags are fetched in a background daemon thread every
    *refresh_interval* seconds (default 300 = 5 minutes).
    """

    def __init__(
        self,
        api_host: str = "",
        client_key: str = "",
        refresh_interval: float = _REFRESH_INTERVAL,
    ) -> None:
        self.api_host = (api_host or os.environ.get("GROWTHBOOK_API_HOST", "")).rstrip("/")
        self.client_key = client_key or os.environ.get("GROWTHBOOK_CLIENT_KEY", "")
        self.refresh_interval = refresh_interval

        self._remote_flags: dict[str, Any] = {}
        self._local_overrides: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last_refresh: float = 0.0

        # Load local overrides immediately
        self._load_local_overrides()

        # Initial remote fetch + start refresh timer
        if self.api_host and self.client_key and _HAS_HTTPX:
            self._fetch_remote()
            self._start_timer()
        elif not _HAS_HTTPX:
            log.debug("FeatureFlags: httpx not installed, remote flags disabled")
        elif not (self.api_host and self.client_key):
            log.debug("FeatureFlags: no GrowthBook config, using local flags only")

    # ── Public API ───────────────────────────────────────────────

    def is_enabled(self, flag_name: str, default: bool = False) -> bool:
        """Check if a boolean feature flag is enabled.

        Resolution order: env var > local file > remote > default.
        """
        value = self._resolve(flag_name)
        if value is None:
            return default
        return bool(value)

    def get_value(self, flag_name: str, default: Any = None) -> Any:
        """Get the full value of a feature flag (any JSON type).

        Resolution order: env var > local file > remote > default.
        """
        value = self._resolve(flag_name)
        return value if value is not None else default

    def list_flags(self) -> dict[str, Any]:
        """Return all known flags (merged local + remote), excluding env overrides."""
        with self._lock:
            merged = dict(DEFAULT_FEATURE_FLAGS)
            merged.update(self._remote_flags)
            merged.update(self._local_overrides)
        return merged

    def set_override(self, flag_name: str, value: Any) -> None:
        """Set a local override and persist to the flags file."""
        with self._lock:
            self._local_overrides[flag_name] = value
        self._save_local_overrides()

    def remove_override(self, flag_name: str) -> bool:
        """Remove a local override. Returns True if it existed."""
        with self._lock:
            removed = self._local_overrides.pop(flag_name, None)
        if removed is not None:
            self._save_local_overrides()
            return True
        return False

    def refresh(self) -> None:
        """Manually trigger a remote flag refresh."""
        self._load_local_overrides()
        if self.api_host and self.client_key:
            self._fetch_remote()

    def shutdown(self) -> None:
        """Stop the background refresh timer."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

    # ── Private helpers ──────────────────────────────────────────

    def _resolve(self, flag_name: str) -> Any:
        """Resolve a flag through the priority chain."""
        flag_name = FLAG_ALIASES.get(flag_name, flag_name)
        # 1. Environment variable override: CCB_FLAG_<UPPER_NAME>
        env_suffix = flag_name.upper().replace("-", "_")
        for env_key in (f"CCB_FLAG_{env_suffix}", f"CLAUDE_CODE_FLAG_{env_suffix}"):
            env_val = os.environ.get(env_key)
            if env_val is not None:
                return _parse_env_value(env_val)

        # 2. Local overrides file
        with self._lock:
            if flag_name in self._local_overrides:
                return self._local_overrides[flag_name]

        # 3. Remote flags
        with self._lock:
            if flag_name in self._remote_flags:
                flag = self._remote_flags[flag_name]
                if isinstance(flag, dict):
                    return flag.get("defaultValue", flag.get("value"))
                return flag

        return DEFAULT_FEATURE_FLAGS.get(flag_name)

    def _load_local_overrides(self) -> None:
        """Load overrides from ~/.ccb/feature_flags.json."""
        data = read_json(_FLAGS_PATH)
        if isinstance(data, dict):
            with self._lock:
                self._local_overrides = data

    def _save_local_overrides(self) -> None:
        """Persist local overrides to disk."""
        try:
            with self._lock:
                data = dict(self._local_overrides)
            write_json(_FLAGS_PATH, data, ensure_ascii=False)
        except OSError as exc:
            log.debug("Failed to save feature_flags.json: %s", exc)

    def _fetch_remote(self) -> None:
        """Fetch flags from GrowthBook API."""
        if not _HAS_HTTPX:
            return
        try:
            url = f"{self.api_host}/api/features/{self.client_key}"
            with httpx.Client(timeout=10) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    features = data.get("features", data)
                    if isinstance(features, dict):
                        with self._lock:
                            self._remote_flags = features
                        self._last_refresh = time.time()
                        log.debug(
                            "FeatureFlags: loaded %d remote flags", len(features)
                        )
                else:
                    log.debug(
                        "FeatureFlags: remote fetch returned %d", resp.status_code
                    )
        except Exception as exc:
            log.debug("FeatureFlags: remote fetch failed: %s", exc)

    def _start_timer(self) -> None:
        self._timer = threading.Timer(self.refresh_interval, self._timer_tick)
        self._timer.daemon = True
        self._timer.start()

    def _timer_tick(self) -> None:
        try:
            self._fetch_remote()
        finally:
            self._start_timer()


# ── Module-level singleton ───────────────────────────────────────

_flags: FeatureFlags | None = None


def get_flags() -> FeatureFlags:
    """Get or create the global FeatureFlags singleton."""
    global _flags
    if _flags is None:
        _flags = FeatureFlags()
    return _flags


def init_flags(**kwargs: Any) -> FeatureFlags:
    """Initialize (or re-initialize) the global flags client."""
    global _flags
    if _flags is not None:
        _flags.shutdown()
    _flags = FeatureFlags(**kwargs)
    return _flags


def is_feature_enabled(flag_name: str, default: bool = False) -> bool:
    return get_flags().is_enabled(flag_name, default)


def get_feature_value(flag_name: str, default: Any = None) -> Any:
    return get_flags().get_value(flag_name, default)


# ── Utilities ────────────────────────────────────────────────────

def _parse_env_value(val: str) -> Any:
    """Parse an environment variable flag value.

    ``1``, ``true``, ``yes`` -> True
    ``0``, ``false``, ``no`` -> False
    Otherwise try JSON parsing, fall back to raw string.
    """
    lower = val.strip().lower()
    if lower in ("1", "true", "yes", "on"):
        return True
    if lower in ("0", "false", "no", "off", ""):
        return False
    try:
        return json.loads(val)
    except (json.JSONDecodeError, ValueError):
        return val
