from __future__ import annotations

import json

from ccb.api.base import Message, Role
from ccb.session import Session
from ccb.session_repository import (
    list_persisted_sessions,
    list_sessions_with_active,
    load_serialized_session,
    load_session,
    save_session,
)


def test_list_sessions_with_active_includes_in_memory_sessions(monkeypatch):
    active = {"s1": Session(id="s1", cwd="/tmp/project", model="m")}
    active["s1"].messages = [Message(role=Role.USER, content="hello")]

    monkeypatch.setattr("ccb.session.Session.list_sessions", lambda limit=20, cwd=None: [])

    sessions = list_sessions_with_active(active_sessions=active)

    assert sessions[0]["id"] == "s1"
    assert sessions[0]["cwd"] == "/tmp/project"
    assert sessions[0]["messages"] == 1


def test_load_serialized_session_prefers_active_session():
    active = {"s1": Session(id="s1", cwd="/tmp/project", model="m")}
    active["s1"].messages = [Message(role=Role.USER, content="hello")]

    payload = load_serialized_session("s1", active_sessions=active)

    assert payload is not None
    assert payload["session_id"] == "s1"
    assert payload["messages"][0]["content"] == "hello"


def test_save_and_load_session_round_trip(tmp_path, monkeypatch):
    session = Session(id="s1", cwd="/tmp/project", model="m")
    session.messages = [Message(role=Role.USER, content="hello")]

    monkeypatch.setattr("ccb.session.claude_dir", lambda: tmp_path)

    path = save_session(session)
    loaded = load_session("s1")

    assert path.exists()
    assert loaded is not None
    assert loaded.id == "s1"
    assert loaded.messages[0].content == "hello"


def test_list_persisted_sessions_filters_by_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr("ccb.session.claude_dir", lambda: tmp_path)

    session_a = Session(id="a", cwd="/tmp/project", model="m")
    session_b = Session(id="b", cwd="/tmp/other", model="m")
    save_session(session_a)
    save_session(session_b)

    sessions = list_persisted_sessions(cwd="/tmp/project")

    assert [session["id"] for session in sessions] == ["a"]


def test_save_session_writes_valid_json_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr("ccb.session.claude_dir", lambda: tmp_path)

    session = Session(id="atomic", cwd="/tmp/project", model="m")
    session.add_user_message("hello")

    path = save_session(session)

    data = json.loads(path.read_text())
    assert data["id"] == "atomic"
    assert data["messages"][0]["content"] == "hello"
