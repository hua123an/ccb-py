import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccb.session import Session


class _FakeRequest:
    def __init__(self, name: str, body):
        self.match_info = {"name": name}
        self._body = body

    async def json(self):
        return self._body


class _BrokenJSONRequest:
    def __init__(self, name: str):
        self.match_info = {"name": name}

    async def json(self):
        raise ValueError("bad json")


@pytest.mark.asyncio
async def test_call_tool_rejects_invalid_input():
    from ccb.http_server import APIServer

    server = APIServer()
    request = _FakeRequest("ask_user_question", {"question": 123})

    response = await server._call_tool(request)

    assert response.status == 400
    body = json.loads(response.body)
    assert body["tool"] == "ask_user_question"
    assert "Invalid tool input:" in body["error"]
    assert "Field 'question' must be a string, got int" in body["error"]


@pytest.mark.asyncio
async def test_call_tool_returns_tool_output_field(monkeypatch):
    from ccb.http_server import APIServer
    from ccb.tools.base import ToolResult

    server = APIServer()
    request = _FakeRequest("ask_user_question", {"question": "继续吗？"})

    class _FakeTool:
        input_schema = {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        }

        async def execute(self, body, cwd):
            assert cwd == os.getcwd()
            return ToolResult(output="ok", is_error=False)

    fake_registry = MagicMock()
    fake_registry.get.return_value = _FakeTool()

    monkeypatch.setattr(
        "ccb.tools.base.create_default_registry",
        lambda cwd: fake_registry,
        raising=False,
    )

    response = await server._call_tool(request)

    assert response.status == 200
    body = json.loads(response.body)
    assert body == {
        "tool": "ask_user_question",
        "result": "ok",
        "is_error": False,
    }


@pytest.mark.asyncio
async def test_call_tool_rejects_invalid_json():
    from ccb.http_server import APIServer

    server = APIServer()
    request = _BrokenJSONRequest("ask_user_question")

    response = await server._call_tool(request)

    assert response.status == 400
    body = json.loads(response.body)
    assert body == {
        "tool": "ask_user_question",
        "error": "Invalid JSON",
    }


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_api_key_header():
    from ccb.http_server import APIServer

    server = APIServer()
    server.set_api_key("secret")
    request = MagicMock()
    request.path = "/v1/chat"
    request.headers = {}
    request.app = {"ccb_api_server": server}

    async def _handler(_request):
        return "ok"

    response = await server._auth_middleware(request, _handler)

    assert response.status == 401


@pytest.mark.asyncio
async def test_auth_middleware_allows_health_without_api_key():
    from ccb.http_server import APIServer

    server = APIServer()
    server.set_api_key("secret")
    request = MagicMock()
    request.path = "/health"
    request.headers = {}
    request.app = {"ccb_api_server": server}

    async def _handler(_request):
        return "ok"

    response = await server._auth_middleware(request, _handler)

    assert response == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_accepts_matching_bearer_token():
    from ccb.http_server import APIServer

    server = APIServer()
    server.set_api_key("secret")
    request = MagicMock()
    request.path = "/v1/chat"
    request.headers = {"Authorization": "Bearer secret"}
    request.app = {"ccb_api_server": server}

    async def _handler(_request):
        return "ok"

    response = await server._auth_middleware(request, _handler)

    assert response == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_rejects_suffix_only_match():
    from ccb.http_server import APIServer

    server = APIServer()
    server.set_api_key("secret")
    request = MagicMock()
    request.path = "/v1/chat"
    request.headers = {"Authorization": "Token not-secret-but-endswithsecret"}
    request.app = {"ccb_api_server": server}

    async def _handler(_request):
        return "ok"

    response = await server._auth_middleware(request, _handler)

    assert response.status == 401


@pytest.mark.asyncio
async def test_chat_passes_current_cwd():
    from ccb.http_server import APIServer

    server = APIServer()
    request = _FakeRequest("ignored", {"message": "hello", "session_id": "s1"})

    with (
        patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
        patch("ccb.session.Session.save") as save,
    ):
        response = await server._chat(request)

    assert response.status == 200
    _, kwargs = run_query.await_args
    assert kwargs["model"] is None
    assert kwargs["cwd"] == os.getcwd()
    assert [m.content for m in kwargs["messages"]] == ["hello"]
    save.assert_called_once()
    body = json.loads(response.body)
    assert body["session_id"] == "s1"
    assert body["messages"][0]["content"] == "hello"
    assert body["messages"][1]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_uses_existing_session_context():
    from ccb.http_server import APIServer

    server = APIServer()
    server._sessions["s1"] = Session(id="s1", cwd="/tmp/http-project", model="session-model")
    request = _FakeRequest("ignored", {"message": "hello", "session_id": "s1"})

    with (
        patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
        patch("ccb.session.Session.save"),
    ):
        response = await server._chat(request)

    assert response.status == 200
    _, kwargs = run_query.await_args
    assert kwargs["model"] == "session-model"
    assert kwargs["cwd"] == "/tmp/http-project"
    assert [m.content for m in kwargs["messages"]] == ["hello"]


@pytest.mark.asyncio
async def test_chat_passes_full_session_history_to_query():
    from ccb.api.base import Message, Role
    from ccb.http_server import APIServer

    server = APIServer()
    server._sessions["s1"] = Session(id="s1", cwd="/tmp/http-project", model="session-model")
    server._sessions["s1"].messages = [
        Message(role=Role.USER, content="first"),
        Message(role=Role.ASSISTANT, content="reply"),
    ]
    request = _FakeRequest("ignored", {"message": "hello", "session_id": "s1"})

    with (
        patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query,
        patch("ccb.session.Session.save"),
    ):
        response = await server._chat(request)

    assert response.status == 200
    _, kwargs = run_query.await_args
    assert [m.content for m in kwargs["messages"]] == ["first", "reply", "hello"]


@pytest.mark.asyncio
async def test_get_session_returns_in_memory_messages():
    from ccb.api.base import Message, Role
    from ccb.http_server import APIServer

    server = APIServer()
    server._sessions["s1"] = Session(id="s1", cwd="/tmp/http-project", model="session-model")
    server._sessions["s1"].messages = [
        Message(role=Role.USER, content="hello"),
        Message(role=Role.ASSISTANT, content="ok"),
    ]
    server._sessions["s1"].total_input_tokens = 100
    server._sessions["s1"].total_output_tokens = 20
    server._sessions["s1"].last_input_tokens = 100
    request = MagicMock()
    request.match_info = {"sid": "s1"}

    response = await server._get_session(request)

    assert response.status == 200
    body = json.loads(response.body)
    assert body["session_id"] == "s1"
    assert body["cwd"] == "/tmp/http-project"
    assert body["model"] == "session-model"
    assert body["total_input_tokens"] == 100
    assert body["total_output_tokens"] == 20
    assert body["last_input_tokens"] == 100
    assert [m["content"] for m in body["messages"]] == ["hello", "ok"]


@pytest.mark.asyncio
async def test_get_session_loads_persisted_session():
    from ccb.api.base import Message, Role
    from ccb.http_server import APIServer
    from ccb.session import Session

    server = APIServer()
    request = MagicMock()
    request.match_info = {"sid": "persisted"}
    persisted = Session(id="persisted", cwd="/tmp/persisted", model="persisted-model")
    persisted.messages = [
        Message(role=Role.USER, content="hello"),
        Message(role=Role.ASSISTANT, content="ok"),
    ]

    with patch("ccb.session.Session.load", return_value=persisted):
        response = await server._get_session(request)

    assert response.status == 200
    body = json.loads(response.body)
    assert body["session_id"] == "persisted"
    assert body["cwd"] == "/tmp/persisted"
    assert body["model"] == "persisted-model"
    assert [m["content"] for m in body["messages"]] == ["hello", "ok"]
    assert "persisted" in server._sessions


@pytest.mark.asyncio
async def test_query_passes_model_system_and_cwd():
    from ccb.http_server import APIServer

    server = APIServer()
    request = _FakeRequest(
        "ignored",
        {"prompt": "hello", "model": "gpt-test", "system_prompt": "sys"},
    )

    with patch("ccb.query_engine.run_query", AsyncMock(return_value="ok")) as run_query:
        response = await server._query(request)

    assert response.status == 200
    run_query.assert_awaited_once_with(
        "hello",
        model="gpt-test",
        system_prompt="sys",
        cwd=os.getcwd(),
    )


@pytest.mark.asyncio
async def test_list_sessions_includes_in_memory_sessions():
    from ccb.http_server import APIServer

    server = APIServer()
    server._sessions["s1"] = Session(id="s1", cwd="/tmp/http-project", model="session-model")
    server._sessions["s1"].messages = [MagicMock(content="x")]
    request = MagicMock()

    with patch("ccb.session.Session.list_sessions", return_value=[]):
        response = await server._list_sessions(request)

    assert response.status == 200
    body = json.loads(response.body)
    assert body["sessions"][0]["id"] == "s1"
    assert body["sessions"][0]["cwd"] == "/tmp/http-project"
    assert body["sessions"][0]["model"] == "session-model"
    assert body["sessions"][0]["messages"] == 1
    assert "updated_at" in body["sessions"][0]


@pytest.mark.asyncio
async def test_status_reports_elapsed_uptime():
    from ccb.http_server import APIServer

    server = APIServer()
    server._start_time = 100.0
    request = MagicMock()

    with patch("ccb.http_server.time.time", return_value=112.5):
        response = await server._status(request)

    assert response.status == 200
    body = json.loads(response.body)
    assert body["uptime"] == 12.5
