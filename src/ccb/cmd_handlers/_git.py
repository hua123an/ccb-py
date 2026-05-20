"""Git-related slash commands."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ccb.display import repl_console as console, print_error, print_info
from ccb.session_repository import save_session
from ccb.session_runtime import emit_runtime_warning

if TYPE_CHECKING:
    from ccb.session import Session


async def cmd_diff(
    args: str,
    cwd: str,
) -> bool:
    """Handle /diff command."""
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
    diff_content = diff_text(staged=staged, cwd=cwd)
    if diff_content:
        from rich.syntax import Syntax
        console.print(Syntax(diff_content[:5000], "diff", theme="monokai"))
    return True


async def cmd_branch(
    args: str,
    cwd: str,
) -> bool:
    """Handle /branch command."""
    from ccb.git_ops import branches, checkout, create_branch, git_available
    if not git_available(cwd):
        print_error("Not in a git repository")
        return True
    if args.strip():
        sub = args.strip().split()
        if sub[0] in ("-b", "create", "new") and len(sub) > 1:
            ok, git_msg = create_branch(sub[1], cwd=cwd)
            print_info(f"{'✓' if ok else '✗'} {git_msg}")
        else:
            ok, git_msg = checkout(sub[0], cwd=cwd)
            print_info(f"{'✓' if ok else '✗'} {git_msg}")
    else:
        from ccb.select_ui import select_one
        brs = branches(cwd=cwd)
        if not brs:
            print_info("No branches found.")
            return True
        items = [{"label": b["name"], "description": "← current" if b["current"] else ""} for b in brs]
        choice = await select_one(items, title="Switch Branch")
        if choice is not None:
            ok, git_msg = checkout(brs[choice]["name"], cwd=cwd)
            print_info(f"{'✓' if ok else '✗'} {git_msg}")
    return True


async def cmd_commit(
    args: str,
    session: Session,
    provider,  # Provider
    registry,  # ToolRegistry
    cwd: str,
    mcp_manager=None,
) -> bool:
    """Handle /commit command."""
    from ccb.git_ops import generate_commit_message_prompt, diff_stat, stage, git_available
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
    try:
        save_session(session)
    except Exception as e:
        emit_runtime_warning(
            "command_commit_persist_failed",
            session_id=session.id,
            cwd=session.cwd or cwd,
            payload={"error": str(e)},
        )
    return True


async def cmd_undo(cwd: str) -> bool:
    """Handle /undo command."""
    from ccb.git_ops import undo_last_commit, git_available
    if not git_available(cwd):
        print_error("Not in a git repository")
        return True
    ok, git_msg = undo_last_commit(cwd=cwd)
    print_info(f"{'✓ Undid last commit' if ok else '✗ ' + git_msg}")
    return True


async def cmd_redo(cwd: str) -> bool:
    """Handle /redo command."""
    from ccb.git_ops import stash_pop, git_available
    if not git_available(cwd):
        print_error("Not in a git repository")
        return True
    ok, git_msg = stash_pop(cwd=cwd)
    print_info(f"{'✓' if ok else '✗'} {git_msg}")
    return True
