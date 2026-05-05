"""TUI panel for managing multi-instance pipe/peer connections.

Uses Rich to display a live dashboard of connected ccb-py instances
with their status, model, host, and current task.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()


def _status_style(status: str) -> str:
    """Return a Rich style for a peer status string."""
    mapping = {
        "active": "bold green",
        "idle": "yellow",
        "stale": "dim",
        "busy": "bold cyan",
        "error": "bold red",
    }
    return mapping.get(status, "")


def _format_age(last_seen: float) -> str:
    """Format seconds-since-last-seen as a human string."""
    age = time.time() - last_seen
    if age < 5:
        return "just now"
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age / 60)}m ago"
    return f"{int(age / 3600)}h ago"


def build_peers_table(
    instance_id: str,
    peers: list[dict[str, Any]],
    title: str = "Connected Instances",
) -> Table:
    """Build a Rich Table for the peers panel."""
    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("Instance", style="bold", min_width=16)
    table.add_column("Host", min_width=15)
    table.add_column("Port", justify="right", min_width=6)
    table.add_column("Model", min_width=20)
    table.add_column("Status", min_width=8)
    table.add_column("Task", ratio=1)
    table.add_column("Last Seen", min_width=10)

    # First row: ourselves
    table.add_row(
        f"{instance_id} (self)",
        "-",
        "-",
        "-",
        "[bold green]active[/bold green]",
        "",
        "now",
    )

    for peer in peers:
        status = peer.get("status", "unknown")
        style = _status_style(status)
        task = peer.get("task", "")
        if len(task) > 60:
            task = task[:57] + "..."
        table.add_row(
            peer.get("id", "?"),
            peer.get("host", "?"),
            str(peer.get("port", "?")),
            peer.get("model", ""),
            f"[{style}]{status}[/{style}]" if style else status,
            task,
            _format_age(peer.get("last_seen", time.time())),
        )

    return table


def show_pipes_panel(
    instance_id: str,
    peers: list[dict[str, Any]],
    title: str = "Pipes Panel",
) -> None:
    """Print a one-shot snapshot of the peers panel."""
    table = build_peers_table(instance_id, peers, title=title)
    console.print(table)


async def live_pipes_panel(
    instance_id: str,
    peer_source: Any,
    refresh_interval: float = 2.0,
    title: str = "Pipes Panel",
) -> None:
    """Show a live-refreshing peers panel.

    Args:
        instance_id: This instance's ID.
        peer_source: An object with a ``get_peers()`` method (PeerDiscovery
            or PipeIPC).
        refresh_interval: Seconds between refreshes.
        title: Panel title.
    """
    get_peers = getattr(peer_source, "get_peers", None)
    if get_peers is None:
        raise TypeError("peer_source must have a get_peers() method")

    with Live(console=console, refresh_per_second=1) as live:
        while True:
            try:
                peers = get_peers()
                table = build_peers_table(instance_id, peers, title=title)
                live.update(table)
                await asyncio.sleep(refresh_interval)
            except asyncio.CancelledError:
                break
