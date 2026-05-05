"""Computer Use tool - screen capture, keyboard and mouse control."""
from __future__ import annotations

import asyncio
import base64
import os
import platform
import shutil
import tempfile
from typing import Any

from ccb.tools.base import Tool, ToolResult


class ComputerUseTool(Tool):
    name = "computer_use"
    description = (
        "Interact with the computer's screen, keyboard, and mouse. "
        "Actions: screenshot (returns base64 PNG), click, type (keyboard input), "
        "key_press (special keys like enter/tab/escape), scroll, mouse_move, drag. "
        "All input actions (click, type, key_press, scroll, mouse_move, drag) "
        "require explicit user confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "screenshot",
                    "click",
                    "type",
                    "key_press",
                    "scroll",
                    "mouse_move",
                    "drag",
                ],
                "description": "The action to perform.",
            },
            "x": {
                "type": "number",
                "description": "X coordinate (for click, mouse_move, drag start).",
            },
            "y": {
                "type": "number",
                "description": "Y coordinate (for click, mouse_move, drag start).",
            },
            "text": {
                "type": "string",
                "description": "Text to type (for 'type' action).",
            },
            "key": {
                "type": "string",
                "description": "Key name for key_press (e.g. enter/tab/escape/backspace/delete/space/up/down/left/right/home/end/pageup/pagedown/f1-f12/ctrl+c/cmd+a).",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button for click (default: left).",
                "default": "left",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction.",
            },
            "amount": {
                "type": "integer",
                "description": "Scroll amount in clicks (default: 3).",
                "default": 3,
            },
            "end_x": {
                "type": "number",
                "description": "End X coordinate (for drag).",
            },
            "end_y": {
                "type": "number",
                "description": "End Y coordinate (for drag).",
            },
        },
        "required": ["action"],
    }

    # Actions that require explicit user confirmation (anything that modifies state)
    CONFIRMATION_REQUIRED_ACTIONS = {
        "click", "type", "key_press", "scroll", "mouse_move", "drag"
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

        # Require confirmation for state-modifying actions
        if action in self.CONFIRMATION_REQUIRED_ACTIONS:
            confirmed = await self._confirm_action(action, input)
            if not confirmed:
                return ToolResult(
                    output=f"Action '{action}' cancelled - user did not confirm",
                    is_error=True,
                )

        dispatch = {
            "screenshot": self._screenshot,
            "click": self._click,
            "type": self._type_text,
            "key_press": self._key_press,
            "scroll": self._scroll,
            "mouse_move": self._mouse_move,
            "drag": self._drag,
        }

        handler = dispatch.get(action)
        if not handler:
            return ToolResult(
                output=f"Error: unknown action '{action}'", is_error=True
            )

        try:
            return await handler(input)
        except Exception as e:
            return ToolResult(output=f"Error during '{action}': {e}", is_error=True)

    async def _confirm_action(self, action: str, input: dict[str, Any]) -> bool:
        """Ask for explicit user confirmation before risky actions."""
        from ccb.display import console

        details = []
        if action == "click":
            details.append(f"at ({input.get('x')}, {input.get('y')}) button={input.get('button', 'left')}")
        elif action == "type":
            details.append(f"text: '{input.get('text', '')}'")
        elif action == "key_press":
            details.append(f"key: {input.get('key')}")
        elif action == "scroll":
            details.append(f"{input.get('direction')} by {input.get('amount', 3)}")
        elif action == "mouse_move":
            details.append(f"to ({input.get('x')}, {input.get('y')})")
        elif action == "drag":
            details.append(f"from ({input.get('x')},{input.get('y')}) to ({input.get('end_x')},{input.get('end_y')})")

        detail_str = " ".join(details)

        try:
            from ccb.repl import get_active_repl
            repl = get_active_repl()
            if repl is not None:
                prompt = f"Confirm computer_use action: {action} {detail_str}"
                answer = await repl.ask_user_question_async(prompt, ["Yes", "No"])
                return answer == "Yes"
        except Exception:
            pass

        console.print(f"\n  [bold yellow]❗ Confirm computer_use: {action} {detail_str}[/bold yellow]")
        try:
            answer = console.input("  [dim]Proceed? (y/n):[/dim] ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    async def _screenshot(self, _input: dict[str, Any]) -> ToolResult:
        """Capture the screen and return a base64-encoded PNG."""
        system = platform.system()

        try:
            if system == "Darwin":
                png_bytes = await self._screenshot_macos()
            elif system == "Linux":
                png_bytes = await self._screenshot_linux()
            elif system == "Windows":
                png_bytes = await self._screenshot_windows()
            else:
                return ToolResult(
                    output=f"Unsupported platform: {system}", is_error=True
                )
        except Exception as e:
            return ToolResult(output=f"Screenshot failed: {e}", is_error=True)

        encoded = base64.b64encode(png_bytes).decode("ascii")
        return ToolResult(
            output=f"data:image/png;base64,{encoded}",
            metadata={"format": "base64_png", "size": len(png_bytes)},
        )

    @staticmethod
    async def _screenshot_macos() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "screencapture", "-x", tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("screencapture failed")
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)

    @staticmethod
    async def _screenshot_linux() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        for cmd in [
            ["scrot", tmp_path],
            ["import", "-window", "root", tmp_path],
        ]:
            if shutil.which(cmd[0]):
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                if proc.returncode == 0:
                    with open(tmp_path, "rb") as f:
                        return f.read()

        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(tmp_path, "PNG")
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @staticmethod
    async def _screenshot_windows() -> bytes:
        from PIL import ImageGrab
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            img = ImageGrab.grab()
            img.save(tmp_path, "PNG")
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # Input actions (click, type, key, scroll, move, drag)
    # ------------------------------------------------------------------

    async def _click(self, input: dict[str, Any]) -> ToolResult:
        x = input.get("x")
        y = input.get("y")
        if x is None or y is None:
            return ToolResult(
                output="Error: 'x' and 'y' are required for click", is_error=True
            )
        button = input.get("button", "left")

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.click(int(x), int(y), button=button)
        except ImportError:
            system = platform.system()
            if system == "Darwin":
                btn = "right" if button == "right" else "left" if button == "left" else "center"
                if btn == "left":
                    cmd = f'cliclick c:{int(x)},{int(y)}'
                else:
                    cmd = f'cliclick rc:{int(x)},{int(y)}'
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                if proc.returncode != 0:
                    return ToolResult(
                        output="Click failed (install pyautogui or cliclick)",
                        is_error=True,
                    )
            else:
                return ToolResult(
                    output="pyautogui is required for click. Install with: pip install pyautogui",
                    is_error=True,
                )

        return ToolResult(output=f"Clicked ({x}, {y}) button={button}")

    async def _type_text(self, input: dict[str, Any]) -> ToolResult:
        text = input.get("text", "")
        if not text:
            return ToolResult(output="Error: 'text' is required for type", is_error=True)

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.typewrite(text) if text.isascii() else pyautogui.write(text)
        except ImportError:
            system = platform.system()
            if system == "Darwin":
                escaped = text.replace("\\", "\\\\").replace('"', '\\"')
                script = f'tell application "System Events" to keystroke "{escaped}"'
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e", script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
            else:
                return ToolResult(
                    output="pyautogui is required for typing. Install with: pip install pyautogui",
                    is_error=True,
                )

        return ToolResult(output=f"Typed {len(text)} characters")

    async def _key_press(self, input: dict[str, Any]) -> ToolResult:
        key = input.get("key", "")
        if not key:
            return ToolResult(output="Error: 'key' is required for key_press", is_error=True)

        key_map = {
            "enter": "enter", "return": "enter",
            "tab": "tab",
            "escape": "escape", "esc": "escape",
            "backspace": "backspace",
            "delete": "delete", "del": "delete",
            "space": "space",
            "up": "up", "down": "down", "left": "left", "right": "right",
            "home": "home", "end": "end",
            "pageup": "pageup", "pagedown": "pagedown",
        }
        for i in range(1, 13):
            key_map[f"f{i}"] = f"f{i}"

        try:
            import pyautogui
            pyautogui.FAILSAFE = True

            if "+" in key:
                parts = [k.strip().lower() for k in key.split("+")]
                mapped = [key_map.get(p, p) for p in parts]
                pyautogui.hotkey(*mapped)
            else:
                mapped = key_map.get(key.lower(), key.lower())
                pyautogui.press(mapped)
        except ImportError:
            return ToolResult(
                output="pyautogui is required for key_press. Install with: pip install pyautogui",
                is_error=True,
            )

        return ToolResult(output=f"Pressed key: {key}")

    async def _scroll(self, input: dict[str, Any]) -> ToolResult:
        direction = input.get("direction", "down")
        amount = input.get("amount", 3)

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            if direction == "down":
                pyautogui.scroll(-amount)
            elif direction == "up":
                pyautogui.scroll(amount)
            elif direction == "left":
                pyautogui.hscroll(-amount)
            elif direction == "right":
                pyautogui.hscroll(amount)
            else:
                return ToolResult(
                    output=f"Error: invalid scroll direction '{direction}'",
                    is_error=True,
                )
        except ImportError:
            return ToolResult(
                output="pyautogui is required for scroll. Install with: pip install pyautogui",
                is_error=True,
            )

        return ToolResult(output=f"Scrolled {direction} by {amount}")

    async def _mouse_move(self, input: dict[str, Any]) -> ToolResult:
        x = input.get("x")
        y = input.get("y")
        if x is None or y is None:
            return ToolResult(
                output="Error: 'x' and 'y' are required for mouse_move", is_error=True
            )

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.moveTo(int(x), int(y))
        except ImportError:
            return ToolResult(
                output="pyautogui is required for mouse_move. Install with: pip install pyautogui",
                is_error=True,
            )

        return ToolResult(output=f"Mouse moved to ({x}, {y})")

    async def _drag(self, input: dict[str, Any]) -> ToolResult:
        x = input.get("x")
        y = input.get("y")
        end_x = input.get("end_x")
        end_y = input.get("end_y")
        if None in (x, y, end_x, end_y):
            return ToolResult(
                output="Error: 'x', 'y', 'end_x', 'end_y' are required for drag",
                is_error=True,
            )

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.moveTo(int(x), int(y))
            pyautogui.drag(int(end_x) - int(x), int(end_y) - int(y), duration=0.5)
        except ImportError:
            return ToolResult(
                output="pyautogui is required for drag. Install with: pip install pyautogui",
                is_error=True,
            )

        return ToolResult(output=f"Dragged from ({x}, {y}) to ({end_x}, {end_y})")
