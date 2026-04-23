"""Jobs — template job queue system.

Manages background jobs that run template-based prompts (e.g. scheduled
cron tasks, batch processing). Jobs persist to disk and support
queued → running → waiting → completed/error lifecycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class JobState:
    """State of a single template job."""
    id: str
    template: str
    template_file: str
    cwd: str
    created_at: str
    updated_at: str
    status: JobStatus
    prompt: str
    summary: str = ""
    error: str = ""
    result: str = ""
    retries: int = 0
    max_retries: int = 3
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "JobState":
        data["status"] = JobStatus(data.get("status", "queued"))
        return JobState(**{
            k: v for k, v in data.items()
            if k in JobState.__dataclass_fields__
        })


def _jobs_dir() -> Path:
    return Path.home() / ".claude" / "jobs"


class JobManager:
    """Manages template-based background jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._on_complete: Callable[[JobState], None] | None = None
        self._load_jobs()

    def _load_jobs(self) -> None:
        """Load persisted jobs from disk."""
        d = _jobs_dir()
        if not d.is_dir():
            return
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                job = JobState.from_dict(data)
                self._jobs[job.id] = job
                # Reset running jobs to queued (process restart)
                if job.status == JobStatus.RUNNING:
                    job.status = JobStatus.QUEUED
                    job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception as e:
                logger.debug("Failed to load job %s: %s", f, e)

    def _persist_job(self, job: JobState) -> None:
        """Save a job to disk."""
        d = _jobs_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{job.id}.json").write_text(
            json.dumps(job.to_dict(), indent=2)
        )

    def create_job(
        self,
        template: str,
        prompt: str,
        cwd: str = ".",
        template_file: str = "",
        priority: int = 0,
    ) -> JobState:
        """Create a new job."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        job = JobState(
            id=str(uuid.uuid4())[:8],
            template=template,
            template_file=template_file,
            cwd=cwd,
            created_at=now,
            updated_at=now,
            status=JobStatus.QUEUED,
            prompt=prompt,
            priority=priority,
        )
        self._jobs[job.id] = job
        self._persist_job(job)
        return job

    def get_job(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def list_jobs(
        self,
        status: JobStatus | None = None,
    ) -> list[JobState]:
        """List jobs, optionally filtered by status."""
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: (-j.priority, j.created_at))

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued or running job."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return False
        job.status = JobStatus.CANCELLED
        job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._persist_job(job)
        # Cancel running task
        task = self._running_tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        return True

    def delete_job(self, job_id: str) -> bool:
        """Delete a job (must not be running)."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status == JobStatus.RUNNING:
            return False
        self._jobs.pop(job_id, None)
        f = _jobs_dir() / f"{job_id}.json"
        if f.exists():
            f.unlink()
        return True

    async def execute_job(
        self,
        job_id: str,
        provider: Any = None,
        registry: Any = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> bool:
        """Execute a single job. Returns True on success."""
        job = self._jobs.get(job_id)
        if not job or job.status not in (JobStatus.QUEUED, JobStatus.ERROR):
            return False

        job.status = JobStatus.RUNNING
        job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._persist_job(job)

        if on_progress:
            on_progress(f"Running job {job.id}: {job.template}")

        try:
            if provider and registry:
                from ccb.api.base import Message, Role
                from ccb.loop import run_turn
                from ccb.session import Session

                session = Session(model=getattr(provider, "model", ""), cwd=job.cwd)
                session.add_user_message(job.prompt)

                await run_turn(provider, session, registry, "Execute this task.")

                # Collect result
                for msg in reversed(session.messages):
                    if msg.role == Role.ASSISTANT and msg.content:
                        job.result = msg.content[:2000]
                        break

            job.status = JobStatus.COMPLETED
            job.summary = job.result[:100] if job.result else "Done"
            job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._persist_job(job)

            if self._on_complete:
                self._on_complete(job)
            return True

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._persist_job(job)
            return False

        except Exception as e:
            job.retries += 1
            if job.retries >= job.max_retries:
                job.status = JobStatus.ERROR
                job.error = str(e)
            else:
                job.status = JobStatus.QUEUED  # retry
            job.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self._persist_job(job)
            return False

    async def process_queue(
        self,
        provider: Any = None,
        registry: Any = None,
        max_concurrent: int = 2,
    ) -> int:
        """Process queued jobs. Returns number of jobs started."""
        queued = self.list_jobs(status=JobStatus.QUEUED)
        running = len([j for j in self._jobs.values() if j.status == JobStatus.RUNNING])
        slots = max(0, max_concurrent - running)

        started = 0
        for job in queued[:slots]:
            task = asyncio.ensure_future(self.execute_job(job.id, provider, registry))
            self._running_tasks[job.id] = task
            started += 1

        return started

    def summary(self) -> dict[str, Any]:
        status_counts = {}
        for job in self._jobs.values():
            s = job.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        return {
            "total": len(self._jobs),
            "by_status": status_counts,
        }


# ── Module singleton ───────────────────────────────────────────

_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
