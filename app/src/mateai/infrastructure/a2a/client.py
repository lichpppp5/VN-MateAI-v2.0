# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/a2a/client.py
===================================
Client Agent2Agent (A2A) tối giản: GIAO VIỆC cho một agent ngoài và nhận kết quả (docs/integrations/agent-gateway.md).

- Agent card: `GET <base>/.well-known/agent-card.json` (dự phòng `agent.json` của bản cũ) → tên, mô tả, kỹ năng, `url` JSON-RPC.
- `message/send` (JSON-RPC 2.0, blocking) với một phần chữ; kết quả là `Message` hoặc `Task`.
- `Task` chưa xong (submitted / working) → hỏi lại `tasks/get` tới khi xong hoặc hết thời gian.
- Trạng thái cần người (`input-required`, `auth-required`) trả về để AI hỏi lại người dùng — không tự trả lời thay.

Không hỗ trợ: streaming (`message/stream`), push notification, gửi tệp / dữ liệu có cấu trúc.
"""
from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
TERMINAL = ("completed", "failed", "canceled", "rejected")
NEEDS_HUMAN = ("input-required", "auth-required")
_ids = itertools.count(1)


class A2AError(RuntimeError):
    pass


def _text(parts: Any) -> str:
    return "\n".join(str(p.get("text") or "") for p in parts or [] if isinstance(p, dict) and p.get("kind", p.get("type")) == "text")


def result_text(obj: Dict[str, Any]) -> str:
    """Chữ của một Message hoặc Task (artifact + thông điệp trạng thái)."""
    if obj.get("kind") == "message" or "parts" in obj:
        return _text(obj.get("parts"))
    out: List[str] = []
    for art in obj.get("artifacts") or []:
        t = _text((art or {}).get("parts"))
        if t:
            out.append(t)
    msg = ((obj.get("status") or {}).get("message") or {})
    t = _text(msg.get("parts"))
    if t:
        out.append(t)
    return "\n".join(out)


class A2AClient:
    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 60.0,
                 client: Optional[httpx.AsyncClient] = None, poll_interval: float = 1.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._own = client is None

    async def fetch_card(self) -> Dict[str, Any]:
        if self.base_url.endswith(".json"):
            urls = [self.base_url]
        else:
            urls = [self.base_url + p for p in CARD_PATHS]
        last = None
        for u in urls:
            r = await self._client.get(u, headers=self.headers, timeout=self.timeout)
            if r.status_code == 200:
                try:
                    card = r.json()
                except ValueError as exc:
                    raise A2AError("Agent card không phải JSON") from exc
                if not isinstance(card, dict) or not card.get("name"):
                    raise A2AError("Agent card thiếu tên")
                card["url"] = urljoin(u, str(card.get("url") or self.base_url))
                return card
            last = r.status_code
        raise A2AError(f"Không đọc được agent card (HTTP {last})")

    async def _rpc(self, url: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        mid = next(_ids)
        r = await self._client.post(url, json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params},
                                    headers={"Content-Type": "application/json", **self.headers}, timeout=self.timeout)
        if r.status_code >= 400:
            raise A2AError(f"HTTP {r.status_code} từ agent: {r.text[:200]}")
        try:
            body = r.json()
        except ValueError as exc:
            raise A2AError("Agent trả nội dung không phải JSON") from exc
        if body.get("error"):
            e = body["error"]
            raise A2AError(f"Agent lỗi {e.get('code')}: {str(e.get('message') or '')[:300]}")
        res = body.get("result")
        if not isinstance(res, dict):
            raise A2AError("Agent trả kết quả không hợp lệ")
        return res

    async def send(self, url: str, text: str, context_id: Optional[str] = None) -> Dict[str, Any]:
        """Gửi việc, chờ tới khi Task xong / cần người / hết thời gian. Trả {"state", "text", "task_id", "context_id"}."""
        msg: Dict[str, Any] = {"role": "user", "parts": [{"kind": "text", "text": text}], "messageId": uuid.uuid4().hex, "kind": "message"}
        if context_id:
            msg["contextId"] = context_id
        deadline = time.monotonic() + self.timeout
        res = await self._rpc(url, "message/send", {"message": msg, "configuration": {"blocking": True}})
        if res.get("kind") == "message" or "parts" in res:
            return {"state": "completed", "text": result_text(res), "task_id": None, "context_id": res.get("contextId")}
        while True:
            state = str((res.get("status") or {}).get("state") or "unknown")
            if state in TERMINAL or state in NEEDS_HUMAN:
                return {"state": state, "text": result_text(res), "task_id": res.get("id"), "context_id": res.get("contextId")}
            if time.monotonic() + self.poll_interval > deadline:
                return {"state": "timeout", "text": result_text(res), "task_id": res.get("id"), "context_id": res.get("contextId")}
            await asyncio.sleep(self.poll_interval)
            res = await self._rpc(url, "tasks/get", {"id": res.get("id")})

    async def close(self) -> None:
        if self._own:
            await self._client.aclose()
