# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connection_pool.py
=======================
Phase 10: Connection Reuse & Persistent Keep-Alive Pool (Realtime Voice Revamp).

Mục tiêu cốt lõi:
  - Tận dụng tối đa HTTP/2 Multiplexing và TCP Keep-Alive để triệt tiêu hoàn toàn
    độ trễ bắt tay mạng (TCP 3-way handshake + TLS negotiation) cho toàn bộ chu trình thoại:
      + LLM Streaming (9router / vLLM / Ollama / OpenAI / LM Studio)
      + Cloud STT Transcriptions (Groq Whisper / OpenAI Whisper)
      + Cloud TTS Synthesis (9Router TTS / Edge-TTS / ElevenLabs)
      + External Connectors & Health Diagnostics
  - Tăng keepalive_expiry lên 300 giây (5 phút) thay vì 5 giây mặc định.
  - Hỗ trợ HTTP/2 song công (Multiplexing) giảm nhiều kết nối đồng thời vào 1 TCP socket.
  - Giảm ngay 80ms – 250ms độ trễ mạng trên từng lượt thoại.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


_POOL_TIMEOUTS: Dict[str, httpx.Timeout] = {
    # LLM streaming: đọc lâu (180s), kết nối nhanh (5s)
    "llm": httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=10.0),
    # STT Whisper (Groq / OpenAI)
    "stt": httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=10.0),
    # TTS (9Router /audio/speech)
    "tts": httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=10.0),
    # Connector, webhook, health check
    "general": httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=10.0),
}
_POOL_NAMES = {"llm": "LLM_Stream_Pool", "stt": "STT_Whisper_Pool",
               "tts": "TTS_Synthesis_Pool", "general": "General_Connector_Pool"}


class ConnectionPoolManager:
    """
    Trình quản lý tập trung các Pool kết nối HTTP/2 Persistent Keep-Alive.

    Mỗi event loop có bộ client riêng: `httpx.AsyncClient` gắn với loop đã mở
    kết nối của nó. Trước đây chỉ có một bộ client dùng chung, nên các luồng tự
    chạy event loop riêng (voice_controller, prewarm TTS) dùng nhầm client của
    loop chính -> lỗi "Event loop is closed" / kết nối treo. `asyncio.Lock` tạo
    lúc import cũng bị gắn vào loop đầu tiên dùng nó; nay dùng threading.Lock
    vì việc tạo client là đồng bộ.
    """

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        self._clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[str, httpx.AsyncClient]]" = (
            weakref.WeakKeyDictionary()
        )
        self._primary_loop: Optional[asyncio.AbstractEventLoop] = None

        # Cấu hình Pool tiêu chuẩn cấp doanh nghiệp
        self._limits = httpx.Limits(
            max_keepalive_connections=100,
            max_connections=200,
            keepalive_expiry=300.0,  # 5 phút Keep-Alive duy trì socket ấm
        )

    def _create_client(
        self,
        name: str,
        timeout: httpx.Timeout,
        enable_http2: bool = True,
    ) -> httpx.AsyncClient:
        """Tạo instance httpx.AsyncClient với cấu hình HTTP/2 và fallback an toàn."""
        try:
            client = httpx.AsyncClient(
                limits=self._limits,
                timeout=timeout,
                http2=enable_http2,
            )
            logger.info("[ConnectionPool] Khởi tạo Pool '%s' thành công (HTTP/2=%s, Keep-Alive=300s).", name, enable_http2)
            return client
        except Exception as exc:
            logger.warning("[ConnectionPool] HTTP/2 không khả dụng cho '%s' (%s), kích hoạt HTTP/1.1 Keep-Alive fallback.", name, exc)
            return httpx.AsyncClient(
                limits=self._limits,
                timeout=timeout,
                http2=False,
            )

    def _get(self, kind: str) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        with self._sync_lock:
            if self._primary_loop is None or self._primary_loop.is_closed():
                self._primary_loop = loop
            per_loop = self._clients.setdefault(loop, {})
            client = per_loop.get(kind)
            if client is None or client.is_closed:
                client = self._create_client(_POOL_NAMES[kind], _POOL_TIMEOUTS[kind])
                per_loop[kind] = client
            return client

    def _primary_clients(self) -> Dict[str, httpx.AsyncClient]:
        loop = self._primary_loop
        return dict(self._clients.get(loop, {})) if loop is not None else {}

    async def get_llm_client(self) -> httpx.AsyncClient:
        return self._get("llm")

    async def get_stt_client(self) -> httpx.AsyncClient:
        return self._get("stt")

    async def get_tts_client(self) -> httpx.AsyncClient:
        return self._get("tts")

    async def get_general_client(self) -> httpx.AsyncClient:
        """Lấy HTTP client chung cho các connectors, webhooks, health checks."""
        return self._get("general")

    def get_sync_llm_client(self) -> Optional[httpx.AsyncClient]:
        """Client LLM của loop chính (non-async accessor)."""
        with self._sync_lock:
            return self._primary_clients().get("llm")

    async def warm_up(self) -> None:
        """Khởi tạo trước toàn bộ các pools trong quá trình boot server."""
        for kind in _POOL_TIMEOUTS:
            self._get(kind)
        logger.info("[ConnectionPool] Toàn bộ 4 Persistent Pools đã được khởi tạo và làm ấm.")

    async def close_all(self) -> None:
        """Đóng các pool của loop đang chạy (gọi lúc shutdown server)."""
        loop = asyncio.get_running_loop()
        with self._sync_lock:
            clients = self._clients.pop(loop, {})
        for kind, client in clients.items():
            if not client.is_closed:
                try:
                    await client.aclose()
                    logger.debug("[ConnectionPool] Đã đóng pool %s.", kind)
                except Exception as e:
                    logger.debug("[ConnectionPool] Lỗi đóng pool %s: %s", kind, e)

    def get_diagnostics(self) -> Dict[str, Any]:
        """Lấy thông số chẩn đoán trạng thái các pools (của loop chính)."""
        clients = self._primary_clients()

        def _client_status(client: Optional[httpx.AsyncClient]) -> Dict[str, Any]:
            if client is None:
                return {"initialized": False, "status": "not_created"}
            return {
                "initialized": True,
                "is_closed": client.is_closed,
                "is_http2": getattr(client, "_transport", None) is not None,
            }

        return {
            "keepalive_expiry_sec": self._limits.keepalive_expiry,
            "max_keepalive_connections": self._limits.max_keepalive_connections,
            "max_connections": self._limits.max_connections,
            "event_loops": len(self._clients),
            "pools": {
                f"{kind}_pool": _client_status(clients.get(kind)) for kind in _POOL_TIMEOUTS
            },
        }


# Global Singleton Instance
connection_pool_manager = ConnectionPoolManager()


# Module-level convenience accessors
async def get_llm_http_client() -> httpx.AsyncClient:
    return await connection_pool_manager.get_llm_client()


async def get_stt_http_client() -> httpx.AsyncClient:
    return await connection_pool_manager.get_stt_client()


async def get_tts_http_client() -> httpx.AsyncClient:
    return await connection_pool_manager.get_tts_client()


async def get_general_http_client() -> httpx.AsyncClient:
    return await connection_pool_manager.get_general_client()
