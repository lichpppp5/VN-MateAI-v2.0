# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/hud_voice.py
===================================
Lệnh thoại từ HUD (/ws/hud) và dữ liệu hiển thị của HUD:
  - `process_command` / `process_command_body`: một lượt thoại (stream LLM +
    vòng agent, TTS từng câu, đồng bộ portal), lệnh mới huỷ lượt cũ của phiên;
  - `broadcast_thinking`: trạng thái "đang suy nghĩ" của model;
  - `get_metrics_payload` / `telemetry_loop`: số liệu máy chủ đẩy lên HUD.

Gọi qua module (`hud_voice.process_command(...)`) để test thay một chỗ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

from mateai.config.loader import get_assistant_name
from mateai.interfaces.http import speech
from mateai.interfaces.websocket.realtime_hub import (
    active_audio_nodes,
    active_hud_websockets,
    active_portal_websockets,
    broadcast_hud,
    broadcast_hud_binary,
    broadcast_portal_ui,
)

logger = logging.getLogger(__name__)


async def broadcast_thinking(state: str, text: str = "", query: str = "") -> None:
    """Phase 87 — báo HUD về QUÁ TRÌNH SUY NGHĨ của model.

    Model suy luận ở field `reasoning`, tách hẳn khỏi `content` — nên câu trả
    lời bạn nghe không bị lẫn suy nghĩ, nhưng trước đây phần suy nghĩ bị vứt
    đi hoàn toàn. Giờ nó được gom gọn (`_compact_reasoning`) rồi đẩy sang HUD
    để hiện trong khung gập lại được.

    `state`:
      - "thinking" → model đang suy nghĩ, chưa có nội dung (hiện vòng xoay)
      - "done"     → suy nghĩ đã xong, `text` là nội dung đã gọn
      - "empty"    → không có suy nghĩ để hiện (lỗi, hoặc model không suy luận)
    """
    await broadcast_hud({
        "type": "thinking",
        "status": state,
        "text": text or "",
        "query": query or "",
        "timestamp": datetime.utcnow().isoformat(),
    })


#: Task đang xử lý lệnh thoại, theo phiên. Lệnh mới tới sẽ HUỶ task cũ.
#:
#: Phase 81: trước đây mỗi lệnh tạo một `asyncio.create_task()` rồi bỏ mặc.
#: Nên khi người dùng nói lệnh thứ hai, lượt thứ nhất vẫn chạy tiếp: vẫn gọi
#: LLM, vẫn sinh TTS, vẫn đẩy `voice_active` xuống HUD. HUD phát hết rồi
#: câu mới mới lên tiếng — người dùng nói xong vẫn phải nghe tiếp, đúng triệu
#: chứng "ra lệnh mà AI không dừng".
#:
#: Huỷ task cũ là đủ: `asyncio.CancelledError` ném ra giữa `await` nên vòng
#: lặp stream LLM và `broadcast_hud` phía sau không chạy nữa.
active_tasks: Dict[str, "asyncio.Task"] = {}


def cancel_task(session_id: str) -> bool:
    """Huỷ lượt đang xử lý của phiên. True nếu có thật sự huỷ được."""
    task = active_tasks.get(session_id)
    if task is None or task.done():
        active_tasks.pop(session_id, None)
        return False
    task.cancel()
    return True


async def process_command(cmd_query: str, session_id: str = "hud", *, caller: str) -> None:
    """
    Bọc lượt thoại, tự dọn sổ task khi xong.

    `finally` là chỗ duy nhất đảm bảo sổ không giữ task chết. Hàm thân có
    nhiều nhánh `return` sớm; dọn ở từng nhánh thì sót nhánh là sổ giữ task đã
    chết, và lệnh sau tới sẽ đi huỷ một task vô hại rồi tưởng đã dừng được lượt
    cũ — sai. Ở đây `finally` chạy ở MỌI đường thoát, kể cả `CancelledError`
    do lệnh mới huỷ, nên không sót đường nào.
    """
    task = asyncio.current_task()
    active_tasks[session_id] = task  # type: ignore[assignment]
    try:
        await process_command_body(cmd_query, session_id, caller=caller)
    finally:
        # Chỉ xoá nếu sổ vẫn đang trỏ tới CHÍNH mình. Lệnh mới tới đã ghi đè
        # sổ rồi, xoá vô điều kiện sẽ làm mất task của lượt đang chạy.
        if active_tasks.get(session_id) is task:
            active_tasks.pop(session_id, None)


#: Câu hỏi lại sau mỗi câu trả lời không tự kết thúc bằng câu hỏi.
from mateai.application.voice.voice_session import FOLLOW_UP_PHRASE as FOLLOW_UP_QUESTION  # noqa: E402
#: Câu chào khi chờ 30 giây không nghe thấy phản hồi (HUD báo `end_conversation`).
from mateai.application.voice.voice_session import FAREWELL_PHRASE as FAREWELL  # noqa: E402 — dùng chung với robot
#: Câu chào khi admin nói không còn yêu cầu (HUD báo `end_conversation` user_done, hoặc máy chủ tự nhận ra).
from mateai.application.voice.voice_session import GOODBYE_PHRASE as GOODBYE  # noqa: E402 — dùng chung với robot


async def say(text: str) -> None:
    """Máy chủ đọc một câu cố định lên HUD (chữ + tiếng). HUD không tự tổng hợp
    giọng được — trước đây nó gọi `speakHudText` không tồn tại nên câu hỏi lại
    không bao giờ phát ra tiếng."""
    from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
    audio = get_cached_audio_bytes(text) or await speech.tts_bytes(text)
    has_audio = bool(audio and len(audio) > 100)
    await broadcast_hud({
        "type": "voice_active", "status": "speaking", "text": text,
        "source_device": "hud", "is_follow_up": True, "has_audio": has_audio,
        "timestamp": datetime.utcnow().isoformat(),
    })
    if has_audio:
        await broadcast_hud_binary(audio)


async def end_conversation(session_id: str = "hud", reason: str = "user_done") -> None:
    """Kết thúc vòng hội thoại rồi về trạng thái chờ. `timeout`: không nghe phản hồi → "Nếu anh không có yêu cầu
    nào khác thì tạm biệt…"; `user_done`: admin nói "không / không cần, cảm ơn…" → "Dạ vâng, em chào anh…".
    Không gọi LLM — câu cố định, có sẵn trong cache âm thanh nên phát ngay."""
    from mateai.application.voice.voice_session import voice_sessions
    voice_sessions.get(session_id).clear_expecting_reply()
    await say(FAREWELL if reason == "timeout" else GOODBYE)
    await broadcast_hud({
        "type": "voice_state", "session_id": session_id, "expecting_reply": False,
        "question": "", "closed": True, "reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    })
    logger.info("[HUD] Kết thúc hội thoại phiên %s (%s)", session_id, reason)


class HudVoiceSink:
    """Đầu ra của HUD (/ws/hud): chữ + audio binary, đồng bộ portal.

    Chữ của một câu được gửi CÙNG LÚC với audio của câu đó (chữ bám theo tiếng);
    TTS lỗi thì vẫn gửi chữ, chỉ không có tiếng.
    """

    def __init__(self, cmd_query: str) -> None:
        self.cmd_query = cmd_query
        self.display_text = ""

    async def on_status(self, status: str, **info: Any) -> None:
        if status == "speaking":
            reasoning = info.get("reasoning") or ""
            await broadcast_thinking("done" if reasoning else "empty", reasoning, self.cmd_query)

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        self.display_text = display_text

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        packet: Dict[str, Any] = {
            "type": "voice_active",
            "status": "speaking",
            "text": text,
            "source_device": "hud",
            # Tiếng đi riêng bằng khung nhị phân ngay sau gói này. Thiếu cờ này
            # HUD tưởng câu không có tiếng: hiện cảnh báo "không có tiếng" và
            # nhảy về idle rồi lại "đang nói" khi khung tiếng tới.
            "has_audio": bool(audio and len(audio) > 100),
            "timestamp": datetime.utcnow().isoformat(),
        }
        if kind in ("filler", "ack"):
            packet["is_filler"] = True
        else:
            packet["display_text"] = self.display_text or text
            packet["query"] = self.cmd_query
        await broadcast_hud(packet)
        if audio and len(audio) > 100:
            await broadcast_hud_binary(audio)
        if kind == "speech":
            await broadcast_portal_ui("voice_response", {
                "query": self.cmd_query,
                "reply": text,
                "display_text": self.display_text or text,
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })


async def process_command_body(cmd_query: str, session_id: str = "hud", *, caller: str) -> None:
    """
    Một lượt nói của HUD. Nghiệp vụ ở mateai.application.voice.voice_turn.process_voice_turn (dùng
    chung mọi kênh); ở đây chỉ còn phần riêng của HUD: câu "thôi/dừng" khi đang
    chờ trả lời, lời đệm sau 1s, trạng thái chờ admin trả lời, về idle.
    """
    from mateai.application.voice.voice_session import voice_sessions, is_stop_reply, looks_like_question
    from mateai.application.conversation.memory_manager import detect_and_handle_context_lifecycle
    from mateai.application.voice.voice_turn import process_voice_turn

    # Ephemeral Data Lifecycle
    detect_and_handle_context_lifecycle(session_id, cmd_query)

    session = voice_sessions.get(session_id)
    if is_stop_reply(cmd_query) and session.expecting_reply:
        # "Không còn gì / thôi / cảm ơn" khi đang chờ: chào tạm biệt rồi đóng lắng nghe,
        # không gọi LLM.
        logger.info("[HUD] Admin không còn yêu cầu: '%s' — chào và về chờ", cmd_query[:80])
        await end_conversation(session_id, "user_done")
        return

    session.clear_expecting_reply()
    logger.info("Standby HUD WS voice command: '%s' (phiên %s)", cmd_query[:100], session_id)

    await broadcast_hud({
        "type": "voice_active",
        "status": "listening",
        "text": cmd_query,
        "source_device": "hud",
        "timestamp": datetime.utcnow().isoformat(),
    })
    await broadcast_thinking("thinking", query=cmd_query)

    sink = HudVoiceSink(cmd_query)
    t_start = time.perf_counter()
    try:
        result = await process_voice_turn(
            cmd_query,
            sink=sink,
            session_id=session_id,
            source_device="hud",
            # RBAC theo người đã đăng nhập trên HUD, không theo nhãn "hud".
            caller=caller,
            filler_after_s=1.0,  # Phase 67/70: lời đệm chỉ khi câu thật chưa về sau 1s
        )
    except asyncio.CancelledError:
        logger.info("[HUD/Stream] Task bị huỷ (lệnh mới đến)")
        raise
    except Exception as exc:
        logger.error("[HUD/Stream] Lỗi lượt nói: %s", exc, exc_info=True)
        await broadcast_thinking("empty", query=cmd_query)
        return

    logger.info(
        "[HUD/Stream] Hoàn tất lượt nói sau %.2fs (%d câu, %d câu đệm)",
        time.perf_counter() - t_start, len(result.sentences), 1 if result.filler_played else 0,
    )

    said = result.reply_text.strip()
    if said:
        # Vòng hội thoại: trả lời → hỏi lại → chờ câu tiếp. Câu trả lời đã tự
        # kết thúc bằng câu hỏi thì dùng chính nó, không hỏi thêm (tránh hỏi hai lần).
        if looks_like_question(said):
            question = said
        else:
            question = FOLLOW_UP_QUESTION
            await say(FOLLOW_UP_QUESTION)
        session.mark_expecting_reply(question)
        await broadcast_hud({
            "type": "voice_state",
            "session_id": session_id,
            "expecting_reply": True,
            "question": question,
            "reask_count": session.reask_count,
            "timestamp": datetime.utcnow().isoformat(),
        })
    else:
        await broadcast_thinking("empty", query=cmd_query)
    # HUD tự về idle khi phát hết tiếng (trước đây máy chủ ĐOÁN thời lượng rồi
    # đẩy "idle" — đè lên trạng thái "đang nghe" của vòng hội thoại).


def get_metrics_payload() -> Dict[str, Any]:
    """
    Tổng hợp telemetry phần cứng, mạng và clients cho Standby HUD.

    Phase 76: KHÔNG còn trường quyền hạn (`security_role` / `security_status` /
    `permission_level`). Bản cũ trả cứng "ADMIN / ZERO-TRUST SENTINEL / FULL
    UNRESTRICTED" cho MỌI kết nối — kể cả khi chưa đăng nhập — vì đây là payload
    broadcast chung, không biết ai đang xem. Vai trò thật nay được gửi riêng
    trong gói `hud_welcome` của từng kết nối (xem `websocket_hud_endpoint`).

    Số đo thiếu được trả `None` (JSON null) để giao diện hiện "chờ kết nối",
    tuyệt đối không bịa giá trị thay thế.
    """
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    vmem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    hw = SYSTEM_HEALTH_CACHE.get("hardware", {})

    # Đĩa: lấy từ cache health; cache còn giá trị mặc định 0.0 (chưa đo) thì
    # đo trực tiếp bằng psutil. Nếu psutil cũng lỗi mới trả None → giao diện
    # hiện "chờ kết nối". Trước đây nhánh lỗi trả 45.0 — một con số bịa.
    disk_pct = hw.get("disk_percent")
    disk_free_gb = hw.get("disk_free_gb")
    disk_total_gb = hw.get("disk_total_gb")
    if not disk_pct or not disk_free_gb or not disk_total_gb:
        try:
            disk_root = psutil.disk_usage("/")
            disk_pct = disk_root.percent
            disk_free_gb = round(disk_root.free / (1024 ** 3), 2)
            disk_total_gb = round(disk_root.total / (1024 ** 3), 2)
        except Exception:
            logger.warning("HUD: không đọc được dung lượng đĩa — trả null thay vì số bịa.")
            disk_pct = None
            disk_free_gb = None
            disk_total_gb = None

    # Xung nhịp CPU: cache 0.0 nghĩa là chưa đo được → thử psutil, không được thì None.
    cpu_freq_mhz = hw.get("cpu_freq_mhz")
    if not cpu_freq_mhz:
        try:
            freq = psutil.cpu_freq()
            cpu_freq_mhz = round(freq.current, 0) if freq else None
        except Exception:
            cpu_freq_mhz = None

    procs = len(psutil.pids())
    connected_clients = len(orchestrator.get_connected_clients())
    skills_count = plugin_manager.get_skill_count()
    skills_enabled = len(plugin_manager.get_all_tools())

    return {
        "cpu_percent": round(cpu, 1),
        "cpu_cores": hw.get("cpu_cores", psutil.cpu_count(logical=True) or 1),
        "cpu_freq_mhz": cpu_freq_mhz,
        "ram_percent": round(vmem.percent, 1),
        "ram_used_gb": round(vmem.used / (1024**3), 2),
        "ram_total_gb": round(vmem.total / (1024**3), 2),
        "disk_percent": None if disk_pct is None else round(disk_pct, 1),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "processes_count": procs,
        "connected_clients": connected_clients,
        "active_audio_hardware": len(active_audio_nodes),
        "active_web_clients": len(active_portal_websockets),
        "skills_count": skills_count,
        "skills_enabled": skills_enabled,
        "net_sent_mbps": hw.get("net_sent_mbps"),
        "net_recv_mbps": hw.get("net_recv_mbps"),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def telemetry_loop() -> None:
    """Phase 33 & 51: Vòng lặp phát số liệu telemetry đầy đủ tới Standby HUD mỗi 2 giây."""
    while True:
        try:
            if active_hud_websockets:
                payload = get_metrics_payload()
                await broadcast_hud({
                    "type": "metrics_update",
                    "data": payload,
                })
        except Exception as exc:
            logger.debug("HUD telemetry loop error: %s", exc)
        await asyncio.sleep(2.0)
