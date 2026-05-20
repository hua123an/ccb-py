"""Session-related slash commands."""
from __future__ import annotations

import copy
import time
from typing import Any, TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info
from ccb.session_repository import list_persisted_sessions, load_session, save_session
from ccb.session_runtime import emit_runtime_warning

if TYPE_CHECKING:
    from ccb.api.base import Provider
    from ccb.session import Session


async def cmd_sessions(
    cmd: str,
    session: Session,
    provider: Provider,
    args: str,
    state: dict[str, Any],
) -> str | bool:
    """Handle /sessions, /resume, /continue commands."""
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

    if cmd in ("/resume", "/continue") and args:
        # Try exact ID match first
        try:
            loaded = load_session(args)
        except Exception as e:
            emit_runtime_warning(
                "command_resume_load_failed",
                session_id=args,
                cwd=session.cwd,
                payload={"error": str(e)},
            )
            loaded = None
        if not loaded:
            for entry in list_persisted_sessions(50, cwd=session.cwd):
                if args in entry["id"] or args.lower() in entry.get("cwd", "").lower():
                    try:
                        loaded = load_session(entry["id"])
                    except Exception as e:
                        emit_runtime_warning(
                            "command_resume_match_load_failed",
                            session_id=entry["id"],
                            cwd=session.cwd,
                            payload={"error": str(e)},
                        )
                        loaded = None
                    break
        if loaded:
            _apply_resume(loaded)
            print_info(f"Resumed session {loaded.id[:8]} ({len(loaded.messages)} msgs, model: {loaded.model})")
        else:
            print_error(f"Session not found: {args}")
        return True

    # Interactive session picker for /resume (no args), /sessions, /continue
    all_sessions = list_persisted_sessions(20, cwd=session.cwd)
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
        title="Select Session" if cmd == "/sessions" else "Resume Session",
    )
    if choice is None:
        print_info("Cancelled.")
        return True

    picked = all_sessions[choice]
    try:
        loaded = load_session(picked["id"])
    except Exception as e:
        emit_runtime_warning(
            "command_session_picker_load_failed",
            session_id=picked["id"],
            cwd=session.cwd,
            payload={"error": str(e)},
        )
        loaded = None
    if loaded:
        _apply_resume(loaded)
        print_info(f"Resumed session {loaded.id[:8]} ({len(loaded.messages)} msgs, model: {loaded.model})")
    else:
        print_error(f"Failed to load session {picked['id'][:8]}")
    return True


async def cmd_fork(
    session: Session,
    args: str,
) -> bool:
    """Handle /fork command."""
    from ccb.session import Session as S

    fork_name = args.strip() if args else ""
    new_session = S(
        cwd=session.cwd,
        model=session.model,
    )
    # Deep-copy all messages into the fork
    for msg in session.messages:
        new_session.messages.append(copy.deepcopy(msg))
    new_session.total_input_tokens = session.total_input_tokens
    new_session.total_output_tokens = session.total_output_tokens
    new_session.last_input_tokens = session.last_input_tokens
    try:
        save_session(new_session)
    except Exception as e:
        emit_runtime_warning(
            "command_fork_persist_failed",
            session_id=new_session.id,
            cwd=new_session.cwd,
            payload={"error": str(e)},
        )
        print_error(f"Failed to persist forked session: {e}")
        return True
    parent_id = session.id[:8]
    fork_id = new_session.id[:8]
    print_info(f"Forked session {parent_id} → {fork_id} ({len(new_session.messages)} msgs)")
    if fork_name:
        print_info(f"Use '/resume {fork_id}' to switch to the fork.")
    else:
        print_info(f"Continue here (original), or '/resume {fork_id}' for the fork.")
    return True


def cmd_session(session: Session) -> bool:
    """Handle /session command."""
    console.print(f"  ID:      {session.id}")
    console.print(f"  Model:   {session.model}")
    console.print(f"  CWD:     {session.cwd}")
    console.print(f"  Msgs:    {len(session.messages)}")
    return True


def cmd_rename(session: Session, args: str) -> bool:
    """Handle /rename command."""
    if not args:
        print_error("Usage: /rename <new name>")
        return True
    session.model = session.model  # trigger save
    print_info(f"Session renamed (saved as {session.id[:8]})")
    return True


async def cmd_snapshot(
    session: Session,
    args: str,
    cwd: str,
) -> bool:
    """Handle /snapshot command."""
    from ccb.workspace_snapshot import get_snapshot_manager
    mgr = get_snapshot_manager()

    if args == "list":
        snaps = mgr.list_all()
        if not snaps:
            console.print("  No snapshots.")
        else:
            console.print(f"  [bold]{len(snaps)} snapshots:[/bold]")
            for snap_item in snaps[:10]:
                time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap_item.created_at))
                dirty = " [yellow](dirty)[/yellow]" if snap_item.git_dirty else ""
                console.print(f"  • {snap_item.id[:20]}… {time_str} {snap_item.git_branch}:{snap_item.git_commit[:8]}{dirty}")
                console.print(f"    {snap_item.description[:60]}")
        return True

    if args == "delete":
        arg = args.split(maxsplit=1)[1] if len(args.split()) > 1 else ""
        if arg:
            if mgr.delete(arg.strip()):
                print_info(f"Deleted snapshot: {arg.strip()[:20]}...")
            else:
                print_error(f"Snapshot not found: {arg.strip()}")
        else:
            print_error("Usage: /snapshot delete <id>")
        return True

    # Create new snapshot
    desc = args if args else ""
    snap = mgr.create(
        cwd=cwd,
        description=desc,
        session_id=session.id,
        session_messages_count=len(session.messages),
    )
    print_info(f"Snapshot created: {snap.id[:20]}...")
    console.print(f"  Branch: {snap.git_branch}")
    console.print(f"  Commit: {snap.git_commit}")
    console.print(f"  Dirty: {'yes' if snap.git_dirty else 'no'}")
    console.print(f"  Untracked: {len(snap.git_untracked_files)} files")
    console.print(f"  Deps tracked: {len(snap.dep_manifests)} manifests")
    return True


async def cmd_restore(
    session: Session,
    args: str,
    cwd: str,
) -> bool:
    """Handle /restore command."""
    from ccb.workspace_snapshot import get_snapshot_manager
    mgr = get_snapshot_manager()

    if not args:
        # List available snapshots
        snaps = mgr.list_all()
        if not snaps:
            console.print("  No snapshots available. Use /snapshot to create one.")
        else:
            console.print("  [bold]Available snapshots:[/bold]")
            for snap_item in snaps[:15]:
                time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap_item.created_at))
                dirty = " [yellow]*[/yellow]" if snap_item.git_dirty else ""
                console.print(f"  • {snap_item.id[:25]} — {time_str}")
                console.print(f"    {snap_item.git_branch}:{snap_item.git_commit[:8]}{dirty} — {snap_item.description[:50]}")
            console.print("\n  [dim]Usage: /restore <snapshot_id>[/dim]")
        return True

    # Load and restore
    snap_id = args.strip()
    restored_snap = mgr.load(snap_id)
    if not restored_snap:
        # Try partial match
        for snap_item in mgr.list_all():
            if snap_item.id.startswith(snap_id):
                restored_snap = snap_item
                break

    if not restored_snap:
        print_error(f"Snapshot not found: {snap_id}")
        return True

    console.print(f"[bold]Restoring snapshot:[/bold] {restored_snap.id[:25]}...")

    # Show comparison first
    changes = mgr.compare(restored_snap, cwd)
    if changes.get("git_commit_changed"):
        console.print(f"  Git: {changes['current_commit']} → {changes['snapshot_commit']}")
    if changes.get("deps_changed"):
        console.print(f"  Dependencies changed: {', '.join(changes['deps_changed'])}")

    # Restore git state
    result = mgr.restore_git(restored_snap, cwd)
    if result["success"]:
        print_info("Git state restored")
        for step in result.get("steps", []):
            console.print(f"  • {step}")
    else:
        print_error(f"Restore failed: {result.get('error', 'unknown error')}")

    # Offer to resume session if available
    if restored_snap.session_id and restored_snap.session_id != session.id:
        console.print(f"\n  [dim]Session context: {restored_snap.session_messages_count} messages in snapshot[/dim]")
        console.print(f"  [dim]Use /resume {restored_snap.session_id[:8]} to restore session[/dim]")

    return True


async def cmd_rewind(
    session: Session,
    args: str,
) -> bool:
    """Handle /rewind command."""
    n = 2
    if args:
        try:
            n = int(args)
        except ValueError:
            pass
    if len(session.messages) >= n:
        _ = session.messages[-n:]  # discarded messages
        session.messages = session.messages[:-n]
        print_info(f"Rewound {n} messages (now {len(session.messages)} remain)")
    else:
        print_info(f"Only {len(session.messages)} messages, cannot rewind {n}")
    return True


def cmd_summary(session: Session) -> bool:
    """Handle /summary command."""
    from ccb.api.base import Role
    user_count = sum(1 for m in session.messages if m.role == Role.USER)
    asst_count = sum(1 for m in session.messages if m.role == Role.ASSISTANT)
    console.print(f"  Session {session.id[:8]} — {user_count} user / {asst_count} assistant messages")
    console.print(f"  Tokens: {session.total_input_tokens + session.total_output_tokens:,}")
    if session.messages:
        first_user = next((m.content[:80] for m in session.messages if m.role == Role.USER), "")
        console.print(f"  First prompt: {first_user}...")
    return True


def cmd_share(session: Session) -> bool:
    """Handle /share command (alias for export md)."""
    from ccb.cmd_handlers._general import _export_session
    _export_session(session, "md")
    print_info("Conversation exported for sharing.")
    return True


def cmd_export(session: Session, args: str) -> bool:
    """Handle /export command."""
    from ccb.cmd_handlers._general import _export_session
    _export_session(session, args)
    return True


def cmd_copy(session: Session) -> bool:
    """Handle /copy command."""
    from ccb.cmd_handlers._general import _copy_last_reply
    _copy_last_reply(session)
    return True


def cmd_stats(session: Session) -> bool:
    """Handle /stats command."""
    from ccb.cmd_handlers._general import _show_stats
    _show_stats(session)
    return True


def cmd_history(session: Session) -> bool:
    """Handle /history command."""
    from ccb.api.base import Role
    for msg_idx, msg_ctx in enumerate(session.messages):
        role = "🧑" if msg_ctx.role == Role.USER else "🤖" if msg_ctx.role == Role.ASSISTANT else "🔧"
        preview = msg_ctx.content[:60].replace("\n", " ") if msg_ctx.content else "(tool result)"
        console.print(f"  {msg_idx+1:3d} {role} {preview}")
    return True


def cmd_tag(args: str, state: dict[str, Any]) -> bool:
    """Handle /tag command."""
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


def cmd_add_dir(args: str, state: dict[str, Any]) -> bool:
    """Handle /add-dir command."""
    if not args:
        print_error("Usage: /add-dir <path>")
        return True
    from pathlib import Path
    p = Path(args).expanduser().resolve()
    if p.is_dir():
        state.setdefault("extra_dirs", []).append(str(p))
        print_info(f"Added directory: {p}")
    else:
        print_error(f"Not a directory: {args}")
    return True


def cmd_passes(args: str, state: dict[str, Any]) -> bool:
    """Handle /passes command."""
    print_info("Multi-pass mode: let the model review its own output")
    state["passes"] = not state.get("passes", False)
    print_info(f"Passes: {'ON' if state['passes'] else 'OFF'}")
    return True


def cmd_thinkback(session: Session) -> bool:
    """Handle /thinkback and /thinkback-play commands."""
    console.print("[bold]🎬 Session Replay[/bold]")
    if session.messages:
        total = len(session.messages)
        console.print(f"Replaying {total} messages from this session:\n")
        for i, msg in enumerate(session.messages, 1):
            role = msg.role.value
            content = msg.content or ""
            preview = (content[:120] + "...") if len(content) > 120 else content
            icon = "🧑" if "user" in str(role) else "🤖"
            console.print(f"  {icon} [{i}/{total}] {preview}")
        console.print(f"\n[dim]Total: {total} messages, {session.total_input_tokens + session.total_output_tokens} tokens[/dim]")
    else:
        print_info("No messages in current session.")
    return True


def cmd_ctx_viz(session: Session) -> bool:
    """Handle /ctx_viz command."""
    from ccb.api.base import Role
    total_chars = sum(len(m.content or "") for m in session.messages)
    console.print("[bold]Context visualization:[/bold]")
    console.print(f"  Messages: {len(session.messages)}")
    console.print(f"  Total chars: {total_chars:,}")
    console.print(f"  Est. tokens: ~{total_chars // 4:,}")
    for msg_idx, msg_ctx in enumerate(session.messages):
        bar_len = min(50, max(1, len(msg_ctx.content or "") // 100))
        role_icon = "🧑" if msg_ctx.role == Role.USER else "🤖"
        bar = "█" * bar_len
        console.print(f"  {msg_idx+1:3d} {role_icon} {bar} ({len(msg_ctx.content or '')} chars)")
    return True


async def cmd_buddy(args: str, state: dict[str, Any], cwd: str) -> bool:
    """Handle /buddy command."""
    from ccb.cmd_handlers._general import _buddy
    await _buddy(args, state, cwd)
    return True
