"""Bash tool - execute shell commands."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import BASH_PROMPT


class BashTool(Tool):
    name = "bash"
    description = BASH_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120).",
                "default": 120,
            },
        },
        "required": ["command"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        command = input.get("command", "")
        timeout = input.get("timeout", 120)

        if not command.strip():
            return ToolResult(output="Error: empty command", is_error=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, "PAGER": "cat"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output_parts = []
            if stdout:
                output_parts.append(stdout.decode(errors="replace"))
            if stderr:
                output_parts.append(stderr.decode(errors="replace"))
            output = "\n".join(output_parts).rstrip()

            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n{output}"

            # Truncate very long output
            max_len = 100_000
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total chars)"

            return ToolResult(output=output or "(no output)", is_error=proc.returncode != 0)

        except asyncio.TimeoutError:
            return ToolResult(output=f"Command timed out after {timeout}s", is_error=True)
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)
