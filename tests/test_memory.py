"""Tests for ccb.memory module."""
import json
import time
from pathlib import Path

import pytest

from ccb.memory import (
    Memory,
    MemoryStore,
    MemoryExtractor,
    apply_decay,
    build_memory_context,
    generate_extract_memories_prompt,
    parse_extracted_memories,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(memory_dir=tmp_path / "mem")


class TestMemoryStore:
    def test_add_and_get(self, store):
        mem = store.add("Python is great", tags=["python", "opinion"])
        assert mem.id
        assert mem.content == "Python is great"

        retrieved = store.get(mem.id)
        assert retrieved is not None
        assert retrieved.content == mem.content
        assert retrieved.access_count >= 1

    def test_update(self, store):
        mem = store.add("old content")
        updated = store.update(mem.id, content="new content", tags=["updated"])
        assert updated is not None
        assert updated.content == "new content"
        assert "updated" in updated.tags

    def test_delete(self, store):
        mem = store.add("to delete")
        assert store.delete(mem.id) is True
        assert store.get(mem.id) is None

    def test_list_all(self, store):
        store.add("a", tags=["x"])
        store.add("b", tags=["y"])
        store.add("c", tags=["x"])
        all_mems = store.list_all()
        assert len(all_mems) == 3

        x_mems = store.list_all(tag="x")
        assert len(x_mems) == 2

    def test_search(self, store):
        store.add("Django REST framework is useful", tags=["django", "rest"])
        store.add("Flask is lightweight", tags=["flask"])
        store.add("React is a frontend library", tags=["react"])

        results = store.search("flask")
        assert len(results) >= 1
        assert any("flask" in r.content.lower() or "flask" in r.tags for r in results)

    def test_clear(self, store):
        store.add("a")
        store.add("b")
        count = store.clear()
        assert count == 2
        assert store.count == 0

    def test_count(self, store):
        assert store.count == 0
        store.add("x")
        assert store.count == 1

    def test_persistence(self, tmp_path):
        mem_dir = tmp_path / "persist"
        s1 = MemoryStore(memory_dir=mem_dir)
        s1.add("persistent memory", tags=["test"])
        assert s1.count == 1

        # Reload
        s2 = MemoryStore(memory_dir=mem_dir)
        assert s2.count == 1
        all_mems = s2.list_all()
        assert all_mems[0].content == "persistent memory"


class TestDecay:
    def test_decay_removes_old_unused(self, store):
        mem = store.add("old memory")
        # Artificially age it — also update the index
        mem_file = store.dir / f"{mem.id}.json"
        data = json.loads(mem_file.read_text())
        data["created_at"] = time.time() - 365 * 86400  # 1 year old
        data["updated_at"] = data["created_at"]
        data["access_count"] = 0
        mem_file.write_text(json.dumps(data))
        # Reload the store so index is fresh
        store._load_index()

        pruned = apply_decay(store, decay_rate=0.5, min_score=0.1)
        assert pruned >= 1


class TestExtractPrompt:
    def test_generate_prompt(self):
        prompt = generate_extract_memories_prompt("User: I prefer dark mode\nAssistant: Noted!")
        assert "extract" in prompt.lower()
        assert "dark mode" in prompt

    def test_parse_memories(self):
        llm_output = '''Here are the memories:
[{"content": "User prefers dark mode", "tags": ["preference", "ui"]}]
'''
        parsed = parse_extracted_memories(llm_output)
        assert len(parsed) == 1
        assert parsed[0]["content"] == "User prefers dark mode"

    def test_parse_invalid(self):
        assert parse_extracted_memories("no json here") == []


class TestBuildContext:
    def test_empty(self, store):
        ctx = build_memory_context(store=store)
        assert ctx == ""

    def test_with_memories(self, store):
        store.add("User likes Python", tags=["preference"])
        store.add("Project uses FastAPI", tags=["tech"])
        ctx = build_memory_context(store=store)
        assert "<memories>" in ctx
        assert "Python" in ctx
        assert "FastAPI" in ctx

    def test_with_query(self, store):
        store.add("User likes Python", tags=["preference"])
        store.add("Server uses Nginx", tags=["infra"])
        ctx = build_memory_context(query="python", store=store)
        assert "Python" in ctx


class TestMemoryExtractor:
    def test_init(self, store):
        ext = MemoryExtractor(store=store)
        assert ext._enabled is True

    def test_toggle(self, store):
        ext = MemoryExtractor(store=store)
        assert ext.toggle() is False
        assert ext.toggle() is True

    def test_similarity(self):
        score = MemoryExtractor._similarity("hello world foo", "hello world bar")
        assert 0 < score < 1
        assert MemoryExtractor._similarity("abc", "abc") == 1.0
        assert MemoryExtractor._similarity("abc", "xyz") == 0.0
