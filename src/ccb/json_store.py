"""Shared JSON file helpers with atomic writes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar


T = TypeVar("T")


def read_json(path: Path, *, default: T | None = None) -> Any | T | None:
    """Read JSON from *path*, returning *default* when unreadable or missing."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return default


def write_json(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    default: Any | None = None,
    newline: bool = False,
) -> Path:
    """Atomically write JSON to *path* and return the final path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, default=default)
    if newline:
        payload += "\n"

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
