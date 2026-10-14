# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/approval_flow.py
=======================================
Duyệt / huỷ tác vụ đang chờ rồi báo kết quả tới HUD, portal, Telegram và đọc lên HUD.
Dùng chung cho `POST /api/v1/security/confirm-action` và nút duyệt trên HUD (`/ws/hud`).
Nghiệp vụ: `application/security/approval_decisions.py` (Supervisor Phase 10, §198).

Người gọi phải kiểm quyền (chỉ admin) TRƯỚC khi gọi `confirm`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

from mateai.application.security import approval_decisions as decisions
from mateai.interfaces.http import speech
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_hud_binary, broadcast_portal_ui

logger = logging.getLogger(__name__)

ApprovalDecisionError = decisions.ApprovalDecisionError
_SPEAKING: Set["asyncio.Task[None]"] = set()


def _now() -> str:
    return datetime.utcnow().isoformat()


async def _speak_on_hud(text: str) -> None:
    try:
        audio = await speech.tts_bytes(text)
        has_audio = bool(audio and len(audio) > 100)
        # Cùng giao thức lượt thoại HUD (hud_voice.say): JSON báo có tiếng + khung NHỊ PHÂN —
        # không nhét MP3 base64 vào JSON (prompt cuối §25, L10).
        await broadcast_hud({"type": "voice_active", "status": "speaking", "text": text, "has_audio": has_audio,
                             "source_device": "security_approval", "timestamp": _now()})
        if has_audio:
            await broadcast_hud_binary(audio)
        await asyncio.sleep(max(4.0, (len(text) / 15.0) + 1.8))
        await broadcast_hud({"type": "voice_active", "status": "idle",
                             "text": "Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...", "timestamp": _now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Phase 34.8] Lỗi async TTS/speak HUD sau duyệt: %s", exc)


async def confirm(approved: bool, approver: str, action_id: Optional[str] = None,
                  skill_name: Optional[str] = None) -> Dict[str, Any]:
    """Ném `ApprovalDecisionError` (404 không có / 409 lệch tên hoặc đã xử lý)."""
    pending = decisions.find_pending(action_id=action_id, skill_name=skill_name)
    key, skill, client_id = pending["id"], pending["tool_name"] or "", pending["target_client"]

    if not approved:
        msg = decisions.reject(pending, approver)
        try:
            await broadcast_hud({"type": "security_approval_resolved", "action_id": key, "status": "rejected",
                                 "skill": skill, "message": msg, "timestamp": _now()})
            await broadcast_hud({"type": "voice_active", "status": "idle", "text": msg, "timestamp": _now()})
        except Exception:  # noqa: BLE001
            pass
        return {"status": "rejected", "message": msg}

    out = await decisions.approve(pending, approver)
    res, reply = out["result"], out["reply"]

    chat = decisions.telegram_chat_of(pending)
    if chat:
        try:
            from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
            await asyncio.to_thread(telegram_gateway.send_incident_alert, f"✅ [ĐÃ PHÊ DUYỆT]\n\n{reply}", target=chat)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Phase 25] Lỗi gửi thông báo Telegram sau duyệt: %s", exc)

    try:
        await broadcast_portal_ui("action_approved_result", {"action_id": key, "skill": skill, "client_id": client_id,
                                                             "query": out["query"], "reply": reply, "result": res})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Phase 25] Lỗi broadcast WebSocket sau duyệt: %s", exc)
    try:
        await broadcast_hud({"type": "security_approval_resolved", "action_id": key, "status": "approved",
                             "skill": skill, "query": out["query"], "reply": reply,
                             "speech_reply": out["speech_reply"],
                             "message": f"Tác vụ '{skill}' đã được phê duyệt qua Web Portal.", "timestamp": _now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Phase 34.8] Lỗi broadcast HUD security_approval_resolved: %s", exc)

    task = asyncio.get_running_loop().create_task(_speak_on_hud(out["speech_reply"]))
    _SPEAKING.add(task)                          # asyncio chỉ giữ tham chiếu yếu tới task
    task.add_done_callback(_SPEAKING.discard)
    return {"status": "success", "client_id": client_id, "skill": skill, "result": res, "reply": reply}
