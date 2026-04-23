"""Tests for ccb.bridge module."""
import asyncio
import json

import pytest

from ccb.bridge import IDEBridge, BridgeMessage


class TestBridgeInit:
    def test_default_init(self):
        bridge = IDEBridge()
        assert bridge.host == "127.0.0.1"
        assert bridge.port == 3200
        assert bridge.is_running is False
        assert bridge.connection_count == 0

    def test_custom_port(self):
        bridge = IDEBridge(port=4000)
        assert bridge.port == 4000


class TestBridgeHandlers:
    @pytest.mark.asyncio
    async def test_ping_handler(self):
        bridge = IDEBridge()
        msg = BridgeMessage(type="request", method="ping")
        result = await bridge._handle_ping(msg)
        assert result["status"] == "ok"
        assert "time" in result

    @pytest.mark.asyncio
    async def test_status_handler(self):
        bridge = IDEBridge()
        msg = BridgeMessage(type="request", method="getStatus")
        result = await bridge._handle_status(msg)
        assert "connected_clients" in result
        assert "version" in result

    @pytest.mark.asyncio
    async def test_execute_command(self):
        bridge = IDEBridge()
        msg = BridgeMessage(type="request", method="executeCommand", params={"command": "test"})
        result = await bridge._handle_execute_command(msg)
        assert result["executed"] == "test"

    @pytest.mark.asyncio
    async def test_open_file(self):
        bridge = IDEBridge()
        msg = BridgeMessage(type="request", method="openFile", params={"path": "/tmp/test.py", "line": 10})
        result = await bridge._handle_open_file(msg)
        assert result["opened"] == "/tmp/test.py"
        assert result["line"] == 10


class TestBridgeCustomHandler:
    @pytest.mark.asyncio
    async def test_register_handler(self):
        bridge = IDEBridge()
        results = []

        async def custom_handler(msg):
            results.append(msg.method)
            return {"custom": True}

        bridge.on("myMethod", custom_handler)
        assert "myMethod" in bridge._handlers


class TestBridgeMessage:
    def test_message_creation(self):
        msg = BridgeMessage(type="request", method="test", id="123", params={"a": 1})
        assert msg.type == "request"
        assert msg.method == "test"
        assert msg.id == "123"
        assert msg.params["a"] == 1

    def test_notification(self):
        msg = BridgeMessage(type="notification", method="fileChanged")
        assert msg.id is None


class TestBridgeRequestNoConnections:
    @pytest.mark.asyncio
    async def test_request_without_connections(self):
        bridge = IDEBridge()
        result = await bridge.request("test/method")
        assert result is None  # No connections

    @pytest.mark.asyncio
    async def test_get_editor_state_empty(self):
        bridge = IDEBridge()
        state = await bridge.get_editor_state()
        assert state == {}

    @pytest.mark.asyncio
    async def test_get_workspace_folders_empty(self):
        bridge = IDEBridge()
        folders = await bridge.get_workspace_folders()
        assert folders == []
