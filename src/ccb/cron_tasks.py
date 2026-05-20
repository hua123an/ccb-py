"""File-backed scheduled task storage compatible with Claude Code's schema."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ccb.cron import compute_next_cron_run, parse_cron_expression
from ccb.json_store import read_json, write_json

CRON_FILE_REL = Path(".claude") / "scheduled_tasks.json"
SCHEDULED_PROJECTS_PATH = Path.home() / ".ccb" / "scheduled_projects.json"


@dataclass
class CronTask:
    id: str
    cron: str
    prompt: str
    createdAt: int
    lastFiredAt: int | None = None
    recurring: bool = False
    permanent: bool = False
    durable: bool = True
    agentId: str | None = None

    def to_dict(self, *, for_disk: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if data["lastFiredAt"] is None:
            data.pop("lastFiredAt")
        if not data["recurring"]:
            data.pop("recurring")
        if not data["permanent"]:
            data.pop("permanent")
        if for_disk or data["durable"]:
            data.pop("durable", None)
        if for_disk or data.get("agentId") is None:
            data.pop("agentId", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronTask" | None:
        if not isinstance(data, dict):
            return None
        task_id = data.get("id")
        cron = data.get("cron")
        prompt = data.get("prompt")
        created_at = data.get("createdAt")
        if not isinstance(task_id, str) or not isinstance(cron, str):
            return None
        if not isinstance(prompt, str) or not isinstance(created_at, (int, float)):
            return None
        if not parse_cron_expression(cron):
            return None
        last = data.get("lastFiredAt")
        return cls(
            id=task_id,
            cron=cron,
            prompt=prompt,
            createdAt=int(created_at),
            lastFiredAt=int(last) if isinstance(last, (int, float)) else None,
            recurring=bool(data.get("recurring")),
            permanent=bool(data.get("permanent")),
            durable=bool(data.get("durable", True)),
            agentId=data.get("agentId") if isinstance(data.get("agentId"), str) else None,
        )


def now_ms() -> int:
    return int(time.time() * 1000)


def get_cron_file_path(project_dir: str | Path | None = None) -> Path:
    root = Path(project_dir or Path.cwd())
    return root / CRON_FILE_REL


def read_cron_tasks(project_dir: str | Path | None = None) -> list[CronTask]:
    path = get_cron_file_path(project_dir)
    parsed = read_json(path)
    if not isinstance(parsed, dict):
        return []
    raw_tasks = parsed.get("tasks") if isinstance(parsed, dict) else None
    if not isinstance(raw_tasks, list):
        return []
    tasks: list[CronTask] = []
    for item in raw_tasks:
        task = CronTask.from_dict(item)
        if task:
            tasks.append(task)
    return tasks


def write_cron_tasks(tasks: list[CronTask], project_dir: str | Path | None = None) -> None:
    path = get_cron_file_path(project_dir)
    payload = {"tasks": [t.to_dict(for_disk=True) for t in tasks if t.durable]}
    write_json(path, payload, ensure_ascii=False, newline=True)


def add_cron_task(
    cron: str,
    prompt: str,
    project_dir: str | Path | None = None,
    *,
    recurring: bool = False,
    permanent: bool = False,
    task_id: str | None = None,
) -> CronTask:
    if not parse_cron_expression(cron):
        raise ValueError(f"Invalid cron expression: {cron}")
    task = CronTask(
        id=task_id or str(uuid.uuid4())[:8],
        cron=cron,
        prompt=prompt,
        createdAt=now_ms(),
        recurring=recurring,
        permanent=permanent,
    )
    tasks = [t for t in read_cron_tasks(project_dir) if t.id != task.id]
    tasks.append(task)
    write_cron_tasks(tasks, project_dir)
    return task


def remove_cron_tasks(ids: list[str], project_dir: str | Path | None = None) -> int:
    remove = set(ids)
    tasks = read_cron_tasks(project_dir)
    kept = [t for t in tasks if t.id not in remove]
    write_cron_tasks(kept, project_dir)
    return len(tasks) - len(kept)


def mark_cron_tasks_fired(ids: list[str], fired_at_ms: int | None = None,
                          project_dir: str | Path | None = None) -> int:
    fired_at = fired_at_ms or now_ms()
    wanted = set(ids)
    updated = 0
    tasks = read_cron_tasks(project_dir)
    for task in tasks:
        if task.id in wanted:
            task.lastFiredAt = fired_at
            updated += 1
    if updated:
        write_cron_tasks(tasks, project_dir)
    return updated


def find_missed_tasks(tasks: list[CronTask], at_ms: int | None = None) -> list[CronTask]:
    now = at_ms or now_ms()
    missed: list[CronTask] = []
    for task in tasks:
        if task.recurring:
            continue
        fire_at = compute_next_cron_run(task.cron, task.createdAt)
        if fire_at is not None and fire_at <= now:
            missed.append(task)
    return missed


def has_cron_tasks(project_dir: str | Path | None = None) -> bool:
    return bool(read_cron_tasks(project_dir))


def register_scheduled_project(project_dir: str | Path | None = None) -> None:
    root = str(Path(project_dir or Path.cwd()).resolve())
    projects = set(list_scheduled_project_dirs(include_missing=True))
    projects.add(root)
    write_json(
        SCHEDULED_PROJECTS_PATH,
        {"projects": sorted(projects)},
        newline=True,
    )


def unregister_scheduled_project(project_dir: str | Path | None = None) -> None:
    root = str(Path(project_dir or Path.cwd()).resolve())
    projects = [p for p in list_scheduled_project_dirs(include_missing=True) if p != root]
    write_json(
        SCHEDULED_PROJECTS_PATH,
        {"projects": projects},
        newline=True,
    )


def list_scheduled_project_dirs(*, include_missing: bool = False) -> list[str]:
    data = read_json(SCHEDULED_PROJECTS_PATH)
    if not isinstance(data, dict):
        return []
    raw = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    projects = [str(Path(p).resolve()) for p in raw if isinstance(p, str)]
    if include_missing:
        return sorted(set(projects))
    return sorted({p for p in projects if Path(p).is_dir()})
