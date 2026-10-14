# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/telegram.py
===========================================
Cổng Telegram: cấu hình (đã che token), bật/tắt, trạng thái, gửi thử cảnh báo, dò chat id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.http.secret_masking import (
    _SECRET_MASK,
    _is_secret_field,
    _mask_secrets,
    _restore_masked_secrets,
)
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class TelegramConfigRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Bật/Tắt Telegram Gateway")
    bot_token: Optional[str] = Field("", description="Telegram Bot Token từ BotFather")
    admin_chat_ids: Optional[List[str]] = Field(default_factory=list, description="Danh sách Chat ID của admin")
    incident_group_id: Optional[str] = Field("", description="Group ID nhận cảnh báo sự cố")


class TelegramTestAlertRequest(BaseModel):
    bot_token: Optional[str] = Field(None, description="Telegram Bot Token từ BotFather")
    admin_chat_ids: Optional[List[str]] = Field(None, description="Danh sách Chat ID của admin")
    incident_group_id: Optional[str] = Field(None, description="Group ID nhận cảnh báo sự cố")
    target_chat_id: Optional[str] = Field(None, description="Chat ID mục tiêu cụ thể")


@router.get(
    "/api/v1/telegram/config",
    summary="Phase 18: Lấy cấu hình và trạng thái Telegram Bot",
    tags=["Telegram"],
)
async def get_telegram_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return Telegram Gateway settings and running status.

    Phase 79: `bot_token` trả về ký hiệu chỗ trống, KHÔNG phải giá trị thật.
    Endpoint này dùng `get_current_user` nên mọi tài khoản đã đăng nhập đều gọi
    được — kể cả role `viewer` chỉ được xem. Trả token thật ở đây tức bất kỳ
    tài khoản nào cũng chiếm được quyền điều khiển bot Telegram.
    Sửa token: nhập lại ở ô cấu hình. Gửi lại ký hiệu khi Lưu thì giữ token
    cũ (xem `_restore_masked_secrets`).
    """
    try:
        from mateai.config.loader import get_config_section
        tg_data = get_config_section("telegram")

        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        is_running = getattr(telegram_gateway, "is_running", False)

        masked = _mask_secrets(dict(tg_data))
        return {
            "status": "success",
            "enabled": tg_data.get("enabled", False),
            "bot_token": masked.get("bot_token", ""),
            "admin_chat_ids": tg_data.get("admin_chat_ids", []),
            "incident_group_id": tg_data.get("incident_group_id", ""),
            "is_running": is_running,
            "config": masked,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post(
    "/api/v1/telegram/toggle",
    summary="Phase 18: Bật hoặc Tắt tính năng Telegram Gateway",
    tags=["Telegram"],
)
async def toggle_telegram_gateway(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Enable or disable Telegram Bot Gateway to prevent conflict and error logs."""
    if current_user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền bật/tắt Telegram Gateway.")
    try:
        from mateai.application.administration import config_governance as gov
        from mateai.interfaces.http.secret_masking import _mask_secrets

        enabled = bool(payload.get("enabled", False))
        _, raw = gov.save_config(str(current_user.get("username", "?")),
                                 lambda c: c.setdefault("telegram", {}).update({"enabled": enabled}),
                                 f"{'Bật' if enabled else 'Tắt'} Telegram Gateway", _mask_secrets)

        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        if enabled:
            if raw["telegram"].get("bot_token"):
                telegram_gateway.stop()
                import asyncio; await asyncio.sleep(0.5)
                telegram_gateway.start()
        else:
            telegram_gateway.stop()

        status_text = "ĐÃ BẬT" if enabled else "ĐÃ TẮT"
        logger.info("Telegram Gateway has been %s by %s", status_text, current_user.get("username", "?"))
        return {
            "status": "success",
            "enabled": enabled,
            "is_running": getattr(telegram_gateway, "is_running", False),
            "message": f"Cổng kết nối Telegram Gateway {status_text} thành công."
        }
    except Exception as exc:
        logger.error("Failed to toggle Telegram gateway: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật Telegram Gateway: {exc}")


@router.put(
    "/api/v1/telegram/config",
    summary="Phase 18: Cập nhật cấu hình Telegram Bot",
    tags=["Telegram"],
)
async def update_telegram_config(
    payload: TelegramConfigRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Save Telegram bot configuration (bot_token, admin_chat_ids, incident_group_id, enabled).
    Restarts the Telegram gateway if enabled and configured.
    """
    if current_user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền cấu hình Telegram Bot.")

    try:
        import json as _json2
        from pathlib import Path as _Path
        from mateai.application.administration import config_governance as gov

        def _apply(raw: Dict[str, Any]) -> None:
            raw.setdefault("telegram", {})
            # Phase 80: chốt bí mật cho endpoint này, y hệt `/api/v1/config`. Ô token trống
            # nghĩa là GIỮ token đang lưu (giao diện không bao giờ nhận lại token thật).
            incoming = {
                "enabled": payload.enabled,
                "bot_token": payload.bot_token,
                "admin_chat_ids": payload.admin_chat_ids,
                "incident_group_id": payload.incident_group_id,
            }
            incoming = _restore_masked_secrets(incoming, raw.get("telegram", {}))
            if incoming["enabled"] is not None:
                raw["telegram"]["enabled"] = incoming["enabled"]
            if incoming["bot_token"]:
                raw["telegram"]["bot_token"] = incoming["bot_token"]
            if incoming["admin_chat_ids"] is not None:
                raw["telegram"]["admin_chat_ids"] = incoming["admin_chat_ids"]
            if incoming["incident_group_id"] is not None:
                raw["telegram"]["incident_group_id"] = incoming["incident_group_id"]

        # `admin_chat_ids` quyết định AI nhận lệnh admin từ chat nào — thay đổi quyền hạn:
        # trước đây không lịch sử, không audit (§70).
        _, raw = gov.save_config(str(current_user.get("username", "?")), _apply,
                                 "Cấu hình Telegram (token / chat admin / nhóm sự cố)", _mask_secrets)

        # Reload settings in-memory
        try:
            from mateai.config.loader import reload_settings
            reload_settings()
        except Exception as r_err:
            logger.warning("Phase 18: Settings reload failed after telegram config write: %s", r_err)

        is_tg_on = raw.get("telegram", {}).get("enabled", False)
        # Restart or stop gateway
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        effective_bot_token = (raw.get("telegram", {}).get("bot_token") or "").strip()
        if is_tg_on and effective_bot_token:
            try:
                telegram_gateway.stop()
                import asyncio; await asyncio.sleep(0.5)
                telegram_gateway.start()
                gateway_status = "restarted"
            except Exception as gw_exc:
                logger.warning("Phase 18: Could not restart Telegram Gateway: %s", gw_exc)
                gateway_status = "config_saved_restart_failed"
        else:
            try:
                telegram_gateway.stop()
            except Exception:
                pass
            gateway_status = "stopped" if not is_tg_on else "config_saved"

        logger.info(
            "Phase 18: Telegram config updated by '%s', gateway=%s",
            current_user.get("username", "?"),
            gateway_status,
        )
        return {
            "status": "success",
            "message": "Cấu hình Telegram đã được lưu thành công.",
            "gateway_status": gateway_status,
        }

    except Exception as exc:
        logger.error("Phase 18: Telegram config update error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật cấu hình Telegram: {exc}")


@router.get(
    "/api/v1/telegram/status",
    summary="Phase 18: Kiểm tra trạng thái Telegram Gateway",
    tags=["Telegram"],
)
async def telegram_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return current Telegram gateway running status."""
    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        running = telegram_gateway.is_running
        return {
            "status": "success",
            "gateway_running": running,
            "message": "Telegram Gateway đang hoạt động." if running else "Telegram Gateway chưa kết nối (chưa cấu hình Bot Token).",
        }
    except Exception as exc:
        return {"status": "error", "gateway_running": False, "message": str(exc)}


@router.post(
    "/api/v1/telegram/test-alert",
    summary="Phase 18: Gửi tin nhắn test đến Telegram incident group hoặc admin chat",
    tags=["Telegram"],
)
async def test_telegram_alert(
    payload: Optional[TelegramTestAlertRequest] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Send a test alert message to verify Telegram configuration."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có thể gửi tin nhắn kiểm thử.")

    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        from datetime import datetime as _dt

        bot_token = payload.bot_token if payload else None
        if bot_token and bot_token.strip() == _SECRET_MASK:
            bot_token = None
        admin_chat_ids = payload.admin_chat_ids if payload else None
        incident_group_id = payload.incident_group_id if payload else None
        target_chat_id = payload.target_chat_id if payload else None

        # Gọi Telegram API đồng bộ (timeout tới 20 s) — chạy ngoài event loop.
        result = await run_blocking(
            telegram_gateway.test_connection,
            bot_token=bot_token,
            admin_chat_ids=admin_chat_ids,
            incident_group_id=incident_group_id,
            target_chat_id=target_chat_id,
            custom_message=(
                f"🔔 <b>VN-MateAI Test Alert</b>\n\n"
                f"✅ Kết nối Telegram thành công!\n"
                f"👤 Gửi bởi: {current_user.get('username', 'Admin')}\n"
                f"🕐 Thời gian: {_dt.now().strftime('%d/%m/%Y %H:%M:%S')}"
            ),
        )
        return result
    except Exception as exc:
        logger.error("Phase 18: Telegram test alert error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi gửi test alert: {exc}")


@router.post(
    "/api/v1/telegram/detect-chat",
    summary="Phase 18: Dò tìm Chat ID gần nhất gửi tới Bot Telegram",
    tags=["Telegram"],
)
async def detect_telegram_chat(
    payload: Optional[TelegramTestAlertRequest] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Auto-detect recent chat IDs from incoming messages to the Telegram bot."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập.")

    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        bot_token = payload.bot_token if payload else None
        chats = await run_blocking(telegram_gateway.get_recent_chats, bot_token=bot_token)
        return {
            "status": "success",
            "count": len(chats),
            "chats": chats,
        }
    except Exception as exc:
        logger.error("Phase 18: Telegram detect chat error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi dò tìm chat: {exc}")
