# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/orchestrator.py
====================
Enterprise Orchestrator for VN-MateAI — Master-Worker LAN Management.

Responsibilities:
  - Track connected Client Agents via WebSocket sessions.
  - Correlate asynchronous tasks dispatched to worker nodes (Task ID ➔ Future).
  - Dispatch remote skill execution and await structured results.
  - Push new skill source code for remote hot-loading on worker nodes.
  - Provide live client inventory for REST API and Web Portal.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _agent_file() -> Path:
    from mateai.config.loader import settings
    return Path(settings.PROJECT_ROOT) / "client_agent" / "agent.py"
_AGENT_VERSION_RE = re.compile(r'^AGENT_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)


def bundled_agent_version() -> Optional[str]:
    """Phiên bản Agent trong gói "Tải Agent" hiện tại (None nếu không đọc được)."""
    try:
        m = _AGENT_VERSION_RE.search(_agent_file().read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def version_tuple(v: Optional[str]) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


class Orchestrator:
    """
    Central coordinator managing LAN worker nodes and task delegation.
    """

    def __init__(self) -> None:
        # Map client_id -> session dict
        self._clients: Dict[str, Dict[str, Any]] = {}
        # Map task_id -> asyncio.Future
        self._pending_tasks: Dict[str, asyncio.Future] = {}
        #: task_id -> client_id: máy nào được trả kết quả task nào, và máy ngắt kết
        #: nối chỉ huỷ task CỦA NÓ (trước đây huỷ task của mọi máy trạm).
        self._task_owner: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running FastAPI asyncio loop for thread-safe cross-thread calls."""
        self._loop = loop

    # ------------------------------------------------------------------
    # Connection Lifecycle
    # ------------------------------------------------------------------

    async def register_client(self, client_id: str, websocket: WebSocket, metadata: Dict[str, Any]) -> None:
        """Register a freshly connected worker client."""
        async with self._lock:
            self._clients[client_id] = {
                "client_id": client_id,
                "websocket": websocket,
                "hostname": metadata.get("hostname", client_id),
                "ip": metadata.get("ip", "127.0.0.1"),
                "platform": metadata.get("platform", "Windows"),
                "skills": metadata.get("skills", []),
                "skills_count": len(metadata.get("skills", [])),
                "connected_at": time.time(),
                "connected_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "last_seen": time.time(),
                "status": "online",
                "agent_version": str(metadata.get("agent_version") or ""),
                "package": str(metadata.get("package") or "source"),
                "metrics": {},
                "last_heartbeat": None,
            }
        logger.info(
            "Orchestrator: Máy trạm [%s] (IP: %s, Kỹ năng: %d) đã kết nối trực tuyến.",
            client_id,
            metadata.get("ip"),
            len(metadata.get("skills", [])),
        )

    async def unregister_client(self, client_id: str, websocket: Optional[WebSocket] = None) -> None:
        """Handle client disconnection."""
        async with self._lock:
            # Kết nối cũ đóng SAU khi máy đã nối lại bằng kết nối mới: đừng xoá phiên mới.
            current = self._clients.get(client_id, {}).get("websocket")
            if websocket is not None and current is not None and current is not websocket:
                return
            if client_id in self._clients:
                del self._clients[client_id]
                logger.info("Orchestrator: Máy trạm [%s] đã ngắt kết nối.", client_id)

        # Huỷ task đang chờ CỦA MÁY NÀY (task của máy trạm khác vẫn chạy tiếp)
        for task_id, future in list(self._pending_tasks.items()):
            if self._task_owner.get(task_id) != client_id:
                continue
            if not future.done():
                future.set_exception(
                    ConnectionResetError(f"Máy trạm '{client_id}' đã ngắt kết nối trong khi thực thi tác vụ.")
                )

    async def offer_update(self, client_id: str) -> bool:
        """Agent cũ hơn gói đang phát hành cho đúng loại của nó -> báo "update_available"."""
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session:
            return False
        try:
            from mateai.interfaces.http.agent_packages import latest_version
            from mateai.config.loader import get_config_section
            if get_config_section("agent_updates").get("enabled") is False:
                return False
            latest = latest_version(session.get("package") or "source")
        except Exception as exc:  # noqa: BLE001
            logger.debug("offer_update: %s", exc)
            return False
        current = session.get("agent_version")
        if not latest or not current or version_tuple(current) >= version_tuple(latest):
            return False
        try:
            await session["websocket"].send_text(json.dumps(
                {"action": "update_available", "version": latest, "package": session.get("package")}))
            logger.info("Đã báo máy trạm [%s] có bản Agent mới %s (đang %s).", client_id, latest, current)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("offer_update gửi lỗi: %s", exc)
            return False

    async def disconnect_client(self, client_id: str, reason: str = "") -> bool:
        """Cắt kết nối một máy trạm (vd vừa bị thu hồi khoá)."""
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return False
        try:
            await session["websocket"].close(code=1008, reason=reason[:100])
        except Exception:  # noqa: BLE001
            pass
        await self.unregister_client(client_id)
        return True

    def resolve_client_id(self, name: str) -> Optional[str]:
        """Mã agent đang kết nối ứng với tên người dùng / AI nói: khớp đúng mã trước, rồi không phân biệt
        hoa / thường và hậu tố miền (`pc-kt-01` = `PC-KT-01.corp.local`), theo mã hoặc tên máy. Nhiều
        máy cùng khớp -> None (không đoán)."""
        from mateai.application.devices import workstation_directory as wd
        if name in self._clients:
            return name
        return wd.resolve(name, wd.build_index(list(self._clients.values()), []))

    def is_client_online(self, client_id: str) -> bool:
        """Check if client is currently connected and online."""
        return self.resolve_client_id(client_id) is not None

    def get_client_ids(self) -> List[str]:
        """Return list of all currently connected client IDs."""
        return list(self._clients.keys())

    def get_connected_clients(self) -> List[Dict[str, Any]]:
        """Return JSON-serializable list of all connected workers."""
        now = time.time()
        clients_list: List[Dict[str, Any]] = []

        for cid, info in self._clients.items():
            uptime_seconds = int(now - info["connected_at"])
            uptime_str = f"{uptime_seconds // 60}m {uptime_seconds % 60}s" if uptime_seconds >= 60 else f"{uptime_seconds}s"
            hb = info.get("last_heartbeat")
            clients_list.append({
                "client_id": info["client_id"],
                "hostname": info["hostname"],
                "ip": info["ip"],
                "platform": info["platform"],
                "skills": info["skills"],
                "skills_count": info["skills_count"],
                "status": info["status"],
                "connected_at": info["connected_at_iso"],
                "uptime": uptime_str,
                "agent_version": info.get("agent_version") or None,
                "package": info.get("package") or "source",
                "metrics": dict(info.get("metrics") or {}),
                "heartbeat_age_s": int(now - hb) if hb else None,
            })
        return clients_list

    # ------------------------------------------------------------------
    # Message Routing & Task Handling
    # ------------------------------------------------------------------

    def handle_incoming_message(self, client_id: str, data: Dict[str, Any]) -> None:
        """Handle incoming response payloads from worker nodes."""
        action = data.get("action")
        task_id = data.get("task_id")

        if client_id in self._clients:
            self._clients[client_id]["last_seen"] = time.time()

        if action == "heartbeat":
            session = self._clients.get(client_id)
            if session is not None:
                session["metrics"] = {k: data.get(k) for k in (
                    "cpu_percent", "ram_percent", "disk_percent", "uptime_s", "skills_count")}
                session["last_heartbeat"] = time.time()
                if data.get("agent_version"):
                    session["agent_version"] = str(data["agent_version"])
                if data.get("package"):
                    session["package"] = str(data["package"])
            return

        if action == "update_status":
            # Agent tự cập nhật: báo lên trang giám sát (đang cài / lỗi + lý do).
            try:
                from mateai.application.operations.topology_events import emit
                ok = data.get("status") == "installing"
                emit("agent_update", stage=str(data.get("status")), node=f"worker:{client_id}",
                     status="running" if ok else "error",
                     detail=(f"{data.get('from')} -> {data.get('to')}" if ok
                             else f"cập nhật lỗi: {data.get('error', '')}"))
            except Exception:  # noqa: BLE001
                pass
            logger.info("Máy trạm [%s] cập nhật Agent: %s", client_id, {k: data.get(k) for k in ("status", "from", "to", "error")})
            return

        owner = self._task_owner.get(task_id) if task_id else None
        if owner is not None and owner != client_id:
            # Một máy trạm không được trả kết quả thay cho máy khác.
            logger.warning("Bỏ qua kết quả task [%s] từ [%s] — task thuộc máy [%s].", task_id, client_id, owner)
            return

        if action in ("result", "install_result", "monitor_result", "kill_result", "visual_result") and task_id:
            future = self._pending_tasks.get(task_id)
            if future and not future.done():
                future.set_result(data)
                logger.debug("Resolved pending task [%s] from client [%s]", task_id, client_id)

        elif action == "task_response" and task_id:
            from mateai.application.devices.task_manager import task_manager
            status = data.get("status", "completed")
            msg = data.get("message")
            task_manager.handle_task_response(client_id, task_id, status, msg, error=data.get("error"))
            future = self._pending_tasks.get(task_id)
            if future and not future.done():
                future.set_result(data)
                logger.debug("Resolved pending task_popup [%s] from client [%s]", task_id, client_id)

    async def execute_on_client(
        self,
        client_id: str,
        skill_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: float = 35.0,
    ) -> Dict[str, Any]:
        """
        Dispatch a skill execution command to a specific worker client via WebSocket.

        Returns:
            Dict containing execution result or error.
        """
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
            }

        ws: WebSocket = session["websocket"]
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        payload = {
            "action": "execute",
            "task_id": task_id,
            "skill_name": skill_name,
            "args": args or {},
        }

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_tasks[task_id] = future
        self._task_owner[task_id] = client_id

        try:
            logger.info("Orchestrator: Gửi lệnh '%s' tới máy trạm [%s] (Task ID: %s)", skill_name, client_id, task_id)
            # Bước 4: Phát âm thanh đệm xác nhận (< 100ms) từ RAM cache khi kích hoạt Tool/Skill
            asyncio.create_task(self._broadcast_skill_acoustic_ack(skill_name))
            await ws.send_text(json.dumps(payload, ensure_ascii=False))

            # Await worker reply with timeout
            response = await asyncio.wait_for(future, timeout=timeout)
            result = response.get("result", {})
            # Kỹ năng tự báo lỗi (`status: error` / `success: False`) là lỗi, dù Agent ≤ 2.2 vẫn gửi success=True khi
            # kết quả chỉ có "message" mà không có khoá "error" — AI từng báo "đã mở" khi kỹ năng từ chối.
            failed = isinstance(result, dict) and (result.get("status") == "error" or result.get("success") is False)
            is_success = not failed and bool(
                response.get("success")
                or (isinstance(result, dict) and (result.get("status") == "success" or result.get("success") is True or not result.get("error")))
            )
            return {
                "status": "success" if is_success else "error",
                "client_id": client_id,
                "remote_execution": True,
                "result": result,
            }

        except asyncio.TimeoutError:
            logger.warning("Hết thời gian chờ phản hồi từ máy trạm [%s] cho task [%s]", client_id, task_id)
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Hết thời gian chờ (Timeout {timeout}s): Máy trạm '{client_id}' không phản hồi kịp.",
            }
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Lỗi gửi lệnh tới máy trạm [%s]: %s", client_id, exc)
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Lỗi truyền thông mạng tới máy trạm '{client_id}': {exc}",
            }
        finally:
            self._pending_tasks.pop(task_id, None)
            self._task_owner.pop(task_id, None)

    async def _broadcast_skill_acoustic_ack(self, skill_name: str = "") -> None:
        """
        Bước 4: Bắn âm thanh đệm xác nhận (< 100ms) từ RAM cache khi kích hoạt Tool/Skill
        để người dùng không có cảm giác bị 'im lặng chết'.
        """
        try:
            from mateai.infrastructure.tts.acoustic_ack import get_acoustic_ack_audio
            ack_audio = await get_acoustic_ack_audio()
            if ack_audio:
                from mateai.interfaces.websocket.realtime_hub import broadcast_hud_binary
                await broadcast_hud_binary(ack_audio)
                logger.info("[Orchestrator] Bắn âm thanh đệm ACK (<100ms) khi kích hoạt skill '%s'", skill_name)
        except Exception as exc:
            logger.debug("[Orchestrator] Acoustic ACK broadcast error: %s", exc)

    async def deploy_skill_to_client(
        self,
        client_id: str,
        filename: str,
        code: str,
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        """
        Deploy and hot-load Python skill code on a remote worker client.
        """
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Máy trạm '{client_id}' không trực tuyến.",
            }

        ws: WebSocket = session["websocket"]
        task_id = f"deploy_{uuid.uuid4().hex[:8]}"
        payload = {
            "action": "install_skill",
            "task_id": task_id,
            "filename": filename,
            "code": code,
        }

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_tasks[task_id] = future
        self._task_owner[task_id] = client_id

        try:
            logger.info("Orchestrator: Đang đẩy kỹ năng mới '%s' tới máy trạm [%s]", filename, client_id)
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success" if response.get("success") else "error",
                "client_id": client_id,
                "filename": filename,
                "error": response.get("error"),
                "skills": response.get("skills", []),
            }
        except Exception as exc:
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Lỗi cài đặt kỹ năng trên máy trạm: {exc}",
            }
        finally:
            self._pending_tasks.pop(task_id, None)
            self._task_owner.pop(task_id, None)

    async def monitor_client(
        self,
        client_id: str,
        monitor_type: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """
        Request real-time telemetry / monitoring data from a client agent.
        monitor_type: 'screen' | 'processes' | 'network' | 'peripherals' | 'security'
        """
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
            }

        ws: WebSocket = session["websocket"]
        task_id = f"mon_{uuid.uuid4().hex[:8]}"
        payload = {
            "action": "monitor",
            "task_id": task_id,
            "type": monitor_type,
            "args": args or {},
        }

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_tasks[task_id] = future
        self._task_owner[task_id] = client_id

        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success" if response.get("success") else "error",
                "client_id": client_id,
                "type": monitor_type,
                "result": response.get("result", {}),
            }
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Hết thời gian chờ phản hồi giám sát ({timeout}s) từ máy trạm '{client_id}'.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Lỗi yêu cầu dữ liệu giám sát tới máy trạm '{client_id}': {exc}",
            }
        finally:
            self._pending_tasks.pop(task_id, None)
            self._task_owner.pop(task_id, None)

    async def kill_client_process(
        self,
        client_id: str,
        pid: int,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        Send a kill process command to target client agent.
        """
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Máy trạm '{client_id}' hiện không trực tuyến.",
            }

        ws: WebSocket = session["websocket"]
        task_id = f"kill_{uuid.uuid4().hex[:8]}"
        payload = {
            "action": "kill_process",
            "task_id": task_id,
            "pid": pid,
        }

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_tasks[task_id] = future
        self._task_owner[task_id] = client_id

        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success" if response.get("success") else "error",
                "client_id": client_id,
                "result": response.get("result", {}),
            }
        except Exception as exc:
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Lỗi gửi lệnh tắt tiến trình tới máy trạm '{client_id}': {exc}",
            }
        finally:
            self._pending_tasks.pop(task_id, None)
            self._task_owner.pop(task_id, None)

    def execute_on_client_sync(
        self,
        client_id: str,
        skill_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: float = 35.0,
    ) -> Dict[str, Any]:
        """
        Thread-safe synchronous bridge for calling execute_on_client
        from background worker threads (such as LLMEngine.ask).
        """
        resolved = self.resolve_client_id(client_id)
        if resolved is None:
            from mateai.application.devices import workstation_directory as wd
            many = wd.ambiguous(client_id, wd.build_index(list(self._clients.values()), []))
            why = (f"Tên '{client_id}' khớp nhiều máy ({', '.join(many)}) — hãy nói rõ mã máy."
                   if many else f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.")
            return {"status": "error", "client_id": client_id, "error": why}
        client_id = resolved

        # If we have a running event loop stored
        loop = self._loop
        if loop and loop.is_running():
            coro = self.execute_on_client(client_id, skill_name, args, timeout)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return future.result(timeout=timeout + 3.0)
            except Exception as exc:
                return {
                    "status": "error",
                    "client_id": client_id,
                    "error": f"Lỗi đồng bộ khi giao tiếp máy trạm '{client_id}': {exc}",
                }
        else:
            # Standalone execution
            try:
                return asyncio.run(self.execute_on_client(client_id, skill_name, args, timeout))
            except Exception as exc:
                return {
                    "status": "error",
                    "client_id": client_id,
                    "error": f"Lỗi thực thi lệnh máy trạm: {exc}",
                }

    async def send_visual_to_client(
        self,
        client_id: str,
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        Phase 32: Dispatch visual overlay command to a target client agent via WebSocket.
        """
        client_id = self.resolve_client_id(client_id) or client_id
        session = self._clients.get(client_id)
        if not session or not session.get("websocket"):
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
            }

        ws: WebSocket = session["websocket"]
        task_id = f"vis_{uuid.uuid4().hex[:8]}"
        payload = {
            "action": "show_visual",
            "task_id": task_id,
            "type": visual_type,
            "title": title,
            "data": data,
            "duration": duration,
        }

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_tasks[task_id] = future
        self._task_owner[task_id] = client_id

        try:
            logger.info("Orchestrator: Gửi visual overlay [%s] tới máy trạm [%s]", visual_type, client_id)
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success",
                "client_id": client_id,
                "type": visual_type,
                "title": title,
                "result": response,
            }
        except asyncio.TimeoutError:
            return {
                "status": "success",
                "client_id": client_id,
                "type": visual_type,
                "title": title,
                "note": "Lệnh hiển thị đã được gửi tới máy trạm thành công.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "client_id": client_id,
                "error": f"Lỗi gửi lệnh visual overlay tới máy trạm '{client_id}': {exc}",
            }
        finally:
            self._pending_tasks.pop(task_id, None)
            self._task_owner.pop(task_id, None)

    async def broadcast_visual(
        self,
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Broadcast visual overlay to all online worker nodes.
        """
        clients = list(self._clients.keys())
        if not clients:
            return []
        tasks = [
            self.send_visual_to_client(cid, visual_type, data, title, duration, timeout=5.0)
            for cid in clients
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def send_visual_to_client_sync(
        self,
        client_id: str,
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """
        Thread-safe synchronous bridge for LLM tool calling.
        """
        loop = self._loop
        if loop and loop.is_running():
            coro = self.send_visual_to_client(client_id, visual_type, data, title, duration, timeout)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return future.result(timeout=timeout + 2.0)
            except Exception as exc:
                return {
                    "status": "error",
                    "client_id": client_id,
                    "error": f"Lỗi đồng bộ visual overlay: {exc}",
                }
        else:
            try:
                return asyncio.run(
                    self.send_visual_to_client(client_id, visual_type, data, title, duration, timeout)
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "client_id": client_id,
                    "error": f"Lỗi thực thi visual overlay: {exc}",
                }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

orchestrator = Orchestrator()
