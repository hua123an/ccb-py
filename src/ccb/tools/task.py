"""Task tools - stop tasks, output results for sub-agents."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import TASK_STOP_PROMPT, TASK_OUTPUT_PROMPT


class TaskStopTool(Tool):
    name = "task_stop"
    description = TASK_STOP_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Reason for stopping.",
            },
            "result": {
                "type": "string",
                "description": "Final result/output of the task.",
            },
        },
        "required": ["result"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        result = input.get("result", "")
        reason = input.get("reason", "")
        output = result
        if reason:
            output = f"[Stopped: {reason}]\n{result}"
        return ToolResult(output=output, metadata={"stop": True})


class TaskOutputTool(Tool):
    name = "task_output"
    description = TASK_OUTPUT_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "output": {
                "type": "string",
                "description": "The output to return.",
            },
        },
        "required": ["output"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        return ToolResult(output=input.get("output", ""))
