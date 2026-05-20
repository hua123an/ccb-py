from __future__ import annotations

import asyncio
import time

from ccb.cron import compute_next_cron_run, parse_cron_expression
from ccb.cron_scheduler import CronScheduler
from ccb.cron_tasks import (
    add_cron_task,
    find_missed_tasks,
    list_scheduled_project_dirs,
    register_scheduled_project,
    read_cron_tasks,
    remove_cron_tasks,
    unregister_scheduled_project,
    write_cron_tasks,
)
from ccb.feature_flags import DEFAULT_FEATURE_FLAGS, FeatureFlags


def test_parse_cron_expression_supports_steps_ranges_and_lists() -> None:
    expr = parse_cron_expression("*/5 9-17 * * 1,2,3,4,5")
    assert expr is not None
    assert parse_cron_expression("60 * * * *") is None
    assert parse_cron_expression("* * *") is None


def test_compute_next_cron_run_returns_future_minute() -> None:
    start = int(time.mktime(time.strptime("2025-01-01 12:00", "%Y-%m-%d %H:%M")) * 1000)
    next_run = compute_next_cron_run("*/15 * * * *", start)
    assert next_run == int(time.mktime(time.strptime("2025-01-01 12:15", "%Y-%m-%d %H:%M")) * 1000)


def test_cron_tasks_persist_original_schema(tmp_path) -> None:
    task = add_cron_task("*/30 * * * *", "Check build status", tmp_path, recurring=True, task_id="task1")
    tasks = read_cron_tasks(tmp_path)
    assert [t.id for t in tasks] == [task.id]
    path = tmp_path / ".claude" / "scheduled_tasks.json"
    data = path.read_text()
    assert '"tasks"' in data
    assert '"cron": "*/30 * * * *"' in data
    assert '"prompt": "Check build status"' in data
    assert '"durable"' not in data


def test_find_missed_one_shot_tasks(tmp_path) -> None:
    task = add_cron_task("* * * * *", "late prompt", tmp_path, recurring=False, task_id="late")
    task.createdAt = int((time.time() - 120) * 1000)
    write_cron_tasks([task], tmp_path)
    assert [t.id for t in find_missed_tasks(read_cron_tasks(tmp_path))] == ["late"]


def test_scheduler_initial_load_fires_missed_tasks_and_removes_them(tmp_path) -> None:
    task = add_cron_task("* * * * *", "late prompt", tmp_path, recurring=False, task_id="late")
    task.createdAt = int((time.time() - 120) * 1000)
    write_cron_tasks([task], tmp_path)
    fired: list[str] = []

    async def main() -> None:
        scheduler = CronScheduler(project_dir=tmp_path, on_fire=fired.append, check_interval=0.01)
        await scheduler._load_initial()

    asyncio.run(main())
    assert fired
    assert remove_cron_tasks(["late"], tmp_path) == 0


def test_scheduled_project_registry_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("ccb.cron_tasks.SCHEDULED_PROJECTS_PATH", tmp_path / "scheduled_projects.json")
    project = tmp_path / "project"
    project.mkdir()
    register_scheduled_project(project)
    assert list_scheduled_project_dirs() == [str(project.resolve())]
    unregister_scheduled_project(project)
    assert list_scheduled_project_dirs(include_missing=True) == []


def test_list_scheduled_project_dirs_ignores_invalid_json(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scheduled_projects.json"
    path.write_text("not-json")
    monkeypatch.setattr("ccb.cron_tasks.SCHEDULED_PROJECTS_PATH", path)

    assert list_scheduled_project_dirs() == []


def test_feature_flag_registry_has_claude_code_parity_defaults(monkeypatch) -> None:
    flags = FeatureFlags()
    all_flags = flags.list_flags()
    assert len(DEFAULT_FEATURE_FLAGS) >= 40
    assert all_flags["tengu_kairos_cron"] is True
    assert flags.is_enabled("scheduled_tasks") is True
    monkeypatch.setenv("CLAUDE_CODE_FLAG_TENGU_KAIROS_CRON", "0")
    assert flags.is_enabled("tengu_kairos_cron") is False
