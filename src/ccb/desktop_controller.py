"""Shared desktop runtime controller for GUI clients."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from ccb.query_engine import run_query
from ccb.session import Session
from ccb.session_repository import list_persisted_sessions, load_session, save_session


@dataclass(slots=True)
class DesktopTurnResult:
    """Structured result for a desktop chat turn."""

    response: str
    snapshot: "DesktopSnapshot"


@dataclass(slots=True)
class DesktopSnapshot:
    """Aggregated runtime state for the desktop UI."""

    session_id: str
    model: str
    provider: str
    account_name: str
    cwd: str
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
    last_input_tokens: int
    context_limit: int
    context_percent: int
    budget_tokens: int
    budget_used_tokens: int
    budget_percent: int
    estimated_cost: str
    last_turn_duration: str
    permission_mode: str
    workspace_rule_count: int
    event_total: int
    job_total: int
    mcp_server_count: int
    last_problem: str


class DesktopSessionController:
    """Manage desktop-visible runtime state independently of any GUI toolkit."""

    def __init__(
        self,
        *,
        model: str,
        cwd: str | None = None,
        session: Session | None = None,
        budget_tokens: int = 0,
    ) -> None:
        effective_cwd = cwd or os.getcwd()
        self._session = session or Session(cwd=effective_cwd, model=model)
        if not self._session.cwd:
            self._session.cwd = effective_cwd
        if model:
            self._session.model = model
        self._budget_tokens = budget_tokens
        self._lock = threading.Lock()

    @property
    def session(self) -> Session:
        return self._session

    @property
    def session_id(self) -> str:
        return self._session.id

    @property
    def cwd(self) -> str:
        return self._session.cwd

    @property
    def model(self) -> str:
        return self._session.model

    def set_model(self, model: str) -> None:
        if model:
            self._session.model = model

    def set_cwd(self, cwd: str) -> None:
        if cwd:
            self._session.cwd = cwd

    def set_budget_tokens(self, budget_tokens: int) -> None:
        self._budget_tokens = max(0, budget_tokens)

    def switch_session(self, session_id: str) -> bool:
        loaded = load_session(session_id)
        if not loaded:
            return False
        with self._lock:
            self._session = loaded
            if not self._session.cwd:
                self._session.cwd = os.getcwd()
        return True

    def new_session(self) -> Session:
        with self._lock:
            self._session = Session(cwd=self._session.cwd or os.getcwd(), model=self._session.model)
            return self._session

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        cwd = self._session.cwd or None
        return list_persisted_sessions(limit=limit, cwd=cwd)

    def get_transcript_messages(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for message in self._session.messages:
            if message.content:
                items.append((message.role.value, message.content))
        return items

    def build_snapshot(self) -> DesktopSnapshot:
        from ccb.config import get_active_account, get_provider
        from ccb.cost_tracker import format_cost, format_duration, get_cost_state
        from ccb.events import event_summary
        from ccb.jobs import get_job_manager
        from ccb.model_limits import get_context_limit
        from ccb.permissions import get_permission_state

        session = self._session
        account = get_active_account() or {}
        provider = get_provider()
        context_limit = get_context_limit(session.model)
        context_used = session.last_input_tokens
        context_percent = min(100, round((context_used * 100 / context_limit))) if context_limit else 0
        budget_used = session.total_input_tokens + session.total_output_tokens
        budget_percent = min(100, round((budget_used * 100 / self._budget_tokens))) if self._budget_tokens else 0

        cost = get_cost_state()
        events = event_summary(200)
        jobs = get_job_manager().summary()
        perms = get_permission_state(session.cwd)
        last_problem = events.get("last_problem") or {}
        payload = last_problem.get("payload") or {}
        problem_parts = [
            str(last_problem.get("kind", "")),
            str(last_problem.get("action", "")),
        ]
        if payload.get("error"):
            problem_parts.append(str(payload["error"]))
        problem = " · ".join(part for part in problem_parts if part)

        return DesktopSnapshot(
            session_id=session.id,
            model=session.model,
            provider=provider,
            account_name=str(account.get("_name", "none")),
            cwd=session.cwd,
            message_count=len(session.messages),
            total_input_tokens=session.total_input_tokens,
            total_output_tokens=session.total_output_tokens,
            last_input_tokens=context_used,
            context_limit=context_limit,
            context_percent=context_percent,
            budget_tokens=self._budget_tokens,
            budget_used_tokens=budget_used,
            budget_percent=budget_percent,
            estimated_cost=format_cost(cost.total_cost_usd),
            last_turn_duration=format_duration(cost.last_turn_duration_ms) if cost.last_turn_duration_ms else "-",
            permission_mode=perms["effective_mode"],
            workspace_rule_count=perms["workspace_rule_count"],
            event_total=int(events.get("total", 0)),
            job_total=int(jobs.get("total", 0)),
            mcp_server_count=0,
            last_problem=problem or "none",
        )

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        from ccb.events import recent_events

        return recent_events(limit)

    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        from ccb.jobs import get_job_manager

        jobs = get_job_manager().list_jobs()
        return [
            {
                "id": job.id,
                "status": job.status.value,
                "template": job.template,
                "cwd": job.cwd,
                "summary": job.summary,
                "error": job.error,
                "updated_at": job.updated_at,
            }
            for job in jobs[:limit]
        ]

    async def submit(self, prompt: str) -> DesktopTurnResult:
        """Append a user message, query the model, and persist the session."""
        text = prompt.strip()
        if not text:
            raise ValueError("Prompt cannot be empty")

        with self._lock:
            self._session.add_user_message(text)
            snapshot_messages = [message for message in self._session.messages]
            model = self._session.model or None
            cwd = self._session.cwd or os.getcwd()

        response = await run_query(
            text,
            model=model,
            cwd=cwd,
            messages=snapshot_messages,
            session=self._session,
        )

        with self._lock:
            self._session.add_assistant_message(response)
            save_session(self._session)
            snapshot = self.build_snapshot()
            return DesktopTurnResult(response=response, snapshot=snapshot)


def run_desktop_task(
    controller: DesktopSessionController,
    prompt: str,
    on_success: Callable[[DesktopTurnResult], None],
    on_error: Callable[[Exception], None],
) -> threading.Thread:
    """Run a desktop turn in a background thread."""
    import asyncio

    def _worker() -> None:
        try:
            result = asyncio.run(controller.submit(prompt))
        except Exception as exc:  # pragma: no cover - exercised via callback contract
            on_error(exc)
            return
        on_success(result)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
