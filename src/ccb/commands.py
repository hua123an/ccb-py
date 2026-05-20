"""Slash command system - all built-in commands.

This module is a thin facade that dispatches to command handlers in submodules.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info
from ccb.session_repository import save_session
from ccb.session_runtime import emit_runtime_warning

if TYPE_CHECKING:
    from ccb.api.base import Provider
    from ccb.mcp.client import MCPManager
    from ccb.session import Session
    from ccb.tools.base import ToolRegistry

# Import all command modules for side-effects (they register handlers)
from ccb.cmd_handlers import _account
from ccb.cmd_handlers import _general
from ccb.cmd_handlers import _git
from ccb.cmd_handlers import _model
from ccb.cmd_handlers import _session


def _persist_session(session: Session, action: str, cwd: str) -> None:
    try:
        save_session(session)
    except Exception as e:
        emit_runtime_warning(
            action,
            session_id=session.id,
            cwd=session.cwd or cwd,
            payload={"error": str(e)},
        )


async def _run_prompt_command(
    *,
    session: Session,
    provider: Provider,
    registry: ToolRegistry,
    cwd: str,
    prompt: str,
    persist_action: str,
    mcp_manager: MCPManager | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    from ccb.loop import run_turn
    from ccb.prompts import get_system_prompt

    session.add_user_message(prompt)
    await run_turn(
        provider,
        session,
        registry,
        get_system_prompt(cwd),
        mcp_manager=mcp_manager,
        state=state,
    )
    _persist_session(session, persist_action, cwd)


async def handle_command(
    cmd: str,
    session: Session,
    provider: Provider,
    registry: ToolRegistry,
    cwd: str,
    mcp_manager: MCPManager | None = None,
    state: dict[str, Any] | None = None,
) -> str | bool:
    """Handle slash commands. Returns 'exit' to quit, True if handled, False if not."""
    state = state or {}
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    # ── Exit ──
    if command in ("/exit", "/quit", "/q"):
        return "exit"

    # ── Help ──
    if command == "/help":
        _general._print_help()
        return True

    # ── Clear ──
    if command == "/clear":
        session.messages.clear()
        print_info("Conversation cleared.")
        return True

    # ── Compact ──
    if command == "/compact":
        await _model._compact(session, provider, args)
        return True

    # ── Model ──
    if command == "/model":
        return await _model.cmd_model(args, session, provider)

    # ── Account ──
    if command == "/account":
        new_provider = await _account.cmd_account(args, provider, session)
        if new_provider is not None:
            state["_new_provider"] = new_provider
        return True

    # ── Cost ──
    if command == "/cost":
        return _model.cmd_cost(session)

    # ── Sessions / Resume / Continue ──
    if command in ("/sessions", "/resume", "/continue"):
        return await _session.cmd_sessions(command, session, provider, args, state)

    # ── Fork ──
    if command == "/fork":
        return await _session.cmd_fork(session, args)

    # ── Config ──
    if command == "/config":
        return _account.cmd_config()

    # ── Theme ──
    if command == "/theme":
        _general._change_theme(args)
        return True

    # ── MCP ──
    if command == "/mcp":
        return await _cmd_mcp(args, mcp_manager)

    # ── Context ──
    if command == "/context":
        return _model.cmd_context(session, provider)

    # ── Status ──
    if command == "/status":
        return _model.cmd_status(session, provider, mcp_manager)

    # ── Memory ──
    if command == "/memory":
        return await _model.cmd_memory(args, session, provider, cwd)

    # ── Remember ──
    if command == "/remember":
        return await _cmd_remember(args, session, provider, cwd)

    # ── Forget ──
    if command == "/forget":
        return _cmd_forget(args, session, cwd)

    # ── Schedule / Cron ──
    if command in ("/schedule", "/cron"):
        from ccb.feature_flags import is_feature_enabled
        if not is_feature_enabled("scheduled_tasks", True):
            print_error("Scheduled tasks are disabled by feature flag 'scheduled_tasks'.")
            return True
        await _general._schedule(args, cwd)
        return True

    # ── Diff ──
    if command == "/diff":
        return await _git.cmd_diff(args, cwd)

    # ── Vim ──
    if command == "/vim":
        current = state.get("vim_mode", False)
        state["vim_mode"] = not current
        print_info(f"Vim mode: {'ON' if state['vim_mode'] else 'OFF'}")
        return True

    # ── Doctor ──
    if command == "/doctor":
        await _general._doctor(cwd, registry, mcp_manager)
        return True

    if command == "/events":
        return await _cmd_events(args)

    # ── Permissions ──
    if command == "/permissions":
        return await _cmd_permissions(args)

    # ── Hooks ──
    if command == "/hooks":
        from ccb.hooks import load_hooks
        hooks = load_hooks(cwd)
        total = sum(len(v) for v in hooks.values())
        if total == 0:
            print_info("No hooks configured. Create .claude/hooks.json")
        else:
            for event, entries in hooks.items():
                if entries:
                    console.print(f"  [bold]{event}[/bold]: {len(entries)} hook(s)")
                    for e in entries:
                        console.print(f"    {e.get('command', '?')}")
        return True

    # ── Branch ──
    if command == "/branch":
        return await _git.cmd_branch(args, cwd)

    # ── Rename ──
    if command == "/rename":
        return _session.cmd_rename(session, args)

    # ── Plan ──
    if command == "/plan":
        from ccb.tools.plan import is_plan_mode, get_current_plan
        if is_plan_mode():
            plan = get_current_plan()
            console.print("[bold]Current plan:[/bold]")
            for i, step in enumerate(plan, 1):
                console.print(f"  {i}. {step}")
        else:
            console.print("  Not in plan mode. The model can enter plan mode via tools.")
        return True

    # ── Budget ──
    if command == "/budget":
        return _model.cmd_budget(args, state, session)

    # ── Version ──
    if command == "/version":
        from ccb import __version__
        console.print(f"  CCB v{__version__}")
        return True

    # ── Init ──
    if command == "/init":
        await _cmd_init(cwd, args)
        return True

    # ── Undo / Redo ──
    if command == "/undo":
        return await _git.cmd_undo(cwd)
    if command == "/redo":
        return await _git.cmd_redo(cwd)

    # ── Copy ──
    if command == "/copy":
        return _session.cmd_copy(session)

    # ── Export ──
    if command == "/export":
        return _session.cmd_export(session, args)

    # ── Stats ──
    if command == "/stats":
        return _session.cmd_stats(session)

    # ── Files ──
    if command == "/files":
        return _model.cmd_files(session)

    # ── Image ──
    if command == "/image":
        return await _cmd_image(args, session, state)

    # ── File (upload) ──
    if command == "/file":
        return await _cmd_file(args, session, state)

    # ── Color ──
    if command == "/color":
        return await _cmd_color(args, state)

    # ── Effort ──
    if command == "/effort":
        return await _model.cmd_effort(args, state)

    # ── Thinking ──
    if command == "/thinking":
        return _model.cmd_thinking(args, state, provider)

    # ── Prefill ──
    if command == "/prefill":
        return _model.cmd_prefill(args, state)

    # ── Fast ──
    if command == "/fast":
        return _model.cmd_fast(state)

    # ── Usage ──
    if command == "/usage":
        return _cmd_usage(session, state)

    # ── Tag ──
    if command == "/tag":
        return _session.cmd_tag(args, state)

    # ── Feedback / BTW ──
    if command in ("/feedback", "/btw"):
        return await _cmd_feedback(command, args, session)

    # ── Login / Logout ──
    if command == "/login":
        return await _account.cmd_login(args)
    if command == "/logout":
        return await _account.cmd_logout()

    # ── Session ──
    if command == "/session":
        return _session.cmd_session(session)

    # ── Add-dir ──
    if command == "/add-dir":
        return _session.cmd_add_dir(args, state)

    # ── Agents ──
    if command == "/agents":
        return await _cmd_agents(args, state, provider, cwd)

    # ── Peers / Pipes ──
    if command in ("/peers", "/pipes"):
        return await _cmd_peers_pipes(command, args, state)

    # ── Tasks ──
    if command == "/tasks":
        return await _cmd_tasks(session)

    # ── Snapshot / Restore ──
    if command == "/snapshot":
        return await _session.cmd_snapshot(session, args, cwd)
    if command == "/restore":
        return await _session.cmd_restore(session, args, cwd)

    # ── Rewind ──
    if command == "/rewind":
        return await _session.cmd_rewind(session, args)

    # ── Summary ──
    if command == "/summary":
        return _session.cmd_summary(session)

    # ── Stickers ──
    if command == "/stickers":
        print_info("Order Claude Code stickers at https://store.anthropic.com")
        return True

    # ── Upgrade ──
    if command == "/upgrade":
        print_info("Upgrade to Max for higher rate limits at https://claude.ai/upgrade")
        return True

    # ── Release Notes ──
    if command == "/release-notes":
        from ccb import __version__
        console.print(f"  CCB v{__version__}")
        console.print("  See https://github.com/anthropics/claude-code for changelog")
        return True

    # ── Privacy Settings ──
    if command == "/privacy-settings":
        return await _cmd_privacy_settings(args)

    # ── Sandbox ──
    if command == "/sandbox":
        return await _cmd_sandbox(args, state, cwd)

    # ── Output Style ──
    if command == "/output-style":
        return await _cmd_output_style(args, state)

    # ── Keybindings ──
    if command == "/keybindings":
        return _cmd_keybindings(state)

    # ── Desktop / Mobile / Voice ──
    if command == "/desktop":
        print_info("Launch the local desktop app with: ccb-py desktop")
        return True
    if command == "/mobile":
        print_info("Download Claude mobile: https://claude.ai/mobile")
        return True
    if command == "/voice":
        return await _cmd_voice(args, session, provider, registry, cwd, mcp_manager)

    # ── Share ──
    if command == "/share":
        return _session.cmd_share(session)

    # ── Plugin ──
    if command == "/plugin":
        await _general._handle_plugin_command(args)
        return True
    if command == "/reload-plugins":
        plugin_dir = Path.home() / ".ccb" / "plugins"
        count = len(list(plugin_dir.iterdir())) if plugin_dir.exists() else 0
        print_info(f"Reloaded {count} plugin(s).")
        return True

    # ── Workflows ──
    if command == "/workflows":
        return await _cmd_skill_entry(
            command,
            args,
            session,
            provider,
            registry,
            cwd,
            mcp_manager,
            kind="workflow",
        )

    # ── History ──
    if command == "/history":
        return _session.cmd_history(session)

    # ── Passes ──
    if command == "/passes":
        return _session.cmd_passes(args, state)

    # ── Thinkback ──
    if command in ("/thinkback", "/thinkback-play"):
        return _session.cmd_thinkback(session)

    # ── Buddy ──
    if command == "/buddy":
        return await _session.cmd_buddy(args, state, cwd)

    # ── Advisor ──
    if command == "/advisor":
        return await _cmd_advisor(session, provider, registry, cwd, mcp_manager)

    # ── Security Review ──
    if command == "/security-review":
        return await _cmd_security_review(session, provider, registry, cwd, mcp_manager)

    # ── PR Comments ──
    if command == "/pr-comments":
        return await _cmd_pr_comments(args, cwd)

    # ── Review ──
    if command == "/review":
        return await _cmd_review(args, session, provider, registry, cwd, mcp_manager)

    # ── Autofix PR ──
    if command == "/autofix-pr":
        return await _cmd_autofix_pr(args, session, provider, registry, cwd, mcp_manager)

    # ── Issue ──
    if command == "/issue":
        return await _cmd_issue(args, cwd)

    # ── Commit ──
    if command == "/commit":
        return await _git.cmd_commit(args, session, provider, registry, cwd, mcp_manager)

    # ── Ctx_viz ──
    if command == "/ctx_viz":
        return _session.cmd_ctx_viz(session)

    # ── Heapdump ──
    if command == "/heapdump":
        return _cmd_heapdump()

    # ── Mock limits ──
    if command == "/mock-limits":
        state["mock_limits"] = not state.get("mock_limits", False)
        print_info(f"Mock rate limits: {'ON' if state['mock_limits'] else 'OFF'}")
        return True

    # ── Break cache ──
    if command == "/break-cache":
        return _cmd_break_cache(state, mcp_manager)

    # ── Claim main ──
    if command == "/claim-main":
        return _cmd_claim_main(state)

    # ── Ant trace ──
    if command == "/ant-trace":
        return _cmd_ant_trace()

    # ── Rate limit options ──
    if command == "/rate-limit-options":
        console.print("[bold]Rate limit handling:[/bold]")
        console.print("  • Auto-retry with exponential backoff")
        console.print("  • /effort low — reduce token usage")
        console.print("  • /model — switch to a less loaded model")
        return True

    # ── Reset limits ──
    if command == "/reset-limits":
        state.pop("mock_limits", None)
        print_info("Rate limit counters reset.")
        return True

    # ── Extra usage ──
    if command == "/extra-usage":
        return _cmd_extra_usage()

    # ── Feature Flags ──
    if command == "/flags":
        return await _cmd_flags(args)

    # ── Daemon ──
    if command == "/daemon":
        return await _cmd_daemon(args)

    # ── Jobs ──
    if command == "/jobs":
        return await _cmd_jobs(args)

    # ── ACP ──
    if command == "/acp":
        return await _cmd_acp()

    # ── Langfuse ──
    if command == "/langfuse":
        return await _cmd_langfuse()

    # ── Sentry ──
    if command == "/sentry":
        return await _cmd_sentry()

    # ── Skills ──
    if command == "/skills":
        return await _cmd_skill_entry(
            command,
            args,
            session,
            provider,
            registry,
            cwd,
            mcp_manager,
            kind="skill",
        )

    # ── Use a skill by /skill_name ──
    if command.startswith("/"):
        skill_handled = await _cmd_skill(command, args, session, provider, registry, cwd, mcp_manager)
        if skill_handled:
            return True

    # ── Plugin-contributed slash commands ──
    try:
        from ccb.plugins import discover_plugin_slash_commands
        plugin_cmds = discover_plugin_slash_commands()
    except Exception:
        plugin_cmds = {}
    if command in plugin_cmds:
        info = plugin_cmds[command]
        cmd_path = Path(info["path"])
        try:
            cmd_raw = cmd_path.read_text()
        except Exception as e:
            print_error(f"Failed to read plugin command: {e}")
            return True
        template = cmd_raw
        if template.startswith("---"):
            end = template.find("\n---", 3)
            if end >= 0:
                template = template[end + 4:].lstrip("\n")
        prompt = template
        for placeholder in ("$ARGUMENTS", "{{ARGS}}", "{{ARGUMENTS}}", "{args}", "{arguments}"):
            prompt = prompt.replace(placeholder, args)
        if args and prompt == template:
            prompt = f"{template}\n\n{args}"
        await _run_prompt_command(
            session=session,
            provider=provider,
            registry=registry,
            cwd=cwd,
            prompt=prompt,
            persist_action="command_plugin_prompt_persist_failed",
            mcp_manager=mcp_manager,
            state=state,
        )
        return True

    # ── Unknown → pass to model ──
    return False


# ---------------------------------------------------------------------------
# Inline command handlers (too small or too coupled to warrant a separate module)
# ---------------------------------------------------------------------------

async def _cmd_mcp(args: str, mcp_manager: MCPManager | None) -> bool:
    if not mcp_manager:
        print_info("MCP not initialized.")
        return True
    if args == "connect":
        connected = await mcp_manager.connect_all()
        print_info(f"Connected: {connected}")
    elif args == "disconnect":
        await mcp_manager.disconnect_all()
        print_info("All MCP servers disconnected.")
    else:
        if not mcp_manager.servers:
            print_info("No MCP servers connected. Use /mcp connect")
        else:
            for name, server in mcp_manager.servers.items():
                status = "✓" if server.connected else "✗"
                console.print(f"  {status} [bold]{name}[/bold] ({server.type})")
                for tool_info in server.tools:
                    console.print(f"      {tool_info['name']}: {tool_info.get('description', '')[:60]}")
    return True


async def _cmd_events(args: str) -> bool:
    from ccb.events import clear_events, recent_events
    event_parts = args.strip().split(maxsplit=1) if args.strip() else []
    event_subcmd = event_parts[0].lower() if event_parts else "list"
    if event_subcmd == "clear":
        clear_events()
        print_info("Event stream cleared.")
    elif event_subcmd in ("list", "recent", ""):
        events = recent_events(30)
        if not events:
            print_info("No recent events.")
        else:
            console.print(f"[bold]Recent events ({len(events)}):[/bold]")
            for event in events:
                payload = event.get("payload") or {}
                detail = ""
                if payload:
                    detail = " " + ", ".join(f"{k}={str(v)[:40]}" for k, v in list(payload.items())[:3])
                console.print(
                    f"  [dim]{event.get('time', '')}[/dim] "
                    f"{event.get('level', 'info'):7s} "
                    f"{event.get('kind', '')}.{event.get('action', '')}{detail}"
                )
    else:
        print_info("Usage: /events [list | clear]")
    return True


async def _cmd_permissions(args: str) -> bool:
    from ccb.permissions import set_bypass_all, _bypass_all
    if args == "bypass":
        set_bypass_all(True)
        print_info("Bypass mode ON - all tools auto-approved")
    elif args == "default":
        set_bypass_all(False)
        print_info("Default permission mode")
    elif not args:
        from ccb.select_ui import select_one
        current_mode = "bypass" if _bypass_all else "default"
        modes = [
            {"label": "default", "description": "Ask before risky tools" + (" ← current" if current_mode == "default" else "")},
            {"label": "bypass", "description": "Auto-approve all tools" + (" ← current" if current_mode == "bypass" else "")},
        ]
        active_idx = 0 if current_mode == "default" else 1
        choice = await select_one(modes, title="Permission Mode", active=active_idx)
        if choice is not None:
            set_bypass_all(choice == 1)
            print_info(f"Permission mode: {'bypass' if choice == 1 else 'default'}")
    return True


async def _cmd_init(cwd: str, args: str) -> bool:
    from pathlib import Path
    target = Path(cwd) / "CLAUDE.md"
    if target.exists() and not args:
        console.print(f"  CLAUDE.md already exists at {target}")
        console.print("  Use /init force to overwrite")
        return True
    template = (
        "# Project Guidelines\n\n"
        "## Overview\n"
        "Describe your project here.\n\n"
        "## Code Style\n"
        "- Follow existing conventions\n\n"
        "## Important Notes\n"
        "- Add any specific instructions for the AI assistant\n"
    )
    target.write_text(template)
    print_info(f"Created {target}")
    return True


async def _cmd_image(args: str, session: Session, state: dict[str, Any]) -> bool:
    if not args:
        console.print("  Usage: /image <path>  — Attach an image to your next message")
        console.print("  Supported: PNG, JPG, GIF, WebP, BMP, TIFF, SVG")
        console.print("  You can also:")
        console.print("    • Drag image files into the terminal")
        console.print("    • Ctrl+V to paste from clipboard (macOS)")
        return True
    from ccb.images import read_image_from_path, normalize_path
    img_path = normalize_path(args)
    img = read_image_from_path(img_path)
    if img:
        from ccb.repl import get_active_repl
        repl = get_active_repl()
        if repl:
            repl._pending_images.append(img.to_dict())
            n = len(repl._pending_images)
            console.print(
                f"  📎 Attached [bold]{img.filename}[/bold]"
                f" ({img.media_type}, {len(img.base64_data) * 3 // 4 // 1024}KB)"
                f" [{n} pending]"
            )
        else:
            pending = state.setdefault("_pending_images", [])
            pending.append(img.to_dict())
            console.print(
                f"  📎 Attached [bold]{img.filename}[/bold]"
                f" ({img.media_type})"
            )
    else:
        print_error(f"Cannot read image: {img_path}")
    return True


async def _cmd_file(args: str, session: Session, state: dict[str, Any]) -> bool:
    if not args:
        console.print("  Usage: /file <path>  — Attach a text file to your next message")
        console.print("  The file content will be inlined in your prompt.")
        return True
    from ccb.images import read_file_as_text, normalize_path
    fpath = normalize_path(args)
    fc = read_file_as_text(fpath)
    if fc:
        from ccb.repl import get_active_repl
        repl = get_active_repl()
        if repl:
            repl._pending_files.append(fc.to_dict())
            n = len(repl._pending_files)
            console.print(
                f"  📄 Attached [bold]{fc.filename}[/bold]"
                f" ({len(fc.content):,} chars) [{n} pending]"
            )
        else:
            pending = state.setdefault("_pending_files", [])
            pending.append(fc.to_dict())
            console.print(
                f"  📄 Attached [bold]{fc.filename}[/bold]"
                f" ({len(fc.content):,} chars)"
            )
    else:
        print_error(f"Cannot read file: {fpath}")
    return True


async def _cmd_color(args: str, state: dict[str, Any]) -> bool:
    colors = ["blue", "green", "red", "yellow", "magenta", "cyan", "white"]
    if args and args in colors:
        state["color"] = args
        print_info(f"Prompt color set to {args}")
    else:
        from ccb.select_ui import select_one
        current = state.get("color", "blue")
        active_idx = colors.index(current) if current in colors else 0
        items = [{"label": c, "description": "← current" if c == current else ""} for c in colors]
        choice = await select_one(items, title="Select Prompt Color", active=active_idx)
        if choice is not None:
            state["color"] = colors[choice]
            print_info(f"Prompt color set to {colors[choice]}")
    return True


def _cmd_usage(session: Session, state: dict[str, Any]) -> bool:
    console.print("[bold]Usage:[/bold]")
    total = session.total_input_tokens + session.total_output_tokens
    console.print(f"  Input:  {session.total_input_tokens:,} tokens")
    console.print(f"  Output: {session.total_output_tokens:,} tokens")
    console.print(f"  Total:  {total:,} tokens")
    budget = state.get("token_budget")
    if budget:
        console.print(f"  Budget: {budget:,} ({total * 100 // budget}% used)")
    return True


async def _cmd_feedback(command: str, args: str, session: Session) -> bool:
    if not args:
        print_error(f"Usage: {command} <message>")
        return True
    from pathlib import Path
    fb_dir = Path.home() / ".ccb" / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    fb_file = fb_dir / f"{int(time.time())}.txt"
    fb_file.write_text(f"{command}: {args}\nSession: {session.id}\nModel: {session.model}\n")
    print_info("Feedback saved. Thank you!")
    return True


async def _cmd_agents(args: str, state: dict[str, Any], provider: Provider, cwd: str) -> bool:
    from ccb.agent_defs import discover_agents, get_agent, apply_agent
    if args and args.strip():
        agent = get_agent(args.strip(), cwd)
        if not agent:
            print_error(f"Agent '{args.strip()}' not found. Use /agents to list.")
            return True
        agent_prompt = apply_agent(agent, provider, state)
        if agent_prompt:
            state["_agent_prompt"] = agent_prompt
        print_info(f"Agent activated: {agent.name}")
        if agent.description:
            console.print(f"  {agent.description}")
        if agent.model:
            console.print(f"  Model: {agent.model}")
        if agent.thinking:
            console.print(f"  Thinking: {agent.thinking}")
        if agent.effort:
            console.print(f"  Effort: {agent.effort}")
        return True

    agents = discover_agents(cwd)
    active = state.get("_active_agent", "")
    console.print("[bold]Agent Definitions:[/bold]")
    if agents:
        for a in agents:
            marker = " ← active" if a.name == active else ""
            src = f" [dim]({a.source})[/dim]" if a.source else " [dim](built-in)[/dim]"
            console.print(f"  • [bold]{a.name}[/bold] — {a.description}{src}{marker}")
    else:
        console.print("  [dim]No agent definitions found.[/dim]")
    console.print()
    console.print("  [dim]Use: /agents <name> to activate[/dim]")
    console.print("  [dim]Define: ~/.ccb/agents/<name>.yaml or .claude/agents/<name>.yaml[/dim]")

    from ccb.coordinator import get_coordinator
    coord = get_coordinator()
    s = coord.summary()
    if s['total'] > 0:
        console.print()
        console.print(f"[bold]Running Sub-agents:[/bold] {s['running']}/{s['total']}")
        for agent_inst in coord.list_agents():
            icon = {"running": "🔄", "done": "✅", "error": "❌", "idle": "⏸"}.get(agent_inst.status, "?")
            console.print(f"  {icon} {agent_inst.name} ({agent_inst.role or 'agent'}) — {agent_inst.status}")
    return True


async def _cmd_peers_pipes(cmd: str, args: str, state: dict[str, Any]) -> bool:
    import os as _os
    from ccb.pipe_ipc import PipeIPC
    from ccb.peer_discovery import PeerDiscovery
    from ccb.pipes_panel import show_pipes_panel

    if args.strip() == "live":
        from ccb.pipes_panel import live_pipes_panel
        pd = state.get("_peer_discovery")
        if pd is None:
            print_info("Peer discovery not active. Starting...")
            pd = PeerDiscovery(instance_id=f"cli-{_os.getpid()}")
            await pd.start()
            state["_peer_discovery"] = pd
        try:
            await live_pipes_panel(pd.instance_id, pd)
        except KeyboardInterrupt:
            pass
        return True

    pd = state.get("_peer_discovery")
    if pd:
        peers = pd.get_peers(include_stale=True)
        show_pipes_panel(pd.instance_id, peers, title="LAN Peers")
    else:
        ipc = PipeIPC()
        _ = ipc.discover_local_peers()
        peers = ipc.list_peers()
        show_pipes_panel(ipc.instance_id, peers, title="Local Pipes")
        if not peers:
            console.print("  [dim]No peers found. Start another ccb-py instance to see it here.[/dim]")
            console.print("  [dim]Use /peers live for a live-refreshing panel.[/dim]")
    return True


async def _cmd_tasks(session: Session) -> bool:
    tc_count = sum(len(m.tool_calls) for m in session.messages if m.tool_calls)
    console.print(f"  Tool calls this session: {tc_count}")
    return True


async def _cmd_advisor(session: Session, provider: Provider, registry, cwd: str, mcp_manager) -> bool:
    await _run_prompt_command(
        session=session,
        provider=provider,
        registry=registry,
        cwd=cwd,
        prompt=(
            "Please act as a senior code reviewer. Review the recent changes and provide "
            "constructive feedback on code quality, potential bugs, and improvements."
        ),
        persist_action="command_advisor_persist_failed",
        mcp_manager=mcp_manager,
    )
    return True


async def _cmd_security_review(session: Session, provider: Provider, registry, cwd: str, mcp_manager) -> bool:
    await _run_prompt_command(
        session=session,
        provider=provider,
        registry=registry,
        cwd=cwd,
        prompt=(
            "Perform a security review of the current codebase. Look for common vulnerabilities "
            "like injection attacks, authentication issues, data exposure, and insecure configurations."
        ),
        persist_action="command_security_review_persist_failed",
        mcp_manager=mcp_manager,
    )
    return True


async def _cmd_pr_comments(args: str, cwd: str) -> bool:
    from ccb.github_ops import pr_comments, gh_available
    if not gh_available():
        print_error("GitHub CLI (gh) not available. Install: https://cli.github.com")
        return True
    number = int(args) if args.strip().isdigit() else None
    comments = pr_comments(number, cwd=cwd)
    if not comments:
        print_info("No PR comments found.")
    else:
        console.print(f"[bold]PR comments ({len(comments)}):[/bold]")
        for c in comments:
            state_tag = f" [{c.state}]" if c.state else ""
            console.print(f"  [bold]{c.author}[/bold]{state_tag}: {c.body[:200]}")
    return True


async def _cmd_review(args: str, session: Session, provider: Provider, registry, cwd: str, mcp_manager) -> bool:
    from ccb.github_ops import generate_review_prompt, gh_available
    if not gh_available():
        print_error("GitHub CLI (gh) not available.")
        return True
    number = int(args) if args.strip().isdigit() else None
    prompt = generate_review_prompt(number, cwd=cwd)
    await _run_prompt_command(
        session=session,
        provider=provider,
        registry=registry,
        cwd=cwd,
        prompt=prompt,
        persist_action="command_review_persist_failed",
        mcp_manager=mcp_manager,
    )
    return True


async def _cmd_remember(args: str, session: Session, provider: Provider, cwd: str) -> bool:
    """Handle /remember command - add a persistent memory."""
    from ccb.memory import get_store
    from ccb.display import repl_console as console, print_info, print_error

    mem_store = get_store()

    if not args.strip():
        print_info("Usage: /remember <text> [#category] [#tag1 #tag2]")
        print_info("Example: /remember User prefers Python #preference #language")
        print_info("         /remember Project uses pytest #project #testing")
        return True

    # Parse: text content, #category, #tags
    parts = args.split("#")
    content = parts[0].strip()
    category = ""
    tags = []

    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        # Check if it's a known category or just a tag
        known_categories = {"user_preference", "project", "codebase", "task_pattern", "general"}
        if p.lower() in known_categories:
            category = p.lower()
        else:
            tags.append(p)

    if not content:
        print_error("Nothing to remember.")
        return True

    # Add the memory
    metadata = {"cwd": cwd, "created_by": "user"}
    mem = mem_store.add(
        content=content,
        tags=tags if tags else None,
        category=category or "general",
        pinned=False,
        importance=1.5,  # user-initiated memories are higher importance
        metadata=metadata,
    )

    console.print(f"  [green]✓[/green] Remembered: {content[:60]}{'...' if len(content) > 60 else ''}")
    if category:
        console.print(f"  [dim]    category: {category}[/dim]")
    if tags:
        console.print(f"  [dim]    tags: {', '.join(tags)}[/dim]")
    console.print(f"  [dim]    id: {mem.id}[/dim]")

    return True


def _cmd_forget(args: str, session: Session, cwd: str) -> bool:
    """Handle /forget command - remove a memory."""
    from ccb.memory import get_store
    from ccb.display import repl_console as console, print_info

    mem_store = get_store()

    if not args.strip():
        # List memories for selection
        memories = mem_store.list_all()
        if not memories:
            print_info("No memories to forget.")
            return True
        console.print(f"[bold]Your memories ({len(memories)}):[/bold]")
        for m in memories[:15]:
            pinned_str = " 📌" if m.pinned else ""
            console.print(f"  {m.id[:12]}... {m.content[:50]}...{pinned_str}")
        console.print("[dim]Use /forget <id> or /forget <search term>[/dim]")
        return True

    args = args.strip()

    # Try as ID first
    if mem_store.delete(args):
        print_info(f"Forgotten: {args}")
        return True

    # Try as search term - delete first match
    results = mem_store.search(args, limit=5)
    if not results:
        print_info(f"No memory found matching '{args}'")
        return True

    # Show matches
    console.print("[bold]Matching memories:[/bold]")
    for i, m in enumerate(results):
        console.print(f"  {i+1}. {m.content[:60]}... (id: {m.id[:12]}...)")
    console.print("[dim]Use /forget <id> to delete a specific one[/dim]")
    return True


async def _cmd_autofix_pr(args: str, session: Session, provider: Provider, registry, cwd: str, mcp_manager) -> bool:
    from ccb.github_ops import generate_autofix_prompt, gh_available
    if not gh_available():
        print_error("GitHub CLI (gh) not available.")
        return True
    number = int(args) if args.strip().isdigit() else None
    prompt = generate_autofix_prompt(number, cwd=cwd)
    await _run_prompt_command(
        session=session,
        provider=provider,
        registry=registry,
        cwd=cwd,
        prompt=prompt,
        persist_action="command_autofix_pr_persist_failed",
        mcp_manager=mcp_manager,
    )
    return True


async def _cmd_issue(args: str, cwd: str) -> bool:
    from ccb.github_ops import issue_list, issue_view, gh_available
    if not gh_available():
        print_error("GitHub CLI (gh) not available.")
        return True
    if args.strip().isdigit():
        issue = issue_view(int(args), cwd=cwd)
        if issue:
            console.print(f"[bold]#{issue.number} {issue.title}[/bold] ({issue.state})")
            console.print(f"  Author: {issue.author}  Labels: {', '.join(issue.labels)}")
            if issue.body:
                console.print(f"  {issue.body[:500]}")
        else:
            print_error(f"Issue #{args} not found")
    else:
        issues = issue_list(cwd=cwd, state=args.strip() or "open")
        if not issues:
            print_info("No issues found.")
        else:
            console.print(f"[bold]Issues ({len(issues)}):[/bold]")
            for issue_item in issues:
                labels = f" [{', '.join(issue_item.labels)}]" if issue_item.labels else ""
                console.print(f"  [bold]#{issue_item.number:5d}[/bold] {issue_item.title}{labels}")
    return True


def _cmd_heapdump() -> bool:
    objects: dict[str, int] = {}
    for obj in list(sys.modules.values()):
        t = type(obj).__name__
        objects[t] = objects.get(t, 0) + 1
    console.print("[bold]Memory snapshot:[/bold]")
    for obj_type, obj_count in sorted(objects.items(), key=lambda x: -x[1])[:15]:
        console.print(f"  {obj_type:30s} {obj_count}")
    return True


def _cmd_break_cache(state: dict[str, Any], mcp_manager) -> bool:
    cleared = 0
    state.pop("_cached_system_prompt", None)
    state.pop("_cached_tools", None)
    cleared += 1
    for mod_name in ["ccb.prompts", "ccb.skills", "ccb.config"]:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "_cache"):
            mod._cache.clear()
            cleared += 1
    state.pop("_file_hashes", None)
    if mcp_manager and hasattr(mcp_manager, "_tool_cache"):
        mcp_manager._tool_cache.clear()
        cleared += 1
    print_info(f"Cache cleared ({cleared} caches invalidated). Next request will be fresh.")
    return True


def _cmd_claim_main(state: dict[str, Any]) -> bool:
    import os
    from pathlib import Path
    pid_file = Path.home() / ".ccb" / "ccb-main.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            print_info(f"⚠ Another main process (PID {old_pid}) is running.")
        except (ProcessLookupError, ValueError):
            pass
    pid_file.write_text(str(os.getpid()))
    state["is_main"] = True
    print_info(f"Claimed main process (PID {os.getpid()})")
    return True


def _cmd_ant_trace() -> bool:
    print_info("Anthropic API trace viewer.")
    from ccb.analytics_tracker import get_tracker
    stats = get_tracker().get_session_stats()
    console.print(f"  Input tokens: {stats.get('input_tokens', 0):,}")
    console.print(f"  Output tokens: {stats.get('output_tokens', 0):,}")
    console.print(f"  API calls: {stats.get('turns', 0)}")
    console.print(f"  Cost: ${stats.get('cost_usd', 0):.4f}")
    return True


def _cmd_extra_usage() -> bool:
    from ccb.analytics_tracker import get_tracker
    stats = get_tracker().get_historical_stats(days=30)
    console.print("[bold]Usage (last 30 days):[/bold]")
    console.print(f"  Sessions: {stats.get('sessions', 0)}")
    console.print(f"  Messages: {stats.get('messages', 0)}")
    console.print(f"  Input tokens: {stats.get('input_tokens', 0):,}")
    console.print(f"  Output tokens: {stats.get('output_tokens', 0):,}")
    console.print(f"  Cost: ${stats.get('cost_usd', 0):.4f}")
    top_tools = sorted(stats.get("tools_used", {}).items(), key=lambda x: -x[1])[:5]
    if top_tools:
        console.print("  Top tools: " + ", ".join(f"{k}({v})" for k, v in top_tools))
    return True


async def _cmd_flags(args: str) -> bool:
    import os as _os
    from ccb.feature_flags import get_flags
    ff = get_flags()
    parts_args = args.strip().split(maxsplit=1) if args.strip() else []
    subcmd = parts_args[0].lower() if parts_args else "list"
    subarg = parts_args[1] if len(parts_args) > 1 else ""

    if subcmd == "list" or subcmd == "":
        all_flags = ff.list_flags()
        if not all_flags:
            print_info("No feature flags configured.")
        else:
            console.print("[bold]Feature Flags:[/bold]")
            for name, flag_val in sorted(all_flags.items()):
                env_suffix = name.upper().replace("-", "_")
                env_keys = (f"CCB_FLAG_{env_suffix}", f"CLAUDE_CODE_FLAG_{env_suffix}")
                env_override = next((_os.environ[k] for k in env_keys if k in _os.environ), None)
                if isinstance(flag_val, dict):
                    flag_val = flag_val.get("defaultValue", flag_val.get("value", flag_val.get("enabled", "?")))
                source = ""
                if env_override is not None:
                    source = " [dim](env override)[/dim]"
                status = "[green]ON[/green]" if flag_val else "[red]OFF[/red]"
                if not isinstance(flag_val, bool):
                    status = str(flag_val)
                console.print(f"  [bold]{name:30s}[/bold] {status}{source}")
    elif subcmd == "toggle":
        flag_name = subarg.strip()
        if not flag_name:
            print_info("Usage: /flags toggle <flag_name>")
        else:
            current = ff.is_enabled(flag_name)
            ff.set_override(flag_name, not current)
            flag_state = "ON" if not current else "OFF"
            print_info(f"Flag '{flag_name}' toggled to {flag_state}")
    elif subcmd == "set":
        set_parts = subarg.split(maxsplit=1)
        if len(set_parts) < 2:
            print_info("Usage: /flags set <flag_name> <value>")
        else:
            flag_name, raw_val = set_parts[0], set_parts[1]
            from ccb.feature_flags import _parse_env_value
            value = _parse_env_value(raw_val)
            ff.set_override(flag_name, value)
            print_info(f"Flag '{flag_name}' set to {value!r}")
    else:
        print_info("Usage: /flags [list | toggle <name> | set <name> <value>]")
    return True


async def _cmd_daemon(args: str) -> bool:
    from ccb.daemon_proc import daemon_status, start_daemon, stop_daemon
    daemon_parts = args.strip().split(maxsplit=1) if args.strip() else []
    daemon_subcmd = daemon_parts[0].lower() if daemon_parts else "status"
    if daemon_subcmd == "start":
        pid = start_daemon()
        if pid:
            print_info(f"Daemon running (pid {pid}).")
        else:
            print_error("Daemon did not start. Check feature flag 'tengu_daemon_enabled'.")
    elif daemon_subcmd == "stop":
        print_info("Daemon stopped." if stop_daemon() else "Daemon is not running.")
    elif daemon_subcmd == "status":
        status = daemon_status()
        console.print("[bold]Daemon:[/bold]")
        console.print(f"  Running: {status['running']}")
        console.print(f"  PID: {status['pid'] or '-'}")
        console.print(f"  Log: {status['log_file']}")
    else:
        print_info("Usage: /daemon [status | start | stop]")
    return True


async def _cmd_jobs(args: str) -> bool:
    from ccb.jobs import JobStatus, get_job_manager
    manager = get_job_manager()
    job_parts = args.strip().split(maxsplit=1) if args.strip() else []
    job_subcmd = job_parts[0].lower() if job_parts else "list"
    job_arg = job_parts[1] if len(job_parts) > 1 else ""
    if job_subcmd in ("list", "ls", ""):
        jobs = manager.list_jobs()
        if not jobs:
            print_info("No background jobs.")
        else:
            console.print(f"[bold]Background jobs ({len(jobs)}):[/bold]")
            for job in jobs[:50]:
                console.print(f"  [bold]{job.id}[/bold] {job.status.value:10s} {job.template} {job.cwd}")
                if job.summary:
                    console.print(f"    {job.summary[:120]}")
                elif job.error:
                    console.print(f"    [red]{job.error[:120]}[/red]")
    elif job_subcmd == "cancel":
        if not job_arg.strip():
            print_info("Usage: /jobs cancel <job-id>")
        else:
            print_info("Job cancelled." if manager.cancel_job(job_arg.strip()) else "Job not found or already complete.")
    elif job_subcmd == "delete":
        if not job_arg.strip():
            print_info("Usage: /jobs delete <job-id>")
        else:
            print_info("Job deleted." if manager.delete_job(job_arg.strip()) else "Job not found or running.")
    elif job_subcmd == "summary":
        summary = manager.summary()
        console.print("[bold]Jobs summary:[/bold]")
        console.print(f"  Total: {summary['total']}")
        for status in JobStatus:
            console.print(f"  {status.value}: {summary['by_status'].get(status.value, 0)}")
    else:
        print_info("Usage: /jobs [list | summary | cancel <id> | delete <id>]")
    return True


async def _cmd_acp() -> bool:
    try:
        from ccb.session_restore import SessionRestorer
        restorer = SessionRestorer()
        sessions = restorer.list_active_sessions()
        if not sessions:
            print_info("No active ACP sessions.")
        else:
            console.print("[bold]ACP Sessions:[/bold]")
            for acp_session in sessions:
                sess_id_str = acp_session.get("id", str(acp_session)) if isinstance(acp_session, dict) else str(acp_session)
                conns = restorer.get_active_connections(sess_id_str)
                conn_str = ", ".join(c.ide_type for c in conns) if conns else "none"
                console.print(f"  {sess_id_str[:12]}  connections: {conn_str}")
    except Exception as e:
        print_info(f"ACP not available: {e}")
    return True


async def _cmd_langfuse() -> bool:
    try:
        from ccb.langfuse_monitor import get_monitor
        mon = get_monitor()
        if mon and mon.secret_key:
            console.print(f"[green]Langfuse active[/green] — host: {mon.host}")
        else:
            print_info("Langfuse not configured. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
    except Exception as e:
        print_info(f"Langfuse not available: {e}")
    return True


async def _cmd_sentry() -> bool:
    try:
        from ccb.sentry_integration import is_initialized
        if is_initialized():
            console.print("[green]Sentry active[/green]")
        else:
            print_info("Sentry not configured. Set SENTRY_DSN environment variable.")
    except Exception as e:
        print_info(f"Sentry not available: {e}")
    return True


async def _cmd_skill(
    command: str,
    args: str,
    session: Session,
    provider: Provider,
    registry,
    cwd: str,
    mcp_manager,
) -> bool:
    from ccb.skills import resolve_skill_prompt

    skill_name = command[1:]
    resolved = resolve_skill_prompt(cwd, skill_name, args)
    if resolved is None:
        return False
    _skill_item, prompt = resolved

    await _run_prompt_command(
        session=session,
        provider=provider,
        registry=registry,
        cwd=cwd,
        prompt=prompt,
        persist_action="command_skill_persist_failed",
        mcp_manager=mcp_manager,
    )
    return True


async def _cmd_skill_entry(
    command: str,
    args: str,
    session: Session,
    provider: Provider,
    registry,
    cwd: str,
    mcp_manager,
    *,
    kind: str,
) -> bool:
    from ccb.skills import resolve_skill_reference

    trimmed = args.strip()
    resolved = resolve_skill_reference(cwd, trimmed, kind=kind) if trimmed else None
    if resolved is not None:
        skill, rest = resolved
        return await _cmd_skill(
            f"/{skill.name}",
            rest,
            session,
            provider,
            registry,
            cwd,
            mcp_manager,
        )
    return _cmd_skill_list(cwd, kind=kind, query=trimmed)


def _cmd_skill_list(cwd: str, *, kind: str, query: str = "") -> bool:
    from ccb.skills import SKILL_KIND, WORKFLOW_KIND, list_skills, search_skills

    wanted_kind = WORKFLOW_KIND if kind == WORKFLOW_KIND else SKILL_KIND
    items = (
        search_skills(cwd, query, kind=wanted_kind, limit=12)
        if query.strip()
        else list_skills(cwd, kind=wanted_kind)
    )
    label = "workflows" if wanted_kind == WORKFLOW_KIND else "skills"
    if not items:
        if query.strip():
            prefix = "/workflows" if wanted_kind == WORKFLOW_KIND else "/skills"
            print_info(f"No {label} matched '{query.strip()}'. Try {prefix} to list available entries.")
        elif wanted_kind == WORKFLOW_KIND:
            print_info("No workflows found. Place .md files in .windsurf/workflows/ or .claude/workflows/.")
        else:
            print_info("No skills found. Add .md files under .claude/skills/ or ~/.claude/skills/.")
        return True

    for item in items:
        source = item.source_label
        console.print(
            f"  [bold]{item.name:20s}[/bold] [{source}] {item.description}"
            f"\n    [dim]{item.invocation_command}[/dim]"
        )
    return True


async def _cmd_sandbox(args: str, state: dict[str, Any], cwd: str) -> bool:
    from ccb.sandbox_exec import get_sandbox
    from ccb.state import get_state as get_global_state

    sandbox = get_sandbox()
    global_state = get_global_state()

    if args == "status":
        info = sandbox.info()
        console.print("[bold]Sandbox Status[/bold]")
        console.print(f"  Backend: {info['backend']}")
        console.print(f"  Available: {'yes' if info['available'] else 'no'}")
        console.print(f"  Enabled: {'yes' if info['enabled'] else 'no'}")
        console.print(f"  Timeout: {info['timeout']}s")
        console.print(f"  Docker image: {info['docker_image']}")
        if info['allowed_paths']:
            console.print(f"  Allowed paths: {', '.join(info['allowed_paths'])}")
        return True

    if args == "on":
        if not sandbox.available:
            print_error("No sandbox backend available. Install Docker, or use macOS sandbox-exec.")
            return True
        sandbox.enable()
        state["sandbox"] = True
        global_state.set("sandbox_mode", True)
        print_info(f"Sandbox mode: ON ({sandbox.backend_name})")
    elif args == "off":
        sandbox.disable()
        state["sandbox"] = False
        global_state.set("sandbox_mode", False)
        print_info("Sandbox mode: OFF")
    elif args == "allow":
        arg = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        path = arg.strip() if arg else cwd
        sandbox.allow_path(path)
        print_info(f"Allowed path: {path}")
    elif args == "image":
        arg = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        if arg:
            sandbox.set_docker_image(arg)
            print_info(f"Docker image: {arg}")
        else:
            print_info(f"Current Docker image: {sandbox._docker_image}")
    else:
        new_state = not state.get("sandbox", False)
        state["sandbox"] = new_state
        global_state.set("sandbox_mode", new_state)
        if new_state:
            if sandbox.available:
                sandbox.enable()
                print_info(f"Sandbox mode: ON ({sandbox.backend_name})")
            else:
                print_error("Cannot enable: no backend available (docker/macos-sandbox/firejail)")
                state["sandbox"] = False
                global_state.set("sandbox_mode", False)
        else:
            sandbox.disable()
            print_info("Sandbox mode: OFF")
    return True


async def _cmd_output_style(args: str, state: dict[str, Any]) -> bool:
    styles = ["rich", "text", "json", "minimal"]
    if args and args in styles:
        state["output_style"] = args
        print_info(f"Output style: {args}")
        return True
    from ccb.select_ui import select_one
    current = state.get("output_style", "rich")
    active_idx = styles.index(current) if current in styles else 0
    items = [{"label": s, "description": "← current" if s == current else ""} for s in styles]
    choice = await select_one(items, title="Select Output Style", active=active_idx)
    if choice is not None:
        state["output_style"] = styles[choice]
        print_info(f"Output style: {styles[choice]}")
    return True


def _cmd_keybindings(state: dict[str, Any]) -> bool:
    console.print("[bold]Keybindings:[/bold]")
    console.print("  Enter        → Send message")
    console.print("  Esc+Enter    → New line")
    console.print("  Ctrl+C       → Cancel / Interrupt")
    console.print("  Ctrl+D       → Exit")
    console.print("  Tab          → Complete slash command")
    console.print("  Up/Down      → History navigation")
    if state.get("vim_mode"):
        console.print("  [dim](Vim mode active — i/a/o to insert, Esc to normal)[/dim]")
    return True


async def _cmd_voice(
    args: str,
    session: Session,
    provider: Provider,
    registry,
    cwd: str,
    mcp_manager,
) -> bool:
    try:
        from ccb.voice_input import VoiceInput
        voice = VoiceInput()
        if args == "info":
            info = voice.info()
            for k, v in info.items():
                console.print(f"  {k}: {v}")
        elif args == "listen":
            print_info("Listening... (speak now, press Ctrl+C to stop)")
            voice_listen_text = await voice.record_and_transcribe(duration=10)
            if voice_listen_text:
                console.print(f"[bold]Transcribed:[/bold] {voice_listen_text}")
                await _run_prompt_command(
                    session=session,
                    provider=provider,
                    registry=registry,
                    cwd=cwd,
                    prompt=voice_listen_text,
                    persist_action="command_voice_persist_failed",
                    mcp_manager=mcp_manager,
                )
            else:
                print_info("No speech detected.")
        elif args and args.startswith("model "):
            model_name = args[6:].strip()
            print_info(f"Whisper model configuration not supported here. (attempted: {model_name})")
        else:
            info = voice.info()
            console.print(f"[bold]Voice input[/bold]: backend={info.get('backend', 'none')}")
            console.print("[dim]  /voice listen — record and transcribe")
            console.print("  /voice info — show details")
            console.print("  /voice model <name> — set whisper model[/dim]")
    except Exception as e:
        print_error(f"Voice input error: {e}")
    return True


async def _cmd_privacy_settings(args: str) -> bool:
    from ccb.config import load_settings, save_settings
    settings = load_settings()
    privacy = settings.get("privacy", {})
    if args == "telemetry off":
        privacy["telemetry"] = False
        settings["privacy"] = privacy
        save_settings(settings)
        print_info("Telemetry disabled")
    elif args == "telemetry on":
        privacy["telemetry"] = True
        settings["privacy"] = privacy
        save_settings(settings)
        print_info("Telemetry enabled")
    else:
        console.print(f"  Telemetry: {'on' if privacy.get('telemetry', True) else 'off'}")
        console.print("  Use: /privacy-settings telemetry off|on")
    return True
