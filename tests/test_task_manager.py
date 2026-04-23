"""Tests for ccb.task_manager module."""
import asyncio
import pytest
from ccb.task_manager import TaskManager, TaskStatus, Task, TaskResult


@pytest.fixture
def mgr():
    return TaskManager(max_concurrent=2)


async def dummy_executor(prompt: str, task_id: str = "") -> str:
    await asyncio.sleep(0.05)
    return f"Result for: {prompt[:30]}"


async def failing_executor(prompt: str, task_id: str = "") -> str:
    raise ValueError("Intentional failure")


class TestTaskCreation:
    def test_create_task(self, mgr):
        t = mgr.create_task("Do something", name="Test Task")
        assert t.id.startswith("task_")
        assert t.name == "Test Task"
        assert t.status == TaskStatus.PENDING

    def test_create_with_priority(self, mgr):
        t = mgr.create_task("Important", priority=10)
        assert t.priority == 10

    def test_create_with_tags(self, mgr):
        t = mgr.create_task("Tagged", tags=["dev", "test"])
        assert "dev" in t.tags


class TestTaskExecution:
    @pytest.mark.asyncio
    async def test_run_task(self, mgr):
        t = mgr.create_task("Hello world")
        result = await mgr.run_task(t, dummy_executor)
        assert result.output == "Result for: Hello world"
        assert t.status == TaskStatus.COMPLETED
        assert t.progress == 1.0

    @pytest.mark.asyncio
    async def test_failed_task(self, mgr):
        t = mgr.create_task("Will fail")
        result = await mgr.run_task(t, failing_executor)
        assert t.status == TaskStatus.FAILED
        assert "Intentional" in result.error

    @pytest.mark.asyncio
    async def test_batch(self, mgr):
        results = await mgr.run_batch(
            ["Task A", "Task B", "Task C"],
            dummy_executor,
            names=["A", "B", "C"],
        )
        assert len(results) == 3
        assert all(isinstance(r, TaskResult) for r in results)
        # At least some should succeed
        assert any(r.output for r in results)


class TestTaskManagement:
    def test_list_tasks(self, mgr):
        mgr.create_task("A")
        mgr.create_task("B", priority=5)
        tasks = mgr.list_tasks()
        assert len(tasks) == 2
        # Higher priority first
        assert tasks[0].priority >= tasks[1].priority

    def test_cancel(self, mgr):
        t = mgr.create_task("To cancel")
        t.status = TaskStatus.RUNNING
        assert mgr.cancel_task(t.id) is True
        assert t.status == TaskStatus.CANCELLED

    def test_clear_completed(self, mgr):
        t = mgr.create_task("Done")
        t.status = TaskStatus.COMPLETED
        cleared = mgr.clear_completed()
        assert cleared == 1
        assert mgr.total_count == 0

    def test_summary(self, mgr):
        t1 = mgr.create_task("A")
        t1.status = TaskStatus.COMPLETED
        t2 = mgr.create_task("B")
        t2.status = TaskStatus.RUNNING
        s = mgr.summary()
        assert s["completed"] == 1
        assert s["running"] == 1


class TestSubtasks:
    def test_create_subtask(self, mgr):
        parent = mgr.create_task("Parent")
        sub = mgr.create_subtask(parent.id, "Child")
        assert sub is not None
        assert sub.parent_id == parent.id

    def test_get_subtasks(self, mgr):
        parent = mgr.create_task("Parent")
        mgr.create_subtask(parent.id, "Child 1")
        mgr.create_subtask(parent.id, "Child 2")
        subs = mgr.get_subtasks(parent.id)
        assert len(subs) == 2

    def test_subtask_progress(self, mgr):
        parent = mgr.create_task("Parent")
        s1 = mgr.create_subtask(parent.id, "S1")
        s2 = mgr.create_subtask(parent.id, "S2")
        s1.progress = 1.0
        s2.progress = 0.5
        assert mgr.subtask_progress(parent.id) == pytest.approx(0.75)


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_failed(self, mgr):
        t = mgr.create_task("Will fail then succeed")
        await mgr.run_task(t, failing_executor)
        assert t.status == TaskStatus.FAILED

        result = await mgr.retry_task(t.id, dummy_executor)
        assert result is not None
        assert t.status == TaskStatus.COMPLETED


class TestContext:
    def test_task_context(self, mgr):
        t = mgr.create_task("Test", name="My Task", tags=["dev"])
        ctx = mgr.get_task_context(t.id)
        assert ctx["task_name"] == "My Task"
        assert "dev" in ctx["tags"]
