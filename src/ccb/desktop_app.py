"""Tk desktop app for ccb-py."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ccb.config import get_api_key, get_api_key_hint, get_model
from ccb.desktop_controller import (
    DesktopExecutionCancelled,
    DesktopSessionController,
    DesktopSnapshot,
    DesktopStreamEvent,
    drain_stream_queue,
    run_desktop_streaming_task,
)


class DesktopApp:
    """Desktop shell that visualizes and drives the CLI runtime."""

    def __init__(self, root: tk.Tk, *, model: str | None = None, cwd: str | None = None) -> None:
        self.root = root
        self.root.title("CCB Desktop")
        self.root.geometry("1480x920")
        self.root.minsize(1180, 760)

        self._controller = DesktopSessionController(model=model or get_model(), cwd=cwd or os.getcwd())
        self._busy = False
        self._stream_queue = None
        self._assistant_stream_marker: str | None = None
        self._pending_permission_id: str | None = None

        self.model_var = tk.StringVar(value=self._controller.model)
        self.cwd_var = tk.StringVar(value=self._controller.cwd)
        self.status_var = tk.StringVar(value="Desktop runtime ready")
        self.permission_var = tk.StringVar(value="")

        self._build_ui()
        self._apply_theme()
        self._render_history()
        self._refresh_all()
        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)
        self.root.bind("<Escape>", self._handle_escape)
        self.root.after(200, self._pump_stream_events)
        self.root.after(3000, self._poll_runtime)

    def _apply_theme(self) -> None:
        self.root.configure(bg="#eef2f6")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Root.TFrame", background="#eef2f6")
        style.configure("Panel.TFrame", background="#e4eaf1")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Hero.TLabel", background="#eef2f6", foreground="#0f172a", font=("Helvetica", 20, "bold"))
        style.configure("Meta.TLabel", background="#eef2f6", foreground="#475467", font=("Helvetica", 11))
        style.configure("CardLabel.TLabel", background="#ffffff", foreground="#475467", font=("Helvetica", 11))
        style.configure("CardValue.TLabel", background="#ffffff", foreground="#175cd3", font=("Helvetica", 15, "bold"))
        style.configure("Side.TLabel", background="#e4eaf1", foreground="#0f172a", font=("Helvetica", 11))
        style.configure("Action.TButton", padding=8)
        style.configure("Danger.TButton", padding=8)

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root_frame, style="Root.TFrame")
        header.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(header, text="CCB Desktop", style="Hero.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="同一套 ccb-py runtime 的桌面外壳：会话、模型、上下文、工具、事件、作业和权限状态都在这里可视化。",
            style="Meta.TLabel",
        ).pack(anchor=tk.W, pady=(4, 0))

        status_strip = ttk.Frame(root_frame, style="Root.TFrame")
        status_strip.pack(fill=tk.X, pady=(0, 12))
        self.status_cards: dict[str, ttk.Label] = {}
        for key, title in [
            ("session", "Session"),
            ("model", "Model"),
            ("context", "Context"),
            ("tokens", "Tokens"),
            ("cost", "Cost"),
            ("runtime", "Runtime"),
        ]:
            card = ttk.Frame(status_strip, style="Card.TFrame", padding=10)
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            ttk.Label(card, text=title, style="CardLabel.TLabel").pack(anchor=tk.W)
            value = ttk.Label(card, text="-", style="CardValue.TLabel")
            value.pack(anchor=tk.W, pady=(6, 0))
            self.status_cards[key] = value

        permission_bar = ttk.Frame(root_frame, style="Panel.TFrame", padding=10)
        permission_bar.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(permission_bar, textvariable=self.permission_var, style="Side.TLabel").pack(side=tk.LEFT)
        self.permission_allow = ttk.Button(permission_bar, text="Allow Once", style="Action.TButton", command=self._allow_permission)
        self.permission_deny = ttk.Button(permission_bar, text="Deny", style="Danger.TButton", command=self._deny_permission)
        self.permission_allow.pack(side=tk.RIGHT)
        self.permission_deny.pack(side=tk.RIGHT, padx=(0, 8))
        self._set_permission_bar(False)

        shell = ttk.Frame(root_frame, style="Root.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(shell, style="Panel.TFrame", padding=12)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))

        center = ttk.Frame(shell, style="Root.TFrame")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = ttk.Frame(shell, style="Panel.TFrame", padding=12)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        self._build_sidebar(left)
        self._build_center(center)
        self._build_inspector(right)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Sessions", style="Side.TLabel").pack(anchor=tk.W)
        session_actions = ttk.Frame(parent, style="Panel.TFrame")
        session_actions.pack(fill=tk.X, pady=(6, 8))
        ttk.Button(session_actions, text="Refresh", style="Action.TButton", command=self._refresh_sessions).pack(side=tk.LEFT)
        ttk.Button(session_actions, text="New", style="Action.TButton", command=self._new_session).pack(side=tk.LEFT, padx=(8, 0))

        self.session_list = tk.Listbox(
            parent,
            width=28,
            height=24,
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            highlightthickness=0,
            selectbackground="#175cd3",
            selectforeground="#ffffff",
        )
        self.session_list.pack(fill=tk.BOTH, expand=True)
        self.session_list.bind("<<ListboxSelect>>", self._on_session_selected)
        self._session_entries: list[dict[str, str | int | float]] = []

    def _build_center(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        controls.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(controls, text="Model", style="Meta.TLabel").grid(row=0, column=0, sticky="w")
        self.model_entry = ttk.Entry(controls, textvariable=self.model_var, width=30)
        self.model_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.model_entry.bind("<Return>", lambda _event: self._apply_model())

        ttk.Label(controls, text="Workspace", style="Meta.TLabel").grid(row=0, column=1, sticky="w")
        self.cwd_entry = ttk.Entry(controls, textvariable=self.cwd_var)
        self.cwd_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        self.cwd_entry.bind("<Return>", lambda _event: self._apply_cwd())

        self.apply_button = ttk.Button(controls, text="Apply", style="Action.TButton", command=self._apply_settings)
        self.apply_button.grid(row=1, column=2, padx=(0, 8))
        self.browse_button = ttk.Button(controls, text="Browse", style="Action.TButton", command=self._pick_directory)
        self.browse_button.grid(row=1, column=3)
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=3)

        transcript_frame = ttk.Frame(parent, style="Root.TFrame")
        transcript_frame.pack(fill=tk.BOTH, expand=True)
        self.transcript = scrolledtext.ScrolledText(
            transcript_frame,
            wrap=tk.WORD,
            font=("Menlo", 13),
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief=tk.FLAT,
            padx=14,
            pady=14,
        )
        self.transcript.pack(fill=tk.BOTH, expand=True)
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.tag_configure("user", foreground="#0b4a6f", font=("Menlo", 13, "bold"))
        self.transcript.tag_configure("assistant", foreground="#175cd3", font=("Menlo", 13, "bold"))
        self.transcript.tag_configure("meta", foreground="#667085", font=("Menlo", 12))
        self.transcript.tag_configure("stream", foreground="#0f172a", font=("Menlo", 13))
        self.transcript.tag_configure("tool", foreground="#344054", font=("Menlo", 12))
        self.transcript.tag_configure("toolerr", foreground="#b42318", font=("Menlo", 12))

        composer = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        composer.pack(fill=tk.BOTH, pady=(12, 8))
        ttk.Label(composer, text="Prompt", style="Meta.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.prompt = tk.Text(
            composer,
            height=7,
            wrap=tk.WORD,
            font=("Menlo", 13),
            bg="#ffffff",
            fg="#0f172a",
            insertbackground="#0f172a",
            relief=tk.FLAT,
            padx=12,
            pady=12,
        )
        self.prompt.pack(fill=tk.BOTH, expand=True)
        self.prompt.bind("<Command-Return>", self._submit_shortcut)
        self.prompt.bind("<Control-Return>", self._submit_shortcut)
        self.prompt.bind("<Command-l>", self._clear_prompt_shortcut)
        self.prompt.bind("<Control-l>", self._clear_prompt_shortcut)
        self.prompt.bind("<Escape>", self._handle_escape)

        actions = ttk.Frame(parent, style="Root.TFrame")
        actions.pack(fill=tk.X)
        ttk.Label(actions, textvariable=self.status_var, style="Meta.TLabel").pack(side=tk.LEFT)
        self.send_button = ttk.Button(actions, text="Send", style="Action.TButton", command=self._submit_prompt)
        self.send_button.pack(side=tk.RIGHT)
        self.stop_button = ttk.Button(actions, text="Stop", style="Danger.TButton", command=self._stop_generation)
        self.stop_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.stop_button.configure(state=tk.DISABLED)

    def _build_inspector(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Inspector", style="Side.TLabel").pack(anchor=tk.W)

        quick = ttk.Frame(parent, style="Panel.TFrame")
        quick.pack(fill=tk.X, pady=(6, 12))
        self.runtime_meta = tk.StringVar(value="-")
        self.session_meta = tk.StringVar(value="-")
        ttk.Label(quick, textvariable=self.runtime_meta, style="Side.TLabel", justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Label(quick, textvariable=self.session_meta, style="Side.TLabel", justify=tk.LEFT).pack(anchor=tk.W, pady=(8, 0))

        ttk.Label(parent, text="Tool Timeline", style="Side.TLabel").pack(anchor=tk.W, pady=(0, 6))
        self.tools_box = scrolledtext.ScrolledText(
            parent,
            width=40,
            height=16,
            wrap=tk.WORD,
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.tools_box.pack(fill=tk.BOTH, expand=True)
        self.tools_box.configure(state=tk.DISABLED)

        ttk.Label(parent, text="MCP Servers", style="Side.TLabel").pack(anchor=tk.W, pady=(12, 6))
        self.mcp_box = scrolledtext.ScrolledText(
            parent,
            width=40,
            height=8,
            wrap=tk.WORD,
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.mcp_box.pack(fill=tk.BOTH, expand=True)
        self.mcp_box.configure(state=tk.DISABLED)

        ttk.Label(parent, text="Agent Activity", style="Side.TLabel").pack(anchor=tk.W, pady=(12, 6))
        self.agent_box = scrolledtext.ScrolledText(
            parent,
            width=40,
            height=8,
            wrap=tk.WORD,
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.agent_box.pack(fill=tk.BOTH, expand=True)
        self.agent_box.configure(state=tk.DISABLED)

        ttk.Label(parent, text="Recent Events", style="Side.TLabel").pack(anchor=tk.W, pady=(12, 6))
        self.events_box = scrolledtext.ScrolledText(
            parent,
            width=40,
            height=10,
            wrap=tk.WORD,
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.events_box.pack(fill=tk.BOTH, expand=True)
        self.events_box.configure(state=tk.DISABLED)

        ttk.Label(parent, text="Background Jobs", style="Side.TLabel").pack(anchor=tk.W, pady=(12, 6))
        self.jobs_box = scrolledtext.ScrolledText(
            parent,
            width=40,
            height=8,
            wrap=tk.WORD,
            font=("Menlo", 11),
            bg="#ffffff",
            fg="#0f172a",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.jobs_box.pack(fill=tk.BOTH, expand=True)
        self.jobs_box.configure(state=tk.DISABLED)

    def _append_block(self, role: str, content: str) -> None:
        title = "You" if role == "user" else "CCB"
        tag = "user" if role == "user" else "assistant"
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"{title}\n", tag)
        self.transcript.insert(tk.END, f"{content.strip()}\n\n")
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.see(tk.END)

    def _append_tool_line(self, text: str, *, is_error: bool = False) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"{text}\n", "toolerr" if is_error else "tool")
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.see(tk.END)

    def _start_stream_block(self) -> None:
        marker = f"stream_{int(time.time() * 1000)}"
        self._assistant_stream_marker = marker
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, "CCB\n", "assistant")
        start = self.transcript.index(tk.END)
        self.transcript.insert(tk.END, "\n\n", "stream")
        end = self.transcript.index(f"{start} lineend")
        self.transcript.tag_add(marker, start, end)
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.see(tk.END)

    def _append_stream_text(self, text: str) -> None:
        if not self._assistant_stream_marker:
            self._start_stream_block()
        marker = self._assistant_stream_marker
        ranges = self.transcript.tag_ranges(marker)
        if len(ranges) != 2:
            return
        start, end = ranges
        current = self.transcript.get(start, end)
        updated = current + text
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.delete(start, end)
        self.transcript.insert(start, updated, "stream")
        new_end = self.transcript.index(f"{start} + {len(updated)} chars")
        self.transcript.tag_remove(marker, "1.0", tk.END)
        self.transcript.tag_add(marker, start, new_end)
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.see(tk.END)

    def _render_history(self) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.delete("1.0", tk.END)
        self.transcript.insert(
            tk.END,
            "Desktop shell 已连接 ccb-py runtime。\n这里展示的不是假状态，而是现有 session / cost / tools / events / jobs / permissions 的聚合视图。\n\n",
            "meta",
        )
        self.transcript.configure(state=tk.DISABLED)
        for role, content in self._controller.get_transcript_messages():
            self._append_block(role, content)

    def _refresh_status_cards(self, snapshot: DesktopSnapshot) -> None:
        self.status_cards["session"].configure(text=f"{snapshot.session_id[:8]} · {snapshot.message_count} msgs")
        self.status_cards["model"].configure(text=f"{snapshot.provider} · {snapshot.model or '-'}")
        self.status_cards["context"].configure(text=f"{snapshot.context_percent}% · {snapshot.last_input_tokens:,}/{snapshot.context_limit:,}")
        self.status_cards["tokens"].configure(text=f"{snapshot.total_input_tokens:,} in · {snapshot.total_output_tokens:,} out")
        self.status_cards["cost"].configure(text=f"{snapshot.estimated_cost} · {snapshot.last_turn_duration}")
        self.status_cards["runtime"].configure(text=f"{snapshot.permission_mode} · {snapshot.event_total} events · {snapshot.job_total} jobs")

    def _refresh_sidebar(self) -> None:
        self._session_entries = self._controller.list_sessions(limit=50)
        self.session_list.delete(0, tk.END)
        current_id = self._controller.session_id
        active_index = None
        for idx, item in enumerate(self._session_entries):
            updated = item.get("updated_at", 0)
            ts = time.strftime("%m/%d %H:%M", time.localtime(updated)) if updated else "--/-- --:--"
            model = str(item.get("model", "") or "-")
            msg_count = int(item.get("messages", 0) or 0)
            label = f"{str(item['id'])[:8]}  {msg_count:>3}m  {self._truncate(model, 18)}  {ts}"
            self.session_list.insert(tk.END, label)
            if item["id"] == current_id:
                active_index = idx
        if active_index is not None:
            self.session_list.selection_clear(0, tk.END)
            self.session_list.selection_set(active_index)

    def _refresh_tool_timeline(self) -> None:
        items = self._controller.get_tool_timeline(limit=32)
        self.tools_box.configure(state=tk.NORMAL)
        self.tools_box.delete("1.0", tk.END)
        if not items:
            self.tools_box.insert(tk.END, "No tool activity in this session.\n")
        for item in items:
            if item["kind"] == "call":
                summary = self._format_input(item["input"])
                self.tools_box.insert(tk.END, f"CALL   {item['name']}\n{summary or '-'}\n\n")
            else:
                prefix = "ERROR " if item.get("is_error") else "RESULT"
                output = self._format_output_preview(item.get("output", ""))
                self.tools_box.insert(tk.END, f"{prefix} {item['name']}\n{output}\n\n")
        self.tools_box.configure(state=tk.DISABLED)

    def _refresh_inspector(self, snapshot: DesktopSnapshot) -> None:
        if snapshot.budget_tokens:
            budget_line = f"Budget: {snapshot.budget_used_tokens:,}/{snapshot.budget_tokens:,} ({snapshot.budget_percent}%)"
        else:
            budget_line = "Budget: not set"
        self.runtime_meta.set(
            f"Account: {snapshot.account_name}\n"
            f"Permissions: {snapshot.permission_mode} ({snapshot.workspace_rule_count} workspace rules)\n"
            f"{budget_line}"
        )
        self.session_meta.set(
            f"Workspace: {self._short_path(snapshot.cwd)}\n"
            f"Last problem: {snapshot.last_problem}\n"
            f"MCP: {snapshot.mcp_server_count} servers / {snapshot.mcp_tool_count} tools · Agents: {snapshot.active_agent_count}"
        )

        self._refresh_tool_timeline()

        mcp_servers = self._controller.mcp_servers()
        self.mcp_box.configure(state=tk.NORMAL)
        self.mcp_box.delete("1.0", tk.END)
        if not mcp_servers:
            self.mcp_box.insert(tk.END, "No MCP servers connected.\n")
        else:
            for server in mcp_servers:
                line = f"{server['name']}  {'connected' if server['connected'] else 'disconnected'}  {server['type']}\n"
                line += f"tools: {server['tool_count']}"
                if server["tools"]:
                    line += f"\n{', '.join(server['tools'][:6])}"
                self.mcp_box.insert(tk.END, line + "\n\n")
        self.mcp_box.configure(state=tk.DISABLED)

        agents = self._controller.agent_activity()
        self.agent_box.configure(state=tk.NORMAL)
        self.agent_box.delete("1.0", tk.END)
        if not agents:
            self.agent_box.insert(tk.END, "No active subagents.\n")
        else:
            for agent in agents:
                status = "done" if agent["done"] else "running"
                line = f"{agent['label']}  {status}  tools={agent['tool_count']}\n{agent['task'][:120]}"
                if agent["last_tool"]:
                    line += f"\nlast: {agent['last_tool']}"
                self.agent_box.insert(tk.END, line + "\n\n")
        self.agent_box.configure(state=tk.DISABLED)

        events = self._controller.recent_events(limit=16)
        self.events_box.configure(state=tk.NORMAL)
        self.events_box.delete("1.0", tk.END)
        for event in events:
            payload = event.get("payload") or {}
            detail = ", ".join(f"{k}={str(v)[:48]}" for k, v in list(payload.items())[:2])
            line = f"{event.get('time','')}  {str(event.get('level','info')).upper()}  {event.get('kind','')}.{event.get('action','')}"
            if detail:
                line += f"\n{detail}"
            self.events_box.insert(tk.END, line + "\n\n")
        self.events_box.configure(state=tk.DISABLED)

        jobs = self._controller.recent_jobs(limit=16)
        self.jobs_box.configure(state=tk.NORMAL)
        self.jobs_box.delete("1.0", tk.END)
        for job in jobs:
            line = f"{job['id']}  {job['status']}  {job['template']}\n{self._short_path(str(job['cwd']))}"
            summary = job.get("summary") or job.get("error") or ""
            if summary:
                line += f"\n{self._truncate(str(summary), 160)}"
            self.jobs_box.insert(tk.END, line + "\n\n")
        self.jobs_box.configure(state=tk.DISABLED)

    def _refresh_all(self) -> None:
        snapshot = self._controller.build_snapshot()
        self.model_var.set(snapshot.model)
        self.cwd_var.set(snapshot.cwd)
        self._refresh_status_cards(snapshot)
        self._refresh_sidebar()
        self._refresh_inspector(snapshot)
        if not self._busy:
            self.status_var.set(
                f"Session {snapshot.session_id[:8]} ready · {snapshot.context_percent}% context · {snapshot.event_total} events"
            )

    def _pump_stream_events(self) -> None:
        if self._stream_queue is not None:
            for event in drain_stream_queue(self._stream_queue):
                self._handle_stream_event(event)
        self.root.after(200, self._pump_stream_events)

    def _poll_runtime(self) -> None:
        if not self._busy:
            self._refresh_all()
        self.root.after(3000, self._poll_runtime)

    def _apply_model(self) -> None:
        self._controller.set_model(self.model_var.get().strip())
        self._refresh_all()

    def _apply_cwd(self) -> None:
        cwd = self.cwd_var.get().strip()
        if not cwd:
            return
        path = Path(cwd).expanduser()
        if not path.exists() or not path.is_dir():
            messagebox.showerror("Invalid workspace", f"Directory does not exist:\n{path}")
            return
        self.cwd_var.set(str(path))
        self._controller.set_cwd(str(path))
        self._refresh_all()

    def _apply_settings(self) -> None:
        self._apply_model()
        self._apply_cwd()

    def _pick_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.cwd_var.get() or os.getcwd())
        if not selected:
            return
        self.cwd_var.set(selected)
        self._apply_cwd()

    def _new_session(self) -> None:
        self._controller.new_session()
        self._render_history()
        self._refresh_all()

    def _on_session_selected(self, _event: tk.Event[tk.Listbox]) -> None:
        if not self.session_list.curselection():
            return
        idx = int(self.session_list.curselection()[0])
        if idx >= len(self._session_entries):
            return
        session_id = str(self._session_entries[idx]["id"])
        if session_id == self._controller.session_id:
            return
        if self._controller.switch_session(session_id):
            self._render_history()
            self._refresh_all()

    def _set_permission_bar(self, visible: bool, message: str = "") -> None:
        self.permission_var.set(message)
        state = tk.NORMAL if visible else tk.DISABLED
        self.permission_allow.configure(state=state)
        self.permission_deny.configure(state=state)

    def _allow_permission(self) -> None:
        if self._pending_permission_id:
            self._controller.approve_permission(self._pending_permission_id, "allow_once")
            self._pending_permission_id = None
            self._set_permission_bar(False)

    def _deny_permission(self) -> None:
        if self._pending_permission_id:
            self._controller.approve_permission(self._pending_permission_id, "deny_once")
            self._pending_permission_id = None
            self._set_permission_bar(False)

    def _stop_generation(self) -> None:
        if self._controller.cancel_active_turn():
            self.status_var.set("Stopping…")

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = busy
        self.send_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.session_list.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.model_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.cwd_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.apply_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.browse_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.prompt.configure(state=tk.DISABLED)
        else:
            self.prompt.configure(state=tk.NORMAL)
            self.prompt.focus_set()
        if message:
            self.status_var.set(message)
        elif not busy:
            self._refresh_all()

    def _submit_shortcut(self, _event: tk.Event[tk.Text]) -> str:
        self._submit_prompt()
        return "break"

    def _clear_prompt_shortcut(self, _event: tk.Event[tk.Text]) -> str:
        if not self._busy:
            self.prompt.delete("1.0", tk.END)
            self.status_var.set("Prompt cleared")
        return "break"

    def _submit_prompt(self) -> None:
        if self._busy:
            return
        prompt = self.prompt.get("1.0", tk.END).strip()
        if not prompt:
            return
        self._apply_settings()
        self.prompt.delete("1.0", tk.END)
        self._append_block("user", prompt)
        self._set_busy(True, "CCB is thinking…")
        self._start_stream_block()
        _thread, self._stream_queue = run_desktop_streaming_task(
            self._controller,
            prompt,
            on_error=lambda exc: self.root.after(0, self._handle_error, exc),
        )

    def _handle_stream_event(self, event: DesktopStreamEvent) -> None:
        if event.type == "text":
            self._append_stream_text(event.text)
            return
        if event.type == "tool_call":
            self._append_tool_line(f"⏺ {event.tool_name}({self._format_input(event.tool_input)})")
            self._refresh_tool_timeline()
            return
        if event.type == "tool_result":
            preview = (event.tool_output or "").strip().splitlines()[0] if event.tool_output else ""
            preview = preview[:180]
            self._append_tool_line(f"│ {event.tool_name}: {preview}", is_error=event.is_error)
            self._refresh_tool_timeline()
            return
        if event.type == "permission_request":
            self._pending_permission_id = event.permission_id
            self._set_permission_bar(True, f"Permission needed: {event.permission_message}")
            self._append_tool_line(f"? Permission needed for {event.tool_name}: {event.permission_message}", is_error=True)
            return
        if event.type == "done" and event.snapshot is not None:
            self._stream_queue = None
            self._pending_permission_id = None
            self._set_permission_bar(False)
            self._refresh_status_cards(event.snapshot)
            self._refresh_sidebar()
            self._refresh_inspector(event.snapshot)
            self._assistant_stream_marker = None
            self._set_busy(False)
            return

    def _handle_error(self, exc: Exception) -> None:
        self._stream_queue = None
        self._pending_permission_id = None
        self._set_permission_bar(False)
        if isinstance(exc, DesktopExecutionCancelled):
            self._assistant_stream_marker = None
            self._append_tool_line("Stopped", is_error=False)
            self._set_busy(False, "Generation stopped")
            return
        if self._assistant_stream_marker:
            self._append_stream_text(f"\n[error] {exc}")
        self._assistant_stream_marker = None
        self._set_busy(False, f"Error: {exc}")
        messagebox.showerror("CCB Desktop", str(exc))

    def _refresh_sessions(self) -> None:
        self._refresh_all()

    def _shutdown(self) -> None:
        async def _close_controller() -> None:
            await self._controller.close()

        try:
            asyncio.run(_close_controller())
        except Exception:
            pass
        self.root.destroy()

    def _handle_escape(self, _event: tk.Event[tk.Misc]) -> str:
        if self._busy:
            self._stop_generation()
            return "break"
        return ""

    @staticmethod
    def _format_input(input_data: dict[str, object] | None) -> str:
        if not input_data:
            return ""
        return ", ".join(f"{k}={str(v)[:36]}" for k, v in list(input_data.items())[:3])

    @staticmethod
    def _format_output_preview(output: object) -> str:
        text = str(output or "").strip()
        if not text:
            return "-"
        first_line = text.splitlines()[0]
        return DesktopApp._truncate(first_line, 180)

    @staticmethod
    def _short_path(path: str) -> str:
        expanded_home = os.path.expanduser("~")
        if path.startswith(expanded_home):
            return "~" + path[len(expanded_home):]
        return path

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"


def launch_desktop_app(*, model: str | None = None, cwd: str | None = None) -> None:
    """Start the Tk desktop app."""
    if not get_api_key():
        hint = get_api_key_hint()
        raise RuntimeError(f"No API key found. {hint}".strip())

    smoke = _probe_tk_window_support()
    if smoke is not None:
        raise RuntimeError(smoke)

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - depends on runtime display availability
        raise RuntimeError(
            "Tk desktop UI is unavailable in this environment. Install Tk support or use a local desktop session."
        ) from exc

    app = DesktopApp(root, model=model, cwd=cwd)
    app.root.mainloop()


def _probe_tk_window_support() -> str | None:
    """Probe Tk window creation in a child process to avoid hard-crashing the caller."""
    probe = (
        "import sys\n"
        "import tkinter as tk\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.destroy()\n"
        "sys.stdout.write('ok')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "Desktop startup timed out while probing Tk. Your local Tk runtime may be unstable."
    except Exception as exc:
        return f"Desktop startup probe failed before launch: {exc}"

    if result.returncode == 0 and result.stdout.strip() == "ok":
        return None

    stderr = (result.stderr or "").strip()
    tk_version = getattr(tk, "TkVersion", "unknown")
    tcl_version = getattr(tk, "TclVersion", "unknown")
    detail = stderr.splitlines()[-1] if stderr else f"exit code {result.returncode}"
    detail = detail[:280]

    if result.returncode < 0:
        signal_num = -result.returncode
        return (
            "Desktop UI launch aborted inside the Tk runtime before a window could open. "
            f"Detected Tk/Tcl {tk_version}/{tcl_version} under {sys.executable}. "
            f"The child process exited via signal {signal_num}. "
            "On your machine this usually means the current Homebrew Tk 9.x stack is not stable for tkinter. "
            "Use a Python build linked against Tk 8.6 / python.org Python, or swap to a non-Tk desktop stack."
        )

    return (
        "Desktop UI launch preflight failed before opening a window. "
        f"Detected Tk/Tcl {tk_version}/{tcl_version} under {sys.executable}. "
        f"Probe detail: {detail}. "
        "This points to a local Tk runtime issue rather than a CCB desktop code path failure."
    )
