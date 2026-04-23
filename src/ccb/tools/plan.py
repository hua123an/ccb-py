"""Plan mode tools - enter/exit plan mode for structured thinking."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import ENTER_PLAN_MODE_PROMPT, EXIT_PLAN_MODE_PROMPT


# Global plan state
_plan_mode = False
_current_plan: list[str] = []


def is_plan_mode() -> bool:
    return _plan_mode


def get_current_plan() -> list[str]:
    return _current_plan.copy()


class EnterPlanModeTool(Tool):
    name = "enter_plan_mode"
    description = ENTER_PLAN_MODE_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "Planned steps, one per line.",
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of the plan.",
            },
        },
        "required": ["plan"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        global _plan_mode, _current_plan
        _plan_mode = True
        plan_raw = input.get("plan", "")
        _current_plan = [s.strip() for s in plan_raw.split("\n") if s.strip()] if isinstance(plan_raw, str) else plan_raw
        summary = input.get("summary", "")

        lines = [f"Plan mode ON ({len(_current_plan)} steps)"]
        if summary:
            lines.append(f"Summary: {summary}")
        for i, step in enumerate(_current_plan, 1):
            lines.append(f"  {i}. {step}")

        return ToolResult(output="\n".join(lines))


class ExitPlanModeTool(Tool):
    name = "exit_plan_mode"
    description = EXIT_PLAN_MODE_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Optional message about plan execution.",
            },
        },
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        global _plan_mode
        _plan_mode = False
        msg = input.get("message", "Executing plan...")
        return ToolResult(output=f"Plan mode OFF. {msg}")
