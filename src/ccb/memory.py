"""Session memory system for ccb-py.

Provides cross-session memory extraction, storage, and retrieval.
Memories are stored in ~/.claude/memory/ as JSON files.
"""
from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


_MEMORY_DIR = Path.home() / ".claude" / "memory"


@dataclass
class Memory:
    id: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str = ""  # session id or "user"
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0
    relevance_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Memory:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class MemoryStore:
    """Persistent memory store backed by JSON files."""

    def __init__(self, memory_dir: Path | None = None):
        self.dir = memory_dir or _MEMORY_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self.dir / "index.json"
        self._index: dict[str, dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        if self._index_file.exists():
            try:
                self._index = json.loads(self._index_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _save_index(self) -> None:
        self._index_file.write_text(json.dumps(self._index, indent=2, ensure_ascii=False))

    def _memory_file(self, mid: str) -> Path:
        return self.dir / f"{mid}.json"

    @staticmethod
    def _generate_id(content: str) -> str:
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"mem_{h}_{int(time.time())}"

    # -- CRUD --

    def add(self, content: str, tags: list[str] | None = None, source: str = "") -> Memory:
        mid = self._generate_id(content)
        now = time.time()
        mem = Memory(
            id=mid, content=content, tags=tags or [],
            source=source, created_at=now, updated_at=now,
        )
        self._memory_file(mid).write_text(json.dumps(mem.to_dict(), ensure_ascii=False, indent=2))
        self._index[mid] = {"tags": mem.tags, "preview": content[:100], "created_at": now}
        self._save_index()
        return mem

    def get(self, mid: str) -> Memory | None:
        f = self._memory_file(mid)
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text())
            mem = Memory.from_dict(data)
            mem.access_count += 1
            f.write_text(json.dumps(mem.to_dict(), ensure_ascii=False, indent=2))
            return mem
        except (json.JSONDecodeError, OSError):
            return None

    def update(self, mid: str, content: str | None = None, tags: list[str] | None = None) -> Memory | None:
        mem = self.get(mid)
        if not mem:
            return None
        if content is not None:
            mem.content = content
        if tags is not None:
            mem.tags = tags
        mem.updated_at = time.time()
        self._memory_file(mid).write_text(json.dumps(mem.to_dict(), ensure_ascii=False, indent=2))
        self._index[mid] = {"tags": mem.tags, "preview": mem.content[:100], "created_at": mem.created_at}
        self._save_index()
        return mem

    def delete(self, mid: str) -> bool:
        f = self._memory_file(mid)
        if f.exists():
            f.unlink()
        if mid in self._index:
            del self._index[mid]
            self._save_index()
            return True
        return False

    def list_all(self, tag: str | None = None) -> list[Memory]:
        memories = []
        for mid in self._index:
            if tag and tag not in self._index[mid].get("tags", []):
                continue
            mem = self.get(mid)
            if mem:
                memories.append(mem)
        memories.sort(key=lambda m: m.updated_at, reverse=True)
        return memories

    def search(self, query: str, limit: int = 10) -> list[Memory]:
        """Simple keyword search across memories."""
        query_lower = query.lower()
        results = []
        for mid in self._index:
            entry = self._index[mid]
            preview = entry.get("preview", "").lower()
            tags_str = " ".join(entry.get("tags", [])).lower()
            if query_lower in preview or query_lower in tags_str:
                mem = self.get(mid)
                if mem:
                    # Score by relevance
                    score = 0.0
                    if query_lower in mem.content.lower():
                        score += 1.0
                    if any(query_lower in t.lower() for t in mem.tags):
                        score += 0.5
                    score += mem.access_count * 0.1
                    mem.relevance_score = score
                    results.append(mem)
        results.sort(key=lambda m: m.relevance_score, reverse=True)
        return results[:limit]

    def clear(self) -> int:
        count = len(self._index)
        for mid in list(self._index):
            f = self._memory_file(mid)
            if f.exists():
                f.unlink()
        self._index = {}
        self._save_index()
        return count

    @property
    def count(self) -> int:
        return len(self._index)


# ---------------------------------------------------------------------------
# Memory extraction prompt
# ---------------------------------------------------------------------------

def generate_extract_memories_prompt(conversation_text: str) -> str:
    """Build a prompt for the LLM to extract memorable facts from a conversation."""
    return (
        "Extract important facts, preferences, and decisions from this conversation "
        "that would be useful to remember in future sessions.\n\n"
        "For each memory, provide:\n"
        "- content: the fact or preference\n"
        "- tags: relevant keywords\n\n"
        "Format as JSON array: [{\"content\": \"...\", \"tags\": [\"...\"]}]\n\n"
        f"Conversation:\n{conversation_text[:10000]}\n\n"
        "Reply with ONLY the JSON array."
    )


def parse_extracted_memories(llm_output: str) -> list[dict[str, Any]]:
    """Parse LLM output from memory extraction."""
    try:
        # Try to find JSON array in the output
        start = llm_output.find("[")
        end = llm_output.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(llm_output[start:end])
    except json.JSONDecodeError:
        pass
    return []


# ---------------------------------------------------------------------------
# Auto-extraction engine
# ---------------------------------------------------------------------------

class MemoryExtractor:
    """Automatically extracts and stores memories from conversations."""

    def __init__(self, store: MemoryStore | None = None, provider: Any = None):
        self._store = store or get_store()
        self._provider = provider
        self._enabled = True

    async def extract_from_session(self, messages: list[Any], session_id: str = "") -> list[Memory]:
        """Extract memories from a list of conversation messages."""
        if not self._enabled or not self._provider:
            return []

        from ccb.api.base import Message, Role

        # Build conversation text
        conv_text = "\n".join(
            f"[{m.role.value}]: {(m.content or '')[:1000]}"
            for m in messages
            if m.content
        )

        if len(conv_text) < 100:
            return []  # Too short

        prompt = generate_extract_memories_prompt(conv_text)
        extract_messages = [Message(role=Role.USER, content=prompt)]

        result = ""
        try:
            async for event in self._provider.stream(
                messages=extract_messages,
                tools=[],
                system="You extract important facts from conversations. Reply with JSON only.",
                max_tokens=2048,
            ):
                if event.type == "text":
                    result += event.text
        except Exception:
            return []

        parsed = parse_extracted_memories(result)
        stored = []
        for item in parsed:
            content = item.get("content", "")
            tags = item.get("tags", [])
            if content and len(content) > 10:
                # Dedup: check if similar memory exists
                existing = self._store.search(content[:50], limit=3)
                if not any(self._similarity(content, e.content) > 0.8 for e in existing):
                    mem = self._store.add(content, tags=tags, source=session_id)
                    stored.append(mem)
        return stored

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Simple word-overlap similarity (Jaccard)."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled


# ---------------------------------------------------------------------------
# Memory decay — reduce relevance of old, unused memories
# ---------------------------------------------------------------------------

def apply_decay(store: MemoryStore | None = None, decay_rate: float = 0.95, min_score: float = 0.01) -> int:
    """Apply time-based decay to all memories. Returns count of pruned memories."""
    s = store or get_store()
    pruned = 0
    now = time.time()
    for mid in list(s._index):
        # Read raw file without incrementing access count
        f = s._memory_file(mid)
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        access_count = data.get("access_count", 0)
        created = data.get("created_at", now)
        updated = data.get("updated_at", created)
        last_active = max(updated, created)
        days_old = (now - last_active) / 86400
        decay = decay_rate ** days_old
        effective_score = decay * (1 + access_count * 0.1)
        if effective_score < min_score and access_count <= 1:
            s.delete(mid)
            pruned += 1
    return pruned


# ---------------------------------------------------------------------------
# Context injection — build memory context for system prompt
# ---------------------------------------------------------------------------

def build_memory_context(query: str = "", limit: int = 10, store: MemoryStore | None = None) -> str:
    """Build a memory context string for injection into the system prompt."""
    s = store or get_store()
    if query:
        memories = s.search(query, limit=limit)
    else:
        memories = s.list_all()[:limit]

    if not memories:
        return ""

    lines = ["<memories>"]
    for m in memories:
        tags_str = f" [tags: {', '.join(m.tags)}]" if m.tags else ""
        lines.append(f"- {m.content}{tags_str}")
    lines.append("</memories>")
    return "\n".join(lines)


# Module-level singleton
_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


_extractor: MemoryExtractor | None = None


def get_extractor() -> MemoryExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor()
    return _extractor
