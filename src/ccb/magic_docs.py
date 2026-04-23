"""MagicDocs — auto-maintain markdown documentation files.

When a file with "# MAGIC DOC: [title]" header is read, it runs periodically
in the background using a forked subagent to update the document with new
learnings from the conversation.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAGIC_DOC_PATTERN = re.compile(r"^#\s*MAGIC\s*DOC:\s*(.+)", re.IGNORECASE)
UPDATE_INTERVAL_SECONDS = 120  # 2 minutes between updates


@dataclass
class MagicDoc:
    """Tracked magic document."""
    path: str
    title: str
    last_read_at: float = 0.0
    last_updated_at: float = 0.0
    update_count: int = 0
    pending_update: bool = False


@dataclass
class MagicDocsEngine:
    """Manages auto-updating of magic doc files."""
    _docs: dict[str, MagicDoc] = field(default_factory=dict)
    _running: bool = False
    _update_task: asyncio.Task[None] | None = None
    _conversation_context: list[str] = field(default_factory=list)

    def register_file_read(self, path: str, content: str) -> MagicDoc | None:
        """Called when any file is read. Detects magic docs and registers them."""
        first_line = content.split("\n", 1)[0] if content else ""
        m = MAGIC_DOC_PATTERN.match(first_line)
        if not m:
            return None

        title = m.group(1).strip()
        abs_path = str(Path(path).resolve())

        if abs_path in self._docs:
            doc = self._docs[abs_path]
            doc.last_read_at = time.time()
            return doc

        doc = MagicDoc(
            path=abs_path,
            title=title,
            last_read_at=time.time(),
        )
        self._docs[abs_path] = doc
        logger.info("Registered magic doc: %s (%s)", title, abs_path)
        return doc

    def add_context(self, text: str) -> None:
        """Add conversation context for magic doc updates."""
        self._conversation_context.append(text)
        # Keep only last 50 context entries
        if len(self._conversation_context) > 50:
            self._conversation_context = self._conversation_context[-50:]
        # Mark docs as pending
        for doc in self._docs.values():
            elapsed = time.time() - doc.last_updated_at
            if elapsed >= UPDATE_INTERVAL_SECONDS:
                doc.pending_update = True

    def get_pending_docs(self) -> list[MagicDoc]:
        """Get docs that need updating."""
        return [d for d in self._docs.values() if d.pending_update]

    async def update_doc(
        self,
        doc: MagicDoc,
        provider: Any = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        """Update a single magic doc using the conversation context.

        Returns the updated content, or None if no update was needed.
        """
        path = Path(doc.path)
        if not path.exists():
            logger.warning("Magic doc no longer exists: %s", doc.path)
            self._docs.pop(doc.path, None)
            return None

        current_content = path.read_text(encoding="utf-8", errors="replace")
        context_text = "\n---\n".join(self._conversation_context[-20:])

        if not context_text.strip():
            doc.pending_update = False
            return None

        if on_progress:
            on_progress(f"Updating magic doc: {doc.title}")

        # Build the update prompt
        prompt = f"""You are updating a "Magic Doc" — a living documentation file that
auto-updates based on conversation context.

Current document ({doc.title}):
```
{current_content}
```

Recent conversation context:
```
{context_text}
```

Instructions:
1. Review the conversation context for new information relevant to this document
2. Update the document with new learnings, corrections, or additions
3. Keep the "# MAGIC DOC: {doc.title}" header line exactly as-is
4. Maintain the existing structure and style
5. If nothing meaningful has changed, return the document unchanged
6. Be concise — this is reference documentation, not a narrative

Return ONLY the updated document content, nothing else."""

        if provider:
            try:
                from ccb.api.base import Message, Role
                response = await provider.complete(
                    messages=[Message(role=Role.USER, content=prompt)],
                    system_prompt="You are a documentation assistant. Return only the updated document.",
                    model=None,
                )
                if response and response.content:
                    updated = response.content.strip()
                    # Ensure header is preserved
                    if not updated.startswith(f"# MAGIC DOC:"):
                        updated = f"# MAGIC DOC: {doc.title}\n\n{updated}"
                    path.write_text(updated, encoding="utf-8")
                    doc.last_updated_at = time.time()
                    doc.update_count += 1
                    doc.pending_update = False
                    logger.info("Updated magic doc: %s", doc.title)
                    return updated
            except Exception as e:
                logger.error("Failed to update magic doc %s: %s", doc.title, e)
        else:
            doc.pending_update = False

        return None

    async def run_update_cycle(
        self,
        provider: Any = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        """Run one update cycle for all pending docs. Returns count updated."""
        pending = self.get_pending_docs()
        updated = 0
        for doc in pending:
            result = await self.update_doc(doc, provider, on_progress)
            if result is not None:
                updated += 1
        return updated

    def start_background(
        self,
        provider: Any = None,
        interval: float = 60.0,
    ) -> None:
        """Start background update loop."""
        if self._running:
            return

        self._running = True

        async def _loop() -> None:
            while self._running:
                try:
                    await self.run_update_cycle(provider)
                except Exception as e:
                    logger.error("MagicDocs background error: %s", e)
                await asyncio.sleep(interval)

        self._update_task = asyncio.ensure_future(_loop())

    def stop_background(self) -> None:
        """Stop background update loop."""
        self._running = False
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            self._update_task = None

    @property
    def tracked_count(self) -> int:
        return len(self._docs)

    @property
    def docs(self) -> list[MagicDoc]:
        return list(self._docs.values())

    def summary(self) -> dict[str, Any]:
        return {
            "tracked": self.tracked_count,
            "pending": len(self.get_pending_docs()),
            "total_updates": sum(d.update_count for d in self._docs.values()),
            "docs": [
                {
                    "title": d.title,
                    "path": d.path,
                    "updates": d.update_count,
                    "pending": d.pending_update,
                }
                for d in self._docs.values()
            ],
        }


# ── Module-level singleton ─────────────────────────────────────

_engine: MagicDocsEngine | None = None


def get_magic_docs() -> MagicDocsEngine:
    global _engine
    if _engine is None:
        _engine = MagicDocsEngine()
    return _engine
