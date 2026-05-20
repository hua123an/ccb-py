"""Slash command system - all built-in commands.

This package is split into logical groups:
- _account: /account, /login, /logout, /config
- _git: /diff, /branch, /commit, /undo, /redo
- _model: /model, /effort, /fast, /thinking, /prefill, /cost, /budget, /context, /status, /files, /memory, /compact
- _session: /sessions, /resume, /continue, /fork, /session, /rename, /snapshot, /restore, /rewind, /summary, /share, /export, /copy, /stats, /history, /tag, /add-dir, /passes, /thinkback, /ctx_viz, /buddy
- _general: all remaining general-purpose commands and helpers

The main dispatcher is ``handle_command`` in the parent ``ccb.commands`` module.
"""
from __future__ import annotations
