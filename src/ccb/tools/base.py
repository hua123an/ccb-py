"""Tool base class and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


class Tool(ABC):
    """Base class for all tools."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def needs_permission(self) -> bool:
        """Whether this tool requires user permission before execution."""
        return True

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult: ...


class ToolRegistry:
    """Central tool registry."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_schemas(self) -> list[dict[str, Any]]:
        return [t.to_api_schema() for t in self._tools.values()]

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())


def create_default_registry(cwd: str) -> ToolRegistry:
    """Create registry with all built-in tools."""
    from ccb.tools.bash import BashTool
    from ccb.tools.file import FileReadTool, FileWriteTool, FileEditTool
    from ccb.tools.grep import GrepTool
    from ccb.tools.glob_tool import GlobTool
    from ccb.tools.agent import AgentTool
    from ccb.tools.web import WebFetchTool, WebSearchTool
    from ccb.tools.todo import TodoWriteTool
    from ccb.tools.notebook import NotebookEditTool
    from ccb.tools.ask_user import AskUserQuestionTool
    from ccb.tools.task import TaskStopTool, TaskOutputTool
    from ccb.tools.mcp_resource import ListMcpResourcesTool, ReadMcpResourceTool
    from ccb.tools.plan import EnterPlanModeTool, ExitPlanModeTool

    registry = ToolRegistry()
    for tool in [
        BashTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GrepTool(),
        GlobTool(),
        AgentTool(),
        WebFetchTool(),
        WebSearchTool(),
        TodoWriteTool(),
        NotebookEditTool(),
        AskUserQuestionTool(),
        TaskStopTool(),
        TaskOutputTool(),
        ListMcpResourcesTool(),
        ReadMcpResourceTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
    ]:
        registry.register(tool)
    return registry
