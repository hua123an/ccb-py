"""AgentSummary — periodic background summarization for coordinator sub-agents.

Forks the sub-agent's conversation every ~30s to generate a 1-2 sentence
progress summary. The summary is stored on the agent's progress state for
UI display.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

SUMMARY_INTERVAL = 30.0  # seconds between summary updates


@dataclass
class AgentProgress:
    """Progress tracking for a single sub-agent."""
    agent_id: str
    task: str
    status: str = "running"
    summary: str = ""
    tool_count: int = 0
    last_tool: str = ""
    started_at: float = field(default_factory=time.time)
    last_summary_at: float = 0.0
    turns: int = 0


class AgentSummaryEngine:
    """Manages periodic summarization of running sub-agents."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentProgress] = {}
        self._summary_tasks: dict[str, asyncio.Task[None]] = {}
        self._on_update: Callable[[str, str], None] | None = None

    def register(self, agent_id: str, task: str) -> AgentProgress:
        """Register a new sub-agent for tracking."""
        progress = AgentProgress(agent_id=agent_id, task=task)
        self._agents[agent_id] = progress
        return progress

    def update_tool(self, agent_id: str, tool_name: str) -> None:
        """Record a tool use for an agent."""
        p = self._agents.get(agent_id)
        if p:
            p.tool_count += 1
            p.last_tool = tool_name

    def update_turn(self, agent_id: str, content: str = "") -> None:
        """Record a turn for an agent."""
        p = self._agents.get(agent_id)
        if p:
            p.turns += 1
            # Use first 200 chars of content as interim summary
            if content:
                p.summary = content[:200].split("\n")[0]

    def complete(self, agent_id: str, final_summary: str = "") -> None:
        """Mark an agent as completed."""
        p = self._agents.get(agent_id)
        if p:
            p.status = "completed"
            if final_summary:
                p.summary = final_summary
            # Cancel summary task
            task = self._summary_tasks.pop(agent_id, None)
            if task and not task.done():
                task.cancel()

    def fail(self, agent_id: str, error: str = "") -> None:
        """Mark an agent as failed."""
        p = self._agents.get(agent_id)
        if p:
            p.status = "failed"
            p.summary = f"Error: {error}" if error else "Failed"
            task = self._summary_tasks.pop(agent_id, None)
            if task and not task.done():
                task.cancel()

    def get_progress(self, agent_id: str) -> AgentProgress | None:
        return self._agents.get(agent_id)

    def get_all_progress(self) -> list[AgentProgress]:
        return list(self._agents.values())

    def active_count(self) -> int:
        return sum(1 for p in self._agents.values() if p.status == "running")

    async def generate_summary(
        self,
        agent_id: str,
        messages: list[Any],
        provider: Any | None = None,
    ) -> str:
        """Generate a brief summary of an agent's progress.

        Uses a lightweight model (haiku-equivalent) to summarize.
        Falls back to extracting the last assistant message text.
        """
        p = self._agents.get(agent_id)
        if not p:
            return ""

        # Fallback: extract last assistant message
        from ccb.api.base import Role
        last_text = ""
        for msg in reversed(messages):
            if getattr(msg, "role", None) == Role.ASSISTANT:
                content = getattr(msg, "content", "")
                if content:
                    last_text = content[:200].split("\n")[0]
                    break

        if not provider:
            return last_text or f"Working... ({p.tool_count} tools used)"

        # Try to generate a proper summary via provider
        try:
            from ccb.api.base import Message, Role
            summary_prompt = (
                f"Write a 1-sentence summary of this sub-agent's progress:\n"
                f"Task: {p.task}\n"
                f"Tools used: {p.tool_count}\n"
                f"Turns: {p.turns}\n"
                f"Last output: {last_text[:300]}\n\n"
                f"Summary (max 30 chars, like a git commit subject):"
            )
            resp = await provider.complete(
                messages=[Message(role=Role.USER, content=summary_prompt)],
                system_prompt="Write a short summary label. Max 30 chars.",
                model=None,
            )
            if resp and resp.content:
                summary = resp.content.strip()[:80]
                p.summary = summary
                p.last_summary_at = time.time()
                if self._on_update:
                    self._on_update(agent_id, summary)
                return summary
        except Exception as e:
            logger.debug("Summary generation failed for %s: %s", agent_id, e)

        return last_text or f"Working... ({p.tool_count} tools used)"

    def start_periodic_summary(
        self,
        agent_id: str,
        get_messages: Callable[[], list[Any]],
        provider: Any | None = None,
        interval: float = SUMMARY_INTERVAL,
    ) -> None:
        """Start periodic summary generation for an agent."""
        async def _loop() -> None:
            while True:
                p = self._agents.get(agent_id)
                if not p or p.status != "running":
                    break
                await asyncio.sleep(interval)
                try:
                    msgs = get_messages()
                    await self.generate_summary(agent_id, msgs, provider)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug("Periodic summary error: %s", e)

        self._summary_tasks[agent_id] = asyncio.ensure_future(_loop())

    def stop_all(self) -> None:
        """Stop all summary tasks."""
        for task in self._summary_tasks.values():
            if not task.done():
                task.cancel()
        self._summary_tasks.clear()

    def clear(self) -> None:
        """Clear all agent tracking."""
        self.stop_all()
        self._agents.clear()

    def summary_dict(self) -> dict[str, Any]:
        return {
            "total": len(self._agents),
            "active": self.active_count(),
            "agents": [
                {
                    "id": p.agent_id,
                    "task": p.task[:60],
                    "status": p.status,
                    "summary": p.summary,
                    "tools": p.tool_count,
                    "turns": p.turns,
                    "elapsed": time.time() - p.started_at,
                }
                for p in self._agents.values()
            ],
        }


# ── Module singleton ───────────────────────────────────────────

_engine: AgentSummaryEngine | None = None


def get_agent_summary_engine() -> AgentSummaryEngine:
    global _engine
    if _engine is None:
        _engine = AgentSummaryEngine()
    return _engine
