# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/robots.py
========================================
Bảng điều khiển TỪNG robot trợ lý (Portal → Robot & Micro). Trước đây chỉ có danh
sách + một nút phát thanh tới mọi robot.

  GET  /api/v1/robots                         chi tiết mọi robot đang kết nối
  POST /api/v1/robots/{id}/say                đọc một câu trên robot đó
  POST /api/v1/robots/{id}/animate            cử động (vẫy tay, gật đầu…)
  POST /api/v1/robots/{id}/volume             âm lượng loa (firmware ≥ 54)
  POST /api/v1/robots/{id}/status             yêu cầu robot báo trạng thái (≥ 54)
  POST /api/v1/robots/{id}/reboot             khởi động lại (≥ 54)
  POST /api/v1/robots/{id}/audio-check        kiểm tra micro: mức tiếng / ồn nền / nhận dạng
  GET  /api/v1/robots/{id}/audio-check        kết quả kiểm tra gần nhất

Lệnh mà firmware robot chưa hỗ trợ trả 409 kèm lý do (không báo thành công giả).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter(tags=["Robot"])

ANIMATIONS = {"wave_hand": "Vẫy tay", "nod_head": "Gật đầu", "look_around": "Nhìn quanh",
              "excited": "Phấn khích", "sad": "Buồn"}
_VIEW = Depends(require_roles(["manager", "admin"]))
_ACT = Depends(require_roles(["admin"]))


class SayRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)


class AnimateRequest(BaseModel):
    animation: str


class VolumeRequest(BaseModel):
    level: int = Field(..., ge=0, le=100)


def _node(device_id: str):
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    node = xiaozhi_gateway.get_node(device_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Robot '{device_id}' không kết nối.")
    return node


def _require(node, feature: str) -> None:
    if feature not in (node.features or []):
        raise HTTPException(status_code=409, detail=(
            f"Firmware {node.firmware_version} của robot chưa hỗ trợ lệnh này — nạp firmware 54 trở lên "
            "(esp32_firmware: pio run -e esp32s3 -t upload)."))


def _audit(user: dict, action: str, device_id: str, details: Optional[Dict[str, Any]] = None) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username")), action, "ROBOT", "SUCCESS",
                                  {"device_id": device_id, **(details or {})})
    except Exception:  # noqa: BLE001
        pass


def _describe(node) -> Dict[str, Any]:
    from mateai.infrastructure.database.db_manager import db_manager
    principal = node.caller if str(node.caller).startswith("device:") else None
    role = grants = None
    if principal:
        try:
            role = db_manager.get_device_role(node.device_id)
            grants = [g.get("tool_name") for g in db_manager.list_approval_grants(principal)]
        except Exception:  # noqa: BLE001
            pass
    busy = node.active_task is not None and not node.active_task.done()
    feats = node.features or []
    return {
        "device_id": node.device_id, "client_host": node.client_host,
        "connected_at": node.connected_at, "last_active": node.last_active,
        "state": node.state, "emotion": node.emotion, "busy": busy,
        "follow_up": node.follow_up, "firmware_version": node.firmware_version,
        "features": feats,
        "supports": {"volume": "volume_ctrl" in feats, "reboot": "reboot" in feats,
                     "status": "status_report" in feats},
        "own_token": principal is not None, "role": role, "approval_grants": grants,
        "audio_stats": node.audio_stats or None, "status_report": node.status_report or None,
        "audio_check": node.audio_check,
    }


@router.get("/api/v1/robots", summary="Chi tiết từng robot đang kết nối")
async def list_robots(user: dict = _VIEW) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    return {"status": "success", "animations": ANIMATIONS,
            "robots": [_describe(n) for n in xiaozhi_gateway.get_all_nodes().values()]}


@router.post("/api/v1/robots/{device_id}/say", summary="Robot đọc một câu")
async def robot_say(device_id: str, payload: SayRequest, user: dict = _ACT) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    node = _node(device_id)
    if node.active_task is not None and not node.active_task.done():
        raise HTTPException(status_code=409, detail="Robot đang xử lý một lượt — thử lại sau.")
    await xiaozhi_gateway.speak(device_id, payload.text.strip())
    _audit(user, "robot_say", device_id, {"chars": len(payload.text)})
    return {"status": "success", "message": f"Robot {device_id} đã đọc câu."}


@router.post("/api/v1/robots/{device_id}/animate", summary="Robot cử động")
async def robot_animate(device_id: str, payload: AnimateRequest, user: dict = _ACT) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    _node(device_id)
    if payload.animation not in ANIMATIONS:
        raise HTTPException(status_code=400, detail=f"Cử động không có: {payload.animation}")
    await xiaozhi_gateway.send_command(device_id, {"type": "cmd", "action": "animate", "anim": payload.animation})
    _audit(user, "robot_animate", device_id, {"animation": payload.animation})
    return {"status": "success", "message": f"Đã gửi lệnh: {ANIMATIONS[payload.animation]}"}


@router.post("/api/v1/robots/{device_id}/volume", summary="Âm lượng loa robot (firmware ≥ 54)")
async def robot_volume(device_id: str, payload: VolumeRequest, user: dict = _ACT) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    _require(_node(device_id), "volume_ctrl")
    await xiaozhi_gateway.send_command(device_id, {"type": "set_volume", "level": payload.level})
    _audit(user, "robot_volume", device_id, {"level": payload.level})
    return {"status": "success", "message": f"Đã đặt âm lượng {payload.level}% (robot xác nhận qua báo cáo trạng thái)."}


@router.post("/api/v1/robots/{device_id}/status", summary="Yêu cầu robot báo trạng thái (firmware ≥ 54)")
async def robot_status(device_id: str, user: dict = _VIEW) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    _require(_node(device_id), "status_report")
    await xiaozhi_gateway.send_command(device_id, {"type": "get_status"})
    return {"status": "success"}


@router.post("/api/v1/robots/{device_id}/reboot", summary="Khởi động lại robot (firmware ≥ 54)")
async def robot_reboot(device_id: str, user: dict = _ACT) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    _require(_node(device_id), "reboot")
    await xiaozhi_gateway.send_command(device_id, {"type": "reboot"})
    _audit(user, "robot_reboot", device_id)
    return {"status": "success", "message": "Robot đang khởi động lại (~20 s rồi tự kết nối lại)."}


@router.post("/api/v1/robots/{device_id}/audio-check", summary="Kiểm tra micro robot")
async def robot_audio_check_start(device_id: str, user: dict = _ACT) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    node = _node(device_id)
    if node.active_task is not None and not node.active_task.done():
        raise HTTPException(status_code=409, detail="Robot đang xử lý một lượt — thử lại sau.")
    import asyncio
    asyncio.create_task(xiaozhi_gateway.start_audio_check(device_id))
    _audit(user, "robot_audio_check", device_id)
    return {"status": "success", "message": "Robot sẽ mời anh nói một câu — kết quả sau ~15 s."}


@router.get("/api/v1/robots/{device_id}/audio-check", summary="Kết quả kiểm tra micro gần nhất")
async def robot_audio_check_result(device_id: str, user: dict = _VIEW) -> Dict[str, Any]:
    node = _node(device_id)
    return {"status": "success", "check": node.audio_check, "now": time.time()}
