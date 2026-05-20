"""General-purpose slash commands."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info
from ccb.json_store import read_json, write_json

if TYPE_CHECKING:
    from ccb.mcp.client import MCPManager
    from ccb.session import Session
    from ccb.tools.base import ToolRegistry


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

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
        ("/snapshot [desc]", "Save workspace snapshot (git+deps+env)"),
        ("/restore [id]", "Restore from workspace snapshot"),
        ("/sandbox [on|off|status]", "Toggle sandbox mode for safe execution"),
        ("/rewind [n]", "Remove last n messages"),
        # ── Enterprise ──
        ("/flags [list|toggle|set]", "Feature flags: list, toggle, or set overrides"),
        ("/daemon [status|start|stop]", "Manage background daemon"),
        ("/jobs [list|summary|cancel|delete]", "Manage background jobs"),
        ("/events [list|clear]", "Show recent runtime events"),
        ("/acp", "Show ACP IDE connections"),
        ("/langfuse", "Langfuse monitoring status"),
        ("/sentry", "Sentry error tracking status"),
        # ── Skills / Review ──
        ("/skills [name] [args]", "List skills or run a skill"),
        ("/review", "Review code changes"),
        ("/test", "Generate tests"),
        ("/explain", "Explain codebase"),
        ("/advisor", "Senior code review"),
        ("/security-review", "Security audit"),
        ("/workflows [name] [args]", "List workflows or run a workflow"),
        # ── Memory / Init ──
        ("/remember <text>", "Remember something permanently"),
        ("/forget [query|id]", "Forget a memory"),
        ("/memory [sub]", "Memory: add|list|search|delete|extract|clear|context"),
        ("/init", "Create CLAUDE.md template"),
        ("/add-dir <path>", "Add working directory"),
        # ── Tools / MCP ──
        ("/mcp [connect|disconnect]", "Manage MCP servers"),
        ("/doctor", "Run diagnostics"),
        ("/permissions", "Manage permissions"),
        ("/hooks", "Show hooks status"),
        ("/plan", "Show/toggle plan mode"),
        ("/tasks", "List background tasks"),
        ("/agents [name]", "List/activate agent definitions"),
        ("/peers [live]", "Show connected instances (live for auto-refresh)"),
        ("/fork", "Fork current session"),
        # ── UI / Preferences ──
        ("/prefill [text]", "Pre-fill model's next reply with text; empty to clear"),
        ("/thinking [on|off|adaptive]", "Toggle/set thinking mode"),
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
        ("/desktop", "Launch local desktop app"),
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


# ---------------------------------------------------------------------------
# Utility helpers (also used by other modules)
# ---------------------------------------------------------------------------

async def _buddy(args: str, state: dict[str, Any], cwd: str) -> None:
    """Buddy system - hatch and manage a coding companion."""
    import hashlib
    import random

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

    buddy_file = Path.home() / ".ccb" / "buddy.json"

    def _load_buddy() -> dict | None:
        if buddy_file.exists():
            try:
                data = read_json(buddy_file, default=None)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    def _save_buddy(data: dict) -> None:
        write_json(buddy_file, data)

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
        existing = _load_buddy()
        if existing:
            species_key = existing.get("species", "duck")
            emoji, _, _ = SPECIES.get(species_key, ("🦆", "", ""))
            console.print(f"\n  {emoji} [bold]{existing['name']}[/bold] the {species_key} is here!")
            console.print("  Use: /buddy pet | /buddy status | /buddy off")
            console.print("  Or:  /buddy hatch  to get a new companion\n")
            state["buddy"] = existing
            return

    # Hatch a new buddy
    seed = hashlib.md5(f"{cwd}{time.time()}".encode()).hexdigest()
    rng = random.Random(seed)
    species_key = rng.choice(list(SPECIES.keys()))
    emoji, default_name, personality = SPECIES[species_key]

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
    console.print("\n  Commands: /buddy pet | /buddy status | /buddy off\n")


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
    from rich.theme import Theme
    console._theme = Theme(themes[args])  # type: ignore[attr-defined]
    print_info(f"Theme changed to: {args}")


def _copy_last_reply(session: Session) -> None:
    """Copy last assistant reply to clipboard."""
    from ccb.api.base import Role
    for msg in reversed(session.messages):
        if msg.role == Role.ASSISTANT and msg.content:
            try:
                import subprocess
                subprocess.run(
                    ["pbcopy"], input=msg.content.encode(), check=True,
                    capture_output=True,
                )
                print_info("Copied to clipboard.")
            except Exception:
                try:
                    import subprocess
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=msg.content.encode(), check=True, capture_output=True,
                    )
                    print_info("Copied to clipboard.")
                except Exception:
                    print_error("Could not copy to clipboard (pbcopy/xclip not found)")
            return
    print_info("No assistant reply to copy.")


def _export_session(session: Session, args: str) -> None:
    """Export conversation to JSON, Markdown, or HTML."""
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
            msg_list = data.get("messages")
            if isinstance(msg_list, list):
                msg_list.append(entry)
        write_json(Path(out_path), data, ensure_ascii=False)

    elif fmt == "md":
        lines = [
            f"# Conversation {session.id[:8]}",
            "",
            f"- **Model**: {session.model}",
            f"- **Created**: {created}",
            f"- **Tokens**: {session.total_input_tokens:,} in / {session.total_output_tokens:,} out",
            f"- **Est. cost**: {format_cost(cost)}",
            "",
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

    elif fmt == "html":
        _export_html(session, out_path, created, cost)

    else:
        print_error(f"Unknown format: {fmt}. Use json, md, or html.")
        return

    print_info(f"Exported to {out_path}")


def _export_html(session: Session, out_path: str, created: str,
                 cost: float | None) -> None:
    """Export session as a self-contained HTML file."""
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
    <span>Cost: {format_cost(float(cost or 0.0))}</span>
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
    tool_msgs = sum(1 for m in session.messages if m.tool_results)
    total_chars = sum(len(m.content or "") for m in session.messages)

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


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

async def _doctor(cwd: str, registry: ToolRegistry, mcp_manager: MCPManager | None) -> None:
    """Run diagnostics."""
    import shutil
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
    from ccb.capabilities import get_capabilities
    caps = get_capabilities(get_provider() or "", model)
    _caps_list = []
    if caps.supports_tools:
        _caps_list.append("tools")
    if caps.supports_thinking:
        _caps_list.append("thinking")
    if caps.supports_images:
        _caps_list.append("images")
    if caps.supports_vision:
        _caps_list.append("vision")
    if caps.supports_prefill:
        _caps_list.append("prefill")
    if caps.supports_parallel_tool_calls:
        _caps_list.append("parallel_tools")
    if caps.supports_system_prompt:
        _caps_list.append("system_prompt")
    console.print(f"    Capabilities:  {', '.join(_caps_list) if _caps_list else '[dim]none[/dim]'}  (max_tokens={caps.recommended_max_tokens}, temp={caps.recommended_temperature})")
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
        (Path.home() / ".ccb.json", "~/.ccb.json"),
        (Path(claude_dir()) / "settings.json", "~/.ccb/settings.json"),
        (Path(cwd) / "CLAUDE.md", "CLAUDE.md (project)"),
        (Path.home() / "CLAUDE.md", "CLAUDE.md (home)"),
    ]
    for cfg_path, label in config_files:
        console.print(f"    {ok if Path(cfg_path).exists() else '[dim]–[/dim]'} {label}")
    console.print()

    console.print("  [bold]Runtime Health[/bold]")
    try:
        from ccb.daemon_proc import daemon_status
        daemon = daemon_status()
        console.print(f"    Daemon:        {ok if daemon['running'] else '[dim]not running[/dim]'}")
        console.print(f"    PID file:      {daemon['pid_file']}")
        console.print(f"    Log file:      {daemon['log_file']}")
    except Exception as e:
        console.print(f"    Daemon:        {fail} {e}")
    try:
        from ccb.feature_flags import is_feature_enabled
        console.print(f"    Scheduled:     {'enabled' if is_feature_enabled('scheduled_tasks', True) else 'disabled'}")
    except Exception as e:
        console.print(f"    Scheduled:     {fail} {e}")
    console.print()

    console.print("  [bold]Scheduled Tasks[/bold]")
    try:
        from ccb.cron_tasks import CronTask, get_cron_file_path, list_scheduled_project_dirs, read_cron_tasks
        cron_path = get_cron_file_path(cwd)
        tasks = read_cron_tasks(cwd)
        projects = list_scheduled_project_dirs(include_missing=True)
        if cron_path.exists():
            console.print(f"    Project file:  {ok} {cron_path}")
            console.print(f"    Project tasks: {len(tasks)}")
            try:
                raw = read_json(cron_path, default={})
                raw_tasks = raw.get("tasks") if isinstance(raw, dict) else None
                invalid = 0
                if isinstance(raw_tasks, list):
                    invalid = sum(1 for item in raw_tasks if CronTask.from_dict(item) is None)
                else:
                    invalid = 1
                console.print(f"    Invalid items: {fail + ' ' + str(invalid) if invalid else ok + ' 0'}")
            except Exception as e:
                console.print(f"    JSON:          {fail} {e}")
        else:
            console.print(f"    Project file:  [dim]not present[/dim] {cron_path}")
        console.print(f"    Registered:    {len(projects)} project(s)")
        missing = [p for p in projects if not Path(p).is_dir()]
        if missing:
            console.print(f"    Missing dirs:   [yellow]{len(missing)}[/yellow]")
        current_registered = str(Path(cwd).resolve()) in projects
        if tasks and not current_registered:
            console.print("    Registration:  [yellow]project has tasks but is not registered[/yellow]")
    except Exception as e:
        console.print(f"    Scheduled:     {fail} {e}")
    console.print()

    console.print("  [bold]Background Jobs[/bold]")
    try:
        from ccb.jobs import JobStatus, get_job_manager
        summary = get_job_manager().summary()
        console.print(f"    Total:         {summary['total']}")
        active = summary["by_status"].get(JobStatus.QUEUED.value, 0) + summary["by_status"].get(JobStatus.RUNNING.value, 0)
        console.print(f"    Active:        {active}")
        errors = summary["by_status"].get(JobStatus.ERROR.value, 0)
        console.print(f"    Errors:        {errors if errors else 0}")
    except Exception as e:
        console.print(f"    Jobs:          {fail} {e}")
    console.print()

    console.print("  [bold]Recent Events[/bold]")
    try:
        from ccb.events import event_summary, events_path, recent_events
        event_file = events_path()
        summary = event_summary(200)
        events = recent_events(5)
        console.print(f"    Event file:    {event_file}")
        console.print(f"    Last 200:      {summary['total']} event(s)")
        by_level = summary["by_level"]
        console.print(
            f"    Problems:      "
            f"{by_level.get('error', 0)} error(s), {by_level.get('warning', 0)} warning(s)"
        )
        if summary["by_kind"]:
            kinds = ", ".join(f"{k}={v}" for k, v in sorted(summary["by_kind"].items())[:5])
            console.print(f"    By kind:       {kinds}")
        loop_events = [e for e in events if e.get("kind") == "loop" and e.get("level") in ("warning", "error")]
        if loop_events:
            loop_actions: dict[str, int] = {}
            for event in loop_events:
                action = str(event.get("action") or "unknown")
                loop_actions[action] = loop_actions.get(action, 0) + 1
            actions = ", ".join(f"{k}={v}" for k, v in sorted(loop_actions.items()))
            console.print(f"    Loop issues:   {actions}")
        if summary["last_problem"]:
            event = summary["last_problem"]
            console.print(
                f"    Last problem:  {event.get('level')} "
                f"{event.get('kind', '')}.{event.get('action', '')} "
                f"[dim]{event.get('time', '')}[/dim]"
            )
        if events:
            for event in events:
                payload = event.get("payload") or {}
                detail = ""
                if payload:
                    detail = " " + ", ".join(f"{k}={str(v)[:30]}" for k, v in list(payload.items())[:2])
                console.print(
                    f"    [dim]{event.get('time', '')}[/dim] "
                    f"{event.get('level', 'info'):7s} "
                    f"{event.get('kind', '')}.{event.get('action', '')}{detail}"
                )
        else:
            console.print("    [dim]No recent events[/dim]")
    except Exception as e:
        console.print(f"    Events:        {fail} {e}")
    console.print()

    # ── Hooks ──
    console.print("  [bold]Hooks[/bold]")
    try:
        from ccb.hooks import load_hooks
        hooks = load_hooks(cwd=cwd)
        if hooks:
            for event, fns in hooks.items():
                console.print(f"    {event}: {len(fns)} hook(s)")
        else:
            console.print("    [dim]No hooks configured[/dim]")
    except Exception:
        console.print("    [dim]No hooks configured[/dim]")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

async def _schedule(args: str, cwd: str) -> None:
    """Manage project scheduled tasks."""
    import shlex
    from ccb.cron import compute_next_cron_run, parse_cron_expression
    from ccb.cron_tasks import (
        add_cron_task,
        has_cron_tasks,
        read_cron_tasks,
        register_scheduled_project,
        remove_cron_tasks,
        unregister_scheduled_project,
    )

    try:
        parts = shlex.split(args.strip())
    except ValueError as e:
        print_error(f"Invalid schedule arguments: {e}")
        return
    sub = parts[0].lower() if parts else "list"

    if sub in ("list", "ls", ""):
        tasks = read_cron_tasks(cwd)
        if not tasks:
            print_info("No scheduled tasks for this project.")
            return
        console.print(f"[bold]Scheduled tasks ({len(tasks)}):[/bold]")
        now_ms = int(time.time() * 1000)
        for task in tasks:
            anchor = task.lastFiredAt if task.recurring and task.lastFiredAt else task.createdAt
            next_ms = compute_next_cron_run(task.cron, anchor)
            next_text = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(next_ms / 1000))
                if next_ms
                else "never"
            )
            flags = []
            if task.recurring:
                flags.append("recurring")
            if task.permanent:
                flags.append("permanent")
            if next_ms and next_ms <= now_ms:
                flags.append("due")
            suffix = f" [{' '.join(flags)}]" if flags else ""
            console.print(f"  [bold]{task.id}[/bold] {task.cron} -> {next_text}{suffix}")
            console.print(f"    {task.prompt[:120]}")
        return

    if sub in ("add", "create"):
        if len(parts) < 3:
            print_error('Usage: /schedule add "<cron>" <prompt>')
            print_info('Example: /schedule add "*/30 * * * *" Check build status')
            return
        if len(parts) >= 7 and parse_cron_expression(" ".join(parts[1:6])):
            cron = " ".join(parts[1:6])
            prompt = " ".join(parts[6:]).strip()
        else:
            cron = parts[1].strip()
            prompt = " ".join(parts[2:]).strip()
        if not parse_cron_expression(cron):
            print_error(f"Invalid cron expression: {cron}")
            return
        task = add_cron_task(cron, prompt, cwd, recurring=True)
        register_scheduled_project(cwd)
        try:
            from ccb.events import emit_event
            emit_event("cron", "repl", "task_added", {"task_id": task.id, "cron": task.cron, "cwd": cwd}, cwd=cwd)
        except Exception:
            pass
        print_info(f"Scheduled task added: {task.id} ({task.cron})")
        return

    if sub in ("once", "one-shot", "oneshot"):
        if len(parts) < 3:
            print_error('Usage: /schedule once "<cron>" <prompt>')
            return
        if len(parts) >= 7 and parse_cron_expression(" ".join(parts[1:6])):
            cron = " ".join(parts[1:6])
            prompt = " ".join(parts[6:]).strip()
        else:
            cron = parts[1].strip()
            prompt = " ".join(parts[2:]).strip()
        if not parse_cron_expression(cron):
            print_error(f"Invalid cron expression: {cron}")
            return
        task = add_cron_task(cron, prompt, cwd, recurring=False)
        register_scheduled_project(cwd)
        try:
            from ccb.events import emit_event
            emit_event("cron", "repl", "task_added_once", {"task_id": task.id, "cron": task.cron, "cwd": cwd}, cwd=cwd)
        except Exception:
            pass
        print_info(f"One-shot scheduled task added: {task.id} ({task.cron})")
        return

    if sub in ("delete", "del", "rm", "remove"):
        ids = parts[1:] if len(parts) > 1 else []
        if not ids:
            print_error("Usage: /schedule delete <task-id> [...]")
            return
        removed = remove_cron_tasks(ids, cwd)
        if removed and not has_cron_tasks(cwd):
            unregister_scheduled_project(cwd)
        try:
            from ccb.events import emit_event
            emit_event("cron", "repl", "task_removed", {"ids": ids, "removed": removed, "cwd": cwd}, cwd=cwd)
        except Exception:
            pass
        print_info(f"Removed {removed} scheduled task(s).")
        return

    print_info("Usage: /schedule [list | add <cron> <prompt> | once <cron> <prompt> | delete <id>]")


# ---------------------------------------------------------------------------
# Plugin helpers
# ---------------------------------------------------------------------------

async def _handle_plugin_command(args: str) -> None:
    """Dispatch ``/plugin [subcommand] [...]``."""
    from ccb import plugins as pg

    parts = args.strip().split(maxsplit=1) if args.strip() else []
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    try:
        if not sub:
            await _plugin_interactive_menu()
            return

        if sub in ("marketplace", "market"):
            await _handle_marketplace_command(rest)
            return

        if sub in ("browse", "search", "discover"):
            await _plugin_browse(rest.strip() or None)
            return

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
            import pathlib
            pathlib.Path.home().joinpath(".ccb", "ccb-debug.log").open("a").write(
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

    mkts = pg.marketplace_list()
    if not mkts:
        print_info("No marketplaces configured.")
        print_info("Add one first:  /plugin marketplace add <owner/repo>")
        print_info("  Example:      /plugin marketplace add anthropics/claude-plugin-directory")
        return

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

    available = pg.marketplace_browse(marketplace_name)
    if not available:
        print_info("No plugins found in marketplace(s).")
        return

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

    print_info("Usage: /schedule [list | add <cron> <prompt> | once <cron> <prompt> | delete <id>]")
