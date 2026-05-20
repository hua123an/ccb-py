"""Scheduled task scheduler for REPL and daemon use."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ccb.cron import compute_next_cron_run
from ccb.cron_tasks import (
    CronTask,
    find_missed_tasks,
    mark_cron_tasks_fired,
    read_cron_tasks,
    remove_cron_tasks,
)
from ccb.cron_tasks_lock import release_scheduler_lock, try_acquire_scheduler_lock

CHECK_INTERVAL_SECONDS = 1.0
RECURRING_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

FireCallback = Callable[[str], None | Awaitable[None]]
FireTaskCallback = Callable[[CronTask], None | Awaitable[None]]
MissedCallback = Callable[[list[CronTask]], None | Awaitable[None]]


@dataclass
class CronScheduler:
    project_dir: str | Path
    on_fire: FireCallback
    on_fire_task: FireTaskCallback | None = None
    on_missed: MissedCallback | None = None
    is_loading: Callable[[], bool] = lambda: False
    assistant_mode: bool = False
    filter_task: Callable[[CronTask], bool] | None = None
    check_interval: float = CHECK_INTERVAL_SECONDS
    lock_owner: str | None = None

    def __post_init__(self) -> None:
        self._next_fire_at: dict[str, int] = {}
        self._in_flight: set[str] = set()
        self._stopped = True
        self._owned = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owned:
            release_scheduler_lock(self.project_dir)
            self._owned = False

    def get_next_fire_time(self) -> int | None:
        values = [v for v in self._next_fire_at.values() if v < 2**62]
        return min(values) if values else None

    async def _maybe_call(self, cb, *args) -> None:
        result = cb(*args)
        if asyncio.iscoroutine(result):
            await result

    async def _run(self) -> None:
        self._owned = try_acquire_scheduler_lock(self.project_dir, self.lock_owner)
        await self._load_initial(require_lock=True)
        try:
            while not self._stopped:
                if not self._owned:
                    self._owned = try_acquire_scheduler_lock(self.project_dir, self.lock_owner)
                await self.check_once()
                await asyncio.sleep(self.check_interval)
        finally:
            if self._owned:
                release_scheduler_lock(self.project_dir)
                self._owned = False

    async def _load_initial(self, *, require_lock: bool = False) -> None:
        if require_lock and not self._owned:
            return
        tasks = self._filtered(read_cron_tasks(self.project_dir))
        missed = find_missed_tasks(tasks)
        if missed:
            if self.on_missed:
                await self._maybe_call(self.on_missed, missed)
            else:
                await self._maybe_call(self.on_fire, _build_missed_notification(missed))
            remove_cron_tasks([t.id for t in missed], self.project_dir)

    def _filtered(self, tasks: list[CronTask]) -> list[CronTask]:
        if not self.filter_task:
            return tasks
        return [t for t in tasks if self.filter_task(t)]

    async def check_once(self) -> None:
        if self.is_loading() and not self.assistant_mode:
            return
        now = int(time.time() * 1000)
        seen: set[str] = set()
        fired_recurring: list[str] = []
        tasks = self._filtered(read_cron_tasks(self.project_dir)) if self._owned else []
        for task in tasks:
            seen.add(task.id)
            if task.id in self._in_flight:
                continue
            next_fire = self._next_fire_at.get(task.id)
            if next_fire is None:
                anchor = task.lastFiredAt if task.recurring and task.lastFiredAt else task.createdAt
                next_fire = compute_next_cron_run(task.cron, anchor) or 2**63
                self._next_fire_at[task.id] = next_fire
            if now < next_fire:
                continue
            await self._fire_task(task)
            aged = bool(task.recurring and not task.permanent and now - task.createdAt >= RECURRING_MAX_AGE_MS)
            if task.recurring and not aged:
                self._next_fire_at[task.id] = compute_next_cron_run(task.cron, now) or 2**63
                fired_recurring.append(task.id)
            else:
                self._in_flight.add(task.id)
                remove_cron_tasks([task.id], self.project_dir)
                self._in_flight.discard(task.id)
                self._next_fire_at.pop(task.id, None)
        if fired_recurring:
            mark_cron_tasks_fired(fired_recurring, now, self.project_dir)
        for task_id in list(self._next_fire_at):
            if task_id not in seen:
                self._next_fire_at.pop(task_id, None)

    async def _fire_task(self, task: CronTask) -> None:
        if self.on_fire_task:
            await self._maybe_call(self.on_fire_task, task)
        else:
            await self._maybe_call(self.on_fire, task.prompt)


def _build_missed_notification(tasks: list[CronTask]) -> str:
    lines = ["Scheduled tasks were missed while ccb was not running:", ""]
    for task in tasks:
        lines.append(f"- {task.id}: {task.prompt}")
    lines.append("")
    lines.append("Run these now if still relevant; otherwise ignore this notification.")
    return "\n".join(lines)
