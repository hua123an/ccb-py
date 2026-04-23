"""Grep tool - search file contents."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import GREP_PROMPT


class GrepTool(Tool):
    name = "grep"
    description = GREP_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Search pattern (regex by default)."},
            "path": {"type": "string", "description": "Directory or file to search in."},
            "include": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')."},
            "fixed_strings": {"type": "boolean", "description": "Treat pattern as literal string.", "default": False},
            "context_lines": {"type": "integer", "description": "Lines of context around matches.", "default": 0},
        },
        "required": ["pattern", "path"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        pattern = input["pattern"]
        path = input.get("path", ".")
        include = input.get("include")
        fixed = input.get("fixed_strings", False)
        ctx = input.get("context_lines", 0)

        if not os.path.isabs(path):
            path = os.path.join(cwd, path)

        cmd = ["rg", "--no-heading", "--line-number", "--color=never", "-i"]
        if fixed:
            cmd.append("-F")
        if ctx:
            cmd.extend(["-C", str(ctx)])
        if include:
            cmd.extend(["-g", include])
        cmd.extend(["--max-count=100", pattern, path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace").rstrip()
            if not output:
                return ToolResult(output="No matches found.")
            if len(output) > 100_000:
                output = output[:100_000] + "\n... (truncated)"
            return ToolResult(output=output)
        except FileNotFoundError:
            # rg not found, fallback to grep
            return await self._fallback_grep(pattern, path, cwd)
        except asyncio.TimeoutError:
            return ToolResult(output="Search timed out", is_error=True)

    async def _fallback_grep(self, pattern: str, path: str, cwd: str) -> ToolResult:
        cmd = ["grep", "-rn", "--color=never", "-i", pattern, path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace").rstrip()
            return ToolResult(output=output or "No matches found.")
        except Exception as e:
            return ToolResult(output=f"Search error: {e}", is_error=True)
