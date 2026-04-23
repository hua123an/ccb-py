"""Notebook tool - edit Jupyter notebooks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ccb.tools.base import Tool, ToolResult
from ccb.tools.tool_prompts import NOTEBOOK_EDIT_PROMPT


class NotebookEditTool(Tool):
    name = "notebook_edit"
    description = NOTEBOOK_EDIT_PROMPT
    input_schema = {
        "type": "object",
        "properties": {
            "notebook_path": {"type": "string", "description": "Path to the .ipynb file."},
            "cell_number": {"type": "integer", "description": "0-indexed cell number."},
            "new_source": {"type": "string", "description": "New cell content."},
            "cell_type": {"type": "string", "enum": ["code", "markdown"], "description": "Cell type."},
            "edit_mode": {"type": "string", "enum": ["replace", "insert"], "description": "Edit mode."},
        },
        "required": ["notebook_path", "new_source"],
    }

    async def execute(self, input: dict[str, Any], cwd: str) -> ToolResult:
        nb_path = input["notebook_path"]
        p = Path(nb_path) if os.path.isabs(nb_path) else Path(cwd) / nb_path

        if not p.exists():
            return ToolResult(output=f"Notebook not found: {p}", is_error=True)

        try:
            nb = json.loads(p.read_text())
        except Exception as e:
            return ToolResult(output=f"Error reading notebook: {e}", is_error=True)

        cells = nb.get("cells", [])
        cell_num = input.get("cell_number", 0)
        new_source = input["new_source"]
        cell_type = input.get("cell_type", "code")
        mode = input.get("edit_mode", "replace")

        new_cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": new_source.splitlines(keepends=True),
            **({"outputs": [], "execution_count": None} if cell_type == "code" else {}),
        }

        if mode == "insert":
            cells.insert(cell_num, new_cell)
        else:
            if cell_num >= len(cells):
                return ToolResult(output=f"Cell {cell_num} out of range (0-{len(cells)-1})", is_error=True)
            cells[cell_num] = new_cell

        nb["cells"] = cells
        try:
            p.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
            return ToolResult(output=f"{'Inserted' if mode == 'insert' else 'Replaced'} cell {cell_num}")
        except Exception as e:
            return ToolResult(output=f"Error writing notebook: {e}", is_error=True)
