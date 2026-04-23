"""Memdir — team memory with shared memory files, scan, and relevance.

Implements the memory directory system for storing, scanning, and retrieving
project/team memories. Memories are stored as markdown files in a directory
structure, with relevance scoring for context injection.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A single memory file entry."""
    path: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0
    relevance_score: float = 0.0
    source: str = "auto"  # auto | user | team | dream

    @property
    def age_hours(self) -> float:
        return (time.time() - self.updated_at) / 3600 if self.updated_at else 0


def _default_memory_dir() -> Path:
    return Path.home() / ".claude" / "memory"


def _team_memory_dir(project_dir: str = "") -> Path:
    """Team memory lives in the project's .claude/ directory."""
    if project_dir:
        return Path(project_dir) / ".claude" / "team-memory"
    return _default_memory_dir() / "team"


@dataclass
class Memdir:
    """Memory directory manager."""
    root: Path = field(default_factory=_default_memory_dir)
    _entries: dict[str, MemoryEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def scan(self) -> list[MemoryEntry]:
        """Scan the memory directory for all .md files."""
        self._entries.clear()
        if not self.root.is_dir():
            return []

        for md_file in self.root.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                title = _extract_title(content, md_file.stem)
                tags = _extract_tags(content)
                stat = md_file.stat()

                entry = MemoryEntry(
                    path=str(md_file),
                    title=title,
                    content=content,
                    tags=tags,
                    created_at=stat.st_ctime,
                    updated_at=stat.st_mtime,
                )
                self._entries[str(md_file)] = entry
            except Exception as e:
                logger.debug("Failed to scan memory file %s: %s", md_file, e)

        return list(self._entries.values())

    def find_relevant(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> list[MemoryEntry]:
        """Find memories relevant to a query using keyword matching.

        Uses TF-IDF-like scoring with recency boost.
        """
        if not self._entries:
            self.scan()

        query_terms = _tokenize(query.lower())
        if not query_terms:
            return []

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries.values():
            score = _relevance_score(entry, query_terms)
            if score >= min_score:
                entry.relevance_score = score
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]

    def add_memory(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        source: str = "auto",
        subdir: str = "",
    ) -> MemoryEntry:
        """Add a new memory file."""
        safe_name = re.sub(r"[^\w\-]", "_", title.lower())[:60]
        if subdir:
            target = self.root / subdir / f"{safe_name}.md"
        else:
            target = self.root / f"{safe_name}.md"

        target.parent.mkdir(parents=True, exist_ok=True)

        # Build content
        lines = [f"# {title}", ""]
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
            lines.append("")
        lines.append(content)
        full_content = "\n".join(lines)

        target.write_text(full_content, encoding="utf-8")

        entry = MemoryEntry(
            path=str(target),
            title=title,
            content=full_content,
            tags=tags or [],
            created_at=time.time(),
            updated_at=time.time(),
            source=source,
        )
        self._entries[str(target)] = entry
        return entry

    def update_memory(self, path: str, content: str) -> bool:
        """Update an existing memory file."""
        p = Path(path)
        if not p.exists():
            return False
        p.write_text(content, encoding="utf-8")
        if path in self._entries:
            self._entries[path].content = content
            self._entries[path].updated_at = time.time()
        return True

    def delete_memory(self, path: str) -> bool:
        """Delete a memory file."""
        p = Path(path)
        if p.exists():
            p.unlink()
            self._entries.pop(path, None)
            return True
        return False

    def get_entry(self, path: str) -> MemoryEntry | None:
        return self._entries.get(path)

    @property
    def entries(self) -> list[MemoryEntry]:
        return list(self._entries.values())

    @property
    def count(self) -> int:
        return len(self._entries)

    def build_context_block(
        self,
        query: str,
        max_tokens: int = 2000,
    ) -> str:
        """Build a context block from relevant memories for injection into prompts."""
        relevant = self.find_relevant(query, top_k=5)
        if not relevant:
            return ""

        lines = ["<project_memories>"]
        est_tokens = 0
        for entry in relevant:
            content = entry.content
            entry_tokens = len(content) // 4
            if est_tokens + entry_tokens > max_tokens:
                # Truncate
                remaining = (max_tokens - est_tokens) * 4
                content = content[:remaining] + "\n..."
            lines.append(f"<memory title=\"{entry.title}\" score=\"{entry.relevance_score:.2f}\">")
            lines.append(content)
            lines.append("</memory>")
            est_tokens += len(content) // 4
            if est_tokens >= max_tokens:
                break
        lines.append("</project_memories>")
        return "\n".join(lines)

    def summary(self) -> dict[str, Any]:
        if not self._entries:
            self.scan()
        sources: dict[str, int] = {}
        for e in self._entries.values():
            sources[e.source] = sources.get(e.source, 0) + 1
        return {
            "root": str(self.root),
            "count": len(self._entries),
            "by_source": sources,
            "total_size": sum(len(e.content) for e in self._entries.values()),
        }


# ── Team Memory ────────────────────────────────────────────────

class TeamMemdir(Memdir):
    """Team memory directory — shared across the team via project repo."""

    def __init__(self, project_dir: str = "") -> None:
        root = _team_memory_dir(project_dir)
        super().__init__(root=root)

    def sync_from_personal(self, personal: Memdir, tags: list[str] | None = None) -> int:
        """Copy relevant memories from personal memdir to team memdir."""
        copied = 0
        for entry in personal.entries:
            if tags and not any(t in entry.tags for t in tags):
                continue
            # Check if already exists
            target = self.root / Path(entry.path).name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(entry.content, encoding="utf-8")
                copied += 1
        return copied

    def get_team_prompts(self) -> str:
        """Build team memory prompts for system prompt injection."""
        self.scan()
        if not self._entries:
            return ""
        lines = ["<team_memories>"]
        for entry in self._entries.values():
            lines.append(f"## {entry.title}")
            lines.append(entry.content[:500])
            lines.append("")
        lines.append("</team_memories>")
        return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────

def _extract_title(content: str, fallback: str) -> str:
    """Extract title from markdown heading."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _extract_tags(content: str) -> list[str]:
    """Extract tags from 'Tags: ...' line."""
    for line in content.split("\n"):
        line = line.strip()
        if line.lower().startswith("tags:"):
            raw = line[5:].strip()
            return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"\w+", text.lower())


def _relevance_score(entry: MemoryEntry, query_terms: list[str]) -> float:
    """Score an entry's relevance to query terms."""
    content_terms = set(_tokenize(entry.content.lower()))
    title_terms = set(_tokenize(entry.title.lower()))
    tag_terms = set(t.lower() for t in entry.tags)

    if not query_terms:
        return 0.0

    # Term match scoring
    title_hits = sum(1 for t in query_terms if t in title_terms)
    content_hits = sum(1 for t in query_terms if t in content_terms)
    tag_hits = sum(1 for t in query_terms if t in tag_terms)

    # Weighted score
    score = (
        title_hits * 3.0 +
        tag_hits * 2.0 +
        content_hits * 1.0
    ) / (len(query_terms) * 3.0)

    # Recency boost (memories updated in last 24h get a 20% boost)
    if entry.age_hours < 24:
        score *= 1.2
    elif entry.age_hours > 720:  # 30 days
        score *= 0.8

    return min(score, 1.0)


# ── Module singletons ──────────────────────────────────────────

_personal: Memdir | None = None
_team: TeamMemdir | None = None


def get_memdir() -> Memdir:
    global _personal
    if _personal is None:
        _personal = Memdir()
    return _personal


def get_team_memdir(project_dir: str = "") -> TeamMemdir:
    global _team
    if _team is None:
        _team = TeamMemdir(project_dir)
    return _team
