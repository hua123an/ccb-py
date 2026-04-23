"""LSP (Language Server Protocol) client for ccb-py.

Connects to language servers for code intelligence:
completions, diagnostics, go-to-definition, references.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Diagnostic:
    path: str
    line: int
    column: int
    severity: str  # "error", "warning", "info", "hint"
    message: str
    source: str = ""


@dataclass
class Location:
    path: str
    line: int
    column: int


@dataclass
class CompletionItem:
    label: str
    kind: str = ""
    detail: str = ""
    documentation: str = ""


# Language server commands for common languages
LANGUAGE_SERVERS = {
    "python": {"cmd": ["pylsp"], "install": "pip install python-lsp-server"},
    "typescript": {"cmd": ["typescript-language-server", "--stdio"], "install": "npm i -g typescript-language-server"},
    "javascript": {"cmd": ["typescript-language-server", "--stdio"], "install": "npm i -g typescript-language-server"},
    "rust": {"cmd": ["rust-analyzer"], "install": "rustup component add rust-analyzer"},
    "go": {"cmd": ["gopls"], "install": "go install golang.org/x/tools/gopls@latest"},
    "c": {"cmd": ["clangd"], "install": "apt install clangd / brew install llvm"},
    "cpp": {"cmd": ["clangd"], "install": "apt install clangd / brew install llvm"},
    "java": {"cmd": ["jdtls"], "install": "brew install jdtls"},
    "lua": {"cmd": ["lua-language-server"], "install": "brew install lua-language-server"},
}


class LSPClient:
    """Simple LSP client over stdio."""

    def __init__(self, language: str, cwd: str | None = None):
        self.language = language
        self.cwd = cwd or os.getcwd()
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._initialized = False
        self._reader_task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        server = LANGUAGE_SERVERS.get(self.language)
        if not server:
            return False
        try:
            subprocess.run(["which", server["cmd"][0]], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    async def start(self) -> bool:
        """Start the language server process."""
        server = LANGUAGE_SERVERS.get(self.language)
        if not server:
            return False
        try:
            self._process = await asyncio.create_subprocess_exec(
                *server["cmd"],
                cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False

        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize
        result = await self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": f"file://{self.cwd}",
            "capabilities": {},
        })
        if result is not None:
            await self._send_notification("initialized", {})
            self._initialized = True
            return True
        return False

    async def stop(self) -> None:
        if self._process:
            await self._send_request("shutdown", {})
            await self._send_notification("exit", {})
            self._process.terminate()
            self._process = None
        if self._reader_task:
            self._reader_task.cancel()

    async def _send_request(self, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        rid = self._request_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        await self._write_message(msg)
        try:
            return await asyncio.wait_for(future, timeout=10)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return None

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write_message(msg)

    async def _write_message(self, msg: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            return
        body = json.dumps(msg)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        self._process.stdin.write(header.encode() + body.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        if not self._process or not self._process.stdout:
            return
        while True:
            try:
                header = await self._process.stdout.readline()
                if not header:
                    break
                if header.startswith(b"Content-Length:"):
                    length = int(header.split(b":")[1].strip())
                    await self._process.stdout.readline()  # empty line
                    body = await self._process.stdout.readexactly(length)
                    msg = json.loads(body)
                    self._handle_message(msg)
            except (asyncio.IncompleteReadError, json.JSONDecodeError, Exception):
                break

    def _handle_message(self, msg: dict[str, Any]) -> None:
        if "id" in msg and msg["id"] in self._pending:
            future = self._pending.pop(msg["id"])
            if "result" in msg:
                future.set_result(msg["result"])
            elif "error" in msg:
                future.set_result(None)
        elif msg.get("method") == "textDocument/publishDiagnostics":
            self._handle_diagnostics(msg.get("params", {}))

    def _handle_diagnostics(self, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        path = uri.replace("file://", "")
        self._diagnostics[path] = [
            Diagnostic(
                path=path,
                line=d.get("range", {}).get("start", {}).get("line", 0),
                column=d.get("range", {}).get("start", {}).get("character", 0),
                severity=["", "error", "warning", "info", "hint"][min(d.get("severity", 0), 4)],
                message=d.get("message", ""),
                source=d.get("source", ""),
            )
            for d in params.get("diagnostics", [])
        ]

    # ── Public API ──

    async def get_diagnostics(self, path: str) -> list[Diagnostic]:
        """Get diagnostics for a file."""
        uri = f"file://{os.path.abspath(path)}"
        await self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self.language,
                "version": 1,
                "text": Path(path).read_text(),
            }
        })
        await asyncio.sleep(2)  # Wait for diagnostics
        return self._diagnostics.get(os.path.abspath(path), [])

    async def get_completions(self, path: str, line: int, column: int) -> list[CompletionItem]:
        uri = f"file://{os.path.abspath(path)}"
        result = await self._send_request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
        })
        if not result:
            return []
        items = result if isinstance(result, list) else result.get("items", [])
        return [
            CompletionItem(
                label=i.get("label", ""),
                kind=str(i.get("kind", "")),
                detail=i.get("detail", ""),
            )
            for i in items[:20]
        ]

    async def goto_definition(self, path: str, line: int, column: int) -> Location | None:
        uri = f"file://{os.path.abspath(path)}"
        result = await self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
        })
        if not result:
            return None
        loc = result[0] if isinstance(result, list) else result
        return Location(
            path=loc.get("uri", "").replace("file://", ""),
            line=loc.get("range", {}).get("start", {}).get("line", 0),
            column=loc.get("range", {}).get("start", {}).get("character", 0),
        )

    async def find_references(self, path: str, line: int, column: int) -> list[Location]:
        uri = f"file://{os.path.abspath(path)}"
        result = await self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
            "context": {"includeDeclaration": True},
        })
        if not result:
            return []
        return [
            Location(
                path=r.get("uri", "").replace("file://", ""),
                line=r.get("range", {}).get("start", {}).get("line", 0),
                column=r.get("range", {}).get("start", {}).get("character", 0),
            )
            for r in result
        ]

    @property
    def all_diagnostics(self) -> dict[str, list[Diagnostic]]:
        return self._diagnostics
