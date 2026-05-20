# -*- coding: utf-8 -*-
"""
Optimistic Swarm Soft-Interrupt Event Broker.
Coordinates concurrent sub-agents to avoid file modification conflicts.
"""
import os
import time
from pathlib import Path
from typing import Dict, Set, List, Any


class SoftInterruptBroker:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # file_path (normalized absolute) -> set of session_ids
        self._subscribers: Dict[str, Set[str]] = {}
        # session_id -> list of pending event dicts
        self._pending_events: Dict[str, List[Dict[str, Any]]] = {}

    def subscribe(self, file_path: str, session_id: str):
        if not file_path or not session_id:
            return
        try:
            norm_path = str(Path(file_path).resolve())
        except Exception:
            norm_path = os.path.abspath(file_path)

        if norm_path not in self._subscribers:
            self._subscribers[norm_path] = set()
        self._subscribers[norm_path].add(session_id)

    def unsubscribe_session(self, session_id: str):
        """Unsubscribe all files for a session when it cleans up."""
        for file_set in self._subscribers.values():
            file_set.discard(session_id)
        if session_id in self._pending_events:
            del self._pending_events[session_id]

    def get_pending_events(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve and clear pending events for a session."""
        events = self._pending_events.get(session_id, [])
        if session_id in self._pending_events:
            self._pending_events[session_id] = []
        return events

    async def publish_file_change(self, actor_id: str, file_path: str, diff_summary: str):
        """Publish a file modification event to all other subscribed sessions."""
        try:
            norm_path = str(Path(file_path).resolve())
        except Exception:
            norm_path = os.path.abspath(file_path)

        subscribers = self._subscribers.get(norm_path, set())
        for sid in subscribers:
            if sid == actor_id:
                continue
            event = {
                "type": "file_touch",
                "actor": actor_id,
                "file": norm_path,
                "diff": diff_summary,
                "timestamp": time.time()
            }
            if sid not in self._pending_events:
                self._pending_events[sid] = []
            self._pending_events[sid].append(event)
