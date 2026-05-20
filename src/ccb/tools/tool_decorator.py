"""@tool decorator - easy custom tool creation from async functions.

Inspired by Anthropic Agent SDK's @tool decorator. Lets users define
tools from simple async functions with type annotations.

Usage:
    from ccb.tools.tool_decorator import tool

    @tool(
        name="my_tool",
        description="Does something useful",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    async def my_tool(x: str) -> dict:
        return {"result": f"Processed {x}"}
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Awaitable

from ccb.tools.base import Tool, ToolResult


class DecoratedTool(Tool):
    """Tool created from a decorated async function."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Awaitable[dict[str, Any] | str]],
        needs_perm: bool = True,
    ):
        self._name = name
        self._description = description
        self._input_schema = input_schema
        self._handler = handler
        self._needs_perm = needs_perm

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def needs_permission(self) -> bool:
        return self._needs_perm

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        try:
            # Pass cwd if the handler accepts it
            sig = inspect.signature(self._handler)
            if "cwd" in sig.parameters:
                result = await self._handler(input, cwd=cwd)
            else:
                result = await self._handler(input)

            if isinstance(result, str):
                return ToolResult(output=result)
            if isinstance(result, dict):
                if result.get("error"):
                    return ToolResult(output=str(result["error"]), is_error=True)
                import json
                return ToolResult(output=json.dumps(result, ensure_ascii=False, indent=2))
            return ToolResult(output=str(result))
        except Exception as e:
            return ToolResult(output=f"Tool error: {e}", is_error=True)


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any] | type | None = None,
    annotations: Any | None = None,
    needs_permission: bool = True,
) -> Callable:
    """Decorator to create a Tool from an async function.

    Args:
        name: Tool name (used by the model to call it)
        description: What the tool does
        input_schema: JSON Schema for the input (dict or Pydantic model)
        annotations: Optional MCP-style tool annotations
        needs_permission: Whether this tool requires user permission

    Example:
        @tool(name="add", description="Add two numbers",
              input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}})
        async def add(input: dict) -> dict:
            return {"result": input["a"] + input["b"]}
    """
    # Handle Pydantic models as input_schema
    schema = input_schema
    if schema is not None and not isinstance(schema, dict):
        # Try to convert Pydantic model to JSON schema
        try:
            if hasattr(schema, "model_json_schema"):
                schema = schema.model_json_schema()
            elif hasattr(schema, "schema"):
                schema = schema.schema()
        except Exception:
            schema = {"type": "object", "properties": {}}

    if schema is None:
        schema = {"type": "object", "properties": {}}

    def decorator(func: Callable) -> Callable:
        t = DecoratedTool(
            name=name,
            description=description,
            input_schema=schema,  # type: ignore
            handler=func,
            needs_perm=needs_permission,
        )
        # Attach the Tool object to the function for easy retrieval
        setattr(func, "_ccb_tool", t)
        return func

    return decorator


def register_decorated_tools(registry, module: Any) -> int:
    """Scan a module for @tool-decorated functions and register them.

    Returns the number of tools registered.
    """
    count = 0
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_ccb_tool"):
            registry.register(obj._ccb_tool)
            count += 1
    return count
