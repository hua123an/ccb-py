"""Todo tool - manage todo lists in Markdown files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import TODO_WRITE_PROMPT


class TodoWriteTool(Tool):
    name = "todo_write"
    description = TODO_WRITE_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the todo markdown file."},
            "todos": {
                "type": "array",
                "description": "List of todo items to write.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["file_path", "todos"],
    }

    @property
    def needs_permission(self) -> bool:
        return True

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        import os
        file_path = input["file_path"]
        todos = input.get("todos", [])
        p = Path(file_path) if os.path.isabs(file_path) else Path(cwd) / file_path

        status_map = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        priority_map = {"high": "🔴", "medium": "🟡", "low": "🟢"}

        lines = ["# TODO\n"]
        for item in todos:
            check = status_map.get(item.get("status", "pending"), "[ ]")
            pri = priority_map.get(item.get("priority", "medium"), "")
            content = item.get("content", "")
            lines.append(f"- {check} {pri} {content}")

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines) + "\n")
            output = f"Wrote {len(todos)} todos to {p}"

            # Nudge toward multi-agent orchestration when the plan grows large.
            # Only trigger on fresh plans (most items still pending) so we
            # don't re-emit the reminder on every subsequent status update.
            pending = sum(1 for t in todos if t.get("status") == "pending")
            if len(todos) >= 10 and pending >= 8:
                output += (
                    "\n\n<system-reminder>\n"
                    f"This plan has {len(todos)} items ({pending} still pending). "
                    "Per the multi-agent orchestration policy, you SHOULD partition "
                    "the independent items into 2–5 disjoint groups and spawn one "
                    "`agent` tool call per group IN THE SAME MESSAGE so they run "
                    "concurrently. Only keep items you must do sequentially for "
                    "yourself (those with cross-dependencies or shared state).\n"
                    "</system-reminder>"
                )
            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)
