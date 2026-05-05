"""Chrome Automation tool - browser control via Chrome DevTools Protocol."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from ccb.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

# Default CDP port
DEFAULT_PORT = 9222


class ChromeUseTool(Tool):
    name = "chrome_use"
    description = (
        "Automate Chrome browser via DevTools Protocol. "
        "Actions: open_url, click_selector, type_in_selector, get_text, "
        "screenshot, execute_js. "
        "All interactive actions (open_url, click_selector, type_in_selector, execute_js) "
        "require explicit user confirmation. "
        "Requires Chrome launched with --remote-debugging-port=9222 (or custom port). "
        "Falls back to web_fetch for read-only operations if Chrome is unavailable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "open_url",
                    "click_selector",
                    "type_in_selector",
                    "get_text",
                    "screenshot",
                    "execute_js",
                ],
                "description": "The browser action to perform.",
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (for open_url).",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for element interaction.",
            },
            "text": {
                "type": "string",
                "description": "Text to type into a selector (for type_in_selector).",
            },
            "code": {
                "type": "string",
                "description": "JavaScript code to execute (for execute_js).",
            },
            "port": {
                "type": "integer",
                "description": "Chrome DevTools Protocol port (default: 9222).",
                "default": DEFAULT_PORT,
            },
            "headless": {
                "type": "boolean",
                "description": "Attempt to connect to headless Chrome (default: false).",
                "default": False,
            },
        },
        "required": ["action"],
    }

    # Actions that require explicit user confirmation
    CONFIRMATION_REQUIRED_ACTIONS = {
        "open_url", "click_selector", "type_in_selector", "execute_js"
    }

    @property
    def needs_permission(self) -> bool:
        return True

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        action = input.get("action", "")
        if not action:
            return ToolResult(output="Error: 'action' is required", is_error=True)

        # Require confirmation for interactive/state-modifying actions
        if action in self.CONFIRMATION_REQUIRED_ACTIONS:
            confirmed = await self._confirm_action(action, input)
            if not confirmed:
                return ToolResult(
                    output=f"Action '{action}' cancelled - user did not confirm",
                    is_error=True,
                )

        port = input.get("port", DEFAULT_PORT)

        # Try to connect to Chrome via CDP
        session = await self._get_cdp_session(port)
        if session is None:
            # Fallback for read-only actions
            if action in ("open_url", "get_text", "screenshot"):
                return await self._fallback(action, input)
            return ToolResult(
                output=(
                    f"Error: Cannot connect to Chrome on port {port}. "
                    "Launch Chrome with: google-chrome --remote-debugging-port=9222 "
                    "or install chrome-remote-interface: pip install chrome-remote-interface"
                ),
                is_error=True,
            )

        try:
            dispatch = {
                "open_url": self._open_url,
                "click_selector": self._click_selector,
                "type_in_selector": self._type_in_selector,
                "get_text": self._get_text,
                "screenshot": self._cdp_screenshot,
                "execute_js": self._execute_js,
            }
            handler = dispatch.get(action)
            if not handler:
                return ToolResult(
                    output=f"Error: unknown action '{action}'", is_error=True
                )
            return await handler(session, input)
        except Exception as e:
            return ToolResult(
                output=f"Chrome action '{action}' failed: {e}", is_error=True
            )
        finally:
            await self._close_session(session)

    async def _confirm_action(self, action: str, input: dict[str, Any]) -> bool:
        """Ask for explicit user confirmation before risky browser actions."""
        from ccb.display import console

        details = []
        if action == "open_url":
            details.append(f"URL: {input.get('url', '')}")
        elif action == "click_selector":
            details.append(f"click selector: {input.get('selector')}")
        elif action == "type_in_selector":
            details.append(f"type into {input.get('selector')}: '{input.get('text')}'")
        elif action == "execute_js":
            code = input.get('code', '')[:100]
            details.append(f"execute JS: {code}{'...' if len(input.get('code',''))>100 else ''}")

        detail_str = " ".join(details)

        try:
            from ccb.repl import get_active_repl
            repl = get_active_repl()
            if repl is not None:
                prompt = f"Confirm chrome_use action: {action} {detail_str}"
                answer = await repl.ask_user_question_async(prompt, ["Yes", "No"])
                return answer == "Yes"
        except Exception:
            pass

        console.print(f"\n  [bold yellow]❗ Confirm chrome_use: {action} {detail_str}[/bold yellow]")
        try:
            answer = console.input("  [dim]Proceed? (y/n):[/dim] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # ------------------------------------------------------------------
    # CDP connection management
    # ------------------------------------------------------------------

    async def _get_cdp_session(self, port: int):
        """Try to get a CDP session connected to the first available page."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/json")
                pages = resp.json()
                if not pages:
                    return None
                ws_url = pages[0].get("webSocketDebuggerUrl")
                if not ws_url:
                    return None
        except Exception:
            return None

        try:
            return await CDPClient.create(ws_url)
        except ImportError:
            logger.warning("websockets library not installed; install with: pip install websockets")
            return None
        except Exception as e:
            logger.warning(f"CDP connection failed: {e}")
            return None

    @staticmethod
    async def _close_session(session) -> None:
        try:
            await session.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CDP action handlers
    # ------------------------------------------------------------------

    async def _open_url(self, session, input: dict[str, Any]) -> ToolResult:
        url = input.get("url", "")
        if not url:
            return ToolResult(output="Error: 'url' is required", is_error=True)
        await session.send("Page.enable", {})
        result = await session.send("Page.navigate", {"url": url})
        await asyncio.sleep(1.5)
        return ToolResult(output=f"Navigated to {url}", metadata=result)

    async def _click_selector(
        self, session, input: dict[str, Any]
    ) -> ToolResult:
        selector = input.get("selector", "")
        if not selector:
            return ToolResult(
                output="Error: 'selector' is required", is_error=True
            )
        js = (
            f"document.querySelector('{self._escape_js(selector)}').click()"
        )
        result = await session.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        if result.get("result", {}).get("subtype") == "error":
            return ToolResult(
                output=f"Click failed: {result['result'].get('description', 'unknown')}",
                is_error=True,
            )
        return ToolResult(output=f"Clicked element: {selector}")

    async def _type_in_selector(
        self, session, input: dict[str, Any]
    ) -> ToolResult:
        selector = input.get("selector", "")
        text = input.get("text", "")
        if not selector or not text:
            return ToolResult(
                output="Error: 'selector' and 'text' are required",
                is_error=True,
            )
        focus_js = f"document.querySelector('{self._escape_js(selector)}').focus()"
        await session.send(
            "Runtime.evaluate", {"expression": focus_js, "returnByValue": True}
        )
        for char in text:
            await session.send(
                "Input.dispatchKeyEvent",
                {"type": "char", "text": char},
            )
            await asyncio.sleep(0.02)
        return ToolResult(output=f"Typed {len(text)} chars into {selector}")

    async def _get_text(
        self, session, input: dict[str, Any]
    ) -> ToolResult:
        selector = input.get("selector")
        if selector:
            js = f"document.querySelector('{self._escape_js(selector)}')?.innerText || ''"
        else:
            js = "document.body.innerText"
        result = await session.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        text = result.get("result", {}).get("value", "")
        if len(text) > 100_000:
            text = text[:100_000] + "\n... (truncated)"
        return ToolResult(output=text)

    async def _cdp_screenshot(
        self, session, _input: dict[str, Any]
    ) -> ToolResult:
        result = await session.send(
            "Page.captureScreenshot", {"format": "png"}
        )
        data = result.get("data", "")
        if not data:
            return ToolResult(output="Screenshot returned no data", is_error=True)
        png_bytes = base64.b64decode(data)
        return ToolResult(
            output=f"data:image/png;base64,{data}",
            metadata={"format": "base64_png", "size": len(png_bytes)},
        )

    async def _execute_js(
        self, session, input: dict[str, Any]
    ) -> ToolResult:
        code = input.get("code", "")
        if not code:
            return ToolResult(output="Error: 'code' is required", is_error=True)
        result = await session.send(
            "Runtime.evaluate",
            {"expression": code, "returnByValue": True, "awaitPromise": True},
        )
        val = result.get("result", {})
        if val.get("subtype") == "error":
            return ToolResult(
                output=f"JS error: {val.get('description', 'unknown')}",
                is_error=True,
            )
        output = val.get("value")
        if output is None:
            output = val.get("description", "(undefined)")
        elif not isinstance(output, str):
            output = json.dumps(output, indent=2, default=str)
        return ToolResult(output=output)

    # ------------------------------------------------------------------
    # Fallback when Chrome is not available
    # ------------------------------------------------------------------

    async def _fallback(
        self, action: str, input: dict[str, Any]
    ) -> ToolResult:
        """Use web_fetch as approximation for read-only actions."""
        url = input.get("url", "")
        if action == "open_url" and url:
            try:
                import httpx
                async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                    resp = await client.get(url, headers={"User-Agent": "CCB/0.1"})
                    text = self._simple_html_to_text(resp.text)
                    if len(text) > 100_000:
                        text = text[:100_000] + "\n... (truncated)"
                    return ToolResult(
                        output=f"[Fallback: web_fetch] Content of {url}:\n{text}",
                        metadata={"fallback": True, "status_code": resp.status_code},
                    )
            except Exception as e:
                return ToolResult(output=f"Fallback fetch failed: {e}", is_error=True)

        if action == "get_text" and url:
            return await self._fallback("open_url", {"url": url})

        if action == "screenshot":
            return ToolResult(
                output="Screenshot requires a live Chrome connection. "
                "Launch Chrome with --remote-debugging-port=9222",
                is_error=True,
            )

        return ToolResult(
            output=f"Chrome not available; cannot perform '{action}' without it.",
            is_error=True,
        )

    @staticmethod
    def _simple_html_to_text(html: str) -> str:
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _escape_js(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


class CDPClient:
    """Minimal Chrome DevTools Protocol client over WebSocket."""

    def __init__(self, ws) -> None:
        self._ws = ws
        self._msg_id = 0

    @classmethod
    async def create(cls, ws_url: str):
        import websockets
        ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)
        return cls(ws)

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict:
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        await self._ws.send(json.dumps(msg))

        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                if "error" in data:
                    raise RuntimeError(
                        f"CDP error: {data['error'].get('message', data['error'])}"
                    )
                return data.get("result", {})

    async def close(self) -> None:
        await self._ws.close()
