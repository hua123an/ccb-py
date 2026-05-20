"""Tests for ccb.pipe_ipc, ccb.peer_discovery, and ccb.pipes_panel."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# pipe_ipc tests
# ---------------------------------------------------------------------------
from ccb.pipe_ipc import (
    PipeIPC,
    PipeMessage,
    MSG_TASK_ASSIGN,
    MSG_HEARTBEAT,
    MSG_DISCOVER,
    VALID_MSG_TYPES,
    _PIPE_DIR,
    _pipe_path_for,
)


class TestPipeMessage:
    def test_to_json_roundtrip(self):
        msg = PipeMessage("inst-1", MSG_HEARTBEAT, {"status": "ok"})
        raw = msg.to_json()
        restored = PipeMessage.from_json(raw)
        assert restored.sender_id == "inst-1"
        assert restored.msg_type == MSG_HEARTBEAT
        assert restored.payload == {"status": "ok"}
        assert isinstance(restored.timestamp, float)

    def test_invalid_msg_type_raises(self):
        with pytest.raises(ValueError, match="Invalid msg_type"):
            PipeMessage("x", "bogus", {})

    def test_all_valid_types(self):
        for t in VALID_MSG_TYPES:
            msg = PipeMessage("x", t, {})
            assert msg.msg_type == t

    def test_timestamp_preserved(self):
        ts = 1700000000.0
        msg = PipeMessage("x", MSG_DISCOVER, {}, timestamp=ts)
        assert msg.timestamp == ts
        raw = msg.to_json()
        restored = PipeMessage.from_json(raw)
        assert restored.timestamp == ts

    def test_payload_with_nested_data(self):
        payload = {"task": "fix bug", "priority": 1, "tags": ["a", "b"]}
        msg = PipeMessage("x", MSG_TASK_ASSIGN, payload)
        restored = PipeMessage.from_json(msg.to_json())
        assert restored.payload == payload


class TestPipeIPCInit:
    def test_default_instance_id(self):
        ipc = PipeIPC()
        assert ipc.instance_id.startswith("ccb-")
        assert len(ipc.instance_id) == 12  # "ccb-" + 8 hex chars

    def test_custom_instance_id(self):
        ipc = PipeIPC(instance_id="my-agent")
        assert ipc.instance_id == "my-agent"

    def test_default_pipe_path(self):
        ipc = PipeIPC(instance_id="abc")
        assert ipc.pipe_path == _PIPE_DIR / "abc.pipe"

    def test_custom_pipe_path(self, tmp_path):
        p = tmp_path / "custom.pipe"
        ipc = PipeIPC(pipe_path=str(p))
        assert ipc.pipe_path == p

    def test_pipe_path_for_helper(self):
        assert _pipe_path_for("foo") == _PIPE_DIR / "foo.pipe"


class TestPipeIPCCreatePipe:
    @pytest.mark.skipif(sys.platform == "win32", reason="FIFO is Unix-only")
    def test_create_pipe_makes_fifo(self, tmp_path):
        import stat
        pipe_path = tmp_path / "test.pipe"
        ipc = PipeIPC(instance_id="test", pipe_path=str(pipe_path))
        ipc.create_pipe()
        try:
            assert pipe_path.exists()
            assert stat.S_ISFIFO(os.stat(str(pipe_path)).st_mode)
        finally:
            pipe_path.unlink(missing_ok=True)

    @pytest.mark.skipif(sys.platform == "win32", reason="FIFO is Unix-only")
    def test_create_pipe_removes_old_fifo(self, tmp_path):
        pipe_path = tmp_path / "test.pipe"
        ipc = PipeIPC(instance_id="test", pipe_path=str(pipe_path))
        ipc.create_pipe()
        ipc.create_pipe()  # second call should not fail
        try:
            assert pipe_path.exists()
        finally:
            pipe_path.unlink(missing_ok=True)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_create_pipe_windows_fallback(self, tmp_path):
        pipe_path = tmp_path / "test.pipe"
        ipc = PipeIPC(instance_id="test", pipe_path=str(pipe_path))
        ipc.create_pipe()
        assert pipe_path.exists()


class TestPipeIPCPeerManagement:
    def test_discover_local_peers_empty(self, tmp_path):
        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        with patch("ccb.pipe_ipc._PIPE_DIR", tmp_path):
            peers = ipc.discover_local_peers()
        assert peers == []

    def test_discover_local_peers_finds_others(self, tmp_path):
        # Create fake peer pipes
        (tmp_path / "peer-a.pipe").write_text("")
        (tmp_path / "peer-b.pipe").write_text("")
        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        with patch("ccb.pipe_ipc._PIPE_DIR", tmp_path):
            peers = ipc.discover_local_peers()
        assert set(peers) == {"peer-a", "peer-b"}

    def test_discover_ignores_self(self, tmp_path):
        (tmp_path / "self.pipe").write_text("")
        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        with patch("ccb.pipe_ipc._PIPE_DIR", tmp_path):
            peers = ipc.discover_local_peers()
        assert "self" not in peers

    def test_list_peers(self):
        ipc = PipeIPC()
        ipc._peers["a"] = Path("/fake/a.pipe")
        ipc._peers["b"] = Path("/fake/b.pipe")
        result = ipc.list_peers()
        ids = {p["id"] for p in result}
        assert ids == {"a", "b"}


class TestPipeIPCStaleCleanup:
    def test_cleanup_removes_old_pipes(self, tmp_path):
        old_pipe = tmp_path / "stale.pipe"
        old_pipe.write_text("")
        # Set mtime to 10 minutes ago
        old_time = time.time() - 600
        os.utime(str(old_pipe), (old_time, old_time))

        new_pipe = tmp_path / "fresh.pipe"
        new_pipe.write_text("")

        ipc = PipeIPC(instance_id="x", pipe_path=str(tmp_path / "x.pipe"))
        with patch("ccb.pipe_ipc._PIPE_DIR", tmp_path):
            ipc._cleanup_stale_pipes()

        assert not old_pipe.exists()
        assert new_pipe.exists()


class TestPipeIPCConnectPipe:
    @pytest.mark.asyncio
    async def test_connect_pipe_registers_peer(self, tmp_path):
        peer_pipe = tmp_path / "peer.pipe"
        peer_pipe.write_text("")
        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        await ipc.connect_pipe("peer", str(peer_pipe))
        assert "peer" in ipc._peers
        assert ipc._peers["peer"] == peer_pipe

    @pytest.mark.asyncio
    async def test_connect_pipe_missing_file(self, tmp_path):
        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        await ipc.connect_pipe("ghost", str(tmp_path / "nonexistent.pipe"))
        assert "ghost" not in ipc._peers


class TestPipeIPCReceiveMessage:
    @pytest.mark.asyncio
    async def test_receive_from_inbox(self):
        ipc = PipeIPC()
        msg = PipeMessage("sender", MSG_HEARTBEAT, {"ping": True})
        await ipc._inbox.put(msg)
        result = await ipc.receive_message(timeout=1.0)
        assert result is not None
        assert result.sender_id == "sender"
        assert result.payload == {"ping": True}

    @pytest.mark.asyncio
    async def test_receive_timeout_returns_none(self):
        ipc = PipeIPC()
        result = await ipc.receive_message(timeout=0.05)
        assert result is None


class TestPipeIPCBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_peers(self, tmp_path):
        # Create real FIFOs on Unix for the test
        if sys.platform == "win32":
            pytest.skip("FIFO is Unix-only")

        pipe_a = tmp_path / "a.pipe"
        pipe_b = tmp_path / "b.pipe"
        os.mkfifo(str(pipe_a))
        os.mkfifo(str(pipe_b))

        ipc = PipeIPC(instance_id="self", pipe_path=str(tmp_path / "self.pipe"))
        ipc._peers["a"] = pipe_a
        ipc._peers["b"] = pipe_b

        # Open read ends so writes don't block

        read_fds: list[int] = []
        for p in (pipe_a, pipe_b):
            fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK)
            read_fds.append(fd)

        sent = await ipc.broadcast(MSG_DISCOVER, {"hello": True})
        assert sent == 2

        for fd in read_fds:
            os.close(fd)


# ---------------------------------------------------------------------------
# peer_discovery tests
# ---------------------------------------------------------------------------
from ccb.peer_discovery import (  # noqa: E402
    PeerDiscovery,
    PeerInfo,
    DEFAULT_PORT,
    BROADCAST_INTERVAL,
    PEER_STALE_TIMEOUT,
    PEER_DEAD_TIMEOUT,
    _get_local_ip,
)


class TestPeerInfo:
    def test_defaults(self):
        info = PeerInfo("p1", "192.168.1.1", 9876)
        assert info.id == "p1"
        assert info.host == "192.168.1.1"
        assert info.port == 9876
        assert info.model == ""
        assert info.status == "active"
        assert isinstance(info.last_seen, float)

    def test_to_dict(self):
        info = PeerInfo("p1", "10.0.0.1", 1234, model="gpt-4", status="busy")
        d = info.to_dict()
        assert d["id"] == "p1"
        assert d["host"] == "10.0.0.1"
        assert d["port"] == 1234
        assert d["model"] == "gpt-4"
        assert d["status"] == "busy"

    def test_is_stale_false_when_fresh(self):
        info = PeerInfo("p1", "h", 1)
        assert not info.is_stale()

    def test_is_stale_true_when_old(self):
        info = PeerInfo("p1", "h", 1, last_seen=time.time() - PEER_STALE_TIMEOUT - 1)
        assert info.is_stale()

    def test_is_dead_false_when_fresh(self):
        info = PeerInfo("p1", "h", 1)
        assert not info.is_dead()

    def test_is_dead_true_when_old(self):
        info = PeerInfo("p1", "h", 1, last_seen=time.time() - PEER_DEAD_TIMEOUT - 1)
        assert info.is_dead()

    def test_refresh_updates_fields(self):
        info = PeerInfo("p1", "h", 1, model="old", status="idle")
        old_ts = info.last_seen
        time.sleep(0.01)
        info.refresh(model="new", status="busy", task="coding")
        assert info.model == "new"
        assert info.status == "busy"
        assert info.task == "coding"
        assert info.last_seen > old_ts

    def test_refresh_preserves_empty_fields(self):
        info = PeerInfo("p1", "h", 1, model="gpt-4")
        info.refresh()
        assert info.model == "gpt-4"


class TestGetLocalIp:
    def test_returns_string(self):
        ip = _get_local_ip()
        assert isinstance(ip, str)
        # Should be a valid-looking IP or loopback
        parts = ip.split(".")
        assert len(parts) == 4


class TestPeerDiscoveryInit:
    def test_defaults(self):
        pd = PeerDiscovery()
        assert pd.instance_id.startswith("ccb-")
        assert pd.port == DEFAULT_PORT

    def test_custom_params(self):
        pd = PeerDiscovery(instance_id="my-id", port=12345, model="claude")
        assert pd.instance_id == "my-id"
        assert pd.port == 12345
        assert pd.model == "claude"


class TestPeerDiscoveryBroadcastHandling:
    def test_handle_broadcast_from_new_peer(self):
        pd = PeerDiscovery(instance_id="self")
        data = json.dumps({
            "id": "peer-1",
            "host": "192.168.1.10",
            "port": 9876,
            "model": "gpt-4",
            "status": "active",
            "task": "review",
            "ts": time.time(),
        }).encode("utf-8")
        pd._handle_broadcast(data, ("192.168.1.10", 9876))
        assert "peer-1" in pd._peers
        peer = pd._peers["peer-1"]
        assert peer.host == "192.168.1.10"
        assert peer.model == "gpt-4"
        assert peer.task == "review"

    def test_handle_broadcast_refreshes_existing(self):
        pd = PeerDiscovery(instance_id="self")
        pd._peers["peer-1"] = PeerInfo("peer-1", "old-host", 1111)
        data = json.dumps({
            "id": "peer-1",
            "host": "new-host",
            "port": 2222,
            "model": "new-model",
            "status": "busy",
        }).encode("utf-8")
        pd._handle_broadcast(data, ("new-host", 2222))
        peer = pd._peers["peer-1"]
        assert peer.model == "new-model"
        assert peer.status == "busy"

    def test_handle_broadcast_ignores_self(self):
        pd = PeerDiscovery(instance_id="self")
        data = json.dumps({"id": "self", "host": "h", "port": 1}).encode("utf-8")
        pd._handle_broadcast(data, ("h", 1))
        assert "self" not in pd._peers

    def test_handle_broadcast_ignores_empty_id(self):
        pd = PeerDiscovery()
        data = json.dumps({"id": "", "host": "h", "port": 1}).encode("utf-8")
        pd._handle_broadcast(data, ("h", 1))
        assert len(pd._peers) == 0

    def test_handle_broadcast_ignores_invalid_json(self):
        pd = PeerDiscovery()
        pd._handle_broadcast(b"not json", ("h", 1))
        assert len(pd._peers) == 0


class TestPeerDiscoveryGetPeers:
    def test_get_peers_filters_stale(self):
        pd = PeerDiscovery()
        pd._peers["fresh"] = PeerInfo("fresh", "h", 1)
        pd._peers["stale"] = PeerInfo(
            "stale", "h", 1, last_seen=time.time() - PEER_STALE_TIMEOUT - 1
        )
        active = pd.get_peers(include_stale=False)
        assert len(active) == 1
        assert active[0]["id"] == "fresh"

    def test_get_peers_include_stale(self):
        pd = PeerDiscovery()
        pd._peers["fresh"] = PeerInfo("fresh", "h", 1)
        pd._peers["stale"] = PeerInfo(
            "stale", "h", 1, last_seen=time.time() - PEER_STALE_TIMEOUT - 1
        )
        all_peers = pd.get_peers(include_stale=True)
        assert len(all_peers) == 2

    def test_get_peers_removes_dead(self):
        pd = PeerDiscovery()
        pd._peers["dead"] = PeerInfo(
            "dead", "h", 1, last_seen=time.time() - PEER_DEAD_TIMEOUT - 1
        )
        result = pd.get_peers()
        assert result == []
        assert "dead" not in pd._peers


class TestPeerDiscoveryPrune:
    def test_prune_dead_removes_old(self):
        pd = PeerDiscovery()
        pd._peers["alive"] = PeerInfo("alive", "h", 1)
        pd._peers["dead"] = PeerInfo(
            "dead", "h", 1, last_seen=time.time() - PEER_DEAD_TIMEOUT - 1
        )
        pd._prune_dead()
        assert "alive" in pd._peers
        assert "dead" not in pd._peers


class TestPeerDiscoveryMakePacket:
    def test_packet_is_valid_json(self):
        pd = PeerDiscovery(instance_id="test", model="claude-sonnet")
        packet = pd._make_presence_packet()
        data = json.loads(packet.decode("utf-8"))
        assert data["id"] == "test"
        assert data["model"] == "claude-sonnet"
        assert "ts" in data


class TestPeerDiscoveryLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        pd = PeerDiscovery(instance_id="test", port=19876)
        await pd.start()
        assert pd._running
        assert pd._listen_task is not None
        assert pd._broadcast_task is not None
        await pd.stop()
        assert not pd._running

    @pytest.mark.asyncio
    async def test_set_current_task(self):
        pd = PeerDiscovery()
        pd.set_current_task("fixing bug #123")
        assert pd._current_task == "fixing bug #123"


class TestPeerDiscoveryTwoInstances:
    """Integration test: two PeerDiscovery instances discovering each other."""

    @pytest.mark.asyncio
    async def test_mutual_discovery(self):
        port = 19877  # unique port to avoid conflicts
        pd1 = PeerDiscovery(instance_id="agent-1", port=port, model="claude")
        pd2 = PeerDiscovery(instance_id="agent-2", port=port, model="gpt-4")

        await pd1.start()
        await pd2.start()

        # Wait for at least one broadcast cycle + processing
        await asyncio.sleep(BROADCAST_INTERVAL + 1.0)

        peers1 = pd1.get_peers()
        peers2 = pd2.get_peers()

        # Each should discover the other
        ids1 = {p["id"] for p in peers1}
        ids2 = {p["id"] for p in peers2}

        await pd1.stop()
        await pd2.stop()

        assert "agent-2" in ids1
        assert "agent-1" in ids2


# ---------------------------------------------------------------------------
# pipes_panel tests
# ---------------------------------------------------------------------------
from ccb.pipes_panel import (  # noqa: E402
    build_peers_table,
    show_pipes_panel,
    _status_style,
    _format_age,
)


class TestStatusStyle:
    def test_active(self):
        assert _status_style("active") == "bold green"

    def test_busy(self):
        assert _status_style("busy") == "bold cyan"

    def test_stale(self):
        assert _status_style("stale") == "dim"

    def test_unknown(self):
        assert _status_style("unknown") == ""


class TestFormatAge:
    def test_just_now(self):
        assert _format_age(time.time()) == "just now"

    def test_seconds(self):
        assert _format_age(time.time() - 15) == "15s ago"

    def test_minutes(self):
        assert _format_age(time.time() - 120) == "2m ago"

    def test_hours(self):
        assert _format_age(time.time() - 7200) == "2h ago"


class TestBuildPeersTable:
    def test_empty_peers(self):
        table = build_peers_table("self", [])
        # Should have at least the self row
        assert table is not None

    def test_with_peers(self):
        peers = [
            {"id": "peer-1", "host": "10.0.0.1", "port": 9876,
             "model": "gpt-4", "status": "active", "task": "coding",
             "last_seen": time.time()},
            {"id": "peer-2", "host": "10.0.0.2", "port": 9876,
             "model": "claude", "status": "stale", "task": "",
             "last_seen": time.time() - 60},
        ]
        table = build_peers_table("self", peers, title="Test Panel")
        assert table is not None

    def test_long_task_truncated(self):
        peers = [
            {"id": "p1", "host": "h", "port": 1, "model": "",
             "status": "active", "task": "x" * 100,
             "last_seen": time.time()},
        ]
        table = build_peers_table("self", peers)
        assert table is not None


class TestShowPipesPanel:
    def test_runs_without_error(self, capsys):
        peers = [
            {"id": "p1", "host": "10.0.0.1", "port": 9876,
             "model": "gpt-4", "status": "active", "task": "test",
             "last_seen": time.time()},
        ]
        # Should not raise
        show_pipes_panel("self", peers)


class TestLivePipesPanel:
    @pytest.mark.asyncio
    async def test_live_panel_cancel(self):
        """Ensure live_pipes_panel can be cancelled cleanly."""
        source = MagicMock()
        source.get_peers.return_value = []

        task = asyncio.create_task(
            __import__("ccb.pipes_panel", fromlist=["live_pipes_panel"]).live_pipes_panel(
                "self", source, refresh_interval=0.1
            )
        )
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_live_panel_rejects_bad_source(self):
        with pytest.raises(TypeError, match="get_peers"):
            from ccb.pipes_panel import live_pipes_panel
            await live_pipes_panel("self", "not-an-object")
