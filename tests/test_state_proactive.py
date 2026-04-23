"""Tests for ccb.state and ccb.proactive modules."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ccb.state import AppState, StateManager, get_state
from ccb.proactive import Suggestion, ProactiveEngine, get_proactive_engine


class TestAppState:
    def test_defaults(self):
        s = AppState()
        assert s.session_id == ""
        assert s.model == ""
        assert s.vim_mode is False
        assert s.total_cost_usd == 0.0

    def test_to_dict(self):
        s = AppState(model="claude-3", cwd="/tmp")
        d = s.to_dict()
        assert d["model"] == "claude-3"
        assert d["cwd"] == "/tmp"
        assert "total_input_tokens" in d


class TestStateManager:
    def test_get_set(self):
        mgr = StateManager()
        mgr.set("model", "gpt-4")
        assert mgr.get("model") == "gpt-4"

    def test_no_op_same_value(self):
        mgr = StateManager()
        mgr.set("model", "gpt-4")
        mgr.set("model", "gpt-4")  # Same value, no notification
        # Only one change in history
        assert len(mgr.recent_changes(10)) == 1

    def test_update_multiple(self):
        mgr = StateManager()
        mgr.update(model="claude-3", cwd="/home")
        assert mgr.get("model") == "claude-3"
        assert mgr.get("cwd") == "/home"

    def test_subscribe_all(self):
        mgr = StateManager()
        changes = []
        mgr.subscribe(lambda k, old, new: changes.append((k, new)))
        mgr.set("model", "test")
        assert len(changes) == 1
        assert changes[0] == ("model", "test")

    def test_subscribe_key(self):
        mgr = StateManager()
        changes = []
        mgr.subscribe(lambda k, old, new: changes.append(new), key="model")
        mgr.set("model", "a")
        mgr.set("cwd", "/tmp")  # Should NOT trigger
        assert len(changes) == 1

    def test_unsubscribe(self):
        mgr = StateManager()
        changes = []
        unsub = mgr.subscribe(lambda k, o, n: changes.append(k))
        mgr.set("model", "a")
        assert len(changes) == 1
        unsub()
        mgr.set("model", "b")
        assert len(changes) == 1  # No new notifications

    def test_snapshot(self):
        mgr = StateManager()
        mgr.set("model", "test")
        snap = mgr.snapshot()
        assert snap["model"] == "test"

    def test_recent_changes(self):
        mgr = StateManager()
        mgr.set("model", "a")
        mgr.set("model", "b")
        changes = mgr.recent_changes(5)
        assert len(changes) == 2
        assert changes[0]["new"] == "a"
        assert changes[1]["new"] == "b"

    def test_history_bounded(self):
        mgr = StateManager()
        for i in range(600):
            mgr.set("message_count", i)
        assert len(mgr._history) <= 400

    def test_save_load(self, tmp_path):
        mgr = StateManager()
        mgr.set("model", "saved-model")
        mgr.set("cwd", "/saved/path")
        path = tmp_path / "state.json"
        mgr.save(path)

        mgr2 = StateManager()
        mgr2.load(path)
        assert mgr2.get("model") == "saved-model"
        assert mgr2.get("cwd") == "/saved/path"

    def test_load_nonexistent(self, tmp_path):
        mgr = StateManager()
        mgr.load(tmp_path / "nonexistent.json")  # Should not crash

    def test_load_corrupted(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        mgr = StateManager()
        mgr.load(path)  # Should not crash


class TestStateSingleton:
    def test_get_state(self):
        s = get_state()
        assert isinstance(s, StateManager)


class TestSuggestion:
    def test_creation(self):
        s = Suggestion(text="Test", category="tip", confidence=0.8)
        assert s.text == "Test"
        assert s.confidence == 0.8


class TestProactiveEngine:
    def test_toggle(self):
        e = ProactiveEngine()
        assert e.enabled is True
        assert e.toggle() is False
        assert e.enabled is False

    def test_disabled_returns_empty(self):
        e = ProactiveEngine()
        e.toggle()  # Disable
        assert e.suggest() == []

    def test_record_action(self):
        e = ProactiveEngine()
        for i in range(110):
            e.record_action(f"action_{i}")
        # History is bounded
        assert len(e._history) <= 60

    def test_suggest_uncommitted(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=True):
            suggestions = e.suggest({"cwd": "/tmp"})
            assert any("/commit" in s.command for s in suggestions)

    def test_suggest_error(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({"last_error": "SomeError occurred"})
            assert any("doctor" in s.command for s in suggestions)

    def test_suggest_long_conversation(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({"message_count": 25})
            assert any("/compact" in s.command for s in suggestions)

    def test_suggest_context_window_full(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({"context_window_usage": 0.9})
            assert any(s.confidence >= 0.9 for s in suggestions)

    def test_suggest_files_changed(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({"files_written": 8})
            assert any("/commit" in s.command for s in suggestions)

    def test_suggest_no_tools(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({"message_count": 10, "tools_called": 0})
            assert any("tool" in s.text.lower() for s in suggestions)

    def test_suggest_repeated_action(self):
        e = ProactiveEngine()
        e._history = ["bash", "bash", "bash"]
        with patch("ccb.proactive._has_uncommitted_changes", return_value=False):
            suggestions = e.suggest({})
            assert any("repeated" in s.text.lower() for s in suggestions)

    def test_sorted_by_confidence(self):
        e = ProactiveEngine()
        with patch("ccb.proactive._has_uncommitted_changes", return_value=True):
            suggestions = e.suggest({
                "message_count": 25,
                "context_window_usage": 0.9,
            })
            if len(suggestions) >= 2:
                for i in range(len(suggestions) - 1):
                    assert suggestions[i].confidence >= suggestions[i + 1].confidence


class TestProactiveSingleton:
    def test_get_proactive_engine(self):
        e = get_proactive_engine()
        assert isinstance(e, ProactiveEngine)
