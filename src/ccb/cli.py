"""CLI entry point with enhanced help and error handling."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import click

from ccb import __version__


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("-p", "--print", "print_mode", default=None,
              help="Run a single prompt non-interactively and exit. "
                   "Reads from stdin if no prompt is given.")
@click.option("-m", "--model", default=None,
              help="Model to use (e.g., 'claude-sonnet-4', 'gpt-4o'). "
                   "See '/account list' for available models.")
@click.option("-r", "--resume", "resume_id", default=None,
              help="Resume a saved session by ID. Use '/sessions' to list sessions.")
@click.option("--bare", is_flag=True,
              help="Minimal mode: skip loading plugins and MCP servers.")
@click.option("--output-format", "output_format", default="rich",
              type=click.Choice(["rich", "text", "json", "stream-json"]),
              help="Output format: rich (colored), text (plain), json, stream-json")
@click.option("--allowed-tools", default=None,
              help="Comma-separated list of allowed tool patterns. "
                   "Example: --allowed-tools bash,file_read,grep")
@click.option("--disallowed-tools", default=None,
              help="Comma-separated list of denied tool patterns. "
                   "Example: --disallowed-tools agent,mcp_*")
@click.option("--system-prompt", default=None,
              help="Override the default system prompt.")
@click.option("--max-tokens", default=None, type=int,
              help="Maximum output tokens (default varies by model).")
@click.option("--version", is_flag=True, help="Show version and exit.")
@click.option("--classic", is_flag=True,
              help="Use classic line-based interface instead of full-screen UI.")
@click.argument("prompt", nargs=-1, metavar="[PROMPT]")
def main(
    print_mode: str | None,
    model: str | None,
    resume_id: str | None,
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
    """CCB - Claude Code CLI for AI-assisted coding.

    \b
    Examples:
      ccb-py                                    # Interactive mode
      ccb-py "Write a hello world function"    # Single prompt
      ccb-py -p "Explain this code" < code.py  # Pipe input
      ccb-py -m gpt-4o "Analyze this"          # Use specific model
      ccb-py --resume abc123                    # Resume session
      ccb-py --classic                          # Line-based interface

    \b
    Keyboard shortcuts:
      Ctrl+C / Ctrl+D   Exit
      Ctrl+L             Clear screen
      Ctrl+Y             Copy last response

    \b
    Slash commands (type / in interactive mode):
      /help              Show all commands
      /account           Manage API accounts
      /model <name>      Switch model
      /compact           Compress conversation
      /sessions          List saved sessions

    Environment variables:
      ANTHROPIC_API_KEY      Anthropic API key
      OPENAI_API_KEY         OpenAI API key
      OPENAI_BASE_URL        OpenAI-compatible base URL
    """
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
            resume_id=resume_id,
            bare=bare,
            interactive=initial_prompt is None,
            output_format=output_format,
            system_prompt_override=system_prompt,
            max_tokens=max_tokens,
            classic=classic,
        ))
    except (KeyboardInterrupt, SystemExit):
        try:
            click.echo("\nBye!")
        except Exception:
            pass
        os._exit(0)
    except RuntimeError as e:
        if "Event loop" not in str(e):
            raise
        os._exit(0)


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
    from ccb.config import get_model, get_permission_mode, get_api_key, get_api_key_hint
    from ccb.display import print_error, print_info, print_banner
    from ccb.loop import run_turn
    from ccb.mcp.client import MCPManager
    from ccb.permissions import is_tool_allowed, set_bypass_all
    from ccb.prompts import get_system_prompt
    from ccb.session import Session
    from ccb.tools.base import create_default_registry

    cwd = os.getcwd()
    used_model = model or get_model()

    # Check API key before creating provider
    api_key = get_api_key()
    if not api_key:
        print_error("No API key found.")
        hint = get_api_key_hint()
        if hint:
            print_error(f"  Hint: {hint}")
        return

    try:
        from ccb.api.router import create_provider
        provider = create_provider(model=used_model)
    except Exception as e:
        print_error(f"Failed to create provider: {e}")
        print_error("Check your API key and model settings with /account")
        return

    try:
        registry = create_default_registry(cwd)
    except Exception as e:
        print_error(f"Failed to initialize tools: {e}")
        return

    # Filter tools by allow/deny rules
    for name in list(registry.names):
        if not is_tool_allowed(name):
            registry._tools.pop(name, None)

    system_prompt = system_prompt_override or get_system_prompt(cwd, model=used_model)

    # MCP
    mcp_manager: MCPManager | None = None
    if not bare:
        try:
            mcp_manager = MCPManager()
            configs = mcp_manager.discover_servers()
            if configs and interactive:
                print_info(f"Connecting to {len(configs)} MCP server(s)...")
                connected = await mcp_manager.connect_all()
                if connected:
                    print_info(f"  MCP: {', '.join(connected)}")
        except Exception as e:
            if interactive:
                print_info(f"MCP init skipped: {e}")
            mcp_manager = None

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
        if output_format == "stream-json":
            import json as _json
            sys.stdout.write(_json.dumps({"type": "session:started", "sessionId": session.id}) + "\n")
            sys.stdout.flush()
        session.add_user_message(initial_prompt)
        try:
            await run_turn(
                provider, session, registry, system_prompt,
                mcp_manager=mcp_manager, output_format=output_format,
                state=state,
            )
        except Exception as e:
            print_error(f"Error during turn: {e}")
        session.save()
        if mcp_manager:
            await mcp_manager.disconnect_all()
        return

    # ── Interactive mode ──

    if classic:
        print_banner(__version__, used_model, cwd)
        if state.get("_bypass_all"):
            print_info("  ⚡ bypass mode\n")
        await _classic_repl(
            provider, session, registry, system_prompt,
            mcp_manager, state, output_format, used_model, cwd,
        )
    else:
        try:
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
        except Exception as e:
            print_error(f"Failed to start REPL: {e}")
            return

    # Cleanup — auto-extract memories from conversation
    session.save()
    if len(session.messages) > 4:
        try:
            from ccb.memory import get_extractor
            extractor = get_extractor()
            extractor.set_provider(provider)
            await extractor.extract_from_session(session.messages, session.id)
        except Exception:
            pass
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
    from ccb.display import console, print_info, print_user_message
    from ccb.loop import run_turn
    from ccb.repl import SLASH_COMMANDS
    from ccb.config import claude_dir

    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

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
            try:
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
            except Exception as e:
                from ccb.display import print_error
                print_error(f"Command error: {e}")
                continue

        # Resolve @ file mentions and attachments
        try:
            from ccb.at_mentions import resolve_all_mentions
            from ccb.images import process_input_attachments

            text_after_at, at_files, at_images = resolve_all_mentions(user_input, cwd)
            remaining_text, auto_images, auto_files, auto_videos, auto_audios = process_input_attachments(text_after_at)
            img_dicts = at_images + ([img.to_dict() for img in auto_images] if auto_images else [])
            file_dicts = at_files + ([fc.to_dict() for fc in auto_files] if auto_files else [])
            media_dicts = [v.to_dict() for v in auto_videos] + [a.to_dict() for a in auto_audios]

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
            if auto_videos:
                names = ", ".join(v.filename for v in auto_videos)
                print_info(f"  🎬 {len(auto_videos)} video(s) attached: {names}")
            if auto_audios:
                names = ", ".join(a.filename for a in auto_audios)
                print_info(f"  🎵 {len(auto_audios)} audio(s) attached: {names}")

            print_user_message(display_text)
            session.add_user_message(
                display_text,
                images=img_dicts or None,
                files=file_dicts or None,
                media=media_dicts or None,
            )
            cur_format = state.get("output_style", output_format)
            await run_turn(
                provider, session, registry, system_prompt,
                mcp_manager=mcp_manager, output_format=cur_format,
                state=state,
            )
            session.save()
            console.print()
        except Exception as e:
            from ccb.display import print_error
            print_error(f"Error: {e}")


if __name__ == "__main__":
    main()