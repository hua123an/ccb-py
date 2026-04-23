"""Tests for ccb.analytics_tracker module."""
import json
import time
from pathlib import Path

import pytest

from ccb.analytics_tracker import AnalyticsTracker, SessionStats, UsageEvent


@pytest.fixture
def tracker(tmp_path):
    return AnalyticsTracker(data_dir=tmp_path / "analytics")


class TestSessionTracking:
    def test_start_session(self, tracker):
        tracker.start_session("sess-001")
        stats = tracker.get_session_stats()
        assert stats["session_id"] == "sess-001"
        assert stats["start_time"] > 0

    def test_end_session(self, tracker):
        tracker.start_session("sess-002")
        result = tracker.end_session()
        assert result.end_time > 0
        assert result.session_id == "sess-002"

    def test_track_message(self, tracker):
        tracker.start_session("sess-003")
        tracker.track_message()
        tracker.track_message()
        assert tracker.get_session_stats()["messages"] == 2


class TestEventTracking:
    def test_track_tool_call(self, tracker):
        tracker.start_session("t1")
        tracker.track_event("tool_call", "bash")
        tracker.track_event("tool_call", "bash")
        tracker.track_event("tool_call", "file_read")
        stats = tracker.get_session_stats()
        assert stats["tools_used"]["bash"] == 2
        assert stats["tools_used"]["file_read"] == 1

    def test_track_api_call(self, tracker):
        tracker.start_session("t2")
        tracker.track_event("api_call", "anthropic", model="claude-3", input_tokens=100, output_tokens=50)
        stats = tracker.get_session_stats()
        assert stats["turns"] == 1
        assert stats["input_tokens"] == 100
        assert stats["output_tokens"] == 50
        assert "claude-3" in stats["models_used"]

    def test_track_error(self, tracker):
        tracker.start_session("t3")
        tracker.track_event("error", "api_error")
        assert tracker.get_session_stats()["errors"] == 1

    def test_track_cost(self, tracker):
        tracker.start_session("t4")
        tracker.track_cost(0.05)
        tracker.track_cost(0.03)
        assert tracker.get_session_stats()["cost_usd"] == pytest.approx(0.08)

    def test_disabled_tracker(self, tracker):
        tracker.enabled = False
        tracker.start_session("t5")
        tracker.track_event("tool_call", "bash")
        assert tracker.get_session_stats()["tools_used"] == {}


class TestLatency:
    def test_track_latency(self, tracker):
        start = time.time() - 0.1  # 100ms ago
        latency = tracker.track_latency("api_call", start)
        assert latency > 0

    def test_latency_stats(self, tracker):
        for i in range(5):
            tracker.track_event("latency", "api_call", latency_ms=100 + i * 10)
        stats = tracker.get_latency_stats()
        assert "api_call" in stats
        assert stats["api_call"]["count"] == 5


class TestHistorical:
    def test_historical_aggregation(self, tmp_path):
        analytics_dir = tmp_path / "analytics"
        analytics_dir.mkdir()

        # Create fake session files
        for i in range(3):
            data = {
                "session_id": f"s{i}",
                "start_time": time.time() - 86400,
                "messages": 10,
                "turns": 5,
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost_usd": 0.1,
                "errors": 0,
                "tools_used": {"bash": 3},
                "commands_used": {},
                "models_used": {},
            }
            (analytics_dir / f"s{i}.json").write_text(json.dumps(data))

        tracker = AnalyticsTracker(data_dir=analytics_dir)
        stats = tracker.get_historical_stats(days=7)
        assert stats["sessions"] == 3
        assert stats["messages"] == 30
        assert stats["input_tokens"] == 3000


class TestExport:
    def test_csv_export(self, tmp_path):
        analytics_dir = tmp_path / "analytics"
        analytics_dir.mkdir()
        data = {
            "session_id": "test",
            "start_time": 1234,
            "messages": 5,
            "turns": 3,
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": 0.01,
            "errors": 0,
        }
        (analytics_dir / "test.json").write_text(json.dumps(data))

        tracker = AnalyticsTracker(data_dir=analytics_dir)
        csv = tracker.export_csv()
        assert "test" in csv
        assert "500" in csv


class TestLangfuse:
    def test_init_without_package(self, tracker):
        result = tracker.init_langfuse("pub", "sec")
        # Will fail because langfuse isn't installed — that's expected
        assert result is False

    def test_trace_without_init(self, tracker):
        assert tracker.langfuse_trace("test") is None
        assert tracker.langfuse_generation("t", "n", "m", "in", "out") is None

    def test_flush_without_init(self, tracker):
        tracker.langfuse_flush()  # Should not raise
