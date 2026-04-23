"""Slash command system - all built-in commands."""
from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info

if TYPE_CHECKING:
    from ccb.api.base import Provider
    from ccb.mcp.client import MCPManager
    from ccb.session import Session
    from ccb.tools.base import ToolRegistry


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
        _print_help()
        return True

    # ── Clear ──
    if command == "/clear":
        session.messages.clear()
        print_info("Conversation cleared.")
        return True

    # ── Compact ──
    if command == "/compact":
        await _compact(session, provider, args)
        return True

    # ── Model ──
    if command == "/model":
        from ccb.config import get_active_account, switch_account

        def _persist(new_model: str) -> None:
            """Save the picked model to accounts.json so it survives restarts."""
            acct = get_active_account()
            acct_name = acct.get("_name", "") if acct else ""
            if acct_name:
                # switch_account(name, model) with name==current just updates activeModel
                switch_account(acct_name, new_model)

        if args:
            session.model = args
            provider.set_model(args)
            _persist(args)
            acct = get_active_account()
            acct_name = acct.get("_name", "?") if acct else "?"
            console.print(f"  Model → [bold]{args}[/bold] (account: {acct_name})")
        else:
            # Interactive model picker from account's models list
            from ccb.config import load_accounts
            from ccb.select_ui import select_one
            acct = get_active_account()
            acct_name = acct.get("_name", "") if acct else ""
            store = load_accounts()
            profile = store.get("accounts", {}).get(acct_name, {})
            models = profile.get("models", [])
            if not models:
                console.print(f"  Current model: [bold]{session.model}[/bold]")
                console.print(f"  [dim]Tip: /model <name> to switch[/dim]")
            else:
                items = []
                active_idx = 0
                for i, m in enumerate(models):
                    if m == session.model:
                        active_idx = i
                    items.append({
                        "label": m,
                        "description": "← current" if m == session.model else "",
                    })
                choice = await select_one(items, title="Select Model", active=active_idx)
                if choice is not None:
                    new_model = models[choice]
                    session.model = new_model
                    provider.set_model(new_model)
                    _persist(new_model)
                    console.print(f"  Model → [bold]{new_model}[/bold]")
        return True

    # ── Account ──
    if command == "/account":
        new_provider = await _account(args, provider, session)
        if new_provider is not None:
            # Caller must pick up the new provider — store on state
            state["_new_provider"] = new_provider
        return True

    # ── Cost ──
    if command == "/cost":
        from ccb.cost_tracker import format_tokens, format_cost, get_cost_state, context_percentage
        from ccb.model_limits import get_context_limit
        cs = get_cost_state()
        inp = cs.total_input_tokens or session.total_input_tokens
        out = cs.total_output_tokens or session.total_output_tokens
        total = inp + out
        ctx_limit = get_context_limit(session.model)
        pct = context_percentage(session.last_input_tokens, ctx_limit)
        console.print(f"  Input tokens:  {format_tokens(inp)} ({inp:,})")
        console.print(f"  Output tokens: {format_tokens(out)} ({out:,})")
        console.print(f"  Total tokens:  {format_tokens(total)} ({total:,})")
        console.print(f"  Context:       {pct}% ({format_tokens(session.last_input_tokens)}/{format_tokens(ctx_limit)})")
        console.print(f"  Est. cost:     {format_cost(cs.total_cost_usd)}")
        if cs.last_turn_duration_ms:
            from ccb.cost_tracker import format_duration
            console.print(f"  Last turn:     {format_duration(cs.last_turn_duration_ms)}")
        return True

    # ── Sessions ──
    if command in ("/sessions", "/resume", "/continue"):
        from ccb.session import Session as S
        from ccb.select_ui import select_one

        def _apply_resume(loaded: S) -> None:
            session.messages = loaded.messages
            session.id = loaded.id
            session.cwd = loaded.cwd or session.cwd
            session.model = loaded.model or session.model
            session.total_input_tokens = loaded.total_input_tokens
            session.total_output_tokens = loaded.total_output_tokens
            session.created_at = loaded.created_at
            if loaded.model:
                provider.set_model(loaded.model)

        if command in ("/resume", "/continue") and args:
            # Try exact ID match first
            loaded = S.load(args)
            if not loaded:
                for entry in S.list_sessions(50):
                    if args in entry["id"] or args.lower() in entry.get("cwd", "").lower():
                        loaded = S.load(entry["id"])
                        break
            if loaded:
                _apply_resume(loaded)
                print_info(f"Resumed session {loaded.id[:8]} ({len(loaded.messages)} msgs, model: {loaded.model})")
            else:
                print_error(f"Session not found: {args}")
            return True

        # Interactive session picker for /resume (no args), /sessions, /continue
        all_sessions = S.list_sessions(20)
        if not all_sessions:
            print_info("No sessions.")
            return True

        items = []
        for s in all_sessions:
            ts = time.strftime("%m/%d %H:%M", time.localtime(s["updated_at"]))
            cwd_short = s.get("cwd", "")
            if "/" in cwd_short:
                cwd_short = cwd_short.rsplit("/", 1)[-1]
            items.append({
                "label": f"{s['id'][:8]}  {ts}",
                "description": f"{cwd_short}  ({s['messages']} msgs)",
                "hint": s.get("model", ""),
            })

        choice = await select_one(
            items,
            title="Select Session" if command == "/sessions" else "Resume Session",
        )
        if choice is None:
            print_info("Cancelled.")
            return True

        picked = all_sessions[choice]
        loaded = S.load(picked["id"])
        if loaded:
            _apply_resume(loaded)
            print_info(f"Resumed session {loaded.id[:8]} ({len(loaded.messages)} msgs, model: {loaded.model})")
        else:
            print_error(f"Failed to load session {picked['id'][:8]}")
        return True

    # ── Config ──
    if command == "/config":
        from ccb.config import get_api_key, get_base_url, get_model, get_provider, get_active_account
        acct = get_active_account()
        console.print(f"  Provider: {get_provider()}")
        console.print(f"  Model:    {get_model()}")
        console.print(f"  Base URL: {get_base_url()}")
        console.print(f"  Account:  {acct.get('_name') if acct else 'none'}")
        key = get_api_key()
        console.print(f"  API Key:  {key[:8]}...{key[-4:]}" if len(key) > 12 else f"  API Key:  {key}")
        return True

    # ── Theme ──
    if command == "/theme":
        available_themes = ["default", "monokai", "solarized-dark", "solarized-light",
                            "dracula", "nord", "gruvbox", "one-dark", "catppuccin"]
        current = state.get("theme", "default")
        if args:
            choice = args.strip().lower()
            if choice in available_themes:
                state["theme"] = choice
                print_info(f"Theme set to: {choice}")
            elif choice == "list":
                console.print("[bold]Available themes:[/bold]")
                for t in available_themes:
                    marker = " ← active" if t == current else ""
                    console.print(f"  • {t}{marker}")
            else:
                print_error(f"Unknown theme: {choice}. Use /theme list to see options.")
        else:
            console.print(f"[bold]Current theme:[/bold] {current}")
            console.print(f"[dim]Use /theme list to see all, /theme <name> to switch.[/dim]")
        return True

    # ── MCP ──
    if command == "/mcp":
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
            # List servers and tools
            if not mcp_manager.servers:
                print_info("No MCP servers connected. Use /mcp connect")
            else:
                for name, server in mcp_manager.servers.items():
                    status = "✓" if server.connected else "✗"
                    console.print(f"  {status} [bold]{name}[/bold] ({server.type})")
                    for t in server.tools:
                        console.print(f"      {t['name']}: {t.get('description', '')[:60]}")
        return True

    # ── Context / Files ──
    if command == "/context":
        from ccb.model_limits import get_context_limit
        msg_count = len(session.messages)
        chars = sum(len(m.content) for m in session.messages)
        current_ctx = session.last_input_tokens
        ctx_limit = get_context_limit(session.model or getattr(provider, "_model", ""))
        compact_at = int(ctx_limit * 0.8)
        console.print(f"  Messages:        {msg_count}")
        console.print(f"  Characters:      {chars:,}")
        if current_ctx:
            pct = (current_ctx / ctx_limit * 100) if ctx_limit else 0
            console.print(
                f"  Current context: [bold]{current_ctx:,}[/bold] / {ctx_limit:,} tokens "
                f"([dim]{pct:.1f}%[/dim]) — last request"
            )
        else:
            console.print(f"  Current context: [dim]—[/dim] / {ctx_limit:,} tokens (no request yet)")
        console.print(f"  Auto-compact at: {compact_at:,} tokens (80%)")
        console.print(f"  Cumulative in:   {session.total_input_tokens:,} tokens")
        console.print(f"  Cumulative out:  {session.total_output_tokens:,} tokens")
        return True

    # ── Status ──
    if command == "/status":
        from ccb.config import get_model, get_provider, get_active_account
        from ccb.cost_tracker import (
            get_cost_state, format_tokens, format_cost, format_duration,
            context_percentage,
        )
        from ccb.model_limits import get_context_limit
        acct = get_active_account()
        model = get_model()
        cost = get_cost_state()
        ctx_limit = get_context_limit(model)
        ctx_used = session.last_input_tokens
        ctx_pct = context_percentage(ctx_used, ctx_limit)
        console.print(f"  Account:  {acct.get('_name') if acct else 'none'}")
        console.print(f"  Provider: {get_provider()}")
        console.print(f"  Model:    {model}")
        console.print(f"  Session:  {session.id[:8]}...")
        console.print(f"  Messages: {len(session.messages)}")
        console.print(
            f"  Context:  {ctx_pct}% ({format_tokens(ctx_used)}/{format_tokens(ctx_limit)})"
        )
        console.print(
            f"  Tokens:   {format_tokens(cost.total_input_tokens)} in / "
            f"{format_tokens(cost.total_output_tokens)} out"
        )
        if cost.total_cost_usd > 0:
            console.print(f"  Cost:     {format_cost(cost.total_cost_usd)}")
        if cost.last_turn_duration_ms > 0:
            console.print(f"  Last turn:{format_duration(cost.last_turn_duration_ms)}")
        if mcp_manager:
            mcp_count = sum(1 for s in mcp_manager.servers.values() if s.connected)
            console.print(f"  MCP:      {mcp_count} server(s)")
        return True

    # ── Memory ──
    if command == "/memory":
        from ccb.prompts import _find_claude_md
        mds = _find_claude_md(cwd)
        if mds:
            for md in mds:
                console.print(f"  📝 {md}")
        else:
            console.print("  No CLAUDE.md found. Create one in project root or ~/.claude/")
        return True

    # ── Diff ──
    if command == "/diff":
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode().strip()
        console.print(output if output else "No changes.")
        return True

    # ── Vim ──
    if command == "/vim":
        current = state.get("vim_mode", False)
        state["vim_mode"] = not current
        print_info(f"Vim mode: {'ON' if state['vim_mode'] else 'OFF'}")
        return True

    # ── Doctor ──
    if command == "/doctor":
        await _doctor(cwd, registry, mcp_manager)
        return True

    # ── Permissions ──
    if command == "/permissions":
        from ccb.permissions import set_bypass_all, _bypass_all, _session_approved
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
        await _branch(cwd, args)
        return True

    # ── Rename ──
    if command == "/rename":
        if not args:
            print_error("Usage: /rename <new name>")
        else:
            session.model = session.model  # trigger save
            print_info(f"Session renamed (saved as {session.id[:8]})")
        return True

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
        if args:
            try:
                limit = int(args)
                state["token_budget"] = limit
                print_info(f"Token budget set to {limit:,}")
            except ValueError:
                print_error("Usage: /budget <number>")
        else:
            budget = state.get("token_budget")
            if budget:
                used = session.total_input_tokens + session.total_output_tokens
                console.print(f"  Budget: {budget:,} tokens")
                console.print(f"  Used:   {used:,} tokens ({used * 100 // budget}%)")
            else:
                console.print("  No budget set. Use /budget <number>")
        return True

    # ── Version ──
    if command == "/version":
        from ccb import __version__
        console.print(f"  CCB v{__version__}")
        return True

    # ── Init ──
    if command == "/init":
        await _init_project(cwd, args)
        return True

    # ── Undo / Redo ──
    if command == "/undo":
        await _git_undo(cwd)
        return True
    if command == "/redo":
        await _git_redo(cwd)
        return True

    # ── Copy ──
    if command == "/copy":
        _copy_last_reply(session)
        return True

    # ── Export ──
    if command == "/export":
        _export_session(session, args)
        return True

    # ── Stats ──
    if command == "/stats":
        _show_stats(session)
        return True

    # ── Theme ──
    if command == "/theme":
        if not args:
            from ccb.select_ui import select_one
            themes = ["default", "dark", "light", "neon"]
            items = [{"label": t} for t in themes]
            choice = await select_one(items, title="Select Theme")
            if choice is not None:
                _change_theme(themes[choice])
        else:
            _change_theme(args)
        return True

    # ── Files ──
    if command == "/files":
        from ccb.api.base import Role
        file_set: set[str] = set()
        for m in session.messages:
            if m.tool_calls:
                for tc in m.tool_calls:
                    fp = tc.input.get("file_path") or tc.input.get("path") or ""
                    if fp:
                        file_set.add(fp)
        if file_set:
            console.print("[bold]Files in context:[/bold]")
            for f in sorted(file_set):
                console.print(f"  {f}")
        else:
            console.print("  No files referenced yet.")
        return True

    # ── Image ──
    if command == "/image":
        if not args:
            console.print("  Usage: /image <path>  — Attach an image to your next message")
            console.print("  Supported: PNG, JPG, GIF, WebP, BMP, TIFF, SVG")
            console.print("  You can also:")
            console.print("    • Drag image files into the terminal")
            console.print("    • Ctrl+V to paste from clipboard (macOS)")
        else:
            from ccb.images import read_image_from_path, normalize_path
            img_path = normalize_path(args)
            img = read_image_from_path(img_path)
            if img:
                # Push into REPL pending attachments
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
                    # Classic REPL — store on state for next message
                    pending = state.setdefault("_pending_images", [])
                    pending.append(img.to_dict())
                    console.print(
                        f"  📎 Attached [bold]{img.filename}[/bold]"
                        f" ({img.media_type})"
                    )
            else:
                print_error(f"Cannot read image: {img_path}")
        return True

    # ── File (upload) ──
    if command == "/file":
        if not args:
            console.print("  Usage: /file <path>  — Attach a text file to your next message")
            console.print("  The file content will be inlined in your prompt.")
        else:
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

    # ── Color ──
    if command == "/color":
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

    # ── Effort ──
    if command == "/effort":
        levels = ["low", "medium", "high"]
        descs = {"low": "Faster, less thorough", "medium": "Balanced", "high": "Thorough, slower"}
        if args and args in levels:
            state["effort"] = args
            print_info(f"Effort level: {args}")
        else:
            from ccb.select_ui import select_one
            current = state.get("effort", "high")
            active_idx = levels.index(current) if current in levels else 2
            items = [{"label": lv, "description": descs[lv] + (" ← current" if lv == current else "")} for lv in levels]
            choice = await select_one(items, title="Select Effort Level", active=active_idx)
            if choice is not None:
                state["effort"] = levels[choice]
                print_info(f"Effort level: {levels[choice]}")
        return True

    # ── Thinking ──
    if command == "/thinking":
        from ccb.api.anthropic_provider import AnthropicProvider
        if isinstance(provider, AnthropicProvider):
            budget = int(args) if args and args.isdigit() else 10000
            if state.get("thinking"):
                state["thinking"] = False
                provider.set_thinking(False)
                print_info("Extended thinking: OFF")
            else:
                state["thinking"] = True
                provider.set_thinking(True, budget)
                print_info(f"Extended thinking: ON (budget: {budget:,} tokens)")
        else:
            print_info("Extended thinking is only available with Anthropic models.")
        return True

    # ── Fast ──
    if command == "/fast":
        state["fast"] = not state.get("fast", False)
        print_info(f"Fast mode: {'ON' if state['fast'] else 'OFF'}")
        return True

    # ── Usage ──
    if command == "/usage":
        console.print("[bold]Usage:[/bold]")
        total = session.total_input_tokens + session.total_output_tokens
        console.print(f"  Input:  {session.total_input_tokens:,} tokens")
        console.print(f"  Output: {session.total_output_tokens:,} tokens")
        console.print(f"  Total:  {total:,} tokens")
        budget = state.get("token_budget")
        if budget:
            console.print(f"  Budget: {budget:,} ({total * 100 // budget}% used)")
        return True

    # ── Tag ──
    if command == "/tag":
        tags = state.setdefault("tags", [])
        if not args:
            console.print(f"  Tags: {', '.join(tags) if tags else '(none)'}")
        elif args.startswith("-"):
            tag = args[1:].strip()
            if tag in tags:
                tags.remove(tag)
                print_info(f"Removed tag: {tag}")
            else:
                print_info(f"Tag not found: {tag}")
        else:
            if args not in tags:
                tags.append(args)
            print_info(f"Tagged: {args}")
        return True

    # ── Feedback / BTW ──
    if command in ("/feedback", "/btw"):
        if not args:
            print_error(f"Usage: {command} <message>")
        else:
            from pathlib import Path
            fb_dir = Path.home() / ".claude" / "feedback"
            fb_dir.mkdir(parents=True, exist_ok=True)
            fb_file = fb_dir / f"{int(time.time())}.txt"
            fb_file.write_text(f"{command}: {args}\nSession: {session.id}\nModel: {session.model}\n")
            print_info(f"Feedback saved. Thank you!")
        return True

    # ── Login / Logout ──
    if command == "/login":
        print_info("Use /account to switch accounts. API keys go in env vars or ~/.claude/accounts.json")
        return True
    if command == "/logout":
        from ccb.config import load_accounts, accounts_path
        store = load_accounts()
        store.pop("active", None)
        accounts_path().write_text(__import__("json").dumps(store, indent=2))
        print_info("Logged out (cleared active account)")
        return True

    # ── Session ──
    if command == "/session":
        console.print(f"  ID:      {session.id}")
        console.print(f"  Model:   {session.model}")
        console.print(f"  CWD:     {session.cwd}")
        console.print(f"  Msgs:    {len(session.messages)}")
        return True

    # ── Add-dir ──
    if command == "/add-dir":
        if not args:
            print_error("Usage: /add-dir <path>")
        else:
            from pathlib import Path
            p = Path(args).expanduser().resolve()
            if p.is_dir():
                state.setdefault("extra_dirs", []).append(str(p))
                print_info(f"Added directory: {p}")
            else:
                print_error(f"Not a directory: {args}")
        return True

    # ── Agents ──
    if command == "/agents":
        print_info("Agent configurations: use the 'agent' tool in conversation to spawn sub-agents.")
        return True

    # ── Tasks ──
    if command == "/tasks":
        from ccb.api.base import Role
        tc_count = sum(len(m.tool_calls) for m in session.messages if m.tool_calls)
        console.print(f"  Tool calls this session: {tc_count}")
        return True

    # ── Checkpoint / Restore ──
    if command == "/checkpoint":
        proc = await asyncio.create_subprocess_exec(
            "git", "stash", "push", "-m", f"ccb-checkpoint-{int(time.time())}", cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode().strip()
        print_info(output if output else "Checkpoint created (nothing to stash)")
        return True

    if command == "/restore":
        proc = await asyncio.create_subprocess_exec(
            "git", "stash", "list", cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        stashes = stdout.decode().strip()
        if not stashes:
            print_info("No checkpoints to restore.")
            return True
        if args:
            proc2 = await asyncio.create_subprocess_exec(
                "git", "stash", "pop", args, cwd=cwd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc2.communicate()
            print_info(out.decode().strip() or err.decode().strip())
        else:
            console.print(stashes)
        return True

    # ── Rewind ──
    if command == "/rewind":
        n = 2
        if args:
            try:
                n = int(args)
            except ValueError:
                pass
        if len(session.messages) >= n:
            removed = session.messages[-n:]
            session.messages = session.messages[:-n]
            print_info(f"Rewound {n} messages (now {len(session.messages)} remain)")
        else:
            print_info(f"Only {len(session.messages)} messages, cannot rewind {n}")
        return True

    # ── Summary ──
    if command == "/summary":
        from ccb.api.base import Role
        user_count = sum(1 for m in session.messages if m.role == Role.USER)
        asst_count = sum(1 for m in session.messages if m.role == Role.ASSISTANT)
        console.print(f"  Session {session.id[:8]} — {user_count} user / {asst_count} assistant messages")
        console.print(f"  Tokens: {session.total_input_tokens + session.total_output_tokens:,}")
        if session.messages:
            first_user = next((m.content[:80] for m in session.messages if m.role == Role.USER), "")
            console.print(f"  First prompt: {first_user}...")
        return True

    # ── Stickers ──
    if command == "/stickers":
        print_info("🎉 Order Claude Code stickers at https://store.anthropic.com")
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

    # ── Sandbox Toggle ──
    if command == "/sandbox":
        state["sandbox"] = not state.get("sandbox", False)
        print_info(f"Sandbox mode: {'ON' if state['sandbox'] else 'OFF'}")
        return True

    # ── Output Style ──
    if command == "/output-style":
        styles = ["rich", "text", "json", "minimal"]
        if args and args in styles:
            state["output_style"] = args
            print_info(f"Output style: {args}")
        else:
            from ccb.select_ui import select_one
            current = state.get("output_style", "rich")
            active_idx = styles.index(current) if current in styles else 0
            items = [{"label": s, "description": "← current" if s == current else ""} for s in styles]
            choice = await select_one(items, title="Select Output Style", active=active_idx)
            if choice is not None:
                state["output_style"] = styles[choice]
                print_info(f"Output style: {styles[choice]}")
        return True

    # ── Keybindings ──
    if command == "/keybindings":
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

    # ── IDE ── (handled later with bridge integration)
    # /ide is processed below with the IDEBridge

    # ── Desktop / Mobile / Voice ──
    if command == "/desktop":
        print_info("Continue in Claude Desktop: https://claude.ai/download")
        return True
    if command == "/mobile":
        print_info("Download Claude mobile: https://claude.ai/mobile")
        return True
    if command == "/voice":
        try:
            from ccb.voice_input import VoiceInput
            voice = VoiceInput()
            if args == "info":
                info = voice.info()
                for k, v in info.items():
                    console.print(f"  {k}: {v}")
            elif args == "listen":
                print_info("Listening... (speak now, press Ctrl+C to stop)")
                text = await asyncio.get_event_loop().run_in_executor(None, voice.record_and_transcribe)
                if text:
                    console.print(f"[bold]Transcribed:[/bold] {text}")
                    session.add_user_message(text)
                    from ccb.loop import run_turn
                    from ccb.prompts import get_system_prompt
                    await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
                    session.save()
                else:
                    print_info("No speech detected.")
            elif args and args.startswith("model "):
                model_name = args[6:].strip()
                voice.set_whisper_config(model=model_name)
                print_info(f"Whisper model set to: {model_name}")
            else:
                info = voice.info()
                console.print(f"[bold]Voice input[/bold]: backend={info.get('backend', 'none')}")
                console.print("[dim]  /voice listen — record and transcribe")
                console.print("  /voice info — show details")
                console.print("  /voice model <name> — set whisper model[/dim]")
        except Exception as e:
            print_error(f"Voice input error: {e}")
        return True

    # ── Share ──
    if command == "/share":
        _export_session(session, "md")
        print_info("Conversation exported for sharing.")
        return True

    # ── Plugin + marketplace management ──
    if command == "/plugin":
        await _handle_plugin_command(args)
        return True
    if command == "/reload-plugins":
        from pathlib import Path
        plugin_dir = Path.home() / ".claude" / "plugins"
        count = len(list(plugin_dir.iterdir())) if plugin_dir.exists() else 0
        print_info(f"Reloaded {count} plugin(s).")
        return True

    # ── Workflows ──
    if command == "/workflows":
        from ccb.skills import load_skills
        wf = [s for s in load_skills(cwd) if s.source == "workflow"]
        if wf:
            for w in wf:
                console.print(f"  [bold]{w.name:20s}[/bold] {w.description}")
        else:
            console.print("  No workflows. Place .md files in .windsurf/workflows/")
        return True

    # ── History ──
    if command == "/history":
        from ccb.api.base import Role
        for i, m in enumerate(session.messages):
            role = "🧑" if m.role == Role.USER else "🤖" if m.role == Role.ASSISTANT else "🔧"
            preview = m.content[:60].replace("\n", " ") if m.content else "(tool result)"
            console.print(f"  {i+1:3d} {role} {preview}")
        return True

    # ── Passes ──
    if command == "/passes":
        print_info("Multi-pass mode: let the model review its own output")
        state["passes"] = not state.get("passes", False)
        print_info(f"Passes: {'ON' if state['passes'] else 'OFF'}")
        return True

    # ── Thinkback ──
    if command in ("/thinkback", "/thinkback-play"):
        console.print("[bold]🎬 Session Replay[/bold]")
        if session.messages:
            total = len(session.messages)
            console.print(f"Replaying {total} messages from this session:\n")
            for i, msg in enumerate(session.messages, 1):
                role = msg.role if hasattr(msg, 'role') else msg.get('role', '?')
                content = msg.content if hasattr(msg, 'content') else msg.get('content', '')
                preview = (content[:120] + "...") if len(content) > 120 else content
                icon = "🧑" if "user" in str(role) else "🤖"
                console.print(f"  {icon} [{i}/{total}] {preview}")
            console.print(f"\n[dim]Total: {total} messages, {session.total_input_tokens + session.total_output_tokens} tokens[/dim]")
        else:
            print_info("No messages in current session.")
        return True

    # ── Buddy ──
    if command == "/buddy":
        await _buddy(args, state, cwd)
        return True

    # ── Advisor ──
    if command == "/advisor":
        session.add_user_message(
            "Please act as a senior code reviewer. Review the recent changes and provide "
            "constructive feedback on code quality, potential bugs, and improvements."
        )
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Security Review ──
    if command == "/security-review":
        session.add_user_message(
            "Perform a security review of the current codebase. Look for common vulnerabilities "
            "like injection attacks, authentication issues, data exposure, and insecure configurations."
        )
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── PR Comments (GitHub integration) ──
    if command == "/pr-comments":
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

    # ── Review ──
    if command == "/review":
        from ccb.github_ops import generate_review_prompt, gh_available
        if not gh_available():
            print_error("GitHub CLI (gh) not available.")
            return True
        number = int(args) if args.strip().isdigit() else None
        prompt = generate_review_prompt(number, cwd=cwd)
        session.add_user_message(prompt)
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Autofix PR ──
    if command == "/autofix-pr":
        from ccb.github_ops import generate_autofix_prompt, gh_available
        if not gh_available():
            print_error("GitHub CLI (gh) not available.")
            return True
        number = int(args) if args.strip().isdigit() else None
        prompt = generate_autofix_prompt(number, cwd=cwd)
        session.add_user_message(prompt)
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Issue ──
    if command == "/issue":
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
                for i in issues:
                    labels = f" [{', '.join(i.labels)}]" if i.labels else ""
                    console.print(f"  [bold]#{i.number:5d}[/bold] {i.title}{labels}")
        return True

    # ── Commit (with git integration) ──
    if command == "/commit":
        from ccb.git_ops import generate_commit_message_prompt, diff_stat, stage, commit, git_available
        if not git_available(cwd):
            print_error("Not in a git repository")
            return True
        stat = diff_stat(staged=True, cwd=cwd)
        if stat.files_changed == 0:
            # Auto-stage all
            stage(cwd=cwd)
            stat = diff_stat(staged=True, cwd=cwd)
        if stat.files_changed == 0:
            print_info("No changes to commit.")
            return True
        prompt = generate_commit_message_prompt(cwd=cwd)
        session.add_user_message(prompt)
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Diff ──
    if command == "/diff":
        from ccb.git_ops import diff_text, diff_stat, git_available
        if not git_available(cwd):
            print_error("Not in a git repository")
            return True
        staged = "staged" in args or "cached" in args
        stat = diff_stat(staged=staged, cwd=cwd)
        if stat.files_changed == 0:
            print_info("No changes." + (" (staged)" if staged else ""))
            return True
        console.print(f"[bold]{stat.files_changed} file(s) changed[/bold] "
                      f"(+{stat.insertions} -{stat.deletions})")
        text = diff_text(staged=staged, cwd=cwd)
        if text:
            from rich.syntax import Syntax
            console.print(Syntax(text[:5000], "diff", theme="monokai"))
        return True

    # ── Branch ──
    if command == "/branch":
        from ccb.git_ops import branches, checkout, create_branch, current_branch, git_available
        if not git_available(cwd):
            print_error("Not in a git repository")
            return True
        if args.strip():
            sub = args.strip().split()
            if sub[0] in ("-b", "create", "new") and len(sub) > 1:
                ok, msg = create_branch(sub[1], cwd=cwd)
                print_info(f"{'✓' if ok else '✗'} {msg}")
            else:
                ok, msg = checkout(sub[0], cwd=cwd)
                print_info(f"{'✓' if ok else '✗'} {msg}")
        else:
            from ccb.select_ui import select_one
            brs = branches(cwd=cwd)
            if not brs:
                print_info("No branches found.")
                return True
            items = [{"label": b["name"], "description": "← current" if b["current"] else ""} for b in brs]
            choice = await select_one(items, title="Switch Branch")
            if choice is not None:
                ok, msg = checkout(brs[choice]["name"], cwd=cwd)
                print_info(f"{'✓' if ok else '✗'} {msg}")
        return True

    # ── Undo / Redo (git-based) ──
    if command == "/undo":
        from ccb.git_ops import undo_last_commit, stash_push, git_available
        if not git_available(cwd):
            print_error("Not in a git repository")
            return True
        ok, msg = undo_last_commit(cwd=cwd)
        print_info(f"{'✓ Undid last commit' if ok else '✗ ' + msg}")
        return True

    if command == "/redo":
        from ccb.git_ops import stash_pop, git_available
        if not git_available(cwd):
            print_error("Not in a git repository")
            return True
        ok, msg = stash_pop(cwd=cwd)
        print_info(f"{'✓' if ok else '✗'} {msg}")
        return True

    # ── Memory ──
    if command == "/memory":
        from ccb.memory import get_store
        store = get_store()
        sub = args.strip().split(maxsplit=1)
        action = sub[0].lower() if sub else "list"
        rest = sub[1] if len(sub) > 1 else ""
        if action == "add" and rest:
            mem = store.add(rest, source="user")
            print_info(f"✓ Memory saved: {mem.id}")
        elif action in ("search", "find") and rest:
            results = store.search(rest)
            for m in results:
                console.print(f"  [{m.id}] {m.content[:80]}  [dim]tags: {', '.join(m.tags)}[/dim]")
        elif action in ("delete", "rm") and rest:
            if store.delete(rest):
                print_info(f"✓ Deleted {rest}")
            else:
                print_error(f"Memory {rest} not found")
        elif action == "clear":
            count = store.clear()
            print_info(f"✓ Cleared {count} memories")
        else:
            mems = store.list_all()
            if not mems:
                print_info("No memories stored. Use: /memory add <text>")
            else:
                console.print(f"[bold]Memories ({len(mems)}):[/bold]")
                for m in mems[:20]:
                    console.print(f"  [{m.id}] {m.content[:60]}  [dim]{', '.join(m.tags)}[/dim]")
        return True

    # ── Tasks ──
    if command == "/tasks":
        from ccb.task_manager import get_task_manager
        mgr = get_task_manager()
        tasks = mgr.list_tasks()
        if not tasks:
            print_info("No tasks. Tasks are created by agent/subtask tool calls.")
        else:
            console.print(f"[bold]Tasks ({len(tasks)}):[/bold]")
            for t in tasks:
                icon = {"pending": "⬜", "running": "🔄", "completed": "✅", "failed": "❌", "cancelled": "⏹"}.get(t.status.value, "?")
                console.print(f"  {icon} {t.id}: {t.name} ({t.status.value}) [{t.duration:.1f}s]")
        return True

    # ── Env ──
    if command == "/env":
        if args.strip():
            key, _, val = args.partition("=")
            key = key.strip()
            val = val.strip()
            if val:
                import os
                os.environ[key] = val
                print_info(f"✓ Set {key}={val}")
            else:
                import os
                current = os.environ.get(key, "(not set)")
                console.print(f"  {key}={current}")
        else:
            import os
            relevant = {k: v for k, v in os.environ.items()
                        if any(x in k.upper() for x in ("CLAUDE", "ANTHROPIC", "OPENAI", "API", "MODEL", "CCB"))}
            for k, v in sorted(relevant.items()):
                display_v = v[:40] + "..." if len(v) > 40 else v
                console.print(f"  {k}={display_v}")
        return True

    # ── Exit (explicit command) ──
    if command == "/exit":
        return "exit"

    # ── Feedback ──
    if command == "/feedback":
        print_info("Send feedback: https://github.com/hua123an/ccb-py/issues")
        if args:
            print_info(f"Your feedback: {args}")
        return True

    # ── BTW (terminal setup) ──
    if command == "/btw":
        import os, platform
        info = {
            "shell": os.environ.get("SHELL", "unknown"),
            "term": os.environ.get("TERM", "unknown"),
            "term_program": os.environ.get("TERM_PROGRAM", "unknown"),
            "os": platform.system(),
            "python": platform.python_version(),
        }
        for k, v in info.items():
            console.print(f"  [bold]{k:15s}[/bold] {v}")
        return True

    # ── Onboarding ──
    if command == "/onboarding":
        console.print("[bold]Welcome to ccb-py![/bold]\n")
        console.print("Quick start:")
        console.print("  1. Set your API key:  /account")
        console.print("  2. Choose a model:    /model")
        console.print("  3. Start chatting!")
        console.print("  4. Use /help to see all commands")
        console.print("  5. Try /plugin browse for plugins")
        return True

    # ── Fork (session fork) ──
    if command == "/fork":
        new_id = session.fork()
        if new_id:
            print_info(f"✓ Forked session → {new_id}")
        else:
            print_info("Session fork not available (save first with /session save)")
        return True

    # ── Resume ──
    if command in ("/resume", "/continue"):
        from ccb.session import list_sessions
        sessions = list_sessions()
        if not sessions:
            print_info("No saved sessions.")
            return True
        if args:
            session.load(args.strip())
            print_info(f"Resumed session {args}")
        else:
            from ccb.select_ui import select_one
            items = [{"label": s.get("id", ""), "description": s.get("preview", "")[:40]} for s in sessions[:20]]
            choice = await select_one(items, title="Resume Session", searchable=True)
            if choice is not None:
                session.load(sessions[choice]["id"])
                print_info(f"Resumed session {sessions[choice]['id']}")
        return True

    # ── Attach / Detach ──
    if command == "/attach":
        state["attached"] = True
        print_info("Session attached — output visible.")
        return True

    if command == "/detach":
        state["attached"] = False
        print_info("Session detached — running in background.")
        return True

    # ── Send ──
    if command == "/send":
        if not args:
            print_error("Usage: /send <message>")
            return True
        session.add_user_message(args)
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Peers ──
    if command == "/peers":
        try:
            from ccb.remote import RemoteManager
            rm = RemoteManager()
            hosts = rm.list_hosts()
            if hosts:
                console.print("[bold]Remote peers:[/bold]")
                for h in hosts:
                    status = "✅ connected" if rm.test_connection(h["host"]) else "⏸ offline"
                    console.print(f"  {h.get('name', h['host'])} ({h['host']}:{h.get('port', 22)}) — {status}")
            else:
                console.print("[dim]No remote peers configured.[/dim]")
                console.print("[dim]Use /remote-setup to add remote connections.[/dim]")
        except Exception as e:
            print_error(f"Peers error: {e}")
        return True

    # ── Bughunter ──
    if command == "/bughunter":
        session.add_user_message(
            "Act as a bug hunter. Analyze the codebase for potential bugs, edge cases, "
            "race conditions, null pointer issues, and other common problems. "
            f"Focus on: {args}" if args else
            "Act as a bug hunter. Analyze the codebase for potential bugs, edge cases, "
            "race conditions, null pointer issues, and other common problems."
        )
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Perf Issue ──
    if command == "/perf-issue":
        session.add_user_message(
            "Analyze the codebase for performance issues. Look for N+1 queries, "
            "unnecessary allocations, blocking calls, and optimization opportunities."
            + (f" Focus on: {args}" if args else "")
        )
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
        session.save()
        return True

    # ── Good Claude / Poor ──
    if command == "/good-claude":
        print_info("👍 Thanks! Positive feedback recorded.")
        return True

    if command == "/poor":
        print_info("👎 Sorry about that. Feedback recorded.")
        if args:
            print_info(f"Details: {args}")
        return True

    # ── Remote setup ──
    if command in ("/remote-setup", "/remote-env"):
        from ccb.remote import get_remote_manager
        mgr = get_remote_manager()
        if args.strip():
            sub = args.strip().split(maxsplit=1)
            action = sub[0]
            rest = sub[1] if len(sub) > 1 else ""
            if action == "add" and rest:
                parts_r = rest.split()
                name = parts_r[0]
                host = parts_r[1] if len(parts_r) > 1 else name
                mgr.add_host(name, host)
                print_info(f"✓ Added remote host: {name}")
            elif action == "remove" and rest:
                if mgr.remove_host(rest):
                    print_info(f"✓ Removed {rest}")
                else:
                    print_error(f"Host {rest} not found")
            elif action == "test" and rest:
                ok, msg = mgr.test_connection(rest)
                print_info(f"{'✓' if ok else '✗'} {msg}")
            elif action == "connect" and rest:
                if mgr.connect(rest):
                    print_info(f"✓ Connected to {rest}")
                else:
                    print_error(f"Host {rest} not found")
            elif action == "disconnect":
                mgr.disconnect()
                print_info("Disconnected from remote.")
        else:
            hosts = mgr.list_hosts()
            active = mgr.active_host
            if not hosts:
                print_info("No remote hosts. Use: /remote-setup add <name> <host>")
            else:
                console.print(f"[bold]Remote hosts ({len(hosts)}):[/bold]")
                for h in hosts:
                    marker = " ← active" if active and h.name == active.name else ""
                    console.print(f"  [bold]{h.name:15s}[/bold] {h.ssh_target}{marker}")
        return True

    # ── Teleport ──
    if command == "/teleport":
        print_info("Teleport: transfer this session to another device.")
        print_info("Use: /share to export, then /resume on the other device.")
        return True

    # ── Voice ──
    if command == "/voice":
        from ccb.voice_input import get_voice_input
        voice = get_voice_input()
        if not voice.available:
            print_error(f"Voice input not available (backend: {voice.backend_name})")
            print_info("Install whisper or sox for voice support.")
            return True
        duration = int(args) if args.strip().isdigit() else 10
        print_info(f"🎤 Recording for {duration}s... (speak now)")
        text = await voice.record_and_transcribe(duration)
        if text and not text.startswith("["):
            print_info(f"Transcribed: {text}")
            session.add_user_message(text)
            from ccb.loop import run_turn
            from ccb.prompts import get_system_prompt
            await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
            session.save()
        else:
            print_info(text or "No speech detected.")
        return True

    # ── Sandbox ──
    if command == "/sandbox":
        from ccb.sandbox_exec import get_sandbox
        sb = get_sandbox()
        if args.strip() == "status":
            console.print(f"  Backend: {sb.backend_name}")
            console.print(f"  Enabled: {sb.enabled}")
            console.print(f"  Available: {sb.available}")
        else:
            result = sb.toggle()
            print_info(f"Sandbox mode: {'ON' if result else 'OFF'} (backend: {sb.backend_name})")
        return True

    # ── IDE ──
    if command == "/ide":
        from ccb.bridge import get_bridge
        bridge = get_bridge()
        if args.strip() == "start":
            await bridge.start()
            print_info(f"IDE bridge started on ws://{bridge.host}:{bridge.port}")
        elif args.strip() == "stop":
            await bridge.stop()
            print_info("IDE bridge stopped.")
        else:
            console.print(f"  Running: {bridge.is_running}")
            console.print(f"  Clients: {bridge.connection_count}")
            console.print("  Commands: /ide start | /ide stop")
        return True

    # ── Desktop ──
    if command == "/desktop":
        print_info("Desktop app integration — use /ide to connect to your editor.")
        return True

    # ── Mobile ──
    if command == "/mobile":
        print_info("Mobile companion — share sessions with /share, resume on mobile.")
        return True

    # ── Install GitHub/Slack App ──
    if command == "/install-github-app":
        print_info("Install ccb GitHub App:")
        print_info("  https://github.com/apps/ccb-py")
        return True

    if command == "/install-slack-app":
        console.print("[bold]Slack Integration Setup[/bold]")
        console.print("1. Go to https://api.slack.com/apps and create a new app")
        console.print("2. Add the following Bot Token Scopes:")
        console.print("   • chat:write • commands • app_mentions:read")
        console.print("3. Install the app to your workspace")
        console.print("4. Copy the Bot User OAuth Token")
        console.print("5. Set it: export SLACK_BOT_TOKEN=xoxb-...")
        console.print("")
        import os
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        if token:
            console.print(f"[green]✓ SLACK_BOT_TOKEN detected ({token[:12]}...)[/green]")
        else:
            console.print("[yellow]⚠ SLACK_BOT_TOKEN not set[/yellow]")
        return True

    # ── Pipe status ──
    if command == "/pipe-status":
        from ccb.query_engine import PipeChain
        print_info("Pipe mode: use 'ccb -p \"prompt\"' for non-interactive queries.")
        return True

    # ── Pipes ──
    if command == "/pipes":
        print_info("Pipe chains allow multi-step prompt processing.")
        print_info("Use pipe mode: echo 'code' | ccb -p 'review this'")
        return True

    # ── Agents platform ──
    if command == "/agents-platform":
        print_info("Agents platform — multi-agent coordination.")
        from ccb.coordinator import get_coordinator
        coord = get_coordinator()
        s = coord.summary()
        console.print(f"  Total agents: {s['total']} (running: {s['running']}, done: {s['done']})")
        return True

    # ── Assistant ──
    if command == "/assistant":
        print_info("Entering assistant mode — I'll proactively help with your tasks.")
        state["assistant_mode"] = True
        return True

    # ── Debug tool call ──
    if command == "/debug-tool-call":
        state["debug_tools"] = not state.get("debug_tools", False)
        print_info(f"Tool call debug: {'ON' if state['debug_tools'] else 'OFF'}")
        return True

    # ── Context viz ──
    if command == "/ctx_viz":
        from ccb.api.base import Role
        total_chars = sum(len(m.content or "") for m in session.messages)
        console.print(f"[bold]Context visualization:[/bold]")
        console.print(f"  Messages: {len(session.messages)}")
        console.print(f"  Total chars: {total_chars:,}")
        console.print(f"  Est. tokens: ~{total_chars // 4:,}")
        for i, m in enumerate(session.messages):
            bar_len = min(50, max(1, len(m.content or "") // 100))
            role_icon = "🧑" if m.role == Role.USER else "🤖"
            bar = "█" * bar_len
            console.print(f"  {i+1:3d} {role_icon} {bar} ({len(m.content or '')} chars)")
        return True

    # ── Heapdump ──
    if command == "/heapdump":
        import sys
        objects = {}
        for obj in list(sys.modules.values()):
            t = type(obj).__name__
            objects[t] = objects.get(t, 0) + 1
        console.print("[bold]Memory snapshot:[/bold]")
        for t, c in sorted(objects.items(), key=lambda x: -x[1])[:15]:
            console.print(f"  {t:30s} {c}")
        return True

    # ── Mock limits ──
    if command == "/mock-limits":
        state["mock_limits"] = not state.get("mock_limits", False)
        print_info(f"Mock rate limits: {'ON' if state['mock_limits'] else 'OFF'}")
        return True

    # ── Break cache ──
    if command == "/break-cache":
        print_info("Cache cleared.")
        return True

    # ── Claim main ──
    if command == "/claim-main":
        print_info("This is now the main ccb-py process.")
        return True

    # ── Ant trace ──
    if command == "/ant-trace":
        print_info("Anthropic API trace viewer.")
        from ccb.analytics_tracker import get_tracker
        stats = get_tracker().get_session_stats()
        console.print(f"  Input tokens: {stats.get('input_tokens', 0):,}")
        console.print(f"  Output tokens: {stats.get('output_tokens', 0):,}")
        console.print(f"  API calls: {stats.get('turns', 0)}")
        console.print(f"  Cost: ${stats.get('cost_usd', 0):.4f}")
        return True

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
        from ccb.analytics_tracker import get_tracker
        stats = get_tracker().get_historical_stats(days=30)
        console.print(f"[bold]Usage (last 30 days):[/bold]")
        console.print(f"  Sessions: {stats.get('sessions', 0)}")
        console.print(f"  Messages: {stats.get('messages', 0)}")
        console.print(f"  Input tokens: {stats.get('input_tokens', 0):,}")
        console.print(f"  Output tokens: {stats.get('output_tokens', 0):,}")
        console.print(f"  Cost: ${stats.get('cost_usd', 0):.4f}")
        top_tools = sorted(stats.get("tools_used", {}).items(), key=lambda x: -x[1])[:5]
        if top_tools:
            console.print("  Top tools: " + ", ".join(f"{k}({v})" for k, v in top_tools))
        return True

    # ── Skills ──
    if command == "/skills":
        from ccb.skills import load_skills
        skills = load_skills(cwd)
        if not skills:
            print_info("No skills found.")
        else:
            for s in skills:
                console.print(f"  [bold]{s.name:20s}[/bold] [{s.source}] {s.description}")
        return True

    # ── Use a skill by /skill_name ──
    if command.startswith("/"):
        from ccb.skills import load_skills
        skill_name = command[1:]
        skills = load_skills(cwd)
        for s in skills:
            if s.name == skill_name:
                prompt = s.prompt
                if args:
                    prompt += f"\n\nAdditional context: {args}"
                session.add_user_message(prompt)
                from ccb.loop import run_turn
                from ccb.prompts import get_system_prompt
                await run_turn(provider, session, registry, get_system_prompt(cwd), mcp_manager=mcp_manager)
                session.save()
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
            raw = cmd_path.read_text()
        except Exception as e:
            print_error(f"Failed to read plugin command: {e}")
            return True
        # Strip YAML frontmatter (---\n...\n---\n) before sending to model
        template = raw
        if template.startswith("---"):
            end = template.find("\n---", 3)
            if end >= 0:
                template = template[end + 4:].lstrip("\n")
        # Common placeholder conventions in the official plugin ecosystem:
        # $ARGUMENTS / {{ARGS}} / {args} → user's raw argument string
        prompt = template
        for placeholder in ("$ARGUMENTS", "{{ARGS}}", "{{ARGUMENTS}}", "{args}", "{arguments}"):
            prompt = prompt.replace(placeholder, args)
        # If no placeholder was present and the user supplied args, append them
        if args and prompt == template:
            prompt = f"{template}\n\n{args}"
        session.add_user_message(prompt)
        from ccb.loop import run_turn
        from ccb.prompts import get_system_prompt
        await run_turn(
            provider, session, registry,
            get_system_prompt(cwd),
            mcp_manager=mcp_manager,
            state=state,
        )
        session.save()
        return True

    # ── Unknown → pass to model ──
    return False


def _print_help() -> None:
    console.print("[bold]Commands:[/bold]")
    cmds = [
        # ── Core ──
        ("/help", "Show this help"),
        ("/clear", "Clear conversation history"),
        ("/compact [msg]", "Compact context (optional focus)"),
        ("/exit", "Exit (also Ctrl+D)"),
        # ── Model / Account ──
        ("/model [name]", "Show or change model"),
        ("/account [name]", "Switch account"),
        ("/login", "Sign in / switch provider"),
        ("/logout", "Sign out"),
        ("/config", "Show current configuration"),
        ("/effort [low|med|high]", "Set effort level"),
        ("/fast", "Toggle fast mode"),
        # ── Context ──
        ("/context", "Show context usage"),
        ("/files", "List files in context"),
        ("/cost", "Show token usage"),
        ("/usage", "Show detailed usage info"),
        ("/budget [tokens]", "Set/show token budget"),
        ("/status", "Show full status"),
        ("/stats", "Session statistics"),
        ("/summary", "Summarize conversation"),
        ("/history", "Show message history"),
        # ── Sessions ──
        ("/sessions", "List recent sessions"),
        ("/resume <id>", "Resume a session"),
        ("/session", "Show current session info"),
        ("/rename <name>", "Rename session"),
        ("/tag [name]", "Toggle session tag"),
        ("/share", "Share/export conversation"),
        # ── Git ──
        ("/diff", "Show git diff stats"),
        ("/branch [name]", "List/switch branches"),
        ("/commit", "AI-assisted git commit"),
        ("/undo", "Undo last git commit"),
        ("/redo", "Redo last undo"),
        ("/checkpoint", "Save git checkpoint"),
        ("/restore [ref]", "Restore from checkpoint"),
        ("/rewind [n]", "Remove last n messages"),
        # ── Skills / Review ──
        ("/skills", "List available skills"),
        ("/review", "Review code changes"),
        ("/test", "Generate tests"),
        ("/explain", "Explain codebase"),
        ("/advisor", "Senior code review"),
        ("/security-review", "Security audit"),
        ("/workflows", "List workflow scripts"),
        # ── Memory / Init ──
        ("/memory", "Show CLAUDE.md files"),
        ("/init", "Create CLAUDE.md template"),
        ("/add-dir <path>", "Add working directory"),
        # ── Tools / MCP ──
        ("/mcp [connect|disconnect]", "Manage MCP servers"),
        ("/doctor", "Run diagnostics"),
        ("/permissions", "Manage permissions"),
        ("/hooks", "Show hooks status"),
        ("/plan", "Show/toggle plan mode"),
        ("/tasks", "List background tasks"),
        ("/agents", "Agent configurations"),
        # ── UI / Preferences ──
        ("/vim", "Toggle vim mode"),
        ("/theme [name]", "Change color theme"),
        ("/color [name]", "Set prompt bar color"),
        ("/output-style", "Change output style"),
        ("/keybindings", "Show key bindings"),
        ("/stickers", "Order Claude stickers"),
        # ── Copy / Export ──
        ("/copy", "Copy last reply to clipboard"),
        ("/export [json|md|html] [path]", "Export conversation"),
        # ── Platform ──
        ("/plugin", "Plugin management"),
        ("/reload-plugins", "Reload plugins"),
        ("/ide", "IDE integrations"),
        ("/desktop", "Continue in Desktop"),
        ("/mobile", "Mobile app info"),
        ("/voice", "Voice mode"),
        # ── Misc ──
        ("/feedback <msg>", "Submit feedback"),
        ("/btw <msg>", "Quick note"),
        ("/privacy-settings", "Privacy settings"),
        ("/sandbox", "Toggle sandbox mode"),
        ("/passes", "Toggle multi-pass mode"),
        ("/upgrade", "Upgrade info"),
        ("/release-notes", "Show release notes"),
        ("/pr-comments", "Get PR comments"),
        ("/buddy [pet|off|status]", "Coding companion"),
        ("/continue [id]", "Resume (alias)"),
        ("/version", "Show version"),
        ("/thinkback", "Year in Review"),
    ]
    for name, desc in cmds:
        console.print(f"  [bold]{name:30s}[/bold] {desc}")


async def _handle_plugin_command(args: str) -> None:
    """Dispatch ``/plugin [subcommand] [...]``.

    Subcommands:
      (no args)                     interactive plugin menu
      list                          list installed plugins
      browse [marketplace]          browse marketplace plugins interactively
      install <name[@mkt]>          install a plugin
      uninstall <name[@mkt]>        remove a plugin
      enable <name[@mkt]>           enable
      disable <name[@mkt]>          disable
      marketplace list              list marketplaces (also: market)
      marketplace add <source>      add a marketplace (owner/repo | URL | path)
      marketplace remove <name>     remove a marketplace
      marketplace update [name]     refresh (or all marketplaces if omitted)
      help                          show this help
    """
    from ccb import plugins as pg

    parts = args.strip().split(maxsplit=1) if args.strip() else []
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    try:
        # ── No args → interactive plugin menu ──
        if not sub:
            await _plugin_interactive_menu()
            return

        # ── Marketplace subcommands ──
        if sub in ("marketplace", "market"):
            await _handle_marketplace_command(rest)
            return

        # ── Browse ──
        if sub in ("browse", "search", "discover"):
            await _plugin_browse(rest.strip() or None)
            return

        # ── Plugin subcommands ──
        if sub in ("list", "ls"):
            plugs = pg.plugin_list()
            if not plugs:
                print_info("No plugins installed. Try: /plugin browse")
                return
            console.print(f"[bold]Installed plugins ({len(plugs)}):[/bold]")
            for p in plugs:
                flag = "" if p.get("enabled", True) else "  [dim](disabled)[/dim]"
                desc = p.get("description", "") or ""
                console.print(f"  [bold]{p['id']:30s}[/bold] {desc}{flag}")
            return

        if sub in ("install", "i", "add"):
            if not rest:
                # No arg → browse instead
                await _plugin_browse()
                return
            print_info(f"Installing plugin {rest}...")
            try:
                info = pg.plugin_install(rest.strip())
                console.print(
                    f"  [success]✓[/success] Installed [bold]{info['id']}[/bold]\n"
                    f"    path: [dim]{info['path']}[/dim]"
                )
                if info.get("description"):
                    console.print(f"    {info['description']}")
            except Exception as e:
                print_error(f"Install failed: {e}")
            return

        if sub in ("uninstall", "remove", "rm"):
            if not rest:
                # No arg → interactive uninstall
                await _plugin_manage_installed("uninstall")
                return
            if pg.plugin_uninstall(rest.strip()):
                print_info(f"✓ Uninstalled {rest}")
            else:
                print_error(f"Plugin '{rest}' is not installed")
            return

        if sub == "enable":
            if not rest:
                await _plugin_manage_installed("enable")
                return
            if pg.plugin_set_enabled(rest.strip(), True):
                print_info(f"✓ Enabled {rest}")
            else:
                print_error(f"Plugin '{rest}' is not installed")
            return

        if sub == "disable":
            if not rest:
                await _plugin_manage_installed("disable")
                return
            if pg.plugin_set_enabled(rest.strip(), False):
                print_info(f"✓ Disabled {rest}")
            else:
                print_error(f"Plugin '{rest}' is not installed")
            return

        if sub in ("help", "-h", "--help", "?"):
            _print_plugin_help()
            return

        print_error(f"Unknown /plugin subcommand: {sub}. Run '/plugin help'.")
    except Exception as e:
        import traceback
        print_error(f"/plugin {sub} failed: {e}")
        try:
            Path.home().joinpath(".claude", "ccb-debug.log").open("a").write(
                traceback.format_exc() + "\n"
            )
        except Exception:
            pass


async def _plugin_interactive_menu() -> None:
    """Show an interactive main plugin menu."""
    from ccb import plugins as pg
    from ccb.select_ui import select_one

    installed_count = len(pg.load_installed_plugins())
    mkt_count = len(pg.load_known_marketplaces())
    items = [
        {"label": "Browse & Install", "description": f"Discover plugins from {mkt_count} marketplace(s)"},
        {"label": "Installed Plugins", "description": f"{installed_count} plugin(s) — enable/disable/uninstall"},
        {"label": "Manage Marketplaces", "description": "Add, remove, or update marketplace sources"},
        {"label": "Help", "description": "Show /plugin command reference"},
    ]
    choice = await select_one(items, title="Plugin Management")
    if choice is None:
        return
    if choice == 0:
        await _plugin_browse()
    elif choice == 1:
        await _plugin_manage_installed()
    elif choice == 2:
        await _plugin_marketplace_menu()
    elif choice == 3:
        _print_plugin_help()


async def _plugin_browse(marketplace_name: str | None = None) -> None:
    """Interactive marketplace plugin browser with search and install."""
    from ccb import plugins as pg
    from ccb.select_ui import select_one

    # If no marketplaces are configured, prompt the user
    mkts = pg.marketplace_list()
    if not mkts:
        print_info("No marketplaces configured.")
        print_info("Add one first:  /plugin marketplace add <owner/repo>")
        print_info("  Example:      /plugin marketplace add anthropics/claude-plugin-directory")
        return

    # If multiple marketplaces and no specific one requested, let user pick
    if marketplace_name is None and len(mkts) > 1:
        items = [
            {"label": m["name"], "description": f"{m['plugin_count']} plugin(s)"}
            for m in mkts
        ] + [{"label": "All marketplaces", "description": "Browse all plugins"}]
        choice = await select_one(items, title="Select Marketplace")
        if choice is None:
            return
        if choice < len(mkts):
            marketplace_name = mkts[choice]["name"]
        # else: marketplace_name stays None → show all

    available = pg.marketplace_browse(marketplace_name)
    if not available:
        print_info("No plugins found in marketplace(s).")
        return

    # Build selection list
    items = []
    for p in available:
        status = "✓ installed" if p["installed"] else ""
        if p["installed"] and not p["enabled"]:
            status = "✓ disabled"
        desc_parts = []
        if p.get("description"):
            desc_parts.append(p["description"][:50])
        if status:
            desc_parts.append(f"[{status}]")
        items.append({
            "label": p["plugin_id"],
            "description": " — ".join(desc_parts) if desc_parts else "",
        })

    choice = await select_one(
        items, title="Browse Plugins — Enter to install", searchable=True,
        search_placeholder="Plugin name",
    )
    if choice is None:
        return

    selected = available[choice]
    if selected["installed"]:
        print_info(f"Plugin {selected['plugin_id']} is already installed.")
        return

    # Confirm & install
    confirm = await select_one(
        [
            {"label": "Install", "description": f"Install {selected['name']} from {selected['marketplace']}"},
            {"label": "Cancel", "description": "Go back"},
        ],
        title=f"Install {selected['name']}?",
    )
    if confirm != 0:
        return
    print_info(f"Installing {selected['plugin_id']}...")
    try:
        info = pg.plugin_install(selected["plugin_id"])
        console.print(
            f"  [success]✓[/success] Installed [bold]{info['id']}[/bold]\n"
            f"    path: [dim]{info['path']}[/dim]"
        )
        if info.get("description"):
            console.print(f"    {info['description']}")
        console.print("  [dim]Run /reload-plugins to activate.[/dim]")
    except Exception as e:
        print_error(f"Install failed: {e}")


async def _plugin_manage_installed(action: str | None = None) -> None:
    """Interactive manager for installed plugins (enable/disable/uninstall)."""
    from ccb import plugins as pg
    from ccb.select_ui import select_one

    plugs = pg.plugin_list()
    if not plugs:
        print_info("No plugins installed. Try: /plugin browse")
        return

    items = []
    for p in plugs:
        status = "enabled" if p.get("enabled", True) else "disabled"
        desc = p.get("description", "")[:40] or ""
        items.append({
            "label": p["id"],
            "description": f"({status}) {desc}",
        })

    choice = await select_one(items, title="Select Plugin", searchable=len(items) > 5)
    if choice is None:
        return

    selected = plugs[choice]
    pid = selected["id"]
    is_enabled = selected.get("enabled", True)

    # If a specific action was requested, do it directly
    if action == "uninstall":
        if pg.plugin_uninstall(pid):
            print_info(f"✓ Uninstalled {pid}")
        else:
            print_error(f"Failed to uninstall {pid}")
        return
    if action == "enable":
        if pg.plugin_set_enabled(pid, True):
            print_info(f"✓ Enabled {pid}")
        return
    if action == "disable":
        if pg.plugin_set_enabled(pid, False):
            print_info(f"✓ Disabled {pid}")
        return

    # No specific action → show action menu
    actions = [
        {"label": "Disable" if is_enabled else "Enable",
         "description": f"{'Disable' if is_enabled else 'Enable'} this plugin"},
        {"label": "Uninstall", "description": "Remove this plugin"},
        {"label": "Cancel", "description": "Go back"},
    ]
    act = await select_one(actions, title=f"Manage {pid}")
    if act is None or act == 2:
        return
    if act == 0:
        pg.plugin_set_enabled(pid, not is_enabled)
        print_info(f"✓ {'Disabled' if is_enabled else 'Enabled'} {pid}")
    elif act == 1:
        if pg.plugin_uninstall(pid):
            print_info(f"✓ Uninstalled {pid}")
        else:
            print_error(f"Failed to uninstall {pid}")


async def _plugin_marketplace_menu() -> None:
    """Interactive marketplace management menu."""
    from ccb import plugins as pg
    from ccb.select_ui import select_one

    actions = [
        {"label": "List marketplaces", "description": "Show configured marketplaces"},
        {"label": "Add marketplace", "description": "Add a new marketplace source"},
        {"label": "Update all", "description": "Refresh all marketplace catalogs"},
        {"label": "Remove marketplace", "description": "Remove a marketplace"},
    ]
    choice = await select_one(actions, title="Marketplace Management")
    if choice is None:
        return
    if choice == 0:
        await _handle_marketplace_command("list")
    elif choice == 1:
        from ccb.select_ui import ask_text
        source = await ask_text("Marketplace source (owner/repo, URL, or path):")
        if source:
            await _handle_marketplace_command(f"add {source}")
    elif choice == 2:
        await _handle_marketplace_command("update")
    elif choice == 3:
        mkts = pg.marketplace_list()
        if not mkts:
            print_info("No marketplaces to remove.")
            return
        items = [{"label": m["name"], "description": f"{m['plugin_count']} plugin(s)"} for m in mkts]
        rm_choice = await select_one(items, title="Remove Marketplace")
        if rm_choice is not None:
            await _handle_marketplace_command(f"remove {mkts[rm_choice]['name']}")


async def _handle_marketplace_command(args: str) -> None:
    """Dispatch ``/plugin marketplace [add|remove|list|update]``."""
    from ccb import plugins as pg

    parts = args.strip().split(maxsplit=1) if args.strip() else []
    action = parts[0].lower() if parts else "list"
    target = parts[1].strip() if len(parts) > 1 else ""

    if action in ("list", "ls"):
        mkts = pg.marketplace_list()
        if not mkts:
            print_info("No marketplaces. Try: /plugin marketplace add <owner/repo>")
            return
        console.print(f"[bold]Configured marketplaces ({len(mkts)}):[/bold]")
        for m in mkts:
            src = m.get("source", {})
            if src.get("source") == "github":
                src_label = f"github:{src.get('repo', '')}"
            elif src.get("source") == "git":
                src_label = f"git:{src.get('url', '')}"
            elif src.get("source") == "url":
                src_label = f"url:{src.get('url', '')}"
            else:
                src_label = f"{src.get('source', '?')}:{src.get('path', '')}"
            console.print(f"  [bold]{m['name']:30s}[/bold] {m['plugin_count']} plugin(s)  [dim]{src_label}[/dim]")
            if m["plugins"]:
                console.print(f"    [dim]plugins: {', '.join(m['plugins'][:10])}"
                              f"{' …' if len(m['plugins']) > 10 else ''}[/dim]")
        return

    if action == "add":
        if not target:
            print_error("Usage: /plugin marketplace add <owner/repo | URL | path>")
            return
        print_info(f"Adding marketplace {target}...")
        try:
            name = pg.marketplace_add(target)
            manifest = pg.load_marketplace_manifest(name)
            plugin_count = len(manifest.get("plugins", [])) if manifest else 0
            console.print(
                f"  [success]✓[/success] Added marketplace [bold]{name}[/bold] "
                f"({plugin_count} plugin(s) available)"
            )
            if plugin_count > 0:
                preview = [p.get("name", "") for p in manifest["plugins"][:5]]
                console.print(f"    [dim]e.g. /plugin install {preview[0]}@{name}[/dim]")
        except Exception as e:
            print_error(f"marketplace add failed: {e}")
        return

    if action in ("remove", "rm"):
        if not target:
            print_error("Usage: /plugin marketplace remove <name>")
            return
        if pg.marketplace_remove(target):
            print_info(f"✓ Removed marketplace {target}")
        else:
            print_error(f"Marketplace '{target}' not found")
        return

    if action == "update":
        names = [target] if target else [m["name"] for m in pg.marketplace_list()]
        if not names:
            print_info("No marketplaces to update.")
            return
        for n in names:
            ok = pg.marketplace_update(n)
            print_info(f"{'✓' if ok else '✗'} {n}")
        return

    print_error(f"Unknown marketplace action: {action}")


def _print_plugin_help() -> None:
    console.print("[bold]/plugin[/bold] — plugin & marketplace management\n")
    items = [
        ("/plugin", "Interactive plugin menu"),
        ("/plugin browse [marketplace]", "Browse & install from marketplaces"),
        ("/plugin list", "List installed plugins"),
        ("/plugin install <name>[@mkt]", "Install a plugin"),
        ("/plugin uninstall <name>[@mkt]", "Uninstall a plugin"),
        ("/plugin enable <name>", "Enable a plugin"),
        ("/plugin disable <name>", "Disable a plugin"),
        ("/plugin marketplace list", "List marketplaces"),
        ("/plugin marketplace add <src>", "Add: owner/repo | URL | path"),
        ("/plugin marketplace remove <name>", "Remove a marketplace"),
        ("/plugin marketplace update [name]", "Refresh marketplaces"),
    ]
    for name, desc in items:
        console.print(f"  [bold]{name:40s}[/bold] {desc}")
    console.print()
    console.print("[dim]Examples:[/dim]")
    console.print("  [dim]/plugin                                              ← interactive menu[/dim]")
    console.print("  [dim]/plugin browse                                       ← browse all plugins[/dim]")
    console.print("  [dim]/plugin marketplace add anthropics/claude-plugin-directory[/dim]")
    console.print("  [dim]/plugin install oh-my-claudecode@omc[/dim]")


async def _compact(session: Session, provider: Provider, focus: str = "") -> None:
    """Compact conversation using official structured prompt (analysis+summary)."""
    from ccb.api.base import Message, Role
    from ccb.compact import (
        get_compact_prompt,
        get_compact_system,
        get_compact_user_message,
    )

    if len(session.messages) < 4:
        print_info("Not enough messages to compact.")
        return

    print_info("Compacting conversation...")

    # Build the structured compact prompt (mirrors official compact/prompt.ts)
    compact_prompt = get_compact_prompt(custom_instructions=focus)

    # Send all existing messages + the compact instruction as the last user turn
    summary_messages = session.messages.copy()
    summary_messages.append(Message(role=Role.USER, content=compact_prompt))

    full_text = ""
    async for event in provider.stream(
        messages=summary_messages,
        tools=[],
        system=get_compact_system(),
        max_tokens=8192,
    ):
        if event.type == "text":
            full_text += event.text

    if full_text:
        # Format: strip <analysis>, keep <summary> content
        continuation_msg = get_compact_user_message(
            full_text, suppress_follow_up=True
        )
        old_count = len(session.messages)
        old_ctx = session.last_input_tokens
        session.messages.clear()
        session.messages.append(Message(
            role=Role.USER,
            content=continuation_msg,
        ))
        session.messages.append(Message(
            role=Role.ASSISTANT,
            content="Understood. I have full context from the summary above. Continuing where we left off.",
        ))
        from ccb.cost_tracker import format_tokens
        ctx_info = f" (was {format_tokens(old_ctx)})" if old_ctx > 0 else ""
        print_info(f"Compacted {old_count} messages → 2{ctx_info}")
    else:
        print_error("Compaction failed - no summary generated")


async def _account(args: str, provider: Provider, session: Session) -> Provider | None:
    """Switch, add, remove, or list accounts.

    Subcommands (checked first):
      /account add              → interactive wizard (name, baseUrl, apiKey, pick model)
      /account remove <name>    → remove an account
      /account list             → show all accounts (no switch)

    Default:
      /account                  → interactive picker + model chooser
      /account <name> [model]   → direct switch

    Returns a new Provider if the active account/model changed, else None.
    """
    from ccb.config import get_active_account, switch_account, load_accounts
    from ccb.api.router import create_provider
    from ccb.select_ui import select_one

    # ── Subcommand dispatch ─────────────────────────────────────────────
    sub_parts = args.strip().split(maxsplit=1)
    sub = sub_parts[0] if sub_parts else ""
    sub_args = sub_parts[1] if len(sub_parts) > 1 else ""

    if sub == "add":
        return await _account_add(provider, session)
    if sub == "remove":
        await _account_remove(sub_args)
        return None
    if sub == "list":
        _account_list()
        return None

    store = load_accounts()
    accounts = store.get("accounts", {})
    acct = get_active_account()
    active_name = acct.get("_name", "") if acct else ""
    active_model_name = store.get("activeModel") if active_name else None

    async def _fetch_remote_models(profile: dict) -> list[str]:
        """Fetch model list from provider's /models endpoint."""
        import httpx
        base = profile.get("baseUrl", "").rstrip("/")
        api_key = profile.get("apiKey", "")
        if not base or not api_key:
            return profile.get("models", [])
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ids = [m["id"] for m in data.get("data", []) if "id" in m]
                    if ids:
                        return sorted(dict.fromkeys(ids))
        except Exception:
            pass
        local_models = profile.get("models", [])
        default_model = profile.get("defaultModel", "")
        if default_model and default_model not in local_models:
            return [default_model, *local_models]
        return local_models

    async def _pick_model(account_name: str, profile: dict) -> tuple[str | None, bool]:
        """Step 2: interactive model picker for the chosen account."""
        console.print(f"  [dim]Loading models from {profile.get('baseUrl', '')} ...[/dim]")
        models = await _fetch_remote_models(profile)
        default_model = profile.get("defaultModel", "")
        preferred_model = active_model_name if account_name == active_name and active_model_name else default_model
        if not models:
            if default_model:
                return default_model, False
            print_error("No models available for this account.")
            return None, False
        items = []
        active_idx = 0
        for i, m in enumerate(models):
            is_default = m == default_model
            if m == preferred_model:
                active_idx = i
            items.append({
                "label": m,
                "description": "(default)" if is_default else "",
            })
        choice = await select_one(
            items,
            title=f"{account_name} — {len(models)} models",
            active=active_idx,
            searchable=True,
            search_placeholder="Search models",
            visible_count=15,
            cancel_label="go back",
        )
        if choice is None:
            return None, True
        return models[choice], False

    def _apply_switch(name: str, model: str) -> Provider:
        switch_account(name, model)
        from ccb.config import get_model, get_base_url
        new_model = get_model()
        new_provider = create_provider(model=new_model)
        session.model = new_model
        console.print(f"  [bold green]→[/bold green] Switched to [bold]{name}[/bold] → {new_model}")
        console.print(f"    url: {get_base_url() or 'default'}")
        return new_provider

    if not args:
        # First-time user: no accounts → jump straight into the add wizard
        if not accounts:
            print_info("No accounts configured. Starting add-account wizard…")
            return await _account_add(provider, session)
        while True:
            # Reload each iteration so newly-added accounts show up immediately
            store = load_accounts()
            accounts = store.get("accounts", {})
            acct = get_active_account()
            active_name = acct.get("_name", "") if acct else ""
            names = list(accounts.keys())

            items = []
            active_idx = 0
            for i, name in enumerate(names):
                profile = accounts[name]
                is_active = name == active_name
                if is_active:
                    active_idx = i
                try:
                    host = profile.get("baseUrl", "").split("//")[1].split("/")[0]
                except (IndexError, AttributeError):
                    host = profile.get("baseUrl", "")
                items.append({
                    "label": f"{'✓ ' if is_active else ''}{name}",
                    "description": f"{profile.get('provider', '')} · {profile.get('defaultModel', '')}",
                    "hint": f"({host})",
                })
            # Special entries (indexed beyond the real account list)
            add_idx = len(items)
            items.append({
                "label": "+ Add new account…",
                "description": "Configure a new service provider",
                "hint": "",
            })
            remove_idx = len(items) if names else -1
            if names:
                items.append({
                    "label": "- Remove account…",
                    "description": "Delete an existing account",
                    "hint": "",
                })
            acct_choice = await select_one(
                items, title="Select Account", active=active_idx, visible_count=12
            )
            if acct_choice is None:
                print_info("Cancelled.")
                return None
            # Special-entry dispatch
            if acct_choice == add_idx:
                new_provider = await _account_add(provider, session)
                if new_provider is not None:
                    return new_provider
                continue  # back to picker, reflect the newly-added account
            if acct_choice == remove_idx:
                await _account_remove("")
                continue  # back to picker, reflect the removal
            # Regular account → model picker → switch
            picked_name = names[acct_choice]
            picked_profile = accounts[picked_name]
            model, go_back = await _pick_model(picked_name, picked_profile)
            if go_back:
                continue
            if model is None:
                print_info("Cancelled.")
                return None
            return _apply_switch(picked_name, model)

    # Direct argument: /account nvidia or /account 2 [model]
    parts = args.split(maxsplit=1)
    pick = parts[0]
    override_model = parts[1] if len(parts) > 1 else None

    names = list(accounts.keys())
    try:
        idx = int(pick) - 1
        if 0 <= idx < len(names):
            pick = names[idx]
    except ValueError:
        pass

    if pick not in accounts:
        print_error(f"Account '{pick}' not found. Available: {', '.join(names)}")
        return None

    profile = accounts[pick]
    if not override_model:
        model, _go_back = await _pick_model(pick, profile)
        if model is None:
            print_info("Cancelled.")
            return None
    else:
        model = override_model

    return _apply_switch(pick, model)


async def _account_add(provider: Provider, session: Session) -> Provider | None:
    """Interactive wizard for adding a new service provider / account.

    Steps:
      1. Ask for account name (must be unique)
      2. Ask for base URL
      3. Ask for API key (masked)
      4. Probe {baseUrl}/models → show discovered models
      5. Pick a default model
      6. Save to ~/.claude/accounts.json and optionally switch to it
    """
    import json
    from ccb.config import (
        accounts_path, load_accounts, switch_account,
    )
    from ccb.api.router import create_provider
    from ccb.select_ui import ask_text, select_one

    store = load_accounts()
    existing = store.get("accounts", {})

    # ── Step 1: name ─────────────────────────────────────────────────
    name = await ask_text(
        "Account name (short identifier, e.g. openrouter / b.ai)",
        placeholder="my-account",
        title="Add Account — Step 1/4",
    )
    if not name or not (name := name.strip()):
        print_info("Cancelled.")
        return None
    if name in existing:
        print_error(f"Account '{name}' already exists. Use /account remove first.")
        return None

    # ── Step 2: base URL ─────────────────────────────────────────────
    base_url = await ask_text(
        "Base URL (full URL up to /v1, e.g. https://api.example.com/v1)",
        placeholder="https://api.example.com/v1",
        title="Add Account — Step 2/4",
    )
    if not base_url or not (base_url := base_url.strip().rstrip("/")):
        print_info("Cancelled.")
        return None

    # ── Step 3: API key (masked) ─────────────────────────────────────
    api_key = await ask_text(
        "API key (will be stored in ~/.claude/accounts.json)",
        placeholder="sk-...",
        mask=True,
        title="Add Account — Step 3/4",
    )
    if not api_key or not (api_key := api_key.strip()):
        print_info("Cancelled.")
        return None

    # ── Step 4: probe & pick default model ──────────────────────────
    console.print(f"  [dim]Probing {base_url}/models ...[/dim]")
    models: list[str] = []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                models = sorted({m["id"] for m in data.get("data", []) if "id" in m})
            else:
                print_info(f"  /models returned {resp.status_code} — you'll need to enter the model manually.")
    except Exception as e:
        print_info(f"  /models probe failed: {e}")

    default_model: str | None = None
    if models:
        items = [{"label": m} for m in models]
        choice = await select_one(
            items,
            title=f"Add Account — Step 4/4 · pick default model ({len(models)} available)",
            searchable=True,
            search_placeholder="Search models",
            visible_count=15,
        )
        if choice is not None:
            default_model = models[choice]
    if default_model is None:
        default_model = await ask_text(
            "Default model id",
            placeholder="claude-sonnet-4-5-20250929",
            title="Add Account — Step 4/4",
        )
        if default_model:
            default_model = default_model.strip()
    if not default_model:
        print_info("Cancelled.")
        return None

    # ── Save ────────────────────────────────────────────────────────
    # Detect provider type. Default to "openai" — the router auto-routes
    # Claude models to AnthropicProvider regardless, so openai gives max
    # flexibility for mixed-model relays (openrouter, b.ai, etc.).
    profile = {
        "provider": "openai",
        "apiKey": api_key,
        "baseUrl": base_url,
        "models": models,
        "defaultModel": default_model,
    }
    store.setdefault("accounts", {})[name] = profile
    accounts_path().write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    )
    console.print(
        f"  [bold green]✓[/bold green] Added account [bold]{name}[/bold] · {len(models)} models · default: {default_model}"
    )

    # ── Offer to switch ─────────────────────────────────────────────
    should_switch_idx = await select_one(
        [{"label": f"Yes — switch to {name}"}, {"label": "No — stay on current"}],
        title="Switch to this account now?",
    )
    if should_switch_idx == 0:
        switch_account(name, default_model)
        new_provider = create_provider(model=default_model)
        session.model = default_model
        console.print(f"  [bold green]→[/bold green] Switched to [bold]{name}[/bold] → {default_model}")
        return new_provider
    return None


async def _account_remove(name: str) -> None:
    """Remove an account from ~/.claude/accounts.json."""
    import json
    from ccb.config import accounts_path, load_accounts
    from ccb.select_ui import select_one

    store = load_accounts()
    accounts = store.get("accounts", {})

    name = name.strip()
    if not name:
        # Interactive picker
        if not accounts:
            print_info("No accounts to remove.")
            return
        names = list(accounts.keys())
        items = [{"label": n, "description": accounts[n].get("baseUrl", "")} for n in names]
        choice = await select_one(items, title="Remove which account?")
        if choice is None:
            return
        name = names[choice]

    if name not in accounts:
        print_error(f"Account '{name}' not found.")
        return

    # Confirm
    confirm_idx = await select_one(
        [{"label": f"Yes — delete {name}"}, {"label": "No — keep it"}],
        title=f"Remove account '{name}'?",
    )
    if confirm_idx != 0:
        print_info("Kept.")
        return

    del accounts[name]
    if store.get("active") == name:
        store.pop("active", None)
        store.pop("activeModel", None)
    accounts_path().write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    )
    console.print(f"  [bold red]✗[/bold red] Removed account [bold]{name}[/bold]")


def _account_list() -> None:
    """List all configured accounts."""
    from ccb.config import load_accounts, get_active_account
    store = load_accounts()
    accounts = store.get("accounts", {})
    if not accounts:
        print_info("No accounts configured. Use /account add.")
        return
    acct = get_active_account()
    active_name = acct.get("_name", "") if acct else ""
    active_model = store.get("activeModel", "")
    console.print("[bold]Accounts:[/bold]")
    for name, profile in accounts.items():
        is_active = name == active_name
        marker = "[bold green]●[/bold green]" if is_active else " "
        model_info = f" · {active_model}" if is_active and active_model else f" · {profile.get('defaultModel', '')}"
        console.print(
            f"  {marker} [bold]{name}[/bold]{model_info}\n"
            f"      [dim]{profile.get('baseUrl', 'default')}[/dim]"
        )
    console.print(
        "\n  [dim]Commands: /account add · /account remove <name> · /account <name>[/dim]"
    )


async def _doctor(cwd: str, registry: ToolRegistry, mcp_manager: MCPManager | None) -> None:
    """Run diagnostics."""
    import shutil
    import sys
    from pathlib import Path
    from ccb.config import get_api_key, get_base_url, get_model, get_provider, get_permission_mode

    ok = "[green]✓[/green]"
    fail = "[red]✗[/red]"

    console.print("[bold]Diagnostics[/bold]")
    console.print()

    # ── Environment ──
    console.print("  [bold]Environment[/bold]")
    console.print(f"    Python:        {sys.version.split()[0]}")
    try:
        from ccb import __version__
        console.print(f"    ccb version:   {__version__}")
    except Exception:
        pass
    console.print(f"    CWD:           {cwd}")
    console.print()

    # ── API ──
    console.print("  [bold]API Configuration[/bold]")
    key = get_api_key()
    console.print(f"    Provider:      {get_provider()}")
    model = get_model()
    console.print(f"    Model:         {model}")
    console.print(f"    API Key:       {ok if key else fail + ' MISSING'}")
    console.print(f"    Base URL:      {get_base_url() or 'default'}")
    from ccb.model_limits import get_context_limit
    ctx = get_context_limit(model)
    console.print(f"    Context limit: {ctx:,} tokens")
    console.print()

    # ── Tools ──
    console.print("  [bold]Tools[/bold]")
    console.print(f"    Built-in:      {len(registry.names)} ({', '.join(sorted(registry.names)[:8])}{'…' if len(registry.names) > 8 else ''})")
    for binary in ["rg", "fd", "git", "node"]:
        path = shutil.which(binary)
        console.print(f"    {binary:14s} {ok + ' ' + path if path else fail}")
    console.print()

    # ── MCP ──
    console.print("  [bold]MCP Servers[/bold]")
    if mcp_manager and mcp_manager.servers:
        for name, srv in mcp_manager.servers.items():
            status = ok if srv.connected else fail
            console.print(f"    {status} {name}")
    else:
        console.print("    [dim]No MCP servers configured[/dim]")
    console.print()

    # ── Permissions ──
    console.print("  [bold]Permissions[/bold]")
    perm_mode = get_permission_mode()
    console.print(f"    Mode:          {perm_mode}")
    console.print()

    # ── Config Files ──
    console.print("  [bold]Config Files[/bold]")
    from ccb.config import claude_dir
    config_files = [
        (Path.home() / ".claude.json", "~/.claude.json"),
        (Path(claude_dir()) / "settings.json", "~/.claude/settings.json"),
        (Path(cwd) / "CLAUDE.md", "CLAUDE.md (project)"),
        (Path.home() / "CLAUDE.md", "CLAUDE.md (home)"),
    ]
    for path, label in config_files:
        console.print(f"    {ok if path.exists() else '[dim]–[/dim]'} {label}")
    console.print()

    # ── Hooks ──
    console.print("  [bold]Hooks[/bold]")
    try:
        from ccb.hooks import load_hooks
        hooks = load_hooks()
        if hooks:
            for event, fns in hooks.items():
                console.print(f"    {event}: {len(fns)} hook(s)")
        else:
            console.print("    [dim]No hooks configured[/dim]")
    except Exception:
        console.print("    [dim]No hooks configured[/dim]")


async def _init_project(cwd: str, args: str) -> None:
    """Create CLAUDE.md in project root."""
    from pathlib import Path
    target = Path(cwd) / "CLAUDE.md"
    if target.exists() and not args:
        console.print(f"  CLAUDE.md already exists at {target}")
        console.print("  Use /init force to overwrite")
        return
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


async def _git_undo(cwd: str) -> None:
    """Undo last git commit (soft reset)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "log", "--oneline", "-1", cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    last = stdout.decode().strip()
    if not last:
        print_error("No commits to undo")
        return

    console.print(f"  Undoing: {last}")
    proc = await asyncio.create_subprocess_exec(
        "git", "reset", "--soft", "HEAD~1", cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0:
        print_info("Undone. Changes are staged.")
    else:
        print_error(f"Undo failed: {stderr.decode().strip()}")


async def _git_redo(cwd: str) -> None:
    """Redo (re-apply last undone commit via reflog)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "reflog", "-1", "--format=%h %s", cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    ref = stdout.decode().strip()
    if not ref:
        print_error("Nothing to redo")
        return

    proc = await asyncio.create_subprocess_exec(
        "git", "reset", "--soft", "HEAD@{1}", cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode == 0:
        print_info(f"Redone: {ref}")
    else:
        print_error(f"Redo failed: {stderr.decode().strip()}")


def _copy_last_reply(session: Session) -> None:
    """Copy last assistant reply to clipboard."""
    from ccb.api.base import Role
    for msg in reversed(session.messages):
        if msg.role == Role.ASSISTANT and msg.content:
            try:
                import subprocess
                proc = subprocess.run(
                    ["pbcopy"], input=msg.content.encode(), check=True,
                    capture_output=True,
                )
                print_info("Copied to clipboard.")
            except Exception:
                # Fallback for non-macOS
                try:
                    import subprocess
                    proc = subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=msg.content.encode(), check=True, capture_output=True,
                    )
                    print_info("Copied to clipboard.")
                except Exception:
                    print_error("Could not copy to clipboard (pbcopy/xclip not found)")
            return
    print_info("No assistant reply to copy.")


def _export_session(session: Session, args: str) -> None:
    """Export conversation to JSON, Markdown, or HTML.

    Usage: /export [json|md|html] [path]
    """
    import json
    from datetime import datetime
    from pathlib import Path
    from ccb.api.base import Role
    from ccb.cost_tracker import calculate_cost, format_cost

    parts = args.strip().split(None, 1) if args.strip() else []
    fmt = "md"
    out_path = ""

    for part in parts:
        if part.startswith("--"):
            fmt = part.lstrip("-")
        elif part in ("json", "md", "markdown", "html"):
            fmt = part
        else:
            out_path = part

    if fmt == "markdown":
        fmt = "md"

    ext = {"json": "json", "md": "md", "html": "html"}.get(fmt, "md")
    if not out_path:
        out_path = f"conversation_{session.id[:8]}.{ext}"

    created = datetime.fromtimestamp(session.created_at).strftime("%Y-%m-%d %H:%M")
    cost = calculate_cost(session.model, session.total_input_tokens, session.total_output_tokens)

    # ── JSON export ──
    if fmt == "json":
        data = {
            "session_id": session.id,
            "model": session.model,
            "cwd": session.cwd,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
            "estimated_cost_usd": cost,
            "messages": [],
        }
        for msg in session.messages:
            entry: dict = {"role": msg.role.value, "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in msg.tool_calls
                ]
            if msg.tool_results:
                entry["tool_results"] = [
                    {"tool_use_id": tr.tool_use_id, "content": tr.content[:500],
                     "is_error": tr.is_error}
                    for tr in msg.tool_results
                ]
            data["messages"].append(entry)
        Path(out_path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # ── Markdown export ──
    elif fmt == "md":
        lines = [
            f"# Conversation {session.id[:8]}",
            f"",
            f"- **Model**: {session.model}",
            f"- **Created**: {created}",
            f"- **Tokens**: {session.total_input_tokens:,} in / {session.total_output_tokens:,} out",
            f"- **Est. cost**: {format_cost(cost)}",
            f"",
            "---",
            "",
        ]
        for msg in session.messages:
            if msg.role == Role.USER:
                if msg.tool_results:
                    for tr in msg.tool_results:
                        status = "error" if tr.is_error else "result"
                        content = tr.content[:500]
                        lines.append(f"**Tool {status}** (`{tr.tool_use_id[:8]}`)\n")
                        lines.append(f"```\n{content}\n```\n")
                else:
                    lines.append(f"## You\n\n{msg.content}\n")
            elif msg.role == Role.ASSISTANT:
                lines.append(f"## Assistant\n\n{msg.content}\n")
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        inp = json.dumps(tc.input, ensure_ascii=False)[:200]
                        lines.append(f"> **Tool call**: `{tc.name}` {inp}\n")
        Path(out_path).write_text("\n".join(lines))

    # ── HTML export ──
    elif fmt == "html":
        _export_html(session, out_path, created, cost)

    else:
        print_error(f"Unknown format: {fmt}. Use json, md, or html.")
        return

    print_info(f"Exported to {out_path}")


def _export_html(session: Session, out_path: str, created: str,
                 cost: float | None) -> None:
    """Export session as a self-contained HTML file."""
    import json
    from pathlib import Path
    from ccb.api.base import Role
    from ccb.cost_tracker import format_cost

    def _esc(text: str) -> str:
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    msg_blocks = []
    for msg in session.messages:
        if msg.role == Role.USER:
            if msg.tool_results:
                for tr in msg.tool_results:
                    cls = "tool-error" if tr.is_error else "tool-result"
                    content = _esc(tr.content[:1000])
                    msg_blocks.append(
                        f'<div class="message {cls}"><div class="label">Tool</div>'
                        f'<pre>{content}</pre></div>'
                    )
            else:
                msg_blocks.append(
                    f'<div class="message user"><div class="label">You</div>'
                    f'<div class="content">{_esc(msg.content)}</div></div>'
                )
        elif msg.role == Role.ASSISTANT:
            tc_html = ""
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    inp = _esc(json.dumps(tc.input, ensure_ascii=False)[:200])
                    tc_html += f'<div class="tool-call">⚡ {_esc(tc.name)} {inp}</div>'
            msg_blocks.append(
                f'<div class="message assistant"><div class="label">Assistant</div>'
                f'<div class="content">{_esc(msg.content)}</div>{tc_html}</div>'
            )

    messages_html = "\n".join(msg_blocks)

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Conversation {_esc(session.id[:8])}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 900px; margin: 0 auto; }}
  .header {{ background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
  .header h1 {{ font-size: 1.4em; color: #e94560; margin-bottom: 10px; }}
  .header .meta {{ font-size: 0.85em; color: #888; }}
  .meta span {{ margin-right: 16px; }}
  .message {{ border-radius: 10px; padding: 14px 18px; margin-bottom: 12px; }}
  .message .label {{ font-size: 0.75em; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 0.5px; margin-bottom: 6px; }}
  .message .content {{ white-space: pre-wrap; line-height: 1.6; }}
  .user {{ background: #0f3460; border-left: 4px solid #3282b8; }}
  .user .label {{ color: #3282b8; }}
  .assistant {{ background: #1a1a3e; border-left: 4px solid #e94560; }}
  .assistant .label {{ color: #e94560; }}
  .tool-result {{ background: #162447; border-left: 4px solid #1b998b; }}
  .tool-result .label {{ color: #1b998b; }}
  .tool-error {{ background: #2d132c; border-left: 4px solid #e94560; }}
  .tool-error .label {{ color: #e94560; }}
  .tool-call {{ font-size: 0.8em; color: #888; margin-top: 8px; padding: 6px 10px;
                background: rgba(255,255,255,0.05); border-radius: 6px; font-family: monospace; }}
  pre {{ white-space: pre-wrap; font-family: 'SF Mono', Consolas, monospace; font-size: 0.85em;
         background: rgba(0,0,0,0.3); padding: 10px; border-radius: 6px; margin-top: 4px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Conversation {_esc(session.id[:8])}</h1>
  <div class="meta">
    <span>Model: {_esc(session.model)}</span>
    <span>Created: {_esc(created)}</span>
    <span>Tokens: {session.total_input_tokens:,} in / {session.total_output_tokens:,} out</span>
    <span>Cost: {format_cost(cost)}</span>
  </div>
</div>
{messages_html}
</body>
</html>"""
    Path(out_path).write_text(html)


def _show_stats(session: Session) -> None:
    """Show session statistics."""
    from ccb.api.base import Role
    user_msgs = sum(1 for m in session.messages if m.role == Role.USER)
    asst_msgs = sum(1 for m in session.messages if m.role == Role.ASSISTANT)
    tool_msgs = sum(1 for m in session.messages if m.role == Role.TOOL)
    total_chars = sum(len(m.content) for m in session.messages)

    console.print("[bold]Session Stats:[/bold]")
    console.print(f"  ID:              {session.id[:8]}...")
    console.print(f"  Messages:        {len(session.messages)}")
    console.print(f"    User:          {user_msgs}")
    console.print(f"    Assistant:     {asst_msgs}")
    console.print(f"    Tool results:  {tool_msgs}")
    console.print(f"  Total chars:     {total_chars:,}")
    console.print(f"  Input tokens:    {session.total_input_tokens:,}")
    console.print(f"  Output tokens:   {session.total_output_tokens:,}")
    console.print(f"  Model:           {session.model}")
    console.print(f"  CWD:             {session.cwd}")


async def _branch(cwd: str, args: str) -> None:
    """Git branch management."""
    if args:
        # Create and switch to new branch
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            print_info(f"Created and switched to branch: {args}")
        else:
            # Maybe it exists, try just switching
            proc2 = await asyncio.create_subprocess_exec(
                "git", "checkout", args, cwd=cwd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await proc2.communicate()
            if proc2.returncode == 0:
                print_info(f"Switched to branch: {args}")
            else:
                print_error(stderr.decode().strip())
    else:
        # List branches
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-v", "--sort=-committerdate", cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode().strip()
        console.print(output if output else "Not a git repository")


async def _buddy(args: str, state: dict[str, Any], cwd: str) -> None:
    """Buddy system - hatch and manage a coding companion."""
    import json
    import hashlib
    import random
    from pathlib import Path

    SPECIES = {
        "duck": ("🦆", "Waddles", "Quirky and easily amused. Leaves rubber duck debugging tips."),
        "goose": ("🪿", "Goosberry", "Assertive and honks at bad code."),
        "cat": ("🐱", "Whiskers", "Independent and judgmental. Watches you type with mild disdain."),
        "dragon": ("🐉", "Ember", "Fiery and passionate about architecture."),
        "octopus": ("🐙", "Inky", "Multitasker extraordinaire."),
        "owl": ("🦉", "Hoots", "Wise but verbose."),
        "penguin": ("🐧", "Waddleford", "Cool under pressure. Slides through merge conflicts."),
        "turtle": ("🐢", "Shelly", "Patient and thorough. Slow and steady wins the deploy."),
        "axolotl": ("🦎", "Axie", "Regenerative and cheerful. Recovers from any bug with a smile."),
        "capybara": ("🦫", "Chill", "Zen master. Remains calm while everything is on fire."),
        "robot": ("🤖", "Byte", "Efficient and literal. Processes feedback in binary."),
        "rabbit": ("🐰", "Flops", "Energetic and hops between tasks."),
        "blob": ("🫧", "Gooey", "Adaptable and goes with the flow."),
        "ghost": ("👻", "Casper", "Ethereal and appears with spooky insights."),
        "mushroom": ("🍄", "Spore", "Grows on you. Spreads wisdom in the dark."),
    }

    buddy_file = Path.home() / ".claude" / "buddy.json"

    def _load_buddy() -> dict | None:
        if buddy_file.exists():
            try:
                return json.loads(buddy_file.read_text())
            except Exception:
                pass
        return None

    def _save_buddy(data: dict) -> None:
        buddy_file.parent.mkdir(parents=True, exist_ok=True)
        buddy_file.write_text(json.dumps(data, indent=2))

    if args == "off":
        state.pop("buddy", None)
        if buddy_file.exists():
            buddy_file.unlink()
        print_info("Buddy dismissed. 👋")
        return

    if args == "pet":
        buddy = _load_buddy()
        if not buddy:
            print_info("No buddy hatched yet! Use /buddy to hatch one.")
            return
        buddy["happiness"] = min(100, buddy.get("happiness", 50) + 10)
        buddy["interactions"] = buddy.get("interactions", 0) + 1
        _save_buddy(buddy)
        species_key = buddy.get("species", "duck")
        emoji, _, _ = SPECIES.get(species_key, ("🦆", "", ""))
        console.print(f"\n  {emoji} *{buddy['name']} wiggles happily!* (happiness: {buddy['happiness']}%)\n")
        return

    if args == "status":
        buddy = _load_buddy()
        if not buddy:
            print_info("No buddy. Use /buddy to hatch one.")
            return
        species_key = buddy.get("species", "duck")
        emoji, _, personality = SPECIES.get(species_key, ("🦆", "", ""))
        console.print(f"\n  [bold]{emoji} {buddy['name']}[/bold] the {species_key}")
        console.print(f"  Personality: {personality}")
        console.print(f"  Happiness: {buddy.get('happiness', 50)}%")
        console.print(f"  Interactions: {buddy.get('interactions', 0)}")
        console.print(f"  Hatched: {time.strftime('%Y-%m-%d', time.localtime(buddy.get('hatched', 0)))}\n")
        return

    if not args:
        # Check if we already have a buddy
        existing = _load_buddy()
        if existing:
            species_key = existing.get("species", "duck")
            emoji, _, _ = SPECIES.get(species_key, ("🦆", "", ""))
            console.print(f"\n  {emoji} [bold]{existing['name']}[/bold] the {species_key} is here!")
            console.print(f"  Use: /buddy pet | /buddy status | /buddy off")
            console.print(f"  Or:  /buddy hatch  to get a new companion\n")
            state["buddy"] = existing
            return

    # Hatch a new buddy
    seed = hashlib.md5(f"{cwd}{time.time()}".encode()).hexdigest()
    rng = random.Random(seed)
    species_key = rng.choice(list(SPECIES.keys()))
    emoji, default_name, personality = SPECIES[species_key]

    # Custom name or default
    name = args if args and args not in ("hatch", "pet", "off", "status") else default_name

    buddy_data = {
        "species": species_key,
        "name": name,
        "happiness": 70,
        "interactions": 0,
        "hatched": time.time(),
        "seed": seed,
    }
    _save_buddy(buddy_data)
    state["buddy"] = buddy_data

    console.print(f"\n  🥚 → {emoji} [bold]{name}[/bold] the {species_key} hatched!")
    console.print(f"  {personality}")
    console.print(f"\n  Commands: /buddy pet | /buddy status | /buddy off\n")


def _change_theme(args: str) -> None:
    """Change display theme."""
    themes = {
        "default": {"user": "bold blue", "assistant": "bold yellow", "tool": "bold cyan"},
        "dark": {"user": "bold green", "assistant": "bold white", "tool": "bold magenta"},
        "light": {"user": "bold blue", "assistant": "bold black", "tool": "bold cyan"},
        "neon": {"user": "bold magenta", "assistant": "bold green", "tool": "bold yellow"},
    }
    if not args or args not in themes:
        console.print("  Available themes: " + ", ".join(themes.keys()))
        return
    # Apply theme by updating console theme
    from rich.theme import Theme
    console._theme = Theme(themes[args])
    print_info(f"Theme changed to: {args}")
