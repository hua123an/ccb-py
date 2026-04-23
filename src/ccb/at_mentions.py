"""@ mention file selection — extract, resolve and attach referenced files.

Supports:
  @filename.py              — relative path from cwd
  @path/to/file.py          — deeper relative path
  @~/path/file.py           — home-relative
  @/absolute/path/file.py   — absolute path
  @"file with spaces.py"    — quoted for spaces
  @file.py#L10-20           — line range (offset-limit)
  @directory/                — directory listing

File index is built from `git ls-files` (fast, respects .gitignore) with
fallback to os.walk for non-git directories.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


# ── Regex patterns ──────────────────────────────────────────────────────

# Match @"quoted path" (with optional line range)
_QUOTED_AT_RE = re.compile(r'(?:^|\s)@"([^"]+)"')

# Match @path (non-quoted, stops at whitespace).
# \w already covers [a-zA-Z0-9_] plus Unicode letters/digits in Python 3.
_REGULAR_AT_RE = re.compile(r'(?:^|\s)@([\w./%~:@\-\\()\[\]#]+)')

# Line range suffix: #L10 or #L10-20
_LINE_RANGE_RE = re.compile(r'^([^#]+)(?:#L(\d+)(?:-(\d+))?)?$')

# Maximum file size to inline (500 KB)
MAX_INLINE_BYTES = 500_000

# Max directory entries to list
MAX_DIR_ENTRIES = 1000


# ── File index ──────────────────────────────────────────────────────────

_file_cache: list[str] | None = None
_file_cache_cwd: str = ""


def _build_file_index(cwd: str) -> list[str]:
    """Build file index from git ls-files, falling back to os.walk."""
    global _file_cache, _file_cache_cwd
    if _file_cache is not None and _file_cache_cwd == cwd:
        return _file_cache

    files: list[str] = []
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode == 0:
            files = [f for f in result.stdout.splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if not files:
        # Fallback: walk top 4 levels (covers most project structures)
        try:
            for root, dirs, fnames in os.walk(cwd):
                depth = root[len(cwd):].count(os.sep)
                if depth >= 4:
                    dirs.clear()
                    continue
                # Skip hidden and common junk dirs
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in (
                        "node_modules", "__pycache__", ".git", "venv", ".venv",
                        "dist", "build", ".tox", ".mypy_cache",
                    )
                ]
                for fn in fnames:
                    if not fn.startswith("."):
                        rel = os.path.relpath(os.path.join(root, fn), cwd)
                        files.append(rel)
                if len(files) > 20000:
                    break
        except OSError:
            pass

    _file_cache = files
    _file_cache_cwd = cwd
    return files


def invalidate_file_cache() -> None:
    """Force rebuild on next query (e.g. after git operations)."""
    global _file_cache
    _file_cache = None


def get_file_suggestions(partial: str, cwd: str, max_results: int = 15) -> list[str]:
    """Return file paths matching partial (fuzzy prefix match).

    Supports:
      - Simple prefix: ``rea`` → ``README.md``
      - Path prefix: ``src/`` → all files under src/
      - Fuzzy: ``srccli`` → ``src/ccb/cli.py`` (subsequence match)
    """
    files = _build_file_index(cwd)
    if not partial:
        # Show top-level files/dirs when @ is typed with nothing after it
        return _top_level_entries(cwd, max_results)

    partial_lower = partial.lower()
    exact: list[str] = []
    prefix: list[str] = []
    contains: list[str] = []
    subseq: list[str] = []

    for f in files:
        fl = f.lower()
        if fl == partial_lower:
            exact.append(f)
        elif fl.startswith(partial_lower):
            prefix.append(f)
        elif partial_lower in fl:
            contains.append(f)
        elif _is_subsequence(partial_lower, fl):
            subseq.append(f)

    # Also check directories
    dirs = _get_directories(files)
    for d in dirs:
        dl = d.lower()
        if dl.startswith(partial_lower):
            if d not in prefix:
                prefix.append(d + "/")

    results = exact + prefix + contains + subseq
    return results[:max_results]


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(c in it for c in needle)


def _get_directories(files: list[str]) -> list[str]:
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
    return sorted(dirs)


def _top_level_entries(cwd: str, max_results: int) -> list[str]:
    """Show top-level files and directories."""
    try:
        entries = sorted(os.listdir(cwd))
        results: list[str] = []
        for e in entries:
            if e.startswith("."):
                continue
            full = os.path.join(cwd, e)
            if os.path.isdir(full):
                results.append(e + "/")
            else:
                results.append(e)
            if len(results) >= max_results:
                break
        return results
    except OSError:
        return []


# ── Extraction ──────────────────────────────────────────────────────────

def extract_at_mentions(text: str) -> list[str]:
    """Extract all @-mentioned file references from input text.

    Returns deduplicated list of raw mention strings (with line range if any).
    """
    quoted: list[str] = []
    regular: list[str] = []

    for m in _QUOTED_AT_RE.finditer(text):
        if m.group(1):
            quoted.append(m.group(1))

    for m in _REGULAR_AT_RE.finditer(text):
        ref = m.group(1)
        # Skip if it starts with " (already handled as quoted)
        if ref and not ref.startswith('"'):
            regular.append(ref)

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for ref in quoted + regular:
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def parse_mention(mention: str) -> tuple[str, int | None, int | None]:
    """Parse a mention into (filename, line_start, line_end).

    Examples:
      "file.py"       → ("file.py", None, None)
      "file.py#L10"   → ("file.py", 10, 10)
      "file.py#L5-20" → ("file.py", 5, 20)
    """
    m = _LINE_RANGE_RE.match(mention)
    if not m:
        return mention, None, None
    filename = m.group(1)
    line_start = int(m.group(2)) if m.group(2) else None
    line_end = int(m.group(3)) if m.group(3) else line_start
    return filename, line_start, line_end


def strip_at_mentions(text: str) -> str:
    """Remove @mentions from input text, returning the clean text."""
    # Remove quoted @mentions
    result = _QUOTED_AT_RE.sub(lambda m: m.group(0)[:1] if m.group(0)[0] == ' ' else '', text)
    # Remove regular @mentions
    result = _REGULAR_AT_RE.sub(lambda m: m.group(0)[:1] if m.group(0)[0] == ' ' else '', result)
    return result.strip()


# ── Resolution ──────────────────────────────────────────────────────────

def resolve_mention(mention: str, cwd: str) -> dict[str, Any] | None:
    """Resolve a single @ mention to a content dict.

    Returns a dict suitable for inclusion as a file attachment in the message:
      {"filename": ..., "content": ..., "source_path": ..., "type": "file"|"directory"}

    Returns None if the path cannot be resolved or read.
    """
    filename, line_start, line_end = parse_mention(mention)

    # Expand ~ and resolve relative to cwd
    if filename.startswith("~"):
        path = Path(filename).expanduser()
    elif os.path.isabs(filename):
        path = Path(filename)
    else:
        path = Path(cwd) / filename

    path = path.resolve()

    if not path.exists():
        return None

    # Directory listing
    if path.is_dir():
        try:
            entries = sorted(os.listdir(path))
            truncated = len(entries) > MAX_DIR_ENTRIES
            names = entries[:MAX_DIR_ENTRIES]
            if truncated:
                names.append(f"… and {len(entries) - MAX_DIR_ENTRIES} more entries")
            return {
                "filename": filename,
                "content": "\n".join(names),
                "source_path": str(path),
                "type": "directory",
            }
        except OSError:
            return None

    # File reading
    if not path.is_file():
        return None

    try:
        raw = path.read_bytes()[:MAX_INLINE_BYTES]
        # Binary check
        if b"\x00" in raw[:8192]:
            # Check if image — delegate to image handling
            from ccb.images import IMAGE_EXTENSIONS
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                from ccb.images import read_image_from_path
                img = read_image_from_path(str(path))
                if img:
                    return {
                        "filename": path.name,
                        "content": None,
                        "source_path": str(path),
                        "type": "image",
                        "image": img.to_dict(),
                    }
            return None  # binary non-image

        text = raw.decode("utf-8", errors="replace")

        # Apply line range
        if line_start is not None:
            lines = text.splitlines()
            start = max(0, line_start - 1)  # 1-indexed → 0-indexed
            end = line_end if line_end else len(lines)
            text = "\n".join(lines[start:end])

        return {
            "filename": filename,
            "content": text,
            "source_path": str(path),
            "type": "file",
        }
    except (OSError, PermissionError):
        return None


def resolve_all_mentions(text: str, cwd: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve all @ mentions in the input text.

    Returns:
        (clean_text, file_dicts, image_dicts)
        - clean_text: input with @mentions removed
        - file_dicts: list of {"filename", "content"} for text files/dirs
        - image_dicts: list of ImageContent dicts for images
    """
    mentions = extract_at_mentions(text)
    if not mentions:
        return text, [], []

    file_dicts: list[dict[str, Any]] = []
    image_dicts: list[dict[str, Any]] = []

    for mention in mentions:
        resolved = resolve_mention(mention, cwd)
        if resolved is None:
            continue

        if resolved["type"] == "image":
            image_dicts.append(resolved["image"])
        else:
            file_dicts.append({
                "filename": resolved["filename"],
                "content": resolved["content"],
                "source_path": resolved.get("source_path", ""),
                "mime_type": "text/plain",
            })

    clean_text = strip_at_mentions(text)
    return clean_text, file_dicts, image_dicts
