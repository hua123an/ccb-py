"""Multi-agent coordinator for ccb-py.

Manages multiple concurrent agent instances, dispatching subtasks,
collecting results, and merging outputs.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class AgentInstance:
    id: str
    name: str
    role: str = ""
    prompt: str = ""
    status: str = "idle"  # idle, running, done, error
    result: str = ""
    error: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0

    @property
    def duration(self) -> float:
        if not self.started_at:
            return 0
        return (self.completed_at or time.time()) - self.started_at


class Coordinator:
    """Orchestrates multiple agents working on parts of a problem."""

    def __init__(self, max_agents: int = 5):
        self._agents: dict[str, AgentInstance] = {}
        self._max_agents = max_agents
        self._semaphore = asyncio.Semaphore(max_agents)

    def create_agent(self, name: str, role: str = "", prompt: str = "") -> AgentInstance:
        aid = f"agent_{uuid.uuid4().hex[:6]}"
        agent = AgentInstance(id=aid, name=name, role=role, prompt=prompt)
        self._agents[aid] = agent
        return agent

    async def run_agent(
        self,
        agent: AgentInstance,
        executor: Callable[[str], Awaitable[str]],
    ) -> str:
        """Run a single agent with the given executor."""
        async with self._semaphore:
            agent.status = "running"
            agent.started_at = time.time()
            try:
                result = await executor(agent.prompt)
                agent.result = result
                agent.status = "done"
                return result
            except Exception as e:
                agent.error = str(e)
                agent.status = "error"
                return ""
            finally:
                agent.completed_at = time.time()

    async def run_parallel(
        self,
        agents: list[AgentInstance],
        executor: Callable[[str], Awaitable[str]],
    ) -> list[str]:
        """Run multiple agents in parallel."""
        tasks = [self.run_agent(a, executor) for a in agents]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def run_sequential(
        self,
        agents: list[AgentInstance],
        executor: Callable[[str], Awaitable[str]],
        chain: bool = False,
    ) -> list[str]:
        """Run agents sequentially. If chain=True, each agent gets previous output."""
        results = []
        prev_output = ""
        for agent in agents:
            if chain and prev_output:
                agent.prompt = f"{agent.prompt}\n\nPrevious output:\n{prev_output}"
            result = await self.run_agent(agent, executor)
            results.append(result)
            prev_output = result
        return results

    async def fan_out_merge(
        self,
        task: str,
        subtasks: list[str],
        executor: Callable[[str], Awaitable[str]],
        merge_prompt: str | None = None,
    ) -> str:
        """Fan-out: split into subtasks, run parallel, merge results."""
        agents = [
            self.create_agent(f"subtask-{i+1}", prompt=sub)
            for i, sub in enumerate(subtasks)
        ]
        results = await self.run_parallel(agents, executor)
        # Merge
        if merge_prompt:
            combined = "\n\n---\n\n".join(
                f"Subtask {i+1} result:\n{r}" for i, r in enumerate(results)
            )
            merge_agent = self.create_agent("merger", prompt=f"{merge_prompt}\n\n{combined}")
            return await self.run_agent(merge_agent, executor)
        return "\n\n".join(results)

    def list_agents(self) -> list[AgentInstance]:
        return sorted(self._agents.values(), key=lambda a: a.started_at or 0, reverse=True)

    def get_agent(self, aid: str) -> AgentInstance | None:
        return self._agents.get(aid)

    @property
    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.status == "running")

    def clear(self) -> int:
        count = len(self._agents)
        self._agents.clear()
        return count

    def summary(self) -> dict[str, Any]:
        agents = list(self._agents.values())
        return {
            "total": len(agents),
            "running": sum(1 for a in agents if a.status == "running"),
            "done": sum(1 for a in agents if a.status == "done"),
            "error": sum(1 for a in agents if a.status == "error"),
        }


# Module singleton
_coord: Coordinator | None = None


def get_coordinator() -> Coordinator:
    global _coord
    if _coord is None:
        _coord = Coordinator()
    return _coord
