"""Remote Control Web UI for ccb-py.

Provides a self-hosted single-page web interface for mobile access,
allowing users to view conversations, send messages, switch sessions,
and monitor server status from any browser.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Embedded HTML / CSS / JS -- no external dependencies, no build step
# ---------------------------------------------------------------------------

_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>ccb-py Remote</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#1a1a2e;--surface:#16213e;--border:#0f3460;--accent:#e94560;
--text:#eee;--muted:#8899aa;--green:#2ecc71;--radius:8px}
body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);
color:var(--text);min-height:100vh;display:flex;flex-direction:column}
header{background:var(--surface);padding:12px 16px;display:flex;
align-items:center;gap:12px;border-bottom:1px solid var(--border)}
header h1{font-size:1.1rem;flex:1}
.badge{background:var(--accent);color:#fff;border-radius:12px;
padding:2px 8px;font-size:.75rem}
.tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border)}
.tab{padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;
color:var(--muted);font-size:.9rem}
.tab.active{color:var(--text);border-bottom-color:var(--accent)}
.panel{display:none;flex:1;overflow:auto;padding:12px}
.panel.active{display:flex;flex-direction:column}
/* Sessions list */
.session-item{background:var(--surface);border:1px solid var(--border);
border-radius:var(--radius);padding:10px 12px;margin-bottom:8px;cursor:pointer}
.session-item:hover{border-color:var(--accent)}
.session-item.active{border-color:var(--accent);background:#1a1a3e}
.session-meta{color:var(--muted);font-size:.75rem;margin-top:4px}
/* Messages */
#messages{flex:1;overflow-y:auto;padding:8px 0}
.msg{margin-bottom:12px;max-width:85%}
.msg.user{margin-left:auto}
.msg .role{font-size:.7rem;color:var(--muted);margin-bottom:2px}
.msg .bubble{padding:8px 12px;border-radius:var(--radius);white-space:pre-wrap;
word-break:break-word;font-size:.88rem;line-height:1.4}
.msg.user .bubble{background:var(--accent);color:#fff}
.msg.assistant .bubble{background:var(--surface);border:1px solid var(--border)}
.msg.tool .bubble{background:#1a2a3e;border:1px solid #2a4a6e;font-family:monospace;
font-size:.78rem;max-height:200px;overflow:auto}
/* Input bar */
.input-bar{display:flex;gap:8px;padding:10px 0;border-top:1px solid var(--border)}
.input-bar textarea{flex:1;background:var(--surface);color:var(--text);
border:1px solid var(--border);border-radius:var(--radius);padding:8px;
resize:none;font-size:.9rem;min-height:40px;max-height:120px}
.input-bar button{background:var(--accent);color:#fff;border:none;
border-radius:var(--radius);padding:0 20px;font-size:.9rem;cursor:pointer}
.input-bar button:disabled{opacity:.5}
/* Status */
.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.status-card{background:var(--surface);border:1px solid var(--border);
border-radius:var(--radius);padding:14px}
.status-card .label{color:var(--muted);font-size:.75rem}
.status-card .value{font-size:1.3rem;margin-top:4px}
/* Tool output viewer */
.tool-detail{background:#0d1b2a;border:1px solid var(--border);
border-radius:var(--radius);padding:10px;margin-top:6px;font-family:monospace;
font-size:.78rem;max-height:300px;overflow:auto;white-space:pre-wrap}
</style>
</head>
<body>
<header>
  <h1>ccb-py Remote</h1>
  <span class="badge" id="conn-badge">connecting</span>
</header>
<div class="tabs">
  <div class="tab active" data-tab="chat">Chat</div>
  <div class="tab" data-tab="sessions">Sessions</div>
  <div class="tab" data-tab="status">Status</div>
</div>

<div class="panel active" id="panel-chat">
  <div id="messages"></div>
  <div class="input-bar">
    <textarea id="input" placeholder="Type a message..." rows="1"></textarea>
    <button id="send-btn" onclick="sendMessage()">Send</button>
  </div>
</div>

<div class="panel" id="panel-sessions">
  <div id="session-list"></div>
</div>

<div class="panel" id="panel-status">
  <div class="status-grid" id="status-grid"></div>
</div>

<script>
const API = '';
let currentSession = null;
let ws = null;

// Tab switching
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'sessions') loadSessions();
    if (t.dataset.tab === 'status') loadStatus();
  });
});

// Auto-resize textarea
const ta = document.getElementById('input');
ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; });
ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });

function authHeaders() {
  const h = {'Content-Type': 'application/json'};
  const token = localStorage.getItem('ccb_token');
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

async function loadSessions() {
  try {
    const r = await fetch(API + '/api/sessions', {headers: authHeaders()});
    const data = await r.json();
    const el = document.getElementById('session-list');
    el.innerHTML = '';
    (data.sessions || []).forEach(s => {
      const d = document.createElement('div');
      d.className = 'session-item' + (currentSession === s.id ? ' active' : '');
      d.innerHTML = `<div>${s.id.slice(0,12)}...</div>
        <div class="session-meta">${s.model || 'unknown'} &middot; ${s.messages || 0} msgs &middot; ${s.cwd || ''}</div>`;
      d.onclick = () => selectSession(s.id);
      el.appendChild(d);
    });
    if (!(data.sessions || []).length) el.innerHTML = '<p style="color:var(--muted)">No sessions found</p>';
  } catch(e) { console.error('loadSessions', e); }
}

async function selectSession(sid) {
  currentSession = sid;
  document.querySelectorAll('.tab')[0].click();
  const r = await fetch(API + '/api/sessions/' + sid + '/messages', {headers: authHeaders()});
  const data = await r.json();
  renderMessages(data.messages || []);
}

function renderMessages(msgs) {
  const el = document.getElementById('messages');
  el.innerHTML = '';
  msgs.forEach(m => {
    const div = document.createElement('div');
    div.className = 'msg ' + (m.role || 'assistant');
    let content = m.content || '';
    if (m.tool_calls && m.tool_calls.length) {
      content += '\n[Tools: ' + m.tool_calls.map(t => t.name).join(', ') + ']';
    }
    if (m.tool_results && m.tool_results.length) {
      m.tool_results.forEach(tr => {
        const td = document.createElement('div');
        td.className = 'msg tool';
        td.innerHTML = `<div class="role">tool: ${tr.tool_use_id || ''}</div>
          <div class="bubble">${esc(tr.content || '').slice(0, 2000)}</div>`;
        el.appendChild(td);
      });
    }
    div.innerHTML = `<div class="role">${m.role || '?'}</div><div class="bubble">${esc(content)}</div>`;
    el.appendChild(div);
  });
  el.scrollTop = el.scrollHeight;
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function sendMessage() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  input.style.height = 'auto';

  // Optimistic local render
  const el = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = `<div class="role">you</div><div class="bubble">${esc(text)}</div>`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;

  document.getElementById('send-btn').disabled = true;

  try {
    const sid = currentSession || '';
    const r = await fetch(API + '/api/sessions/' + sid + '/message', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({message: text}),
    });
    const data = await r.json();
    if (data.session_id && !currentSession) currentSession = data.session_id;
    if (data.messages) renderMessages(data.messages);
    else if (data.response) {
      const rd = document.createElement('div');
      rd.className = 'msg assistant';
      rd.innerHTML = `<div class="role">assistant</div><div class="bubble">${esc(data.response)}</div>`;
      el.appendChild(rd);
      el.scrollTop = el.scrollHeight;
    }
  } catch(e) {
    const ed = document.createElement('div');
    ed.className = 'msg assistant';
    ed.innerHTML = `<div class="bubble" style="color:var(--accent)">Error: ${esc(String(e))}</div>`;
    el.appendChild(ed);
  }
  document.getElementById('send-btn').disabled = false;
}

async function loadStatus() {
  try {
    const r = await fetch(API + '/api/status', {headers: authHeaders()});
    const data = await r.json();
    const el = document.getElementById('status-grid');
    const items = [
      ['Model', data.model || 'n/a'],
      ['Provider', data.provider || 'n/a'],
      ['Sessions', data.active_sessions ?? 0],
      ['Uptime', data.uptime || 'n/a'],
      ['Input Tokens', (data.total_input_tokens || 0).toLocaleString()],
      ['Output Tokens', (data.total_output_tokens || 0).toLocaleString()],
      ['Est. Cost', '$' + (data.estimated_cost || '0.00')],
      ['CWD', data.cwd || 'n/a'],
    ];
    el.innerHTML = items.map(([l,v]) =>
      `<div class="status-card"><div class="label">${l}</div><div class="value">${v}</div></div>`
    ).join('');
  } catch(e) { console.error('loadStatus', e); }
}

// WebSocket for real-time updates
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => {
    document.getElementById('conn-badge').textContent = 'connected';
    document.getElementById('conn-badge').style.background = 'var(--green)';
  };
  ws.onclose = () => {
    document.getElementById('conn-badge').textContent = 'disconnected';
    document.getElementById('conn-badge').style.background = 'var(--accent)';
    setTimeout(connectWS, 3000);
  };
  ws.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      if (data.type === 'message' && data.session_id === currentSession) {
        const el = document.getElementById('messages');
        const div = document.createElement('div');
        div.className = 'msg ' + (data.role || 'assistant');
        div.innerHTML = `<div class="role">${data.role || 'assistant'}</div>
          <div class="bubble">${esc(data.content || '')}</div>`;
        el.appendChild(div);
        el.scrollTop = el.scrollHeight;
      }
    } catch(e) {}
  };
}
connectWS();
loadSessions();
</script>
</body>
</html>"""


class RemoteControlServer:
    """Self-hosted web UI for remote access to ccb-py.

    Serves a single-page mobile-friendly app with REST API and WebSocket
    endpoints for viewing/interacting with sessions.

    Args:
        host: Bind address (default ``127.0.0.1``).
        port: Bind port (default ``8090``).
        token: Optional bearer token for auth (also read from
            ``CCB_REMOTE_TOKEN`` env var).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8090, token: str = ""):
        self.host = host
        self.port = port
        self.token = token or os.environ.get("CCB_REMOTE_TOKEN", "")
        self._ws_clients: dict[str, Any] = {}
        self._running = False
        self._start_time = time.time()
        self._active_sessions: dict[str, dict[str, Any]] = {}

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the HTTP + WebSocket server."""
        try:
            from aiohttp import web
        except ImportError:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/sessions", self._handle_list_sessions)
        app.router.add_get("/api/sessions/{sid}/messages", self._handle_get_messages)
        app.router.add_post("/api/sessions/{sid}/message", self._handle_send_message)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/ws", self._handle_websocket)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._running = True
        print(f"ccb-py Remote Control: http://{self.host}:{self.port}")

    def register_session(self, session_id: str, model: str = "", cwd: str = "") -> None:
        """Register an active session so it appears in the web UI."""
        self._active_sessions[session_id] = {
            "id": session_id,
            "model": model,
            "cwd": cwd,
            "created_at": time.time(),
        }

    async def broadcast(self, event: str, data: Any = None) -> int:
        """Push an event to all connected WebSocket clients."""
        msg = json.dumps({"type": event, **(data or {})})
        sent = 0
        for ws in list(self._ws_clients.values()):
            try:
                await ws.send_str(msg)
                sent += 1
            except Exception:
                pass
        return sent

    @property
    def is_running(self) -> bool:
        return self._running

    def summary(self) -> dict[str, Any]:
        """Return a status summary dict."""
        return {
            "running": self._running,
            "host": self.host,
            "port": self.port,
            "ws_clients": len(self._ws_clients),
            "active_sessions": len(self._active_sessions),
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "auth_enabled": bool(self.token),
        }

    # ── Auth helper ───────────────────────────────────────────────

    def _check_auth(self, request: Any) -> bool:
        """Return True if request is authorized (or auth is disabled)."""
        if not self.token:
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:] == self.token
        # Also accept query param for simple mobile use
        return request.query.get("token", "") == self.token

    # ── Route handlers ────────────────────────────────────────────

    async def _handle_index(self, request: Any) -> Any:
        from aiohttp import web
        if not self._check_auth(request):
            return web.Response(status=401, text="Unauthorized")
        return web.Response(text=_HTML_PAGE, content_type="text/html")

    async def _handle_list_sessions(self, request: Any) -> Any:
        from aiohttp import web
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Merge persisted sessions with in-memory active ones
        sessions: list[dict[str, Any]] = []
        try:
            from ccb.session import Session
            sessions = Session.list_sessions(limit=30)
        except Exception:
            pass

        # Add any active sessions not already in the list
        known_ids = {s["id"] for s in sessions}
        for sid, info in self._active_sessions.items():
            if sid not in known_ids:
                sessions.insert(0, info)

        return web.json_response({"sessions": sessions})

    async def _handle_get_messages(self, request: Any) -> Any:
        from aiohttp import web
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        sid = request.match_info["sid"]
        try:
            from ccb.session import Session
            session = Session.load(sid)
            if session:
                msgs = []
                for m in session.messages:
                    d: dict[str, Any] = {
                        "role": m.role.value,
                        "content": m.content,
                    }
                    if m.tool_calls:
                        d["tool_calls"] = [
                            {"id": tc.id, "name": tc.name, "input": tc.input}
                            for tc in m.tool_calls
                        ]
                    if m.tool_results:
                        d["tool_results"] = [
                            {"tool_use_id": tr.tool_use_id, "content": tr.content, "is_error": tr.is_error}
                            for tr in m.tool_results
                        ]
                    msgs.append(d)
                return web.json_response({"session_id": sid, "messages": msgs})
        except Exception:
            pass
        return web.json_response({"session_id": sid, "messages": []})

    async def _handle_send_message(self, request: Any) -> Any:
        from aiohttp import web
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        sid = request.match_info["sid"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message = body.get("message", "").strip()
        if not message:
            return web.json_response({"error": "Empty message"}, status=400)

        # Try to use the query engine for a response
        try:
            from ccb.query_engine import run_query
            result = await run_query(message)
            # Broadcast to WebSocket clients
            await self.broadcast("message", {
                "session_id": sid,
                "role": "assistant",
                "content": result,
            })
            return web.json_response({
                "session_id": sid,
                "response": result,
            })
        except ImportError:
            return web.json_response({
                "session_id": sid,
                "response": f"[queued] {message}",
                "note": "query_engine not available; message recorded",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_status(self, request: Any) -> Any:
        from aiohttp import web
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        uptime_s = time.time() - self._start_time
        hours = int(uptime_s // 3600)
        mins = int((uptime_s % 3600) // 60)

        model = ""
        provider = ""
        total_in = 0
        total_out = 0
        try:
            from ccb.config import get_model, get_provider
            model = get_model()
            provider = get_provider()
        except Exception:
            pass
        try:
            from ccb.cost_tracker import get_cost_state
            cs = get_cost_state()
            total_in = getattr(cs, "total_input_tokens", 0)
            total_out = getattr(cs, "total_output_tokens", 0)
        except Exception:
            pass

        return web.json_response({
            "running": True,
            "model": model,
            "provider": provider,
            "active_sessions": len(self._active_sessions),
            "uptime": f"{hours}h {mins}m",
            "uptime_seconds": round(uptime_s, 1),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "estimated_cost": f"{(total_in * 0.003 + total_out * 0.015) / 1000:.4f}",
            "cwd": os.getcwd(),
            "ws_clients": len(self._ws_clients),
        })

    async def _handle_websocket(self, request: Any) -> Any:
        from aiohttp import web
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        ws_id = str(uuid.uuid4())[:8]
        self._ws_clients[ws_id] = ws

        try:
            async for msg in ws:
                if msg.type == 1:  # TEXT
                    try:
                        data = json.loads(msg.data)
                        method = data.get("method", "")
                        if method == "subscribe":
                            await ws.send_json({"type": "subscribed", "channel": data.get("channel", "")})
                        elif method == "ping":
                            await ws.send_json({"type": "pong", "time": time.time()})
                        elif method == "send_message":
                            sid = data.get("session_id", "")
                            text = data.get("message", "")
                            if text:
                                try:
                                    from ccb.query_engine import run_query
                                    result = await run_query(text)
                                    await ws.send_json({"type": "response", "session_id": sid, "content": result})
                                except Exception as e:
                                    await ws.send_json({"type": "error", "error": str(e)})
                        else:
                            await ws.send_json({"type": "error", "error": f"Unknown method: {method}"})
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "error": "Invalid JSON"})
                elif msg.type == 258:  # ERROR
                    break
        finally:
            self._ws_clients.pop(ws_id, None)
        return ws


# Convenience: module-level singleton
_server: RemoteControlServer | None = None


def get_remote_server(**kwargs: Any) -> RemoteControlServer:
    """Get or create the module-level RemoteControlServer singleton."""
    global _server
    if _server is None:
        _server = RemoteControlServer(**kwargs)
    return _server
