"""Tests for ccb.session module."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ccb.session import Session
from ccb.api.base import Message, Role, ToolCall, ToolResult


@pytest.fixture
def session():
    return Session(id="test-session", cwd="/tmp/test", model="claude-3")


class TestSessionBasic:
    def test_creation(self, session):
        assert session.id == "test-session"
        assert session.cwd == "/tmp/test"
        assert session.model == "claude-3"
        assert session.created_at > 0
        assert len(session.messages) == 0

    def test_add_user_message(self, session):
        session.add_user_message("Hello")
        assert len(session.messages) == 1
        assert session.messages[0].role == Role.USER
        assert session.messages[0].content == "Hello"

    def test_add_assistant_message(self, session):
        session.add_assistant_message("Hi there!")
        assert len(session.messages) == 1
        assert session.messages[0].role == Role.ASSISTANT

    def test_add_with_tool_calls(self, session):
        tc = [ToolCall(id="tc1", name="bash", input={"command": "ls"})]
        session.add_assistant_message("Running...", tool_calls=tc)
        assert len(session.messages[0].tool_calls) == 1

    def test_add_tool_results(self, session):
        results = [ToolResult(tool_use_id="tc1", content="file.txt")]
        session.add_tool_results(results)
        assert len(session.messages) == 1
        assert session.messages[0].tool_results[0].content == "file.txt"

    def test_add_usage(self, session):
        session.add_usage({"input_tokens": 100, "output_tokens": 50})
        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 50
        assert session.last_input_tokens == 100

        session.add_usage({"input_tokens": 200, "output_tokens": 80})
        assert session.total_input_tokens == 300
        assert session.total_output_tokens == 130
        assert session.last_input_tokens == 200


class TestSessionSerialization:
    def test_to_dict(self, session):
        session.add_user_message("Hi")
        session.add_assistant_message("Hello!")
        d = session._to_dict()
        assert d["id"] == "test-session"
        assert len(d["messages"]) == 2

    def test_roundtrip(self, session):
        session.add_user_message("Hello", images=[{"type": "base64", "data": "abc"}])
        tc = [ToolCall(id="tc1", name="bash", input={"command": "ls"})]
        session.add_assistant_message("Running...", tool_calls=tc)
        session.add_tool_results([ToolResult(tool_use_id="tc1", content="output")])

        d = session._to_dict()
        restored = Session._from_dict(d)
        assert restored.id == session.id
        assert len(restored.messages) == 3
        assert restored.messages[0].content == "Hello"
        assert len(restored.messages[1].tool_calls) == 1
        assert restored.messages[1].tool_calls[0].name == "bash"
        assert len(restored.messages[2].tool_results) == 1

    def test_from_dict_missing_fields(self):
        d = {"id": "minimal", "messages": []}
        s = Session._from_dict(d)
        assert s.id == "minimal"
        assert s.cwd == ""
        assert s.model == ""


class TestSessionPersistence:
    def test_save_and_load(self, session, tmp_path):
        session.add_user_message("Test message")
        with patch("ccb.session.claude_dir", return_value=tmp_path):
            path = session.save()
            assert path.exists()

            loaded = Session.load("test-session")
            assert loaded is not None
            assert loaded.id == "test-session"
            assert len(loaded.messages) == 1

    def test_load_nonexistent(self, tmp_path):
        with patch("ccb.session.claude_dir", return_value=tmp_path):
            assert Session.load("nonexistent") is None

    def test_load_corrupted(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "bad.json").write_text("not json")
        with patch("ccb.session.claude_dir", return_value=tmp_path):
            assert Session.load("bad") is None

    def test_list_sessions(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(3):
            data = {"id": f"s{i}", "cwd": "/tmp", "model": "claude-3",
                    "messages": [], "updated_at": time.time() - i * 100}
            (sessions_dir / f"s{i}.json").write_text(json.dumps(data))

        with patch("ccb.session.claude_dir", return_value=tmp_path):
            sessions = Session.list_sessions()
            assert len(sessions) == 3
            # Should be sorted by modification time
            assert sessions[0]["id"] in ("s0", "s1", "s2")

    def test_list_sessions_empty(self, tmp_path):
        with patch("ccb.session.claude_dir", return_value=tmp_path):
            assert Session.list_sessions() == []


class TestMessageSerialization:
    def test_msg_to_dict_basic(self):
        m = Message(role=Role.USER, content="Hello")
        d = Session._msg_to_dict(m)
        assert d["role"] == "user"
        assert d["content"] == "Hello"

    def test_msg_to_dict_with_images(self):
        m = Message(role=Role.USER, content="", images=[{"type": "url", "url": "http://img"}])
        d = Session._msg_to_dict(m)
        assert len(d["images"]) == 1

    def test_msg_from_dict_error_result(self):
        d = {
            "role": "user",
            "content": "",
            "tool_results": [{"tool_use_id": "t1", "content": "error", "is_error": True}],
        }
        m = Session._msg_from_dict(d)
        assert m.tool_results[0].is_error is True
