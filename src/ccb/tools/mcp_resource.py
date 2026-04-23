"""MCP Resource tools - list and read MCP server resources."""
from __future__ import annotations

from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import LIST_MCP_RESOURCES_PROMPT, READ_MCP_RESOURCE_PROMPT


class ListMcpResourcesTool(Tool):
    name = "list_mcp_resources"
    description = LIST_MCP_RESOURCES_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Optional: specific MCP server name to list resources from.",
            },
        },
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        # This requires the MCP manager - handled in loop.py
        return ToolResult(output="MCP resources listed via loop integration")


class ReadMcpResourceTool(Tool):
    name = "read_mcp_resource"
    description = READ_MCP_RESOURCE_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "MCP server name.",
            },
            "uri": {
                "type": "string",
                "description": "Resource URI to read.",
            },
        },
        "required": ["server", "uri"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        return ToolResult(output="MCP resource read via loop integration")
