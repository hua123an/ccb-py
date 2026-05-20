"""Session memory system for ccb-py.

Provides cross-session memory extraction, storage, and retrieval.
Memories are stored in ~/.ccb/memory/ as JSON files.
"""
from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ccb.json_store import read_json, write_json


_MEMORY_DIR = Path.home() / ".ccb" / "memory"


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
    # Enhanced fields for persistent memory
    category: str = ""  # e.g., "user_preference", "project", "codebase", "task_pattern"
    pinned: bool = False  # pinned memories never deleted by decay
    importance: float = 1.0  # 0.5=low, 1.0=normal, 2.0=high importance
    expires_at: float = 0.0  # 0 means never expires
    metadata: dict[str, Any] = field(default_factory=dict)  # extra data like cwd, language, etc.
    # TencentDB-style layered memory
    layer: str = "L1"  # L0=raw, L1=atom, L2=scenario, L3=persona, L4=skill
    node_id: str = ""  # unique node identifier for traceable references
    evidence_refs: list[str] = field(default_factory=list)  # references to original evidence (node_ids)
    mermaid_diagram: str = ""  # Mermaid representation for compressed memory
    # Context offload thresholds
    offload_triggered: bool = False  # whether this memory triggered context offload
    compression_ratio: float = 0.0  # compression ratio when offloaded

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Memory:
        # Filter to only known fields for forward compatibility
        fields = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**fields)

    def is_expired(self) -> bool:
        """Check if memory has expired."""
        if self.expires_at <= 0:
            return False  # never expires
        import time
        return time.time() > self.expires_at

    def should_preserve(self, min_importance: float = 0.5) -> bool:
        """Check if memory should be preserved against decay."""
        return self.pinned or self.importance >= min_importance

    def get_trace_path(self) -> list[str]:
        """Get full trace path from current node back to original evidence."""
        path = [self.node_id] if self.node_id else [self.id]
        path.extend(self.evidence_refs)
        return path

    def to_mermaid_node(self) -> str:
        """Convert memory to a Mermaid node for symbolic representation."""
        if not self.node_id:
            self.node_id = f"MEM_{self.id[:12]}"
        label = self.content[:50].replace('"', "'").replace('\n', ' ')
        if len(self.content) > 50:
            label += "..."
        return f'{self.node_id}["{label}"]'


class MemoryStore:
    """Persistent memory store backed by JSON files."""

    def __init__(self, memory_dir: Path | None = None):
        self.dir = memory_dir or _MEMORY_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self.dir / "index.json"
        self._index: dict[str, dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        data = read_json(self._index_file, default={})
        self._index = data if isinstance(data, dict) else {}

    def _save_index(self) -> None:
        write_json(self._index_file, self._index, ensure_ascii=False)

    def _memory_file(self, mid: str) -> Path:
        return self.dir / f"{mid}.json"

    @staticmethod
    def _generate_id(content: str) -> str:
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"mem_{h}_{int(time.time())}"

    # -- CRUD --

    def add(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str = "",
        category: str = "",
        pinned: bool = False,
        importance: float = 1.0,
        expires_at: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        mid = self._generate_id(content)
        now = time.time()
        mem = Memory(
            id=mid,
            content=content,
            tags=tags or [],
            source=source,
            created_at=now,
            updated_at=now,
            category=category,
            pinned=pinned,
            importance=importance,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        write_json(self._memory_file(mid), mem.to_dict(), ensure_ascii=False)
        
        # Synchronize to SQLite semantic memory graph
        try:
            from ccb.memory_graph import MemoryGraphEngine
            engine = MemoryGraphEngine()
            engine.insert_node(mid, category or "general", content)
            if mem.evidence_refs:
                for ref in mem.evidence_refs:
                    engine.insert_edge(ref, mid, "RelatesTo")
        except Exception:
            pass

        self._index[mid] = {
            "tags": mem.tags,
            "preview": content[:100],
            "created_at": now,
            "category": category,
            "pinned": pinned,
        }
        self._save_index()
        return mem

    def get(self, mid: str, update_access: bool = True) -> Memory | None:
        f = self._memory_file(mid)
        if not f.exists():
            return None
        data = read_json(f)
        if not isinstance(data, dict):
            return None
        mem = Memory.from_dict(data)
        if update_access:
            mem.access_count += 1
            write_json(f, mem.to_dict(), ensure_ascii=False)
        return mem

    def update(
        self,
        mid: str,
        content: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        pinned: bool | None = None,
        importance: float | None = None,
        expires_at: float | None = None,
        layer: str | None = None,
        node_id: str | None = None,
        evidence_refs: list[str] | None = None,
        mermaid_diagram: str | None = None,
        offload_triggered: bool | None = None,
        compression_ratio: float | None = None,
    ) -> Memory | None:
        mem = self.get(mid, update_access=False)
        if not mem:
            return None
        if content is not None:
            mem.content = content
        if tags is not None:
            mem.tags = tags
        if category is not None:
            mem.category = category
        if pinned is not None:
            mem.pinned = pinned
        if importance is not None:
            mem.importance = importance
        if expires_at is not None:
            mem.expires_at = expires_at
        # TencentDB-style layer updates
        if layer is not None:
            mem.layer = layer
        if node_id is not None:
            mem.node_id = node_id
        if evidence_refs is not None:
            mem.evidence_refs = evidence_refs
        if mermaid_diagram is not None:
            mem.mermaid_diagram = mermaid_diagram
        if offload_triggered is not None:
            mem.offload_triggered = offload_triggered
        if compression_ratio is not None:
            mem.compression_ratio = compression_ratio
        write_json(self._memory_file(mid), mem.to_dict(), ensure_ascii=False)
        
        # Synchronize to SQLite semantic memory graph
        try:
            from ccb.memory_graph import MemoryGraphEngine
            engine = MemoryGraphEngine()
            engine.insert_node(mid, mem.category or "general", mem.content)
            if evidence_refs is not None:
                for ref in evidence_refs:
                    engine.insert_edge(ref, mid, "RelatesTo")
        except Exception:
            pass

        self._index[mid] = {
            "tags": mem.tags,
            "preview": mem.content[:100],
            "created_at": mem.created_at,
            "category": mem.category,
            "pinned": mem.pinned,
        }
        self._save_index()
        return mem

    def pin(self, mid: str) -> Memory | None:
        """Pin a memory so it's never deleted by decay."""
        return self.update(mid, pinned=True)

    def unpin(self, mid: str) -> Memory | None:
        """Unpin a memory."""
        return self.update(mid, pinned=False)

    def set_importance(self, mid: str, importance: float) -> Memory | None:
        """Set importance level (0.5=low, 1.0=normal, 2.0=high)."""
        return self.update(mid, importance=importance)

    def set_category(self, mid: str, category: str) -> Memory | None:
        """Set category for a memory."""
        return self.update(mid, category=category)

    def delete(self, mid: str) -> bool:
        f = self._memory_file(mid)
        if f.exists():
            f.unlink()
        
        # Synchronize deletion to SQLite memory graph
        try:
            from ccb.memory_graph import MemoryGraphEngine
            import sqlite3
            import os
            db_path = os.path.expanduser("~/.ccb/memory_graph.db")
            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM memory_nodes WHERE id=?", (mid,))
        except Exception:
            pass

        if mid in self._index:
            del self._index[mid]
            self._save_index()
            return True
        return False

    def list_all(self, tag: str | None = None, category: str | None = None, include_expired: bool = False) -> list[Memory]:
        memories = []
        for mid in self._index:
            if tag and tag not in self._index[mid].get("tags", []):
                continue
            if category and self._index[mid].get("category", "") != category:
                continue
            mem = self.get(mid)
            if not mem:
                continue
            if not include_expired and mem.is_expired():
                self.delete(mid)  # auto-cleanup expired
                continue
            memories.append(mem)
        memories.sort(key=lambda m: (m.pinned, m.importance, m.updated_at), reverse=True)
        return memories

    def search(
        self,
        query: str,
        limit: int = 10,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Memory]:
        """Search with optional category and tag filters."""
        query_lower = query.lower()
        results = []
        for mid in self._index:
            if category and self._index[mid].get("category", "") != category:
                continue
            entry = self._index[mid]
            preview = entry.get("preview", "").lower()
            tags_str = " ".join(entry.get("tags", [])).lower()
            entry_tags = entry.get("tags", [])
            if tags and not all(t in entry_tags for t in tags):
                continue
            if query_lower and query_lower not in preview and query_lower not in tags_str:
                continue
            mem = self.get(mid)
            if not mem or mem.is_expired():
                continue
            # Score by relevance
            score = 0.0
            if query_lower in mem.content.lower():
                score += 1.0
            if any(query_lower in t.lower() for t in mem.tags):
                score += 0.5
            score += mem.access_count * 0.1
            score += mem.importance * 0.5
            if mem.pinned:
                score += 100.0  # pinned memories always first
            mem.relevance_score = score
            results.append(mem)
        results.sort(key=lambda m: m.relevance_score, reverse=True)
        return results[:limit]

    def get_by_category(self, category: str, limit: int = 20) -> list[Memory]:
        """Get all memories in a specific category."""
        return self.search("", limit=limit, category=category)

    def get_pinned(self) -> list[Memory]:
        """Get all pinned memories."""
        return [m for m in self.list_all() if m.pinned]

    def get_recent(self, limit: int = 10) -> list[Memory]:
        """Get most recently accessed memories."""
        memories = self.list_all()
        memories.sort(key=lambda m: m.updated_at, reverse=True)
        return memories[:limit]

    def get_by_layer(self, layer: str, limit: int = 20) -> list[Memory]:
        """Get memories by layer (L0-L4)."""
        return [m for m in self.list_all() if m.layer == layer][:limit]

    def generate_mermaid_canvas(self, memories: list[Memory] | None = None) -> str:
        """Generate a Mermaid diagram from memories (TencentDB-style symbolic compression)."""
        if not memories:
            memories = self.list_all()
        if not memories:
            return "graph TD\n  empty[\"No memories yet\"]"

        nodes = []
        edges = []
        current_layer = None

        for mem in memories:
            node_id = mem.node_id or f"MEM_{mem.id[:8]}"
            label = mem.content[:40].replace('"', "'").replace('\n', ' ')
            if len(mem.content) > 40:
                label += "..."

            nodes.append(f'    {node_id}["{label}"]::: {mem.layer.lower()}')

            if mem.evidence_refs:
                for ref in mem.evidence_refs:
                    edges.append(f"    {ref} --> {node_id}")

            if mem.layer != current_layer:
                edges.append(f"    subgraph {mem.layer} [Layer {mem.layer}]")
                current_layer = mem.layer

        mermaid = ["graph TD"]
        mermaid.append("    %% Layer styles")
        mermaid.append("    classDef L0 fill:#e0e0e0,stroke:#9e9e9e")
        mermaid.append("    classDef L1 fill:#90caf9,stroke:#1976d2")
        mermaid.append("    classDef L2 fill:#a5d6a7,stroke:#388e3c")
        mermaid.append("    classDef L3 fill:#ffcc80,stroke:#f57c00")
        mermaid.append("    classDef L4 fill:#ce93d8,stroke:#7b1fa2")
        mermaid.append("")

        for node in nodes:
            mermaid.append(node)
        mermaid.append("")
        for edge in edges:
            mermaid.append(edge)

        return "\n".join(mermaid)

    def check_offload_threshold(self, context_ratio: float) -> tuple[bool, str]:
        """Check if context offload should be triggered based on usage ratio.

        Args:
            context_ratio: Current context usage as ratio (0.0 to 1.0+)

        Returns:
            Tuple of (should_offload, reason)
        """
        from ccb.context_policy import should_trigger_offload

        return should_trigger_offload(context_ratio)

    def get_trace_path(self, mid: str) -> list[str]:
        """Get full trace path for a memory (node_id + evidence_refs)."""
        mem = self.get(mid)
        if not mem:
            return []
        path = [mem.node_id] if mem.node_id else [mem.id]
        path.extend(mem.evidence_refs or [])
        return path

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

    # Patterns that indicate important info worth remembering
    IMPORTANT_PATTERNS = [
        "preference", "like", "dislike", "always", "never", "must", "don't",
        "remember", "important", "never forget", "note that",
        "project", "codebase", "architecture", "setup", "config",
        "bug", "issue", "broken", "fix", "workaround",
        "task", "workflow", "process", "习惯了",
    ]

    def __init__(self, store: MemoryStore | None = None, provider: Any = None):
        self._store = store or get_store()
        self._provider = provider
        self._enabled = True
        self._last_extraction_time = 0.0
        self._extraction_interval = 30.0  # minimum seconds between extractions

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    def is_important_content(self, text: str) -> bool:
        """Quick check if content seems worth extracting."""
        text_lower = text.lower()
        return any(p in text_lower for p in self.IMPORTANT_PATTERNS)

    async def extract_from_message(
        self,
        message_content: str,
        role: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> list[Memory]:
        """Extract memories from a single message (real-time mode).

        Args:
            message_content: The message text to analyze
            role: Message role (user/assistant)
            metadata: Optional metadata like cwd, language, etc.
        """
        if not self._enabled or not self._provider:
            return []
        if not message_content or len(message_content) < 50:
            return []
        if role == "system":
            return []

        # Check if content seems important enough
        if not self.is_important_content(message_content):
            return []

        # Rate limit extractions
        import time
        now = time.time()
        if now - self._last_extraction_time < self._extraction_interval:
            return []
        self._last_extraction_time = now

        prompt = (
            f"Extract any important facts, preferences, or decisions from this message.\n"
            f"Return JSON array: [{{\"content\": \"...\", \"tags\": [\"...\"], \"category\": \"...\", \"importance\": 1.0}}]\n"
            f"Categories: user_preference, project, codebase, task_pattern, general\n"
            f"Importance: 0.5=low, 1.0=normal, 2.0=high\n\n"
            f"Message: {message_content[:2000]}\n\n"
            f"Reply with ONLY JSON array."
        )

        result = ""
        try:
            from ccb.api.base import Message as APIMessage, Role as APIRole
            extract_messages = [APIMessage(role=APIRole.USER, content=prompt)]
            async for event in self._provider.stream(
                messages=extract_messages,
                tools=[],
                system="You extract important facts from text. Reply with JSON only.",
                max_tokens=1024,
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
            category = item.get("category", "general")
            importance = item.get("importance", 1.0)
            if content and len(content) > 10:
                existing = self._store.search(content[:50], limit=3)
                if not any(self._similarity(content, e.content) > 0.8 for e in existing):
                    mem = self._store.add(
                        content,
                        tags=tags,
                        category=category,
                        importance=importance,
                        metadata=metadata or {},
                    )
                    stored.append(mem)
        return stored

    async def extract_from_session(
        self,
        messages: list[Any],
        session_id: str = "",
        category: str = "general",
    ) -> list[Memory]:
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
            mem_category = item.get("category", category)
            importance = item.get("importance", 1.0)
            if content and len(content) > 10:
                # Dedup: check if similar memory exists
                existing = self._store.search(content[:50], limit=3)
                if not any(self._similarity(content, e.content) > 0.8 for e in existing):
                    mem = self._store.add(
                        content,
                        tags=tags,
                        source=session_id,
                        category=mem_category,
                        importance=importance,
                    )
                    stored.append(mem)
        return stored

    async def learn_from_pattern(
        self,
        pattern: str,
        description: str,
        tags: list[str],
        category: str = "task_pattern",
        importance: float = 1.5,
    ) -> Memory:
        """Explicitly learn a pattern (e.g., "Python project uses pytest")."""
        return self._store.add(
            content=f"{pattern}: {description}",
            tags=tags,
            category=category,
            importance=importance,
            metadata={"learned_from": "pattern", "pattern": pattern},
        )

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


# ---------------------------------------------------------------------------
# Cross-session learning
# ---------------------------------------------------------------------------

class SessionLearner:
    """Learn patterns from session history."""

    def __init__(self, store: MemoryStore | None = None):
        self._store = store or get_store()
        self._session_stats: dict[str, int] = {}  # path -> access count
        self._load_stats()

    def _load_stats(self) -> None:
        """Load session statistics from memory store."""
        stats_file = self._store.dir / "session_stats.json"
        data = read_json(stats_file, default={})
        self._session_stats = data if isinstance(data, dict) else {}

    def _save_stats(self) -> None:
        """Save session statistics."""
        stats_file = self._store.dir / "session_stats.json"
        write_json(stats_file, self._session_stats)

    def record_session_activity(self, cwd: str, files_accessed: list[str] | None = None) -> None:
        """Record session activity for pattern learning."""
        if not cwd:
            return

        # Update cwd access count
        self._session_stats[cwd] = self._session_stats.get(cwd, 0) + 1

        # Track file patterns
        if files_accessed:
            key = f"files:{cwd}"
            existing = self._session_stats.get(key, [])
            # Keep last 50 files
            self._session_stats[key] = (files_accessed + existing)[:50]

        self._save_stats()

    def get_project_patterns(self, cwd: str) -> dict[str, Any]:
        """Get learned patterns for a project directory."""
        return {
            "cwd_access_count": self._session_stats.get(cwd, 0),
            "recent_files": self._session_stats.get(f"files:{cwd}", [])[:10],
        }

    def learn_language_preference(self, cwd: str, language: str, importance: float = 1.5) -> Memory | None:
        """Learn that a project uses a specific language."""
        pattern = f"Project uses {language}"
        existing = self._store.search(pattern, limit=1, category="project")
        if existing:
            return None  # Already exists
        return self._store.add(
            content=pattern,
            tags=[language, "language", "project"],
            category="project",
            importance=importance,
            metadata={"cwd": cwd, "learned_from": "session"},
        )

    def learn_workflow_pattern(self, task: str, steps: list[str], importance: float = 1.5) -> Memory | None:
        """Learn a common workflow pattern."""
        content = f"Task '{task}' typically involves: {' → '.join(steps)}"
        existing = self._store.search(task, limit=1, category="task_pattern")
        if existing:
            return None
        return self._store.add(
            content=content,
            tags=["workflow", task.lower(), "pattern"],
            category="task_pattern",
            importance=importance,
            metadata={"steps": steps, "learned_from": "session"},
        )

    def learn_user_preference(self, preference: str, context: str = "", importance: float = 1.5) -> Memory | None:
        """Learn a user preference."""
        content = f"User preference: {preference}"
        if context:
            content += f" (in context: {context})"
        # Check dedup
        existing = self._store.search(preference[:30], limit=1, category="user_preference")
        if existing:
            return None
        return self._store.add(
            content=content,
            tags=["preference", "user"],
            category="user_preference",
            importance=importance,
        )


def get_session_learner() -> SessionLearner:
    """Get the global session learner instance."""
    global _session_learner
    if _session_learner is None:
        _session_learner = SessionLearner()
    return _session_learner


_session_learner: SessionLearner | None = None


# ---------------------------------------------------------------------------
# Memory decay — reduce relevance of old, unused memories
# ---------------------------------------------------------------------------

def apply_decay(
    store: MemoryStore | None = None,
    decay_rate: float = 0.95,
    min_score: float = 0.01,
    min_importance: float = 1.5,
) -> int:
    """Apply time-based decay to all memories. Returns count of pruned memories.

    Pinned memories and high-importance memories (importance >= min_importance) are preserved.
    Only memories with access_count <= 1 and score below min_score are pruned.
    """
    s = store or get_store()
    pruned = 0
    now = time.time()
    for mid in list(s._index):
        # Read raw file without incrementing access count
        f = s._memory_file(mid)
        if not f.exists():
            continue
        try:
            data = read_json(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        # Never delete pinned memories
        if data.get("pinned", False):
            continue

        importance = data.get("importance", 1.0)
        # Never delete high-importance memories
        if importance >= min_importance:
            continue

        access_count = data.get("access_count", 0)
        created = data.get("created_at", now)
        updated = data.get("updated_at", created)
        expires_at = data.get("expires_at", 0)

        # Check explicit expiration
        if expires_at > 0 and now > expires_at:
            s.delete(mid)
            pruned += 1
            continue

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

def build_memory_context(
    query: str = "",
    limit: int = 10,
    store: MemoryStore | None = None,
    category: str | None = None,
    cwd: str | None = None,
) -> str:
    """Build a memory context string for injection into the system prompt.

    Args:
        query: Optional search query to find relevant memories
        limit: Maximum number of memories to include
        store: Memory store to use (default: global singleton)
        category: Optional category filter
        cwd: Current working directory to match project memories
    """
    s = store or get_store()

    # Get pinned memories first (always included)
    pinned = s.get_pinned()
    pinned_memories = [m for m in pinned if not m.is_expired()]

    # Get regular memories based on semantic graph search
    warnings = []
    if query:
        try:
            from ccb.memory_graph import MemoryGraphEngine
            engine = MemoryGraphEngine()
            subgraph = engine.search_semantic_subgraph(query, limit=limit)
            warnings = subgraph.get("warnings", [])
            regular = []
            for node in subgraph.get("subgraph_nodes", []):
                mem = s.get(node["id"], update_access=False)
                if mem:
                    regular.append(mem)
        except Exception:
            regular = s.search(query, limit=limit, category=category)
    else:
        regular = s.list_all(category=category)[:limit]

    # Filter out expired and deduplicate
    seen_ids = {m.id for m in pinned_memories}
    memories = list(pinned_memories)
    for m in regular:
        if m.id not in seen_ids and not m.is_expired():
            memories.append(m)
            seen_ids.add(m.id)

    # Sort by importance and recency
    memories.sort(key=lambda m: (m.pinned, m.importance, m.access_count, m.updated_at), reverse=True)
    memories = memories[:limit]

    if not memories:
        return ""

    lines = ["<memories>"]
    for m in memories:
        pinned_marker = " 📌" if m.pinned else ""
        importance_marker = " ⭐" if m.importance >= 2.0 else (" ⚡" if m.importance >= 1.5 else "")
        tags_str = f" [tags: {', '.join(m.tags)}]" if m.tags else ""
        cat_str = f" [{m.category}]" if m.category else ""
        lines.append(
            f"- {m.content}{tags_str}{cat_str}{pinned_marker}{importance_marker}"
        )
    lines.append("</memories>")
    if warnings:
        lines.append("\n<memory_warnings>")
        for w in warnings:
            lines.append(f"[!] {w}")
        lines.append("</memory_warnings>")
    return "\n".join(lines)


def build_memory_hint(cwd: str | None = None, task: str | None = None) -> str:
    """Build contextual memory hints based on cwd and task.

    Args:
        cwd: Current working directory for project-specific memories
        task: Current task context for relevant pattern memories
    """
    hints = []

    # Project memories based on cwd
    if cwd:
        project_mems = get_store().search(
            query=cwd,
            limit=5,
            category="project",
        )
        if project_mems:
            hints.append(f"Project context from {cwd}:")
            for m in project_mems[:3]:
                hints.append(f"  • {m.content}")

    # Task pattern memories
    if task:
        task_mems = get_store().search(
            query=task,
            limit=3,
            category="task_pattern",
        )
        if task_mems:
            hints.append("Relevant patterns:")
            for m in task_mems[:2]:
                hints.append(f"  • {m.content}")

    return "\n".join(hints) if hints else ""


def analyze_memories(
    store: MemoryStore | None = None,
    cwd: str | None = None,
) -> str:
    """Generate a comprehensive memory analysis report.

    Inspired by MemoryAgent's analyze command, produces a structured report
    with: summary, topic classification, key entities, timeline, relationships,
    knowledge gaps, and recommended actions.

    Args:
        store: Memory store to analyze (default: global singleton)
        cwd: Current working directory for project-specific focus
    """
    from io import StringIO
    from rich.console import Console
    from rich.table import Table
    from rich.box import ROUNDED

    s = store or get_store()
    all_memories = s.list_all()

    if not all_memories:
        return "[bold cyan]📊 Memory Analysis Report[/bold cyan]\n\n[dim]No memories stored yet.[/dim]"

    # Categorize
    categories: dict[str, list[Memory]] = {}
    for m in all_memories:
        cat = m.category or "uncategorized"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(m)

    # Sort by importance
    sorted_memories = sorted(all_memories, key=lambda m: m.importance, reverse=True)
    total = len(all_memories)
    pinned = sum(1 for m in all_memories if m.pinned)
    avg_importance = sum(m.importance for m in all_memories) / total if total else 0

    output: list[str] = []
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor")

    # Header
    output.append("")
    output.append("[bold cyan]╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮[/bold cyan]")
    output.append("[bold cyan]┃[/bold cyan]  [bold white]📊 Memory Analysis Report[/bold white]                          [bold cyan]┃[/bold cyan]")
    output.append("[bold cyan]╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯[/bold cyan]")
    output.append("")

    # Summary Table
    summary_table = Table(box=ROUNDED, show_header=False, padding=1)
    summary_table.add_column(style="cyan", width=22)
    summary_table.add_column(style="white", width=12)
    summary_table.add_column(style="cyan", width=22)
    summary_table.add_column(style="white", width=12)
    summary_table.add_row("Total memories", f"[bold]{total}[/bold]", "Pinned", f"[yellow]📌 {pinned}[/yellow]" if pinned else "0")
    summary_table.add_row("Categories", f"[bold]{len(categories)}[/bold]", "Avg importance", f"[bold]{avg_importance:.1f}[/bold]")
    console.print(summary_table)
    output.append(buf.getvalue().strip())
    buf.truncate(0)
    buf.seek(0)
    output.append("")

    # Topic classification
    output.append("[bold cyan]🏷️  Topics by Category[/bold cyan]")
    topic_table = Table(box=ROUNDED, show_header=False, padding=(0, 1))
    topic_table.add_column(width=18)
    topic_table.add_column(width=50)
    for cat, mems in sorted(categories.items(), key=lambda x: len(x[1]), reverse=True)[:6]:
        preview = ", ".join(m.content[:30] for m in mems[:2])
        color = {
            "user_preference": "yellow",
            "language": "green",
            "project": "blue",
            "codebase": "magenta",
            "task_pattern": "cyan",
            "workflow": "red",
        }.get(cat, "white")
        topic_table.add_row(f"[{color}]{cat}[/{color}]", f"[dim]{preview}...[/dim]")
    console.print(topic_table)
    output.append(buf.getvalue().strip())
    buf.truncate(0)
    buf.seek(0)
    output.append("")

    # High importance memories
    output.append("[bold cyan]⭐  High-Importance Memories[/bold cyan]")
    high_imp = [m for m in sorted_memories if m.importance >= 1.5][:8]
    if high_imp:
        for m in high_imp:
            pin_mark = " [yellow]📌[/yellow]" if m.pinned else ""
            imp_bar = "█" * int(m.importance) + "░" * (5 - int(m.importance))
            content = m.content[:65] + "..." if len(m.content) > 65 else m.content
            layer_mark = f" [dim][{m.layer}][/dim]" if hasattr(m, 'layer') and m.layer != 'L1' else ""
            output.append(f"  [cyan]│[/cyan] {imp_bar} [dim]{m.importance:.1f}[/dim] {content}{pin_mark}{layer_mark}")
    else:
        output.append("  [dim]No memories above importance threshold 1.5[/dim]")
    output.append("")

    # Layer distribution
    output.append("[bold cyan]📦  Layer Distribution[/bold cyan]")
    layer_colors = {"L0": "dim", "L1": "cyan", "L2": "green", "L3": "yellow", "L4": "magenta"}
    layer_desc = {"L0": "Raw", "L1": "Atom", "L2": "Scenario", "L3": "Persona", "L4": "Skill"}
    for layer in ["L0", "L1", "L2", "L3", "L4"]:
        mems = [m for m in all_memories if m.layer == layer] if hasattr(all_memories[0], 'layer') else []
        layer_name = layer_desc.get(layer, layer)
        color = layer_colors.get(layer, "white")
        count = len(mems)
        bar = "▓" * count + "░" * max(0, 10 - count) if count > 0 else "░" * 10
        output.append(f"  [bold {color}]{layer}[/bold {color}] {layer_name:10} [dim]{bar}[/dim] [white]{count}[/white]")

    output.append("")
    output.append("[bold cyan]💡  Suggested Actions[/bold cyan]")
    if pinned == 0:
        output.append("  [dim]•[/dim] Pin important memories with [yellow]/remember ... --pin[/yellow]")
    if total < 5:
        output.append("  [dim]•[/dim] Add more context with [yellow]/remember[/yellow]")
    if not any(m.category == "user_preference" for m in all_memories):
        output.append("  [dim]•[/dim] Use [yellow]/remember[/yellow] to record your preferences")
    output.append("  [dim]•[/dim] View all with [yellow]/memory list[/yellow] or [yellow]/memory mermaid[/yellow]")
    output.append("")
    output.append("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")

    return "\n".join(output)


def _format_age(timestamp: float) -> str:
    """Format a timestamp as human-readable age."""
    import datetime
    now = time.time()
    diff = now - timestamp
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff/60)}m ago"
    if diff < 86400:
        return f"{int(diff/3600)}h ago"
    if diff < 604800:
        return f"{int(diff/86400)}d ago"
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%b %d")


# ---------------------------------------------------------------------------
# Project Advisor — analyzes project structure and provides suggestions
# ---------------------------------------------------------------------------

class ProjectAdvisor:
    """Analyze project structure and provide contextual suggestions.

    Inspired by Claude Code's project-aware hints. Detects project type,
    structure, and recommends appropriate actions or memory patterns.
    """

    PROJECT_INDICATORS = {
        "package.json": ("Node.js/npm", "javascript"),
        "requirements.txt": ("Python (pip)", "python"),
        "pyproject.toml": ("Python (modern)", "python"),
        "Pipfile": ("Python (Pipenv)", "python"),
        "Cargo.toml": ("Rust", "rust"),
        "go.mod": ("Go", "go"),
        "Makefile": ("C/C++", "c_cpp"),
        "CMakeLists.txt": ("C/C++ (CMake)", "c_cpp"),
        "pom.xml": ("Java (Maven)", "java"),
        "build.gradle": ("Java (Gradle)", "java"),
        "composer.json": ("PHP", "php"),
        "Gemfile": ("Ruby", "ruby"),
    }

    FRAMEWORK_INDICATORS = {
        "react": "React", "vue": "Vue.js", "angular": "Angular",
        "next": "Next.js", "nuxt": "Nuxt", "gatsby": "Gatsby",
        "svelte": "Svelte", "astro": "Astro",
        "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
        "express": "Express", "nest": "NestJS",
        "rails": "Rails", "spring": "Spring",
    }

    def __init__(self, cwd: str | None = None):
        import os as _os
        self._cwd = cwd or _os.getcwd()
        self._cached_info: dict | None = None

    def detect(self) -> dict[str, Any]:
        """Detect project type and return structured info."""
        if self._cached_info:
            return self._cached_info

        import os
        indicators = []
        detected_type = None
        detected_framework = None

        for filename, (proj_type, _) in self.PROJECT_INDICATORS.items():
            if os.path.exists(os.path.join(self._cwd, filename)):
                indicators.append(filename)
                if detected_type is None:
                    detected_type = proj_type

        # Check for frameworks in package.json
        pkg_json = os.path.join(self._cwd, "package.json")
        if os.path.exists(pkg_json):
            try:
                import json
                pkg = json.loads(open(pkg_json).read())
                deps = list(pkg.get("dependencies", {}).keys()) + list(pkg.get("devDependencies", {}).keys())
                for dep, fw in self.FRAMEWORK_INDICATORS.items():
                    if dep in deps:
                        detected_framework = fw
                        break
            except Exception:
                pass

        has_git = os.path.exists(os.path.join(self._cwd, ".git"))
        has_md = os.path.exists(os.path.join(self._cwd, "CLAUDE.md"))

        self._cached_info = {
            "type": detected_type or "Unknown",
            "framework": detected_framework,
            "indicators": indicators,
            "has_git": has_git,
            "has_claude_md": has_md,
        }
        return self._cached_info

    def get_suggestions(self) -> list[str]:
        """Get actionable suggestions based on project analysis."""
        info = self.detect()
        suggestions = []

        if not info["has_claude_md"]:
            suggestions.append("💡 Create CLAUDE.md to help AI understand your project")
        if not info["has_git"]:
            suggestions.append("💡 Initialize git for version control")

        if info["framework"] == "React":
            suggestions.append("🔍 Use /review for React component review")
        elif info["framework"] == "FastAPI":
            suggestions.append("🔍 FastAPI - /test can generate API tests")
        elif "Python" in (info["type"] or ""):
            suggestions.append("🐍 Python - /test can generate pytest")

        from ccb.memory import get_store
        if get_store().count < 3:
            suggestions.append("🧠 Add memories with /remember")

        return suggestions[:5]

    def get_status_line(self) -> str:
        """Get a one-line status summary."""
        info = self.detect()
        parts = []
        if info["type"] != "Unknown":
            parts.append(info["type"])
        if info["framework"]:
            parts.append(info["framework"])
        if info["has_claude_md"]:
            parts.append("📝 CLAUDE.md")
        return " · ".join(parts) if parts else "No project detected"


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
