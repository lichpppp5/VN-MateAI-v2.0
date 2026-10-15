# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/mcp/client.py
===================================
Client Model Context Protocol (MCP) tối giản: đủ để dùng CÔNG CỤ của một MCP server có sẵn trong doanh nghiệp
(docs/integrations/agent-gateway.md). Không dùng SDK `mcp` (kéo theo starlette / anyio / sse-starlette dễ xung đột phiên bản FastAPI).

Hỗ trợ:
- `initialize` → `notifications/initialized`, `tools/list` (có phân trang `cursor`), `tools/call`, `ping`.
- Streamable HTTP: POST JSON-RPC; máy chủ trả `application/json` hoặc `text/event-stream` (SSE); giữ `Mcp-Session-Id`, gửi
  `MCP-Protocol-Version`. Phiên hết hạn (HTTP 404 khi đã có session) → khởi tạo lại một lần.
- stdio: chạy tiến trình con, mỗi dòng một thông điệp JSON-RPC. Yêu cầu do server gửi ngược (vd `ping`) được trả lời / từ chối.

Không hỗ trợ (chưa cần cho công cụ): resources, prompts, sampling, elicitation, roots.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "VN-MateAI", "version": "1.0"}
MAX_TOOLS = 500
MAX_LIST_PAGES = 20
_MAX_LINE = 8 * 1024 * 1024


class MCPError(RuntimeError):
    """Lỗi kết nối / giao thức / lỗi JSON-RPC do server trả."""


def _rpc_error(msg: Dict[str, Any]) -> MCPError:
    err = msg.get("error") or {}
    return MCPError(f"MCP lỗi {err.get('code')}: {str(err.get('message') or '')[:300]}")


# ── HTTP (Streamable HTTP) ──────────────────────────────────────────────────

class HttpTransport:
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, verify_tls: bool = True,
                 client: Optional[httpx.AsyncClient] = None) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout, verify=verify_tls, follow_redirects=False)
        self._own_client = client is None
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None

    def _hdrs(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    async def request(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        r = await self._client.post(self.url, json=msg, headers=self._hdrs(), timeout=self.timeout)
        if r.status_code == 404 and self.session_id:
            raise SessionExpired()
        if r.status_code >= 400:
            raise MCPError(f"HTTP {r.status_code} từ MCP server: {r.text[:200]}")
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype == "text/event-stream":
            return _pick_sse(r.text, msg["id"])
        try:
            body = r.json()
        except ValueError as exc:
            raise MCPError(f"MCP server trả nội dung không phải JSON ({ctype or 'không rõ'})") from exc
        if isinstance(body, list):                 # batch: lấy đúng phản hồi theo id
            body = next((m for m in body if isinstance(m, dict) and m.get("id") == msg["id"]), {})
        if not isinstance(body, dict) or body.get("id") != msg["id"]:
            raise MCPError("MCP server trả phản hồi không khớp yêu cầu")
        return body

    async def notify(self, msg: Dict[str, Any]) -> None:
        r = await self._client.post(self.url, json=msg, headers=self._hdrs(), timeout=self.timeout)
        if r.status_code >= 400 and r.status_code != 404:
            logger.debug("[MCP] notify %s -> HTTP %s", msg.get("method"), r.status_code)

    async def close(self) -> None:
        if self.session_id:
            try:
                await self._client.delete(self.url, headers=self._hdrs(), timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
        if self._own_client:
            await self._client.aclose()


class SessionExpired(MCPError):
    pass


def _pick_sse(text: str, want_id: Any) -> Dict[str, Any]:
    """Đọc các sự kiện SSE, trả thông điệp JSON-RPC có đúng `id` (bỏ qua thông báo tiến độ / log)."""
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data:
            continue
        try:
            msg = json.loads(data)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id and ("result" in msg or "error" in msg):
            return msg
    raise MCPError("Luồng SSE của MCP server không có phản hồi cho yêu cầu")


# ── stdio ───────────────────────────────────────────────────────────────────

#: Biến môi trường được chuyển cho tiến trình MCP stdio. KHÔNG chuyển cả môi trường của máy chủ: trong đó có mật khẩu mặc định, khoá
#: LLM, chuỗi kết nối CSDL (VNMATEAI_*, VNMATE_*…). Chỉ những gì một chương trình cần để chạy + proxy / chứng chỉ; còn lại admin khai
#: báo riêng trong `env` của server (được mã hoá khi lưu).
SAFE_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "PROGRAMDATA", "PROGRAMFILES", "TEMP", "TMP",
    "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "USERNAME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "PYTHONIOENCODING", "PYTHONUTF8",
)


def child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra or {})
    return env


class StdioTransport:
    def __init__(self, command: str, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None,
                 cwd: Optional[str] = None, timeout: float = 30.0) -> None:
        self.command, self.args, self.env, self.cwd, self.timeout = command, list(args or []), dict(env or {}), cwd, timeout
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.protocol_version: Optional[str] = None

    async def _start(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            self.command, *self.args, cwd=self.cwd or None, env=child_env(self.env),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=_MAX_LINE)
        self._stderr_task = asyncio.get_running_loop().create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            logger.debug("[MCP stdio %s] %s", os.path.basename(self.command), line.decode("utf-8", "replace").rstrip()[:300])

    async def _send(self, msg: Dict[str, Any]) -> None:
        await self._start()
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def request(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        await self._send(msg)
        return await asyncio.wait_for(self._read_until(msg["id"]), self.timeout)

    async def _read_until(self, want_id: Any) -> Dict[str, Any]:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise MCPError(f"Tiến trình MCP đã thoát (mã {self.proc.returncode})")
            try:
                msg = json.loads(line)
            except ValueError:
                continue                               # dòng log lạc vào stdout
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
            if "method" in msg and "id" in msg:        # server hỏi ngược
                reply = {"jsonrpc": "2.0", "id": msg["id"]}
                if msg["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "VN-MateAI không hỗ trợ yêu cầu này"}
                await self._send(reply)

    async def notify(self, msg: Dict[str, Any]) -> None:
        await self._send(msg)

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        if self._stderr_task:
            self._stderr_task.cancel()


# ── client ──────────────────────────────────────────────────────────────────

class MCPClient:
    """Một kết nối tới một MCP server. Dùng tuần tự (khoá nội bộ); tự khởi tạo lần đầu / sau khi phiên hết hạn."""

    def __init__(self, transport: Any) -> None:
        self.t = transport
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self._ready = False

    async def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        msg = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        out = await self.t.request(msg)
        if "error" in out:
            raise _rpc_error(out)
        return out.get("result")

    async def _initialize(self) -> None:
        res = await self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO})
        if not isinstance(res, dict):
            raise MCPError("MCP server trả kết quả initialize không hợp lệ")
        self.server_info = res.get("serverInfo") or {}
        self.capabilities = res.get("capabilities") or {}
        self.t.protocol_version = str(res.get("protocolVersion") or PROTOCOL_VERSION)
        await self.t.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._ready = True

    async def _with_session(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        async with self._lock:
            if not self._ready:
                await self._initialize()
            try:
                return await self._call(method, params)
            except SessionExpired:
                logger.info("[MCP] Phiên hết hạn — khởi tạo lại")
                self.t.session_id = None
                self._ready = False
                await self._initialize()
                return await self._call(method, params)

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            res = await self._with_session("tools/list", {"cursor": cursor} if cursor else {})
            for t in (res or {}).get("tools") or []:
                if isinstance(t, dict) and t.get("name"):
                    tools.append(t)
            cursor = (res or {}).get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        return tools[:MAX_TOOLS]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        res = await self._with_session("tools/call", {"name": name, "arguments": arguments or {}})
        return res if isinstance(res, dict) else {"content": [], "isError": True}

    async def ping(self) -> None:
        await self._with_session("ping")

    async def close(self) -> None:
        self._ready = False
        await self.t.close()


def content_text(result: Dict[str, Any], limit: int = 8000) -> str:
    """Gộp phần chữ của kết quả `tools/call`; phần không phải chữ (ảnh, tệp) chỉ ghi loại."""
    parts = []
    for c in result.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
        elif c.get("type") == "resource" and isinstance(c.get("resource"), dict) and "text" in c["resource"]:
            parts.append(str(c["resource"]["text"]))
        else:
            parts.append(f"[{c.get('type')}]")
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit] + f"\n… (cắt, tổng {len(text)} ký tự)"
