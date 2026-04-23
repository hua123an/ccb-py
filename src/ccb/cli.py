"""CLI entry point."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import click

from ccb import __version__


@click.command()
@click.option("-p", "--print", "print_mode", default=None, help="Non-interactive: run a single prompt and exit.")
@click.option("-m", "--model", default=None, help="Model to use.")
@click.option("-r", "--resume", default=None, help="Resume a session by ID.")
@click.option("--bare", is_flag=True, help="Minimal mode, no plugins/MCP.")
@click.option("--output-format", default="rich", type=click.Choice(["rich", "text", "json"]), help="Output format.")
@click.option("--allowed-tools", default=None, help="Comma-separated list of allowed tool patterns.")
@click.option("--disallowed-tools", default=None, help="Comma-separated list of denied tool patterns.")
@click.option("--system-prompt", default=None, help="Override system prompt.")
@click.option("--max-tokens", default=None, type=int, help="Max output tokens.")
@click.option("--version", is_flag=True, help="Show version and exit.")
@click.option("--classic", is_flag=True, help="Use classic (non-fullscreen) REPL.")
@click.argument("prompt", nargs=-1)
def main(
    print_mode: str | None,
    model: str | None,
    resume: str | None,
    bare: bool,
    output_format: str,
    allowed_tools: str | None,
    disallowed_tools: str | None,
    system_prompt: str | None,
    max_tokens: int | None,
    version: bool,
    classic: bool,
    prompt: tuple[str, ...],
) -> None:
    """CCB - Claude Code CLI"""
    if version:
        click.echo(f"{__version__} (CCB)")
        return

    # stdin pipe support
    initial_prompt = print_mode
    if not initial_prompt and prompt:
        initial_prompt = " ".join(prompt)
    if not initial_prompt and not sys.stdin.isatty():
        initial_prompt = sys.stdin.read().strip()

    # Non-interactive → text output (pipe, -p, positional prompt)
    if initial_prompt and output_format == "rich":
        output_format = "text"

    # Apply tool filters
    if allowed_tools or disallowed_tools:
        from ccb.permissions import set_tool_filters
        set_tool_filters(
            allowed=[t.strip() for t in allowed_tools.split(",")] if allowed_tools else None,
            denied=[t.strip() for t in disallowed_tools.split(",")] if disallowed_tools else None,
        )

    try:
        asyncio.run(_async_main(
            initial_prompt=initial_prompt,
            model=model,
            resume_id=resume,
            bare=bare,
            interactive=initial_prompt is None,
            output_format=output_format,
            system_prompt_override=system_prompt,
            max_tokens=max_tokens,
            classic=classic,
        ))
    except KeyboardInterrupt:
        click.echo("\nBye!")
        sys.exit(0)


async def _async_main(
    initial_prompt: str | None,
    model: str | None,
    resume_id: str | None,
    bare: bool,
    interactive: bool,
    output_format: str = "rich",
    system_prompt_override: str | None = None,
    max_tokens: int | None = None,
    classic: bool = False,
) -> None:
    from ccb.api.router import create_provider
    from ccb.config import get_model, get_permission_mode
    from ccb.display import console, print_banner, print_error, print_info, print_user_message
    from ccb.loop import run_turn
    from ccb.mcp.client import MCPManager
    from ccb.permissions import is_tool_allowed, set_bypass_all
    from ccb.prompts import get_system_prompt
    from ccb.session import Session
    from ccb.tools.base import create_default_registry

    cwd = os.getcwd()
    used_model = model or get_model()
    provider = create_provider(model=used_model)
    registry = create_default_registry(cwd)

    # Filter tools by allow/deny rules
    for name in list(registry.names):
        if not is_tool_allowed(name):
            registry._tools.pop(name, None)

    system_prompt = system_prompt_override or get_system_prompt(cwd, model=used_model)

    # MCP
    mcp_manager: MCPManager | None = None
    if not bare:
        mcp_manager = MCPManager()
        configs = mcp_manager.discover_servers()
        if configs and interactive:
            print_info(f"Connecting to {len(configs)} MCP server(s)...")
            connected = await mcp_manager.connect_all()
            if connected:
                print_info(f"  MCP: {', '.join(connected)}")

    # Resume or new session
    session: Session
    if resume_id:
        loaded = Session.load(resume_id)
        if loaded:
            session = loaded
            if interactive:
                print_info(f"Resumed session {resume_id[:8]}...")
        else:
            print_error(f"Session {resume_id} not found")
            return
    else:
        session = Session(cwd=cwd, model=used_model)

    # Shared state dict
    state: dict[str, Any] = {"vim_mode": False}
    if max_tokens:
        state["max_tokens"] = max_tokens

    if get_permission_mode() == "bypassPermissions":
        set_bypass_all(True)

    # Non-interactive mode
    if not interactive and initial_prompt:
        session.add_user_message(initial_prompt)
        await run_turn(
            provider, session, registry, system_prompt,
            mcp_manager=mcp_manager, output_format=output_format,
            state=state,
        )
        session.save()
        if mcp_manager:
            await mcp_manager.disconnect_all()
        return

    # ── Interactive mode ──

    if classic:
        # Classic line-based REPL (fallback)
        await _classic_repl(
            provider, session, registry, system_prompt,
            mcp_manager, state, output_format, used_model, cwd,
        )
    else:
        # Full-screen REPL (default)
        from ccb.repl import REPLApp, set_active_repl
        repl = REPLApp(
            version=__version__,
            model=used_model,
            cwd=cwd,
            provider=provider,
            session=session,
            registry=registry,
            system_prompt=system_prompt,
            mcp_manager=mcp_manager,
            state=state,
            output_format=output_format,
        )
        set_active_repl(repl)
        try:
            await repl.run()
        finally:
            set_active_repl(None)

    # Cleanup
    session.save()
    if mcp_manager:
        await mcp_manager.disconnect_all()


async def _classic_repl(
    provider: Any,
    session: Any,
    registry: Any,
    system_prompt: str,
    mcp_manager: Any,
    state: dict[str, Any],
    output_format: str,
    model: str,
    cwd: str,
) -> None:
    """Classic line-based REPL (non-fullscreen fallback)."""
    from ccb.commands import handle_command
    from ccb.display import console, print_banner, print_info, print_user_message
    from ccb.loop import run_turn
    from ccb.repl import SLASH_COMMANDS

    print_banner(__version__, model, cwd)
    if state.get("_bypass_all"):
        print_info("  ⚡ bypass mode\n")

    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from ccb.config import claude_dir

    history_file = claude_dir() / "input_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    completer = WordCompleter(SLASH_COMMANDS, sentence=True)
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    ps = PromptSession(
        history=FileHistory(str(history_file)),
        vi_mode=state.get("vim_mode", False),
        completer=completer,
        complete_while_typing=True,
        key_bindings=kb,
    )

    while True:
        try:
            ps.vi_mode = state.get("vim_mode", False)
            user_input = await ps.prompt_async("❯ ", multiline=False)
        except (EOFError, KeyboardInterrupt):
            print_info("\nBye!")
            break

        if not user_input.strip():
            continue

        if user_input.startswith("/"):
            handled = await handle_command(
                user_input, session, provider, registry, cwd,
                mcp_manager=mcp_manager, state=state,
            )
            if handled == "exit":
                break
            if "_new_provider" in state:
                provider = state.pop("_new_provider")
            if handled:
                continue

        # 1) Resolve @ file mentions
        from ccb.at_mentions import resolve_all_mentions
        text_after_at, at_files, at_images = resolve_all_mentions(user_input, cwd)
        # 2) Detect image/file paths in remaining input (drag-drop)
        from ccb.images import process_input_attachments
        remaining_text, auto_images, auto_files = process_input_attachments(text_after_at)
        img_dicts = at_images + ([img.to_dict() for img in auto_images] if auto_images else [])
        file_dicts = at_files + ([fc.to_dict() for fc in auto_files] if auto_files else [])
        # Drain pending attachments from /image, /file commands
        if state.get("_pending_images"):
            img_dicts.extend(state.pop("_pending_images"))
        if state.get("_pending_files"):
            file_dicts.extend(state.pop("_pending_files"))
        display_text = remaining_text or text_after_at or user_input
        if at_files:
            names = ", ".join(f.get("filename", "file") for f in at_files)
            print_info(f"  📄 {len(at_files)} @-mentioned file(s): {names}")
        if at_images:
            names = ", ".join(f.get("filename", "image") for f in at_images)
            print_info(f"  📎 {len(at_images)} @-mentioned image(s): {names}")
        if auto_images:
            names = ", ".join(img.filename for img in auto_images)
            print_info(f"  📎 {len(auto_images)} image(s) attached: {names}")
        if auto_files:
            names = ", ".join(fc.filename for fc in auto_files)
            print_info(f"  📄 {len(auto_files)} file(s) attached: {names}")
        print_user_message(display_text)
        session.add_user_message(
            display_text,
            images=img_dicts or None,
            files=file_dicts or None,
        )
        cur_format = state.get("output_style", output_format)
        await run_turn(
            provider, session, registry, system_prompt,
            mcp_manager=mcp_manager, output_format=cur_format,
            state=state,
        )
        session.save()
        console.print()


if __name__ == "__main__":
    main()
