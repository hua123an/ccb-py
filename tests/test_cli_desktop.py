from __future__ import annotations

from unittest.mock import patch


def test_desktop_command_invokes_launcher() -> None:
    from ccb.cli import main

    assert "desktop" in main.commands
    command = main.commands["desktop"]
    called: dict[str, str | None] = {}

    def _fake_launch_desktop_app(*, model=None, cwd=None) -> None:
        called["model"] = model
        called["cwd"] = cwd

    with patch("ccb.desktop_app.launch_desktop_app", _fake_launch_desktop_app):
        command.callback(model="gpt-4o", cwd="/tmp/demo")

    assert called == {"model": "gpt-4o", "cwd": "/tmp/demo"}
