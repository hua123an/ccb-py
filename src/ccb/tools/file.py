"""File tools - read, write, edit."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import FILE_READ_PROMPT, FILE_WRITE_PROMPT, FILE_EDIT_PROMPT


class FileReadTool(Tool):
    name = "file_read"
    description = FILE_READ_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute or relative path to the file."},
            "offset": {"type": "integer", "description": "1-indexed line to start reading from."},
            "limit": {"type": "integer", "description": "Number of lines to read."},
        },
        "required": ["file_path"],
    }

    @property
    def needs_permission(self) -> bool:
        return False

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        file_path = input["file_path"]
        p = Path(file_path) if os.path.isabs(file_path) else Path(cwd) / file_path

        if not p.exists():
            return ToolResult(output=f"File not found: {p}", is_error=True)
        if not p.is_file():
            return ToolResult(output=f"Not a file: {p}", is_error=True)

        try:
            content = p.read_text(errors="replace")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = content.splitlines(keepends=True)
        offset = input.get("offset")
        limit = input.get("limit")

        if offset:
            start = max(0, offset - 1)
            end = start + limit if limit else len(lines)
            lines = lines[start:end]
            # Add line numbers
            numbered = []
            for i, line in enumerate(lines, start=start + 1):
                numbered.append(f"{i:6d}\t{line.rstrip()}")
            return ToolResult(output="\n".join(numbered))

        # Full file with line numbers
        numbered = [f"{i:6d}\t{line.rstrip()}" for i, line in enumerate(lines, 1)]
        output = "\n".join(numbered)

        if len(output) > 200_000:
            output = output[:200_000] + f"\n... (truncated, {len(lines)} total lines)"

        return ToolResult(output=output)


class FileWriteTool(Tool):
    name = "file_write"
    description = FILE_WRITE_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to write."},
            "content": {"type": "string", "description": "Content to write to the file."},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        file_path = input["file_path"]
        content = input["content"]
        p = Path(file_path) if os.path.isabs(file_path) else Path(cwd) / file_path

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return ToolResult(output=f"Wrote {len(content)} chars to {p}")
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)


class FileEditTool(Tool):
    name = "file_edit"
    description = FILE_EDIT_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit."},
            "old_string": {"type": "string", "description": "Exact string to find and replace."},
            "new_string": {"type": "string", "description": "String to replace old_string with."},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences. Default false."},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        file_path = input["file_path"]
        old_string = input["old_string"]
        new_string = input["new_string"]
        replace_all = input.get("replace_all", False)
        p = Path(file_path) if os.path.isabs(file_path) else Path(cwd) / file_path

        if not p.exists():
            # Create new file if old_string is empty
            if not old_string:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(new_string)
                    n = new_string.count("\n") + (1 if new_string and not new_string.endswith("\n") else 0)
                    return ToolResult(output=f"Created {p} (+{n} lines)")
                except Exception as e:
                    return ToolResult(output=f"Error creating file: {e}", is_error=True)
            return ToolResult(output=f"File not found: {p}", is_error=True)

        try:
            content = p.read_text()
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        count = content.count(old_string)
        if count == 0:
            return ToolResult(output="old_string not found in file", is_error=True)
        if not replace_all and count > 1:
            return ToolResult(
                output=f"old_string found {count} times (must be unique, or set replace_all=true)", is_error=True
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        # Compute line diff
        old_lines = content.splitlines()
        new_lines = new_content.splitlines()
        added = max(0, len(new_lines) - len(old_lines))
        removed = max(0, len(old_lines) - len(new_lines))
        # More precise: count actual changed lines from old/new string
        old_str_lines = old_string.count("\n") + (1 if old_string else 0)
        new_str_lines = new_string.count("\n") + (1 if new_string else 0)
        replacements = count if replace_all else 1
        lines_removed = old_str_lines * replacements
        lines_added = new_str_lines * replacements

        try:
            p.write_text(new_content)
            parts = [str(p)]
            if lines_added > 0:
                parts.append(f"+{lines_added}")
            if lines_removed > 0:
                parts.append(f"-{lines_removed}")
            if replace_all and count > 1:
                parts.append(f"({count} replacements)")
            return ToolResult(output=" ".join(parts))
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
