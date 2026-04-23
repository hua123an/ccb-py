"""Agent tool - spawn a sub-agent for parallel/complex tasks."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import AGENT_PROMPT


class AgentTool(Tool):
    name = "agent"
    description = AGENT_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task for the sub-agent to complete."},
        },
        "required": ["task"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        # Agent execution is handled specially by the main loop
        # This method is a fallback if called directly
        return ToolResult(output="Agent tool must be handled by the main loop.", is_error=True)
