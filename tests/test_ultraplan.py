"""Tests for ccb.ultraplan, ccb.coordinator, ccb.buddy_impl, ccb.migrations modules."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from ccb.ultraplan import PlanStep, Plan, PlanManager, StepStatus, generate_plan_prompt
from ccb.coordinator import AgentInstance, Coordinator
from ccb.buddy_impl import Buddy, BuddyState, PETS
from ccb.migrations import migrate_config, migrate_installed_plugins, CURRENT_VERSION


# ── UltraPlan ──

class TestPlanStep:
    def test_duration_not_started(self):
        s = PlanStep(id="s1", title="Step 1")
        assert s.duration == 0

    def test_duration_running(self):
        s = PlanStep(id="s1", title="Step 1", started_at=time.time() - 10)
        assert s.duration >= 9


class TestPlan:
    def test_progress_empty(self):
        p = Plan(id="p1", title="Test")
        assert p.progress == 0.0

    def test_progress_partial(self):
        p = Plan(id="p1", title="Test", steps=[
            PlanStep(id="s1", title="A", status=StepStatus.COMPLETED),
            PlanStep(id="s2", title="B", status=StepStatus.PENDING),
        ])
        assert p.progress == 0.5

    def test_next_steps_dependency(self):
        p = Plan(id="p1", title="Test", steps=[
            PlanStep(id="s1", title="A", status=StepStatus.COMPLETED),
            PlanStep(id="s2", title="B", depends_on=["s1"]),
            PlanStep(id="s3", title="C", depends_on=["s2"]),
        ])
        nexts = p.next_steps()
        assert len(nexts) == 1
        assert nexts[0].id == "s2"

    def test_critical_path(self):
        p = Plan(id="p1", title="Test", steps=[
            PlanStep(id="s1", title="A"),
            PlanStep(id="s2", title="B", depends_on=["s1"]),
            PlanStep(id="s3", title="C", depends_on=["s2"]),
        ])
        path = p.critical_path()
        assert [s.id for s in path] == ["s1", "s2", "s3"]

    def test_status_summary(self):
        p = Plan(id="p1", title="Test", steps=[
            PlanStep(id="s1", title="A", status=StepStatus.COMPLETED),
            PlanStep(id="s2", title="B", status=StepStatus.PENDING),
            PlanStep(id="s3", title="C", status=StepStatus.PENDING),
        ])
        summary = p.status_summary
        assert summary["completed"] == 1
        assert summary["pending"] == 2

    def test_to_markdown(self):
        p = Plan(id="p1", title="Test Plan", steps=[
            PlanStep(id="s1", title="Do thing", status=StepStatus.COMPLETED),
        ])
        md = p.to_markdown()
        assert "Test Plan" in md
        assert "✅" in md


class TestPlanManager:
    def test_create_plan(self):
        mgr = PlanManager()
        plan = mgr.create_plan("My Plan")
        assert plan.title == "My Plan"
        assert mgr.active_plan is plan

    def test_add_step(self):
        mgr = PlanManager()
        plan = mgr.create_plan("Test")
        step = mgr.add_step(None, "s1", "Step 1")
        assert step is not None
        assert len(plan.steps) == 1

    def test_update_step(self):
        mgr = PlanManager()
        plan = mgr.create_plan("Test")
        mgr.add_step(None, "s1", "Step 1")
        assert mgr.update_step("s1", StepStatus.COMPLETED, output="Done") is True

    def test_blocked_propagation(self):
        mgr = PlanManager()
        plan = mgr.create_plan("Test")
        mgr.add_step(None, "s1", "Step 1")
        mgr.add_step(None, "s2", "Step 2", depends_on=["s1"])
        mgr.update_step("s1", StepStatus.FAILED)
        assert plan.steps[1].status == StepStatus.BLOCKED

    def test_delete_plan(self):
        mgr = PlanManager()
        plan = mgr.create_plan("To delete")
        assert mgr.delete_plan(plan.id) is True
        assert mgr.active_plan is None

    def test_generate_prompt(self):
        prompt = generate_plan_prompt("Build a web app")
        assert "Build a web app" in prompt
        assert "JSON" in prompt


# ── Coordinator ──

class TestCoordinator:
    def test_create_agent(self):
        c = Coordinator()
        a = c.create_agent("agent-1", role="coder", prompt="Write code")
        assert a.name == "agent-1"
        assert a.status == "idle"

    @pytest.mark.asyncio
    async def test_run_agent(self):
        c = Coordinator()
        a = c.create_agent("test", prompt="hello")

        async def executor(prompt: str) -> str:
            return f"Result: {prompt}"

        result = await c.run_agent(a, executor)
        assert result == "Result: hello"
        assert a.status == "done"

    @pytest.mark.asyncio
    async def test_run_parallel(self):
        c = Coordinator()
        agents = [c.create_agent(f"a{i}", prompt=f"task {i}") for i in range(3)]

        async def executor(prompt: str) -> str:
            return prompt.upper()

        results = await c.run_parallel(agents, executor)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_run_sequential_chain(self):
        c = Coordinator()
        agents = [
            c.create_agent("step1", prompt="start"),
            c.create_agent("step2", prompt="continue"),
        ]

        async def executor(prompt: str) -> str:
            return prompt + " -> done"

        results = await c.run_sequential(agents, executor, chain=True)
        assert len(results) == 2
        assert "Previous output" in agents[1].prompt

    @pytest.mark.asyncio
    async def test_error_handling(self):
        c = Coordinator()
        a = c.create_agent("bad", prompt="fail")

        async def executor(prompt: str) -> str:
            raise RuntimeError("boom")

        result = await c.run_agent(a, executor)
        assert result == ""
        assert a.status == "error"

    def test_summary(self):
        c = Coordinator()
        c.create_agent("a1")
        assert c.summary()["total"] == 1

    def test_clear(self):
        c = Coordinator()
        c.create_agent("a1")
        c.create_agent("a2")
        assert c.clear() == 2
        assert c.summary()["total"] == 0


# ── Buddy ──

class TestBuddy:
    def test_state_defaults(self):
        s = BuddyState()
        assert s.enabled is False
        assert s.pet == "cat"
        assert s.level == 1

    def test_gain_xp(self):
        s = BuddyState(xp=95, level=1)
        leveled = s.gain_xp(10)
        assert leveled is True
        assert s.level == 2
        assert s.xp == 5  # 95 + 10 - 100

    def test_buddy_toggle(self, tmp_path):
        with patch.object(Buddy, "_load"):
            b = Buddy()
            b._config_path = tmp_path / "buddy.json"
            assert b.toggle() is True
            assert b.enabled is True

    def test_set_pet(self, tmp_path):
        with patch.object(Buddy, "_load"):
            b = Buddy()
            b._config_path = tmp_path / "buddy.json"
            assert b.set_pet("dog") is True
            assert b.state.pet == "dog"
            assert b.set_pet("dragon") is False  # Not available

    def test_render_disabled(self, tmp_path):
        with patch.object(Buddy, "_load"):
            b = Buddy()
            b._config_path = tmp_path / "buddy.json"
            assert b.render() == ""

    def test_render_enabled(self, tmp_path):
        with patch.object(Buddy, "_load"):
            b = Buddy()
            b._config_path = tmp_path / "buddy.json"
            b._state.enabled = True
            rendered = b.render()
            assert "Buddy" in rendered

    def test_all_pets_have_idle(self):
        for pet_name, pet_art in PETS.items():
            assert "idle" in pet_art, f"{pet_name} missing idle art"


# ── Migrations ──

class TestMigrations:
    def test_migrate_nonexistent(self, tmp_path):
        result = migrate_config(tmp_path / "nonexistent.json")
        assert result["_config_version"] == CURRENT_VERSION

    def test_migrate_v0_to_v2(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "api_key": "sk-123",
            "provider": "anthropic",
            "theme": "monokai",
        }))
        result = migrate_config(path)
        assert result["_config_version"] == CURRENT_VERSION
        assert "providers" in result
        assert "settings" in result

    def test_migrate_current_version(self, tmp_path):
        path = tmp_path / "config.json"
        data = {"_config_version": CURRENT_VERSION, "key": "value"}
        path.write_text(json.dumps(data))
        result = migrate_config(path)
        assert result["key"] == "value"

    def test_migrate_plugins_flat(self, tmp_path):
        path = tmp_path / "installed_plugins.json"
        path.write_text(json.dumps({
            "plugin-a": {"name": "plugin-a", "version": "1.0"},
        }))
        result = migrate_installed_plugins(path)
        assert result.get("version") == 2
        assert "plugins" in result

    def test_migrate_plugins_already_v2(self, tmp_path):
        path = tmp_path / "installed_plugins.json"
        data = {"version": 2, "plugins": {"a": [{"name": "a"}]}}
        path.write_text(json.dumps(data))
        result = migrate_installed_plugins(path)
        assert result == data
