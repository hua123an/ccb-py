"""Tests for ComputerUseTool and ChromeUseTool."""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest



# ======================================================================
# ComputerUseTool
# ======================================================================

class TestComputerUseTool:
    """Tests for the computer_use tool."""

    @pytest.fixture
    def tool(self):
        from ccb.tools.computer_use import ComputerUseTool
        return ComputerUseTool()

    def test_name_and_schema(self, tool):
        assert tool.name == "computer_use"
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "action" in schema["properties"]
        assert "action" in schema["required"]
        actions = schema["properties"]["action"]["enum"]
        assert "screenshot" in actions
        assert "click" in actions
        assert "type" in actions
        assert "key_press" in actions
        assert "scroll" in actions
        assert "mouse_move" in actions
        assert "drag" in actions

    def test_needs_permission(self, tool):
        assert tool.needs_permission is True

    def test_to_api_schema(self, tool):
        api = tool.to_api_schema()
        assert api["name"] == "computer_use"
        assert "description" in api
        assert "input_schema" in api

    # -- execute dispatching ------------------------------------------

    @pytest.mark.asyncio
    async def test_execute_no_action(self, tool):
        result = await tool.execute({}, "/tmp")
        assert result.is_error
        assert "action" in result.output.lower()

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, tool):
        result = await tool.execute({"action": "fly"}, "/tmp")
        assert result.is_error
        assert "unknown" in result.output.lower()

    # -- screenshot ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_screenshot_macos(self, tool):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with (
            patch("ccb.tools.computer_use.platform") as mock_platform,
            patch("ccb.tools.computer_use.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("builtins.open", mock_open(read_data=fake_png)),
            patch("ccb.tools.computer_use.os.unlink"),
        ):
            mock_platform.system.return_value = "Darwin"
            result = await tool._screenshot({})

        assert not result.is_error
        assert result.output.startswith("data:image/png;base64,")
        decoded = base64.b64decode(result.output.split(",", 1)[1])
        assert decoded == fake_png

    @pytest.mark.asyncio
    async def test_screenshot_linux_scrot(self, tool):
        fake_png = b"\x89PNG" + b"\x00" * 50
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with (
            patch("ccb.tools.computer_use.platform") as mock_platform,
            patch("ccb.tools.computer_use.shutil.which", side_effect=lambda cmd: cmd == "scrot"),
            patch("ccb.tools.computer_use.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("builtins.open", mock_open(read_data=fake_png)),
            patch("ccb.tools.computer_use.os.unlink"),
        ):
            mock_platform.system.return_value = "Linux"
            result = await tool._screenshot({})

        assert not result.is_error
        assert "base64" in result.output

    @pytest.mark.asyncio
    async def test_screenshot_unsupported_platform(self, tool):
        with patch("ccb.tools.computer_use.platform") as mock_platform:
            mock_platform.system.return_value = "Unsupported"
            result = await tool._screenshot({})

        assert result.is_error
        assert "unsupported" in result.output.lower()

    # -- click ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_click_with_pyautogui(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._click({"x": 100, "y": 200, "button": "left"})

        assert not result.is_error
        assert "100" in result.output
        assert "200" in result.output
        mock_pyautogui.click.assert_called_once_with(100, 200, button="left")

    @pytest.mark.asyncio
    async def test_click_missing_coords(self, tool):
        result = await tool._click({"x": 100})
        assert result.is_error
        assert "required" in result.output.lower()

    @pytest.mark.asyncio
    async def test_click_no_pyautogui_macos_fallback(self, tool):
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with (
            patch.dict("sys.modules", {"pyautogui": None}),
            patch("ccb.tools.computer_use.platform") as mock_platform,
            patch("ccb.tools.computer_use.asyncio.create_subprocess_shell", return_value=mock_proc),
        ):
            mock_platform.system.return_value = "Darwin"
            result = await tool._click({"x": 50, "y": 75})

        assert not result.is_error

    # -- type ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_type_text_with_pyautogui(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._type_text({"text": "hello world"})

        assert not result.is_error
        assert "11" in result.output  # len("hello world")

    @pytest.mark.asyncio
    async def test_type_text_empty(self, tool):
        result = await tool._type_text({"text": ""})
        assert result.is_error
        assert "required" in result.output.lower()

    @pytest.mark.asyncio
    async def test_type_text_no_pyautogui_macos_fallback(self, tool):
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with (
            patch.dict("sys.modules", {"pyautogui": None}),
            patch("ccb.tools.computer_use.platform") as mock_platform,
            patch("ccb.tools.computer_use.asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            mock_platform.system.return_value = "Darwin"
            result = await tool._type_text({"text": "test"})

        assert not result.is_error
        assert "4" in result.output

    # -- key_press -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_key_press_single(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._key_press({"key": "enter"})

        assert not result.is_error
        mock_pyautogui.press.assert_called_once_with("enter")

    @pytest.mark.asyncio
    async def test_key_press_combo(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._key_press({"key": "ctrl+c"})

        assert not result.is_error
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    @pytest.mark.asyncio
    async def test_key_press_empty(self, tool):
        result = await tool._key_press({"key": ""})
        assert result.is_error

    # -- scroll --------------------------------------------------------

    @pytest.mark.asyncio
    async def test_scroll_down(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._scroll({"direction": "down", "amount": 5})

        assert not result.is_error
        mock_pyautogui.scroll.assert_called_once_with(-5)

    @pytest.mark.asyncio
    async def test_scroll_up(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._scroll({"direction": "up", "amount": 3})

        assert not result.is_error
        mock_pyautogui.scroll.assert_called_once_with(3)

    @pytest.mark.asyncio
    async def test_scroll_invalid_direction(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._scroll({"direction": "diagonal"})

        assert result.is_error

    # -- mouse_move ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_mouse_move(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._mouse_move({"x": 300, "y": 400})

        assert not result.is_error
        mock_pyautogui.moveTo.assert_called_once_with(300, 400)

    @pytest.mark.asyncio
    async def test_mouse_move_missing_coords(self, tool):
        result = await tool._mouse_move({})
        assert result.is_error

    # -- drag ----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_drag(self, tool):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = await tool._drag({
                "x": 10, "y": 20, "end_x": 110, "end_y": 120,
            })

        assert not result.is_error
        mock_pyautogui.moveTo.assert_called_once_with(10, 20)
        mock_pyautogui.drag.assert_called_once_with(100, 100, duration=0.5)

    @pytest.mark.asyncio
    async def test_drag_missing_params(self, tool):
        result = await tool._drag({"x": 10, "y": 20})
        assert result.is_error
        assert "required" in result.output.lower()

    # -- full execute dispatch -----------------------------------------

    @pytest.mark.asyncio
    async def test_execute_screenshot(self, tool):
        fake_png = b"\x89PNG" + b"\x00" * 50
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with (
            patch("ccb.tools.computer_use.platform") as mock_platform,
            patch("ccb.tools.computer_use.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("builtins.open", mock_open(read_data=fake_png)),
            patch("ccb.tools.computer_use.os.unlink"),
        ):
            mock_platform.system.return_value = "Darwin"
            result = await tool.execute({"action": "screenshot"}, "/tmp")

        assert not result.is_error
        assert "base64" in result.output


# ======================================================================
# ChromeUseTool
# ======================================================================

class TestChromeUseTool:
    """Tests for the chrome_use tool."""

    @pytest.fixture
    def tool(self):
        from ccb.tools.chrome_use import ChromeUseTool
        t = ChromeUseTool()
        # Mock confirmation to avoid stdin issues in pytest
        t._confirm_action = AsyncMock(return_value=True)
        return t

    def test_name_and_schema(self, tool):
        assert tool.name == "chrome_use"
        schema = tool.input_schema
        assert "action" in schema["properties"]
        actions = schema["properties"]["action"]["enum"]
        assert "open_url" in actions
        assert "click_selector" in actions
        assert "type_in_selector" in actions
        assert "get_text" in actions
        assert "screenshot" in actions
        assert "execute_js" in actions

    def test_needs_permission(self, tool):
        assert tool.needs_permission is True

    @pytest.mark.asyncio
    async def test_execute_no_action(self, tool):
        result = await tool.execute({}, "/tmp")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_chrome_fallback_open_url(self, tool):
        """When Chrome is unavailable, open_url falls back to web_fetch."""
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello World</body></html>"
        mock_resp.status_code = 200

        with patch("ccb.tools.chrome_use.ChromeUseTool._get_cdp_session", return_value=None):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                result = await tool.execute(
                    {"action": "open_url", "url": "https://example.com"}, "/tmp"
                )

        assert not result.is_error
        assert "Fallback" in result.output
        assert "Hello World" in result.output

    @pytest.mark.asyncio
    async def test_no_chrome_fallback_screenshot_fails(self, tool):
        """Screenshot has no fallback and should error when Chrome is down."""
        with patch("ccb.tools.chrome_use.ChromeUseTool._get_cdp_session", return_value=None):
            result = await tool.execute({"action": "screenshot"}, "/tmp")

        assert result.is_error
        assert "Chrome" in result.output

    @pytest.mark.asyncio
    async def test_no_chrome_non_readonly_fails(self, tool):
        """Actions like click_selector have no fallback."""
        with patch("ccb.tools.chrome_use.ChromeUseTool._get_cdp_session", return_value=None):
            result = await tool.execute({"action": "click_selector", "selector": "button"}, "/tmp")

        assert result.is_error

    # -- CDP session tests with mock -----------------------------------

    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.send = AsyncMock(return_value={"result": {"value": "ok"}})
        session.close = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_open_url_via_cdp(self, tool, mock_session):
        mock_session.send.return_value = {}
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {"action": "open_url", "url": "https://example.com"}, "/tmp"
            )

        assert not result.is_error
        assert "Navigated" in result.output
        mock_session.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_open_url_missing_url(self, tool, mock_session):
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute({"action": "open_url"}, "/tmp")

        assert result.is_error
        assert "url" in result.output.lower()

    @pytest.mark.asyncio
    async def test_click_selector_via_cdp(self, tool, mock_session):
        mock_session.send.return_value = {"result": {"type": "undefined"}}
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {"action": "click_selector", "selector": "button.submit"}, "/tmp"
            )

        assert not result.is_error
        assert "button.submit" in result.output

    @pytest.mark.asyncio
    async def test_click_selector_missing(self, tool, mock_session):
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute({"action": "click_selector"}, "/tmp")

        assert result.is_error

    @pytest.mark.asyncio
    async def test_type_in_selector_via_cdp(self, tool, mock_session):
        mock_session.send.return_value = {"result": {"type": "undefined"}}
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {
                    "action": "type_in_selector",
                    "selector": "input#name",
                    "text": "Alice",
                },
                "/tmp",
            )

        assert not result.is_error
        assert "5" in result.output  # len("Alice")

    @pytest.mark.asyncio
    async def test_get_text_via_cdp(self, tool, mock_session):
        mock_session.send.return_value = {
            "result": {"value": "Page Title Text"}
        }
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {"action": "get_text", "selector": "h1"}, "/tmp"
            )

        assert not result.is_error
        assert "Page Title Text" in result.output

    @pytest.mark.asyncio
    async def test_screenshot_via_cdp(self, tool, mock_session):
        fake_png = b"\x89PNG" + b"\x00" * 20
        mock_session.send.return_value = {
            "data": base64.b64encode(fake_png).decode()
        }
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute({"action": "screenshot"}, "/tmp")

        assert not result.is_error
        assert result.output.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_execute_js_via_cdp(self, tool, mock_session):
        mock_session.send.return_value = {
            "result": {"value": 42, "type": "number"}
        }
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {"action": "execute_js", "code": "document.title"}, "/tmp"
            )

        assert not result.is_error
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_execute_js_error(self, tool, mock_session):
        mock_session.send.return_value = {
            "result": {
                "subtype": "error",
                "description": "ReferenceError: foo is not defined",
            }
        }
        with patch.object(tool, "_get_cdp_session", return_value=mock_session):
            result = await tool.execute(
                {"action": "execute_js", "code": "foo.bar"}, "/tmp"
            )

        assert result.is_error
        assert "error" in result.output.lower()


# ======================================================================
# CDPClient
# ======================================================================

class TestCDPClient:
    def test_escape_js(self):
        from ccb.tools.chrome_use import ChromeUseTool
        assert ChromeUseTool._escape_js("it's") == "it\\'s"
        assert ChromeUseTool._escape_js("a\\b") == "a\\\\b"
        assert ChromeUseTool._escape_js("line\nbreak") == "line\\nbreak"

    def test_simple_html_to_text(self):
        from ccb.tools.chrome_use import ChromeUseTool
        html = "<html><head><title>T</title></head><body><p>Hello</p></body></html>"
        text = ChromeUseTool._simple_html_to_text(html)
        assert "Hello" in text
        assert "<p>" not in text

    @pytest.mark.asyncio
    async def test_cdp_client_send(self):
        from ccb.tools.chrome_use import CDPClient

        mock_ws = AsyncMock()
        response = json.dumps({"id": 1, "result": {"value": "hello"}})
        mock_ws.recv = AsyncMock(return_value=response)

        client = CDPClient(mock_ws)
        result = await client.send("Runtime.evaluate", {"expression": "1+1"})

        assert result == {"value": "hello"}
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["method"] == "Runtime.evaluate"
        assert sent["id"] == 1

    @pytest.mark.asyncio
    async def test_cdp_client_send_error(self):
        from ccb.tools.chrome_use import CDPClient

        mock_ws = AsyncMock()
        response = json.dumps({
            "id": 1,
            "error": {"code": -1, "message": "not found"},
        })
        mock_ws.recv = AsyncMock(return_value=response)

        client = CDPClient(mock_ws)
        with pytest.raises(RuntimeError, match="not found"):
            await client.send("Page.navigate", {"url": "about:blank"})

    @pytest.mark.asyncio
    async def test_cdp_client_close(self):
        from ccb.tools.chrome_use import CDPClient

        mock_ws = AsyncMock()
        client = CDPClient(mock_ws)
        await client.close()
        mock_ws.close.assert_awaited_once()


# ======================================================================
# Registry integration
# ======================================================================

class TestRegistryIntegration:
    def test_computer_use_in_registry(self):
        from ccb.tools.base import create_default_registry
        registry = create_default_registry("/tmp")
        assert "computer_use" in registry.names
        assert registry.get("computer_use").name == "computer_use"

    def test_chrome_use_in_registry(self):
        from ccb.tools.base import create_default_registry
        registry = create_default_registry("/tmp")
        assert "chrome_use" in registry.names
        assert registry.get("chrome_use").name == "chrome_use"

    def test_schemas_include_new_tools(self):
        from ccb.tools.base import create_default_registry
        registry = create_default_registry("/tmp")
        names = [s["name"] for s in registry.all_schemas()]
        assert "computer_use" in names
        assert "chrome_use" in names
