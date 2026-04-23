"""Tests for new modules: auto_dream, context_collapse, magic_docs,
tips, policy_limits, agent_summary, session_transcript, tool_use_summary,
upstream_proxy, jobs, memdir.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest


# ── AutoDream ──────────────────────────────────────────────────

class TestAutoDream:
    def test_config_defaults(self):
        from ccb.auto_dream import AutoDreamConfig
        cfg = AutoDreamConfig()
        assert cfg.min_hours == 24.0
        assert cfg.min_sessions == 5
        assert cfg.enabled is True

    def test_read_last_consolidated_at_no_file(self, tmp_path):
        from ccb.auto_dream import read_last_consolidated_at
        with patch("ccb.auto_dream._lock_path", return_value=tmp_path / "nope.json"):
            assert read_last_consolidated_at() == 0.0

    def test_read_write_lock(self, tmp_path):
        from ccb.auto_dream import read_last_consolidated_at, _write_lock, _lock_path
        lp = tmp_path / "lock.json"
        with patch("ccb.auto_dream._lock_path", return_value=lp):
            _write_lock(12345.0)
            assert read_last_consolidated_at() == 12345.0

    def test_list_sessions(self, tmp_path):
        from ccb.auto_dream import list_sessions_touched_since
        with patch("ccb.auto_dream._sessions_dir", return_value=tmp_path):
            (tmp_path / "a.json").write_text("{}")
            (tmp_path / "b.json").write_text("{}")
            result = list_sessions_touched_since(0.0)
            assert set(result) == {"a", "b"}

    def test_dream_task_tracking(self):
        from ccb.auto_dream import DreamTask
        t = DreamTask(task_id="t1", sessions_reviewing=3)
        t.add_turn("hello", 2, ["/tmp/a.py"])
        assert len(t.turns) == 1
        assert t.files_touched == ["/tmp/a.py"]

    def test_build_prompt(self):
        from ccb.auto_dream import build_consolidation_prompt
        p = build_consolidation_prompt("/mem", "/trans", ["s1", "s2"])
        assert "s1" in p
        assert "/mem" in p

    def test_init(self):
        from ccb.auto_dream import init_auto_dream, _initialized
        init_auto_dream()
        from ccb.auto_dream import _initialized as inited
        assert inited is True

    def test_kill_dream_task(self):
        from ccb.auto_dream import DreamTask, _dream_tasks, kill_dream_task
        _dream_tasks["x"] = DreamTask(task_id="x", status="running", prior_mtime=100.0)
        assert kill_dream_task("x") is True
        assert _dream_tasks["x"].status == "killed"
        assert kill_dream_task("nope") is False


# ── ContextCollapse ────────────────────────────────────────────

class TestContextCollapse:
    def test_init_and_stats(self):
        from ccb.context_collapse import init_context_collapse, get_stats
        init_context_collapse()
        s = get_stats()
        assert s.collapsed_spans == 0
        assert s.collapsed_messages == 0

    def test_project_view_no_commits(self):
        from ccb.context_collapse import init_context_collapse, project_view
        init_context_collapse()
        msgs = [MagicMock(id=f"m{i}", content=f"msg {i}") for i in range(5)]
        result = project_view(msgs)
        assert len(result) == 5

    def test_collapse_small_list_no_change(self):
        from ccb.context_collapse import init_context_collapse, apply_collapses_if_needed
        init_context_collapse()
        msgs = [MagicMock(id=f"m{i}", content=f"msg {i}") for i in range(5)]
        result = apply_collapses_if_needed(msgs, context_limit=200000)
        assert len(result) == 5  # too few to collapse

    def test_recover_from_overflow_small(self):
        from ccb.context_collapse import init_context_collapse, recover_from_overflow
        init_context_collapse()
        msgs = [MagicMock(id=f"m{i}", content=f"msg {i}") for i in range(5)]
        committed, result = recover_from_overflow(msgs)
        assert committed == 0

    def test_summarize_message(self):
        from ccb.context_collapse import _summarize_message
        msg = MagicMock()
        msg.role = MagicMock()
        msg.role.value = "user"
        from ccb.api.base import Role
        msg.role = Role.USER
        msg.content = "Hello world"
        msg.tool_results = []
        s = _summarize_message(msg)
        assert "User" in s
        assert "Hello" in s

    def test_subscribe(self):
        from ccb.context_collapse import init_context_collapse, subscribe
        init_context_collapse()
        called = []
        unsub = subscribe(lambda: called.append(1))
        # Trigger notification
        from ccb.context_collapse import _notify
        _notify()
        assert len(called) == 1
        unsub()

    def test_truncate(self):
        from ccb.context_collapse import _truncate
        assert _truncate("short") == "short"
        long = "x" * 200
        assert len(_truncate(long)) <= 180


# ── MagicDocs ──────────────────────────────────────────────────

class TestMagicDocs:
    def test_detect_magic_doc(self, tmp_path):
        from ccb.magic_docs import MagicDocsEngine
        engine = MagicDocsEngine()
        content = "# MAGIC DOC: API Guide\n\nThis is the API guide."
        f = tmp_path / "api.md"
        f.write_text(content)
        doc = engine.register_file_read(str(f), content)
        assert doc is not None
        assert doc.title == "API Guide"

    def test_non_magic_doc(self):
        from ccb.magic_docs import MagicDocsEngine
        engine = MagicDocsEngine()
        doc = engine.register_file_read("/tmp/normal.md", "# Normal Doc\nHello")
        assert doc is None

    def test_add_context(self):
        from ccb.magic_docs import MagicDocsEngine
        engine = MagicDocsEngine()
        content = "# MAGIC DOC: Test\nContent"
        engine.register_file_read("/tmp/test.md", content)
        engine.add_context("Some conversation text")
        assert len(engine._conversation_context) == 1

    def test_get_pending(self):
        from ccb.magic_docs import MagicDocsEngine, UPDATE_INTERVAL_SECONDS
        engine = MagicDocsEngine()
        content = "# MAGIC DOC: Test\nContent"
        doc = engine.register_file_read("/tmp/test.md", content)
        doc.pending_update = True
        pending = engine.get_pending_docs()
        assert len(pending) == 1

    def test_summary(self):
        from ccb.magic_docs import MagicDocsEngine
        engine = MagicDocsEngine()
        s = engine.summary()
        assert s["tracked"] == 0

    def test_singleton(self):
        from ccb.magic_docs import get_magic_docs
        e1 = get_magic_docs()
        e2 = get_magic_docs()
        assert e1 is e2


# ── Tips ───────────────────────────────────────────────────────

class TestTips:
    def test_tip_registry(self):
        from ccb.tips import TipRegistry
        reg = TipRegistry()
        assert len(reg.all_tips) > 10
        assert reg.get("compact") is not None

    def test_tip_history(self):
        from ccb.tips import TipHistory
        h = TipHistory()
        assert h.show_count("x") == 0
        h.record("x")
        assert h.show_count("x") == 1
        assert h.last_shown("x") > 0

    def test_tip_scheduler_cooldown(self):
        from ccb.tips import TipScheduler, TipHistory
        h = TipHistory()
        h.record("x")  # recent tip
        s = TipScheduler(history=h, cooldown_seconds=9999)
        assert s.should_show_tip() is False

    def test_tip_scheduler_pick(self):
        from ccb.tips import TipScheduler, TipHistory
        h = TipHistory()
        s = TipScheduler(history=h, cooldown_seconds=0, show_probability=1.0)
        tip = s.pick_tip()
        assert tip is not None
        assert tip.text

    def test_format_tip(self):
        from ccb.tips import TipScheduler, Tip
        s = TipScheduler()
        t = Tip(id="test", text="Hello tip")
        assert "💡" in s.format_tip(t)

    def test_categories(self):
        from ccb.tips import TipRegistry
        reg = TipRegistry()
        git_tips = reg.tips_for_category("git")
        assert len(git_tips) >= 1

    def test_save_load_history(self, tmp_path):
        from ccb.tips import TipScheduler, TipHistory
        h = TipHistory()
        h.record("compact")
        s = TipScheduler(history=h)
        s._persistence_path = tmp_path / "tips.json"
        s.save_history()
        assert s._persistence_path.exists()

        s2 = TipScheduler()
        s2._persistence_path = tmp_path / "tips.json"
        s2.load_history()
        assert s2.history.show_count("compact") == 1


# ── PolicyLimits ───────────────────────────────────────────────

class TestPolicyLimits:
    def test_default_allowed(self):
        from ccb.policy_limits import PolicyLimitsService
        svc = PolicyLimitsService()
        assert svc.is_allowed("any_feature") is True

    def test_blocked_features(self):
        from ccb.policy_limits import PolicyLimitsService, PolicyRestriction
        svc = PolicyLimitsService()
        svc._state.restrictions["bash"] = PolicyRestriction(key="bash", allowed=False)
        assert "bash" in svc.blocked_features()
        assert svc.is_allowed("bash") is False
        assert svc.is_allowed("other") is True

    def test_cache_roundtrip(self, tmp_path):
        from ccb.policy_limits import PolicyLimitsService, PolicyRestriction
        svc = PolicyLimitsService()
        svc._state.restrictions["x"] = PolicyRestriction(key="x", allowed=False)
        cache_path = tmp_path / "policy.json"
        with patch.object(svc, "_cache_path", return_value=cache_path):
            svc._save_cache()
            assert cache_path.exists()

            svc2 = PolicyLimitsService()
            with patch.object(svc2, "_cache_path", return_value=cache_path):
                svc2._load_cache()
                assert svc2.is_allowed("x") is False

    def test_summary(self):
        from ccb.policy_limits import PolicyLimitsService
        svc = PolicyLimitsService()
        s = svc.summary()
        assert "initialized" in s
        assert "blocked" in s


# ── AgentSummary ───────────────────────────────────────────────

class TestAgentSummary:
    def test_register_and_update(self):
        from ccb.agent_summary import AgentSummaryEngine
        e = AgentSummaryEngine()
        p = e.register("a1", "fix bug")
        assert p.agent_id == "a1"
        e.update_tool("a1", "bash")
        assert p.tool_count == 1
        e.update_turn("a1", "working on it")
        assert p.turns == 1
        assert "working" in p.summary

    def test_complete(self):
        from ccb.agent_summary import AgentSummaryEngine
        e = AgentSummaryEngine()
        e.register("a1", "task")
        e.complete("a1", "Done fixing")
        p = e.get_progress("a1")
        assert p.status == "completed"
        assert p.summary == "Done fixing"

    def test_fail(self):
        from ccb.agent_summary import AgentSummaryEngine
        e = AgentSummaryEngine()
        e.register("a1", "task")
        e.fail("a1", "timeout")
        assert e.get_progress("a1").status == "failed"

    def test_active_count(self):
        from ccb.agent_summary import AgentSummaryEngine
        e = AgentSummaryEngine()
        e.register("a1", "t1")
        e.register("a2", "t2")
        e.complete("a2")
        assert e.active_count() == 1

    def test_summary_dict(self):
        from ccb.agent_summary import AgentSummaryEngine
        e = AgentSummaryEngine()
        e.register("a1", "task")
        s = e.summary_dict()
        assert s["total"] == 1
        assert s["active"] == 1

    def test_singleton(self):
        from ccb.agent_summary import get_agent_summary_engine
        e1 = get_agent_summary_engine()
        e2 = get_agent_summary_engine()
        assert e1 is e2


# ── SessionTranscript ──────────────────────────────────────────

class TestSessionTranscript:
    def test_export_basic(self):
        from ccb.session_transcript import export_transcript
        from ccb.api.base import Role
        session = MagicMock()
        session.id = "s1"
        session.model = "test-model"
        session.cwd = "/tmp"
        msg1 = MagicMock()
        msg1.role = Role.USER
        msg1.content = "Hello"
        msg1.tool_results = []
        msg1.images = []
        msg1.files = []
        msg2 = MagicMock()
        msg2.role = Role.ASSISTANT
        msg2.content = "Hi there!"
        msg2.tool_calls = []
        session.messages = [msg1, msg2]

        md = export_transcript(session)
        assert "# Session Transcript" in md
        assert "🧑 You" in md
        assert "Hello" in md
        assert "🤖 Claude" in md
        assert "Hi there!" in md

    def test_export_compact(self):
        from ccb.session_transcript import export_transcript_compact
        session = MagicMock()
        session.id = "s1"
        session.model = "m"
        session.cwd = "."
        session.messages = []
        md = export_transcript_compact(session)
        assert "Transcript" in md


# ── ToolUseSummary ─────────────────────────────────────────────

class TestToolUseSummary:
    def test_heuristic_single_bash(self):
        from ccb.tool_use_summary import summarize_batch_sync
        calls = [{"name": "bash", "input": {"command": "ls"}}]
        results = [{"content": "file1\nfile2", "is_error": False}]
        s = summarize_batch_sync(calls, results)
        assert "command" in s.lower() or "ran" in s.lower()

    def test_heuristic_multi_read(self):
        from ccb.tool_use_summary import summarize_batch_sync
        calls = [
            {"name": "file_read", "input": {"file_path": "a.py"}},
            {"name": "file_read", "input": {"file_path": "b.py"}},
        ]
        results = [
            {"content": "...", "is_error": False},
            {"content": "...", "is_error": False},
        ]
        s = summarize_batch_sync(calls, results)
        assert "2" in s and "file" in s.lower()

    def test_heuristic_mixed(self):
        from ccb.tool_use_summary import summarize_batch_sync
        calls = [
            {"name": "grep", "input": {"pattern": "foo"}},
            {"name": "file_edit", "input": {"file_path": "x.py"}},
        ]
        results = [
            {"content": "", "is_error": False},
            {"content": "", "is_error": False},
        ]
        s = summarize_batch_sync(calls, results)
        assert "grep" in s or "file_edit" in s

    def test_empty(self):
        from ccb.tool_use_summary import summarize_batch_sync
        assert "No tools" in summarize_batch_sync([], [])


# ── UpstreamProxy ──────────────────────────────────────────────

class TestUpstreamProxy:
    def test_proxy_config_defaults(self):
        from ccb.upstream_proxy import ProxyConfig
        c = ProxyConfig()
        assert c.listen_port == 8901
        assert c.listen_host == "127.0.0.1"

    def test_stats_record(self):
        from ccb.upstream_proxy import ProxyStats
        s = ProxyStats()
        s.record_request(100.0, 500, 1000)
        assert s.total_requests == 1
        assert s.avg_latency_ms == 100.0
        s.record_error()
        assert s.total_errors == 1

    def test_summary(self):
        from ccb.upstream_proxy import UpstreamProxy, ProxyConfig
        p = UpstreamProxy(ProxyConfig(upstream_url="https://api.example.com"))
        s = p.summary()
        assert s["running"] is False
        assert s["upstream"] == "https://api.example.com"


# ── Jobs ───────────────────────────────────────────────────────

class TestJobs:
    def test_create_job(self, tmp_path):
        from ccb.jobs import JobManager, JobStatus
        with patch("ccb.jobs._jobs_dir", return_value=tmp_path):
            m = JobManager()
            j = m.create_job("test-template", "do stuff", cwd="/tmp")
            assert j.status == JobStatus.QUEUED
            assert j.template == "test-template"
            assert (tmp_path / f"{j.id}.json").exists()

    def test_list_jobs(self, tmp_path):
        from ccb.jobs import JobManager, JobStatus
        with patch("ccb.jobs._jobs_dir", return_value=tmp_path):
            m = JobManager()
            m.create_job("t1", "p1")
            m.create_job("t2", "p2")
            assert len(m.list_jobs()) == 2
            assert len(m.list_jobs(status=JobStatus.COMPLETED)) == 0

    def test_cancel_job(self, tmp_path):
        from ccb.jobs import JobManager, JobStatus
        with patch("ccb.jobs._jobs_dir", return_value=tmp_path):
            m = JobManager()
            j = m.create_job("t", "p")
            assert m.cancel_job(j.id) is True
            assert m.get_job(j.id).status == JobStatus.CANCELLED

    def test_delete_job(self, tmp_path):
        from ccb.jobs import JobManager
        with patch("ccb.jobs._jobs_dir", return_value=tmp_path):
            m = JobManager()
            j = m.create_job("t", "p")
            jid = j.id
            assert m.delete_job(jid) is True
            assert m.get_job(jid) is None

    def test_summary(self, tmp_path):
        from ccb.jobs import JobManager
        with patch("ccb.jobs._jobs_dir", return_value=tmp_path):
            m = JobManager()
            m.create_job("t", "p")
            s = m.summary()
            assert s["total"] == 1

    def test_job_state_roundtrip(self):
        from ccb.jobs import JobState, JobStatus
        j = JobState(
            id="j1", template="t", template_file="", cwd=".",
            created_at="2025-01-01", updated_at="2025-01-01",
            status=JobStatus.QUEUED, prompt="do stuff",
        )
        d = j.to_dict()
        assert d["status"] == "queued"
        j2 = JobState.from_dict(d)
        assert j2.status == JobStatus.QUEUED


# ── Memdir ─────────────────────────────────────────────────────

class TestMemdir:
    def test_scan_empty(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        entries = m.scan()
        assert entries == []

    def test_add_and_scan(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        entry = m.add_memory("Test Memory", "Some content", tags=["test"])
        assert entry.title == "Test Memory"
        entries = m.scan()
        assert len(entries) == 1
        assert entries[0].title == "Test Memory"
        assert "test" in entries[0].tags

    def test_find_relevant(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        m.add_memory("Python Setup", "How to set up Python env", tags=["python"])
        m.add_memory("Git Workflow", "How to use git", tags=["git"])
        results = m.find_relevant("python setup")
        assert len(results) > 0
        assert results[0].title == "Python Setup"

    def test_update_memory(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        entry = m.add_memory("Test", "old content")
        assert m.update_memory(entry.path, "new content")
        assert m.get_entry(entry.path).content == "new content"

    def test_delete_memory(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        entry = m.add_memory("Test", "content")
        assert m.delete_memory(entry.path)
        assert m.get_entry(entry.path) is None

    def test_context_block(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        m.add_memory("Auth Flow", "OAuth token handling", tags=["auth"])
        block = m.build_context_block("auth token")
        assert "<project_memories>" in block
        assert "Auth Flow" in block

    def test_team_memdir(self, tmp_path):
        from ccb.memdir import TeamMemdir
        tm = TeamMemdir.__new__(TeamMemdir)
        tm.root = tmp_path / "team"
        tm._entries = {}
        tm.root.mkdir(parents=True)
        tm.add_memory("Shared Rule", "Team convention", source="team")
        assert tm.count == 1

    def test_summary(self, tmp_path):
        from ccb.memdir import Memdir
        m = Memdir(root=tmp_path / "mem")
        m.add_memory("Test", "content")
        s = m.summary()
        assert s["count"] == 1

    def test_extract_title(self):
        from ccb.memdir import _extract_title
        assert _extract_title("# My Title\nContent", "fallback") == "My Title"
        assert _extract_title("No heading", "fallback") == "fallback"

    def test_extract_tags(self):
        from ccb.memdir import _extract_tags
        assert _extract_tags("Tags: a, b, c") == ["a", "b", "c"]
        assert _extract_tags("No tags here") == []
