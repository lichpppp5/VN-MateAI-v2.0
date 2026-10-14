# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/websockets.py
============================================
Các kênh WebSocket của máy chủ:
  - `/ws/audio-stream[/{id}]`, `/api/v1/xiaozhi/ws[/{id}]` — robot ESP32/Xiaozhi
    (token thiết bị bắt buộc; cũng là đường DUY NHẤT mở trên cổng 8000);
  - `/ws/portal-ui`, `/ws/topology`, `/ws/hud` — giao diện trình duyệt (JWT);
  - `/ws/v1/voice-stream` — thoại thời gian thực;
  - `/ws/client` — LAN worker (enrollment secret).
Xác thực: `mateai.interfaces.http.ws_auth`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import hud_voice, log_stream, ws_auth
from mateai.interfaces.websocket.realtime_hub import (
    active_audio_nodes,
    active_hud_websockets,
    active_portal_websockets,
    active_topology_websockets,
    broadcast_hud,
    broadcast_hud_binary,
    broadcast_portal_ui,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Task nền tạo từ handler (lượt thoại HUD, duyệt): asyncio chỉ giữ tham chiếu YẾU.
_TASKS: set = set()


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _is_admin(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and user.get("role") == "admin"


async def _handle_audio_stream(websocket: WebSocket, device_id: str) -> None:
    """
    Xử lý luồng âm thanh WebSocket thời gian thực cho từng mạch ESP32 Xiaozhi độc lập.
    Phase 43: Xiaozhi Desktop Companion Protocol (LCD UI, Barge-in, High-Fidelity I2S, Sentinel Wake).

    Zero-Trust: bắt buộc device enrollment secret (hoặc JWT admin/manager) qua
    ``?token=``. Trước đây bất kỳ host nào cũng mở được luồng âm thanh của thiết
    bị — có nghĩa là nghe/ghi được mọi thứ người dùng nói trong nhà.
    """
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway

    # Alias cũ `/ws/audio-stream`: firmware trong repo đã dùng `/api/v1/xiaozhi/ws`.
    # Chỉ gỡ alias khi log chạy thật cho thấy không thiết bị nào còn dùng
    # (docs/migration/legacy-removal-plan.md L12) — cảnh báo này là bằng chứng đó.
    if websocket.url.path.startswith("/ws/audio-stream"):
        logger.warning("[DEPRECATED] Thiết bị '%s' (%s) dùng đường cũ %s — nên chuyển sang /api/v1/xiaozhi/ws.",
                       device_id, websocket.client.host if websocket.client else "unknown", websocket.url.path)

    auth_method = ws_auth.device_auth_method(websocket, device_id)
    if auth_method is None:
        # Ghi đường dẫn + CÓ gửi token hay không (không ghi giá trị) — đủ để biết
        # firmware gọi sai đường / thiếu token / token không khớp device_id.
        has_token = bool(websocket.query_params.get("token")) or \
            websocket.headers.get("authorization", "").lower().startswith("bearer ")
        logger.warning(
            "Từ chối thiết bị '%s' kết nối từ %s (đường %s, %s): thiếu hoặc sai token thiết bị.",
            device_id,
            websocket.client.host if websocket.client else "unknown",
            websocket.url.path,
            "có gửi token" if has_token else "KHÔNG gửi token",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu enrollment token hợp lệ.")
        return

    await xiaozhi_gateway.handle_client(websocket, device_id, auth_method=auth_method)


@router.websocket("/ws/audio-stream/{device_id}")
@router.websocket("/api/v1/xiaozhi/ws/{device_id}")
async def audio_stream_device_ws(websocket: WebSocket, device_id: str) -> None:
    """WebSocket âm thanh đa điểm (Hardware Multiplexing) theo mã thiết bị/phòng."""
    await _handle_audio_stream(websocket, device_id)


@router.websocket("/ws/audio-stream")
@router.websocket("/api/v1/xiaozhi/ws")
async def audio_stream_legacy_ws(websocket: WebSocket) -> None:
    """Endpoint tương thích ngược gán mặc định device_id='esp32-default'."""
    await _handle_audio_stream(websocket, device_id="esp32-default")


@router.websocket("/ws/portal-ui")
async def websocket_portal_ui(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time Web Portal UI synchronization.
    Supports switch_tab, show_toast, ping/pong, and telemetry broadcasting.

    Zero-Trust: bắt buộc JWT hợp lệ qua ``?token=``. Endpoint này chỉ phục vụ
    portal đã đăng nhập, đồng thời stream log hệ thống và cảnh báo bảo mật —
    trước đây ai cũng mở được và đọc được toàn bộ log.
    """
    ws_user = ws_auth.authenticate_websocket(websocket)
    if ws_user is None:
        logger.warning(
            "Từ chối Portal-UI WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return

    await websocket.accept()
    active_portal_websockets.add(websocket)
    logger.info(
        "Portal UI WebSocket connected (%d active client tabs, user=%s).",
        len(active_portal_websockets), ws_user.get("username"),
    )
    try:
        # Welcome frame
        await websocket.send_text(json.dumps({
            "event": "connected",
            "message": "Kết nối WebSocket Portal-UI thành công.",
            "timestamp": datetime.utcnow().isoformat(),
        }, ensure_ascii=False))

        # Push recent log entries so client immediately gets history
        _h = log_stream.get_handler()
        if _h:
            recent_logs = _h.get_recent_logs(limit=150)
            if recent_logs:
                await websocket.send_text(json.dumps({
                    "event": "log_history",
                    "logs": recent_logs,
                    "count": len(recent_logs),
                }, ensure_ascii=False))

        while True:
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            action = data.get("action") or data.get("event")
            if action == "ping":
                await websocket.send_text(json.dumps({"event": "pong"}))
            elif action in ("switch_tab", "show_toast") and not _is_admin(ws_user):
                # Phát lên màn hình của MỌI người: trước Phase 10 tài khoản bất kỳ (kể cả
                # viewer) giả được thông báo hệ thống trên portal của admin.
                logger.warning("[Portal-UI] Bỏ '%s' từ '%s' (vai trò %s): chỉ admin được phát.",
                               action, ws_user.get("username"), ws_user.get("role"))
            elif action == "switch_tab":
                tab_name = data.get("tab") or data.get("target_tab")
                if tab_name:
                    await broadcast_portal_ui("switch_tab", {"tab": tab_name})
            elif action == "show_toast":
                msg = data.get("message") or data.get("text") or ""
                t_type = data.get("type", "info")
                if msg:
                    await broadcast_portal_ui("show_toast", {"message": msg, "type": t_type})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Portal UI WebSocket error: %s", exc)
    finally:
        active_portal_websockets.discard(websocket)
        logger.info("Portal UI WebSocket disconnected. (%d remaining).", len(active_portal_websockets))


@router.websocket("/ws/topology")
async def websocket_topology_endpoint(websocket: WebSocket) -> None:
    """
    Phase 88: WebSocket cho giao diện Topology & Real-time Live Flow Animation.
    Nhận sự kiện tool_executed và đồng bộ trạng thái đường nối trên canvas.

    Zero-Trust: bắt buộc JWT (?token=). Trước đây ai cũng xem được luồng tool
    đang chạy và bơm sự kiện "trigger" giả lên sơ đồ.
    """
    if ws_auth.authenticate_websocket(websocket) is None:
        logger.warning(
            "Từ chối Topology WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return
    await websocket.accept()
    active_topology_websockets.add(websocket)
    logger.info("Topology viewer connected (%d active sessions).", len(active_topology_websockets))
    try:
        # Mở trang: gửi ngay trạng thái THẬT + sự kiện gần đây (trước đây gửi
        # "active_nodes": 11 viết cứng). Sau đó vòng topology_loop đẩy snapshot
        # 2 s/lần và từng sự kiện bước xử lý ngay khi xảy ra.
        from mateai.interfaces.http.topology import snapshot
        from mateai.application.operations import topology_events
        snap = await asyncio.to_thread(snapshot)
        await websocket.send_text(json.dumps({"event": "snapshot", **snap}, ensure_ascii=False, default=str))
        await websocket.send_text(json.dumps({"event": "history", "events": topology_events.recent(150)},
                                             ensure_ascii=False, default=str))

        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # Chỉ còn ping: client không được phát sự kiện lên sơ đồ của người khác
                # (trước đây ai đăng nhập cũng bơm được "trigger" giả hiện như thật).
                if msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Topology websocket disconnected: %s", exc)
    finally:
        active_topology_websockets.discard(websocket)
        logger.info("Topology viewer disconnected (%d remaining).", len(active_topology_websockets))


@router.websocket("/ws/hud")
async def websocket_hud_endpoint(websocket: WebSocket) -> None:
    """
    Phase 33: WebSocket endpoint for VN-MateAI Sci-Fi Standby HUD.
    Pushes real-time audio visualizer state, speech typewriter text,
    system telemetry metrics and streaming terminal logs.

    Zero-Trust: client phải nối kèm JWT hợp lệ (?token=). Kết nối không token vẫn
    được phép để xem telemetry, nhưng mọi hành động đặc quyền (confirm_action) sẽ bị
    từ chối. Trước đây endpoint này tự cấp quyền admin cho bất kỳ ai gửi
    confirm_action — không cần đăng nhập.
    """
    await websocket.accept()

    # Xác thực (nếu có) — browser WebSocket API không cho set header Authorization,
    # nên token được truyền qua query param.
    ws_user = ws_auth.authenticate_websocket(websocket)

    active_hud_websockets.add(websocket)
    logger.info(
        "VN-MateAI HUD connected (%d active HUD displays, authenticated=%s).",
        len(active_hud_websockets),
        ws_user is not None,
    )

    try:
        # 1. Send Welcome Packet with dynamic Assistant Name (default Ly Ly)
        ai_name = get_assistant_name()
        await websocket.send_text(json.dumps({
            "type": "hud_welcome",
            "message": f"Hệ thống trợ lý AI {ai_name} sẵn sàng. Neural Link established.",
            "assistant_name": ai_name,
            "status": "idle",
            # Phase 76: vai trò THẬT của riêng kết nối này (None nếu chưa đăng nhập).
            # Trước đây quyền hạn bị nhét vào payload telemetry broadcast chung nên
            # luôn hiện "ADMIN" cho mọi người, kể cả khách chưa xác thực.
            "authenticated": ws_user is not None,
            "username": (ws_user or {}).get("username"),
            "role": (ws_user or {}).get("role"),
            "timestamp": datetime.utcnow().isoformat(),
        }, ensure_ascii=False))

        # 2. Push initial rich telemetry immediately
        payload = hud_voice.get_metrics_payload()
        await websocket.send_text(json.dumps({
            "type": "metrics_update",
            "data": payload,
        }, ensure_ascii=False))

        # 3. Nếu chưa xác thực, báo HUD biết chỉ ở chế độ xem
        if ws_user is None:
            await websocket.send_text(json.dumps({
                "type": "auth_required",
                "message": "HUD chưa xác thực: chỉ xem được telemetry, "
                           "các hành động phê duyệt sẽ bị từ chối. "
                           "Hãy đăng nhập trên portal để kích hoạt.",
                "timestamp": datetime.utcnow().isoformat(),
            }, ensure_ascii=False))

        wake_state = {"busy": False}
        while True:
            raw_text = await websocket.receive_text()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            action = data.get("action") or data.get("type")
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif action == "voice_command":
                cmd_query = (data.get("query") or "").strip()
                if cmd_query and ws_user is None:
                    # Zero-Trust: HUD chưa đăng nhập chỉ xem telemetry. Trước đây
                    # lệnh chạy dưới danh tính "hud" = quyền admin cho bất kỳ ai
                    # mở được cổng 443.
                    await websocket.send_text(json.dumps({
                        "type": "auth_required",
                        "message": "HUD chưa đăng nhập: không nhận lệnh thoại. "
                                   "Hãy đăng nhập trên portal rồi mở lại HUD.",
                        "timestamp": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False))
                elif cmd_query:
                    # Phase 81: huỷ lượt cũ trước khi nhận lượt mới. HUD đã
                    # dừng phát audio phía trình duyệt, nhưng nếu lượt cũ còn
                    # chạy ở đây thì nó vẫn sinh TTS và đẩy xuống — HUD sẽ phát
                    # tiếp lời của lượt cũ sau khi lượt mới đã bắt đầu, nghe
                    # như hai người nói chồng.
                    if hud_voice.cancel_task("hud"):
                        logger.info("[HUD] Có lệnh mới — huỷ lượt thoại đang chạy")
                        await broadcast_hud({
                            "type": "voice_active",
                            "status": "listening",
                            "text": "",
                            "interrupted": True,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    _spawn(hud_voice.process_command(
                        cmd_query, caller=str(ws_user.get("username") or "anonymous")))
            elif action == "wake_clip":
                # Gọi tên trợ lý trên HUD: trình duyệt chỉ gửi đoạn CÓ TIẾNG NÓI (0,3–6 s, PCM16 16 kHz, base64); nhận dạng
                # OFFLINE ở máy chủ (âm thanh phòng không ra ngoài). Chỉ phiên đã đăng nhập; mỗi kết nối một đoạn một lúc.
                if ws_user is None or wake_state["busy"]:
                    continue
                pcm = _decode_wake_clip(data.get("pcm16"))
                if pcm is None:
                    continue
                wake_state["busy"] = True
                _spawn(_hud_wake(websocket, pcm, wake_state))
            elif action == "end_conversation":
                # HUD báo: chờ 30s không nghe phản hồi ("timeout") hoặc admin nói
                # không còn yêu cầu ("user_done"). Chỉ phiên đã đăng nhập.
                if ws_user is not None:
                    reason = "timeout" if data.get("reason") == "timeout" else "user_done"
                    _spawn(hud_voice.end_conversation("hud", reason))
            elif action == "confirm_action":
                approved = bool(data.get("approved", True))
                action_id = data.get("action_id")
                skill_name = data.get("skill_name")
                # Zero-Trust: chỉ admin được phê duyệt hành động rủi ro cao — cùng
                # quy tắc với POST /api/v1/security/confirm-action (`approval_flow` không
                # tự kiểm quyền — người gọi kiểm).
                if ws_user is None or ws_user.get("role") != "admin":
                    await websocket.send_text(json.dumps({
                        "type": "security_approval_rejected",
                        "message": "Yêu cầu phê duyệt bị từ chối: cần đăng nhập "
                                   "với tài khoản admin.",
                        "action_id": action_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False))
                    continue
                _spawn(_hud_confirm(websocket, approved, ws_user, action_id, skill_name))
            elif action == "simulate" and _is_admin(ws_user):
                # Chỉ admin: trước Phase 10 kết nối CHƯA đăng nhập cũng phát chữ tuỳ ý
                # lên mọi HUD.
                sim_type = data.get("simulate_type", "voice_active")
                if sim_type == "voice_active":
                    ai_name = get_assistant_name()
                    await broadcast_hud({
                        "type": "voice_active",
                        "status": data.get("status", "speaking"),
                        "text": data.get("text", f"Hệ thống VN-MateAI {ai_name} đang ở trạng thái sẵn sàng cao nhất."),
                        "timestamp": datetime.utcnow().isoformat(),
                    })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("HUD WebSocket error: %s", exc)
    finally:
        active_hud_websockets.discard(websocket)
        logger.info("VN-MateAI HUD disconnected (%d remaining).", len(active_hud_websockets))


#: Đoạn gọi tên từ HUD: 0,3–6 s PCM16 mono 16 kHz.
HUD_WAKE_MIN_BYTES = 9600
HUD_WAKE_MAX_BYTES = 192000


def _decode_wake_clip(b64: Any) -> Optional[bytes]:
    """base64 → PCM16; sai định dạng / ngoài 0,3–6 s → None (bỏ qua, không báo lỗi — HUD gửi liên tục)."""
    import base64
    import binascii
    if not isinstance(b64, str) or len(b64) > HUD_WAKE_MAX_BYTES * 2:
        return None
    try:
        pcm = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not (HUD_WAKE_MIN_BYTES <= len(pcm) <= HUD_WAKE_MAX_BYTES) or len(pcm) % 2:
        return None
    return pcm


async def _hud_wake(websocket: WebSocket, pcm: bytes, wake_state: Dict[str, Any]) -> None:
    """Đoạn HUD gửi có gọi tên không. Có → báo RIÊNG HUD đó: chỉ gọi tên (HUD mở mic nghe lệnh) hoặc kèm lệnh (HUD chạy
    lệnh). Phần lệnh nhận dạng lại bằng bộ nhận dạng chính (model nhỏ nghe sai lệnh: "mấy giờ rồi" → "máy giá đôi")."""
    from mateai.infrastructure.audio.wake_word_engine import detect_wake, find_wake_command
    try:
        t0 = time.perf_counter()
        command, heard, stage = await detect_wake(pcm)
        if command is None:
            logger.debug("[HUD/Wake] đoạn %.1f s không gọi tên (%s, %d ms)", len(pcm) / 32000, stage,
                         (time.perf_counter() - t0) * 1000)
            return
        if command:
            from mateai.infrastructure.audio.audio_processor import audio_engine
            from mateai.interfaces.websocket.xiaozhi_gateway import _pcm16_to_wav
            full = await audio_engine.transcribe_audio(_pcm16_to_wav(pcm))
            again = find_wake_command(full)
            command = again if again is not None else command
        ms = round((time.perf_counter() - t0) * 1000)
        logger.info("[HUD/Wake] gọi tên sau %d ms (%s)%s", ms, stage, f" kèm lệnh: '{command[:60]}'" if command else "")
        await websocket.send_text(json.dumps({"type": "wake_detected", "command": command, "ms": ms,
                                              "timestamp": datetime.utcnow().isoformat()}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HUD/Wake] lỗi xử lý đoạn gọi tên: %s", exc)
    finally:
        wake_state["busy"] = False


async def _hud_confirm(websocket: WebSocket, approved: bool, user: Dict[str, Any],
                       action_id: Optional[str], skill_name: Optional[str]) -> None:
    """Duyệt từ HUD qua luồng chung với REST. Lỗi (không còn yêu cầu, lệch tên, đã xử lý)
    báo về ĐÚNG HUD đã bấm — trước Phase 10 lỗi bị nuốt trong task nền."""
    from mateai.interfaces.http import approval_flow
    try:
        await approval_flow.confirm(approved, str(user.get("username") or "admin"),
                                    action_id=action_id, skill_name=skill_name)
    except approval_flow.ApprovalDecisionError as exc:
        try:
            await websocket.send_text(json.dumps({
                "type": "security_approval_rejected", "message": exc.detail, "action_id": action_id,
                "timestamp": datetime.utcnow().isoformat(),
            }, ensure_ascii=False))
        except Exception:  # noqa: BLE001 — HUD đã đóng
            pass


@router.websocket("/ws/v1/voice-stream")
async def websocket_realtime_voice_endpoint(websocket: WebSocket) -> None:
    """
    Phase 1: Realtime Voice WebSocket Endpoint (/ws/v1/voice-stream). Bí danh cũ
    `/ws/voice` đã gỡ (realtime P6): không client nào trong repo dùng.
    Hỗ trợ Event Protocol chuẩn hóa, truyền âm thanh Binary Frame, và Barge-In Cancellation.
    """
    # Zero-Trust: bắt buộc JWT. Trước đây kết nối ẩn danh chạy dưới tên
    # "web_user" (quyền viewer) — vẫn đọc được dữ liệu tổ chức qua tool và tốn
    # chi phí LLM mà không gắn với ai.
    ws_user = ws_auth.authenticate_websocket(websocket)
    if ws_user is None:
        logger.warning(
            "Từ chối Voice WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return
    from mateai.application.security import rate_limit
    from mateai.interfaces.websocket.realtime_voice_ws import handle_realtime_voice_endpoint
    who = str(ws_user.get("username") or "?")
    # Prompt cuối §97: số phiên thoại đồng thời mỗi người (mỗi phiên giữ STT/LLM/TTS riêng).
    if not rate_limit.open_session("ws_voice", who, rate_limit.limit("ws_voice_sessions_per_user", 5)):
        logger.warning("Từ chối phiên thoại thứ quá ngưỡng của '%s'.", who)
        await websocket.close(code=1013, reason="Quá số phiên thoại đồng thời — đóng bớt tab rồi thử lại.")
        return
    try:
        await handle_realtime_voice_endpoint(websocket, user=ws_user)
    finally:
        rate_limit.close_session("ws_voice", who)


@router.websocket("/ws/client")
async def websocket_client_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for LAN worker nodes (client agents).
    Manages persistent connection, handshake registration, and message routing.

    Zero-Trust: bắt buộc enrollment secret (hoặc JWT admin/manager) qua query param
    ``?token=``. Trước đây bất kỳ máy nào trong LAN cũng đăng ký được làm worker và
    nhận lệnh thực thi skill — tức là remote code execution không cần xác thực.
    """
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    principal = ws_auth.authenticate_worker_principal(websocket)
    if principal is None:
        logger.warning(
            "Từ chối worker WebSocket từ %s: thiếu hoặc sai enrollment token.",
            websocket.client.host if websocket.client else "unknown",
        )
        # Chặn ở tầng WebSocket (code 1008 = Policy Violation) trước khi accept.
        await websocket.close(code=1008, reason="Unauthorized: thiếu enrollment token hợp lệ.")
        return

    await websocket.accept()
    client_id = "unknown"
    try:
        # First message is registration handshake
        init_raw = await websocket.receive_text()
        init_data = json.loads(init_raw)
        if principal.get("kind") == "device":
            # Máy có khoá riêng: tên máy do MÁY CHỦ cấp lúc đăng ký — Agent không
            # tự xưng là máy khác được (trước đây client_id lấy nguyên từ Agent).
            init_data["client_id"] = principal["client_id"]
            from mateai.application.devices import worker_enrollment
            worker_enrollment.touch(principal["client_id"], init_data.get("agent_version"), init_data.get("package"))
        client_id = init_data.get("client_id") or (websocket.client.host if websocket.client else "unknown_worker")
        await orchestrator.register_client(client_id, websocket, init_data)
        await orchestrator.offer_update(client_id)

        # Message loop
        while True:
            msg_raw = await websocket.receive_text()
            try:
                msg_data = json.loads(msg_raw)
            except json.JSONDecodeError:
                continue
            orchestrator.handle_incoming_message(client_id, msg_data)

    except WebSocketDisconnect:
        logger.info("Worker client [%s] disconnected.", client_id)
    except Exception as exc:
        logger.error("Worker WebSocket error [%s]: %s", client_id, exc)
    finally:
        await orchestrator.unregister_client(client_id, websocket)
        if principal.get("kind") == "device":
            from mateai.application.devices import worker_enrollment
            worker_enrollment.touch(principal["client_id"])
