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


def validate_input(input: Any, schema: dict[str, Any]) -> list[str]:
    """Validate input against a JSON Schema-style schema.

    Returns a list of error messages (empty if valid).
    Handles: required fields, type checking, enum values, oneOf, arrays, objects.
    """
    if schema.get("type") != "object":
        return []
    if not isinstance(input, dict):
        return [f"Input must be an object, got {type(input).__name__}"]

    return _validate_object(input, schema)


def _type_error(path: str, expected: str, value: Any) -> str:
    actual = type(value).__name__
    if expected == "integer" and isinstance(value, float):
        actual = "float"
    return f"Field '{path}' must be {('an' if expected[0] in 'aeiou' else 'a')} {expected}, got {actual}"


def _validate_one_of(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    variants = schema.get("oneOf", [])
    if not variants:
        return []
    variant_errors: list[str] = []
    for variant in variants:
        errors = _validate_value(value, variant, path)
        if not errors:
            return []
        variant_errors.extend(errors)
    deduped = list(dict.fromkeys(variant_errors))
    if len(deduped) == 1:
        return deduped
    return [f"Field '{path}' does not match any allowed schema ({'; '.join(deduped[:2])})"]


def _validate_object(value: dict[str, Any], schema: dict[str, Any], path: str = "") -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in value:
            errors.append(f"Missing required field: '{field if not path else f'{path}.{field}'}'")

    for field, field_value in value.items():
        if field not in properties:
            continue
        field_path = field if not path else f"{path}.{field}"
        errors.extend(_validate_value(field_value, properties[field], field_path))
    return errors


def _validate_array(value: list[Any], schema: dict[str, Any], path: str) -> list[str]:
    item_schema = schema.get("items")
    if not item_schema:
        return []
    errors: list[str] = []
    for i, item in enumerate(value):
        errors.extend(_validate_value(item, item_schema, f"{path}[{i}]"))
    return errors


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    if "oneOf" in schema:
        return _validate_one_of(value, schema, path)

    errors: list[str] = []
    field_type = schema.get("type")

    if field_type == "string":
        if not isinstance(value, str):
            errors.append(_type_error(path, "string", value))
            return errors
    elif field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(_type_error(path, "integer", value))
            return errors
    elif field_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(_type_error(path, "number", value))
            return errors
    elif field_type == "boolean":
        if not isinstance(value, bool):
            errors.append(_type_error(path, "boolean", value))
            return errors
    elif field_type == "array":
        if not isinstance(value, list):
            errors.append(_type_error(path, "array", value))
            return errors
        errors.extend(_validate_array(value, schema, path))
    elif field_type == "object":
        if not isinstance(value, dict):
            errors.append(_type_error(path, "object", value))
            return errors
        errors.extend(_validate_object(value, schema, path))

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        errors.append(f"Field '{path}' must be one of: {allowed}")

    return errors


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

    @property
    def requires_confirmation(self) -> bool:
        """
        Whether this tool requires explicit user confirmation for each execution.
        Used for high-risk tools (e.g. computer control, browser automation).
        """
        return False

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
    from ccb.tools.code_interpreter import CodeInterpreterTool
    from ccb.tools.image_gen import ImageGenerationTool
    from ccb.tools.computer_use import ComputerUseTool
    from ccb.tools.chrome_use import ChromeUseTool

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
        CodeInterpreterTool(),
        ImageGenerationTool(),
        TodoWriteTool(),
        NotebookEditTool(),
        AskUserQuestionTool(),
        TaskStopTool(),
        TaskOutputTool(),
        ListMcpResourcesTool(),
        ReadMcpResourceTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        ComputerUseTool(),
        ChromeUseTool(),
    ]:
        registry.register(tool)
    return registry
