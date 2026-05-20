"""Tests for enterprise integrations: Langfuse, Sentry, and Feature Flags."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch
import pytest


# ═══════════════════════════════════════════════════════════════════
# Langfuse Monitor
# ═══════════════════════════════════════════════════════════════════

class TestLangfuseMonitor:
    def test_disabled_without_credentials(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        mon = LangfuseMonitor()
        assert mon.enabled is False

    def test_disabled_without_httpx(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", False):
            mon = LangfuseMonitor(api_key="k", public_key="pk")
            assert mon.enabled is False

    def test_enabled_with_credentials(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="secret", public_key="pub")
            assert mon.enabled is True

    def test_trace_start_returns_id(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            tid = mon.trace_start(name="test")
            assert len(tid) == 32  # uuid hex
            assert tid in mon._traces

    def test_trace_start_custom_id(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            tid = mon.trace_start(trace_id="custom123", name="test")
            assert tid == "custom123"

    def test_trace_end_removes_trace(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            tid = mon.trace_start(name="t")
            assert tid in mon._traces
            mon.trace_end(tid)
            assert tid not in mon._traces

    def test_trace_start_noop_when_disabled(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        mon = LangfuseMonitor()
        assert mon.trace_start(name="t") == ""

    def test_span_lifecycle(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            tid = mon.trace_start(name="t")
            sid = mon.span_start(tid, "read_file", {"path": "/tmp"})
            assert len(sid) == 32
            assert sid in mon._spans
            mon.span_end(sid, output="ok", metadata={"size": 100})
            assert sid not in mon._spans

    def test_span_noop_when_disabled(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        mon = LangfuseMonitor()
        assert mon.span_start("parent", "name") == ""

    def test_generation_log_buffers(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            tid = mon.trace_start(name="t")
            mon.generation_log(tid, model="claude-3", input_tokens=100, output_tokens=50, latency_ms=200)
            assert len(mon._buffer) >= 1

    def test_flush_sends_batch(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            mon.trace_start(name="t")
            assert len(mon._buffer) > 0
            with patch.object(mon, "_send_batch") as mock_send:
                mon.flush()
                mock_send.assert_called_once()
            assert len(mon._buffer) == 0

    def test_auto_flush_at_batch_size(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p", batch_size=2)
            with patch.object(mon, "_send_batch") as mock_send:
                mon.trace_start(name="t1")
                mon.trace_start(name="t2")
                mock_send.assert_called()

    def test_shutdown_flushes(self):
        from ccb.langfuse_monitor import LangfuseMonitor
        with patch("ccb.langfuse_monitor._HAS_HTTPX", True):
            mon = LangfuseMonitor(api_key="s", public_key="p")
            mon.trace_start(name="t")
            with patch.object(mon, "_send_batch"):
                mon.shutdown()
            assert mon._timer is None

    def test_singleton_get_monitor(self):
        import ccb.langfuse_monitor as mod
        mod._monitor = None
        m = mod.get_monitor()
        assert m is not None
        assert mod.get_monitor() is m  # same instance

    def test_init_monitor_replaces(self):
        import ccb.langfuse_monitor as mod
        mod._monitor = None
        m1 = mod.init_monitor()
        m2 = mod.init_monitor(api_key="s", public_key="p")
        assert m1 is not m2


# ═══════════════════════════════════════════════════════════════════
# Sentry Integration
# ═══════════════════════════════════════════════════════════════════

class TestSentryIntegration:
    def test_not_initialized_by_default(self):
        from ccb.sentry_integration import is_initialized
        # Reset state
        import ccb.sentry_integration as mod
        mod._initialized = False
        assert is_initialized() is False

    def test_init_without_sdk(self):
        from ccb.sentry_integration import init_sentry
        with patch("ccb.sentry_integration._HAS_SENTRY", False):
            assert init_sentry(dsn="https://example.com") is False

    def test_init_without_dsn(self):
        from ccb.sentry_integration import init_sentry
        with patch("ccb.sentry_integration._HAS_SENTRY", True):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SENTRY_DSN", None)
                assert init_sentry(dsn="") is False

    def test_capture_exception_noop_when_disabled(self):
        from ccb.sentry_integration import capture_exception
        import ccb.sentry_integration as mod
        mod._initialized = False
        result = capture_exception(ValueError("test"))
        assert result is None

    def test_capture_message_noop_when_disabled(self):
        from ccb.sentry_integration import capture_message
        import ccb.sentry_integration as mod
        mod._initialized = False
        result = capture_message("hello")
        assert result is None

    def test_set_user_noop_when_disabled(self):
        from ccb.sentry_integration import set_user
        import ccb.sentry_integration as mod
        mod._initialized = False
        set_user("u1", "alice")  # should not raise

    def test_add_breadcrumb_noop_when_disabled(self):
        from ccb.sentry_integration import add_breadcrumb
        import ccb.sentry_integration as mod
        mod._initialized = False
        add_breadcrumb("tool", "executed")  # should not raise

    def test_tool_span_yields_when_disabled(self):
        from ccb.sentry_integration import tool_span
        import ccb.sentry_integration as mod
        mod._initialized = False
        entered = False
        with tool_span("test"):
            entered = True
        assert entered is True

    def test_before_send_filters_keyboard_interrupt(self):
        from ccb.sentry_integration import _before_send
        event = {"exception": {}}
        hint = {"exc_info": (KeyboardInterrupt, KeyboardInterrupt(), None)}
        assert _before_send(event, hint) is None

    def test_before_send_passes_normal(self):
        from ccb.sentry_integration import _before_send
        event = {"exception": {}}
        hint = {"exc_info": (ValueError, ValueError("x"), None)}
        result = _before_send(event, hint)
        assert result is event

    def test_capture_exception_with_context(self):
        """Test that capture_exception passes context when sentry is initialized."""
        from ccb.sentry_integration import capture_exception
        import ccb.sentry_integration as mod
        mock_scope = MagicMock()
        mock_scope.__enter__ = MagicMock(return_value=mock_scope)
        mock_scope.__exit__ = MagicMock(return_value=False)
        mock_configure = MagicMock(return_value=mock_scope)
        mock_capture = MagicMock(return_value="evt123")
        old_configure = getattr(mod, "_sdk_configure_scope", None)
        old_capture = getattr(mod, "_sdk_capture_exception", None)
        old_has_sentry = mod._HAS_SENTRY
        old_initialized = mod._initialized
        mod._HAS_SENTRY = True
        mod._initialized = True
        mod._sdk_configure_scope = mock_configure
        mod._sdk_capture_exception = mock_capture
        try:
            result = capture_exception(ValueError("bad"), context={"tool": "read"})
            assert result == "evt123"
            mock_scope.set_extra.assert_called_with("tool", "read")
        finally:
            mod._HAS_SENTRY = old_has_sentry
            mod._initialized = old_initialized
            if old_configure is not None:
                mod._sdk_configure_scope = old_configure
            if old_capture is not None:
                mod._sdk_capture_exception = old_capture


# ═══════════════════════════════════════════════════════════════════
# Feature Flags
# ═══════════════════════════════════════════════════════════════════

class TestFeatureFlags:
    def test_local_flags_from_json(self, tmp_path):
        from ccb.feature_flags import FeatureFlags
        flags_file = tmp_path / "flags.json"
        flags_file.write_text(json.dumps({"dark_mode": True, "max_items": 42}))
        with patch("ccb.feature_flags._FLAGS_PATH", flags_file):
            ff = FeatureFlags()
            assert ff.is_enabled("dark_mode") is True
            assert ff.get_value("max_items") == 42

    def test_env_override_takes_priority(self):
        from ccb.feature_flags import FeatureFlags
        with patch.dict(os.environ, {"CCB_FLAG_MY_FEAT": "1"}):
            ff = FeatureFlags()
            assert ff.is_enabled("my_feat") is True

    def test_env_override_false(self):
        from ccb.feature_flags import FeatureFlags
        ff = FeatureFlags()
        ff.set_override("x", True)
        with patch.dict(os.environ, {"CCB_FLAG_X": "0"}):
            assert ff.is_enabled("x") is False

    def test_default_when_not_set(self):
        from ccb.feature_flags import FeatureFlags
        ff = FeatureFlags()
        assert ff.is_enabled("nonexistent") is False
        assert ff.is_enabled("nonexistent", default=True) is True
        assert ff.get_value("missing", default="fallback") == "fallback"

    def test_set_and_remove_override(self, tmp_path):
        from ccb.feature_flags import FeatureFlags
        flags_file = tmp_path / "flags.json"
        with patch("ccb.feature_flags._FLAGS_PATH", flags_file):
            ff = FeatureFlags()
            ff.set_override("my_flag", True)
            assert ff.is_enabled("my_flag") is True
            assert flags_file.exists()
            removed = ff.remove_override("my_flag")
            assert removed is True
            assert ff.is_enabled("my_flag", default=False) is False

    def test_remove_nonexistent_returns_false(self, tmp_path):
        from ccb.feature_flags import FeatureFlags
        flags_file = tmp_path / "flags.json"
        with patch("ccb.feature_flags._FLAGS_PATH", flags_file):
            ff = FeatureFlags()
            assert ff.remove_override("nope") is False

    def test_list_flags_merges(self):
        from ccb.feature_flags import FeatureFlags
        ff = FeatureFlags()
        ff._remote_flags = {"remote_a": {"defaultValue": True}}
        ff._local_overrides = {"local_b": "hello"}
        flags = ff.list_flags()
        assert "remote_a" in flags
        assert "local_b" in flags

    def test_parse_env_value_truthy(self):
        from ccb.feature_flags import _parse_env_value
        assert _parse_env_value("1") is True
        assert _parse_env_value("true") is True
        assert _parse_env_value("YES") is True
        assert _parse_env_value("on") is True

    def test_parse_env_value_falsy(self):
        from ccb.feature_flags import _parse_env_value
        assert _parse_env_value("0") is False
        assert _parse_env_value("false") is False
        assert _parse_env_value("no") is False
        assert _parse_env_value("") is False

    def test_parse_env_value_json(self):
        from ccb.feature_flags import _parse_env_value
        assert _parse_env_value('{"a":1}') == {"a": 1}
        assert _parse_env_value("[1,2]") == [1, 2]

    def test_parse_env_value_string_fallback(self):
        from ccb.feature_flags import _parse_env_value
        assert _parse_env_value("hello world") == "hello world"

    def test_remote_flag_dict_resolution(self):
        from ccb.feature_flags import FeatureFlags
        ff = FeatureFlags()
        ff._remote_flags = {"feat": {"defaultValue": 42, "value": 99}}
        assert ff.get_value("feat") == 42  # defaultValue used

    def test_singleton(self):
        import ccb.feature_flags as mod
        mod._flags = None
        f = mod.get_flags()
        assert f is not None
        assert mod.get_flags() is f

    def test_init_flags_replaces(self):
        import ccb.feature_flags as mod
        mod._flags = None
        f1 = mod.init_flags()
        f2 = mod.init_flags(api_host="http://x", client_key="k")
        assert f1 is not f2

    def test_shutdown_stops_timer(self):
        from ccb.feature_flags import FeatureFlags
        ff = FeatureFlags()
        mock_timer = MagicMock()
        ff._timer = mock_timer
        ff.shutdown()
        mock_timer.cancel.assert_called_once()
        assert ff._timer is None

    def test_refresh_loads_local(self, tmp_path):
        from ccb.feature_flags import FeatureFlags
        flags_file = tmp_path / "flags.json"
        flags_file.write_text('{"refreshed": true}')
        with patch("ccb.feature_flags._FLAGS_PATH", flags_file):
            ff = FeatureFlags()
            ff._local_overrides = {}
            ff.refresh()
            assert ff.is_enabled("refreshed") is True


# ═══════════════════════════════════════════════════════════════════
# Integration: commands.py /flags command
# ═══════════════════════════════════════════════════════════════════

class TestFlagsCommand:
    @pytest.mark.asyncio
    async def test_flags_list(self):
        """Test /flags command lists flags."""
        from ccb.commands import handle_command
        session = MagicMock()
        provider = MagicMock()
        registry = MagicMock()
        import ccb.feature_flags as mod
        mod._flags = None
        ff = mod.init_flags()
        ff.set_override("test_flag", True)
        result = await handle_command("/flags", session, provider, registry, "/tmp")
        assert result is True

    @pytest.mark.asyncio
    async def test_flags_toggle(self):
        """Test /flags toggle <name>."""
        from ccb.commands import handle_command
        session = MagicMock()
        provider = MagicMock()
        registry = MagicMock()
        import ccb.feature_flags as mod
        mod._flags = None
        ff = mod.init_flags()
        # Set up a flag to toggle
        ff.set_override("toggle_me", True)
        result = await handle_command("/flags toggle toggle_me", session, provider, registry, "/tmp")
        assert result is True

    @pytest.mark.asyncio
    async def test_flags_set(self):
        """Test /flags set <name> <value>."""
        from ccb.commands import handle_command
        session = MagicMock()
        provider = MagicMock()
        registry = MagicMock()
        import ccb.feature_flags as mod
        mod._flags = None
        ff = mod.init_flags()
        result = await handle_command("/flags set new_flag on", session, provider, registry, "/tmp")
        assert result is True
        assert ff.is_enabled("new_flag") is True

    @pytest.mark.asyncio
    async def test_skills_list_command(self):
        from ccb.commands import handle_command
        from ccb.skills import Skill

        session = MagicMock()
        provider = MagicMock()
        registry = MagicMock()
        skills = [
            Skill(name="review", description="Review code", prompt="p", source="bundled", kind="skill"),
        ]
        with (
            patch("ccb.skills.load_skills", return_value=skills),
            patch("ccb.commands.console.print") as print_fn,
        ):
            result = await handle_command("/skills", session, provider, registry, "/tmp")

        assert result is True
        assert print_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_workflows_list_command(self):
        from ccb.commands import handle_command
        from ccb.skills import Skill

        session = MagicMock()
        provider = MagicMock()
        registry = MagicMock()
        workflows = [
            Skill(name="shipit", description="Release workflow", prompt="p", source="project", kind="workflow"),
        ]
        with (
            patch("ccb.skills.load_skills", return_value=workflows),
            patch("ccb.commands.console.print") as print_fn,
        ):
            result = await handle_command("/workflows", session, provider, registry, "/tmp")

        assert result is True
        assert print_fn.call_count == 1


# ═══════════════════════════════════════════════════════════════════
# Integration: loop.py auto-logging hooks
# ═══════════════════════════════════════════════════════════════════

class TestLoopIntegration:
    def test_langfuse_hook_import(self):
        """Verify the Langfuse hook can be imported from loop."""
        # This just tests the import path exists
        import importlib
        mod = importlib.import_module("ccb.loop")
        assert hasattr(mod, "run_turn")

    def test_sentry_hook_import(self):
        """Verify sentry_integration can be imported."""
        import importlib
        mod = importlib.import_module("ccb.sentry_integration")
        assert hasattr(mod, "capture_exception")
        assert hasattr(mod, "tool_span")
