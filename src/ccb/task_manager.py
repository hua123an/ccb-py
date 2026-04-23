"""Multi-task parallel execution system for ccb-py.

Supports running multiple agent tasks concurrently with isolated
contexts and progress tracking.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskResult:
    output: str = ""
    error: str = ""
    files_changed: list[str] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0


@dataclass
class Task:
    id: str
    name: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0  # higher = more important
    result: TaskResult | None = None
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    progress: float = 0.0  # 0.0 - 1.0
    parent_id: str | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.started_at == 0:
            return 0.0
        end = self.completed_at if self.completed_at else time.time()
        return end - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "priority": self.priority,
            "progress": self.progress,
            "duration": round(self.duration, 1),
            "created_at": self.created_at,
            "output": self.result.output[:200] if self.result else "",
            "error": self.result.error if self.result else "",
        }


class TaskManager:
    """Manages concurrent task execution with isolation."""

    def __init__(self, max_concurrent: int = 3):
        self._tasks: dict[str, Task] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._on_complete: list[Callable[[Task], None]] = []

    def create_task(
        self,
        prompt: str,
        name: str = "",
        priority: int = 0,
        parent_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Task:
        tid = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(
            id=tid,
            name=name or f"Task {len(self._tasks) + 1}",
            prompt=prompt,
            priority=priority,
            created_at=time.time(),
            parent_id=parent_id,
            tags=tags or [],
        )
        self._tasks[tid] = task
        return task

    async def submit(self, task: Task) -> None:
        """Submit a task for execution."""
        await self._queue.put(task.id)

    async def run_task(self, task: Task, executor: Callable[..., Any]) -> TaskResult:
        """Execute a single task with the given executor function."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            async with self._semaphore:
                result = await executor(task.prompt, task_id=task.id)
                task.result = TaskResult(
                    output=result if isinstance(result, str) else str(result),
                )
                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.result = TaskResult(error="Task cancelled")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.result = TaskResult(error=str(e))
        finally:
            task.completed_at = time.time()
            for cb in self._on_complete:
                try:
                    cb(task)
                except Exception:
                    pass

        return task.result or TaskResult()

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        return True

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        parent_id: str | None = None,
    ) -> list[Task]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        if parent_id:
            tasks = [t for t in tasks if t.parent_id == parent_id]
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks

    def on_complete(self, callback: Callable[[Task], None]) -> None:
        self._on_complete.append(callback)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    @property
    def total_count(self) -> int:
        return len(self._tasks)

    def clear_completed(self) -> int:
        ids = [
            tid for tid, t in self._tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        for tid in ids:
            del self._tasks[tid]
        return len(ids)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self._tasks.values():
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        return counts

    # ── Worker pool ──

    async def start_workers(self, executor: Callable[..., Any]) -> None:
        """Start background worker pool that pulls from the queue."""
        self._running = True
        for i in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(executor, i))
            self._workers.append(worker)

    async def stop_workers(self) -> None:
        self._running = False
        # Signal workers to stop
        for _ in self._workers:
            await self._queue.put("")  # Sentinel
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def _worker_loop(self, executor: Callable[..., Any], worker_id: int) -> None:
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not task_id:  # Sentinel
                break
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.PENDING:
                await self.run_task(task, executor)

    # ── Batch operations ──

    async def run_batch(
        self,
        prompts: list[str],
        executor: Callable[..., Any],
        names: list[str] | None = None,
    ) -> list[TaskResult]:
        """Create and run multiple tasks, returning all results."""
        tasks = []
        for i, prompt in enumerate(prompts):
            name = names[i] if names and i < len(names) else f"Batch {i+1}"
            t = self.create_task(prompt, name=name)
            tasks.append(t)

        # Run all concurrently with semaphore
        results = await asyncio.gather(
            *[self.run_task(t, executor) for t in tasks],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, TaskResult) else TaskResult(error=str(r))
            for r in results
        ]

    # ── Subtask support ──

    def create_subtask(self, parent_id: str, prompt: str, name: str = "") -> Task | None:
        """Create a subtask linked to a parent."""
        parent = self._tasks.get(parent_id)
        if not parent:
            return None
        task = self.create_task(prompt, name=name or f"Sub: {parent.name}", parent_id=parent_id)
        return task

    def get_subtasks(self, parent_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.parent_id == parent_id]

    def subtask_progress(self, parent_id: str) -> float:
        """Get aggregate progress of all subtasks."""
        subs = self.get_subtasks(parent_id)
        if not subs:
            return 0.0
        return sum(t.progress for t in subs) / len(subs)

    # ── Task isolation context ──

    def get_task_context(self, task_id: str) -> dict[str, Any]:
        """Get isolated context for a task (for use in agent execution)."""
        task = self._tasks.get(task_id)
        if not task:
            return {}
        return {
            "task_id": task.id,
            "task_name": task.name,
            "parent_id": task.parent_id,
            "prompt": task.prompt,
            "tags": task.tags,
            "created_at": task.created_at,
        }

    # ── Retry ──

    async def retry_task(self, task_id: str, executor: Callable[..., Any]) -> TaskResult | None:
        """Retry a failed task."""
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return None
        task.status = TaskStatus.PENDING
        task.result = None
        task.progress = 0.0
        task.started_at = 0.0
        task.completed_at = 0.0
        return await self.run_task(task, executor)

    # ── Wait ──

    async def wait_for_task(self, task_id: str, timeout: float = 300) -> Task | None:
        """Wait for a task to complete."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return task
            await asyncio.sleep(0.5)
        return self._tasks.get(task_id)

    async def wait_all(self, timeout: float = 600) -> list[Task]:
        """Wait for all tasks to complete."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(
                t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                for t in self._tasks.values()
            ):
                break
            await asyncio.sleep(0.5)
        return list(self._tasks.values())


# Module singleton
_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
