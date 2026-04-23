"""Glob tool - find files by pattern."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import GLOB_PROMPT


class GlobTool(Tool):
    name = "glob"
    description = GLOB_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')."},
            "path": {"type": "string", "description": "Base directory to search from."},
        },
        "required": ["pattern"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        pattern = input["pattern"]
        path = input.get("path", cwd)
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)

        # Try fd first, fallback to find
        try:
            cmd = ["fd", "--glob", pattern, path, "--max-results=50", "--type=f"]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout.decode(errors="replace").rstrip()
            if output:
                return ToolResult(output=output)
            return ToolResult(output="No files found.")
        except FileNotFoundError:
            pass

        # Fallback: Python glob
        from pathlib import Path as P

        base = P(path)
        matches = sorted(base.glob(pattern))[:50]
        if not matches:
            return ToolResult(output="No files found.")

        lines = []
        for m in matches:
            if m.is_file():
                try:
                    size = m.stat().st_size
                    lines.append(f"{m}  ({size:,} bytes)")
                except OSError:
                    lines.append(str(m))
        return ToolResult(output="\n".join(lines) if lines else "No files found.")
