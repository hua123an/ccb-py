"""Tests for ccb.mcp.server module."""
import pytest

from ccb.mcp.server import MCPServer, create_default_server


@pytest.fixture
def server():
    return create_default_server()


class TestMCPServerBasic:
    @pytest.mark.asyncio
    async def test_initialize(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}
        })
        assert resp["result"]["serverInfo"]["name"] == "ccb-py"
        assert server._initialized is True

    @pytest.mark.asyncio
    async def test_ping(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}
        })
        assert resp["result"] == {}

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "nonexistent", "params": {}
        })
        assert "error" in resp


class TestToolRegistry:
    @pytest.mark.asyncio
    async def test_list_tools(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
        })
        tools = resp["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "echo" in names
        assert "bash" in names
        assert "file_read" in names
        assert "file_write" in names
        assert "grep" in names
        assert "glob" in names

    @pytest.mark.asyncio
    async def test_call_echo(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hello"}}
        })
        content = resp["result"]["content"]
        assert content[0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 3,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}}
        })
        assert "error" in resp


class TestResources:
    @pytest.mark.asyncio
    async def test_list_resources(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}
        })
        resources = resp["result"]["resources"]
        uris = [r["uri"] for r in resources]
        assert "ccb://version" in uris
        assert "ccb://tools" in uris
        assert "ccb://status" in uris

    @pytest.mark.asyncio
    async def test_read_resource(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "resources/read",
            "params": {"uri": "ccb://version"}
        })
        assert "contents" in resp["result"]

    @pytest.mark.asyncio
    async def test_read_unknown_resource(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 3,
            "method": "resources/read",
            "params": {"uri": "ccb://nonexistent"}
        })
        assert "error" in resp


class TestPrompts:
    @pytest.mark.asyncio
    async def test_list_prompts(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "prompts/list", "params": {}
        })
        prompts = resp["result"]["prompts"]
        names = [p["name"] for p in prompts]
        assert "code-review" in names
        assert "explain" in names
        assert "test-gen" in names

    @pytest.mark.asyncio
    async def test_get_prompt(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "prompts/get",
            "params": {"name": "code-review"}
        })
        assert "description" in resp["result"]

    @pytest.mark.asyncio
    async def test_get_unknown_prompt(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 3,
            "method": "prompts/get",
            "params": {"name": "nonexistent"}
        })
        assert "error" in resp


class TestNotifications:
    @pytest.mark.asyncio
    async def test_notification_no_response(self, server):
        resp = await server.handle_request({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        })
        assert resp is None


class TestCustomServer:
    @pytest.mark.asyncio
    async def test_register_and_call(self):
        server = MCPServer(name="test", version="0.1")
        async def my_handler(args):
            return f"Result: {args.get('x', 0) * 2}"
        server.register_tool("double", "Double a number",
                             {"type": "object", "properties": {"x": {"type": "integer"}}},
                             my_handler)
        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "double", "arguments": {"x": 21}}
        })
        assert "42" in resp["result"]["content"][0]["text"]
