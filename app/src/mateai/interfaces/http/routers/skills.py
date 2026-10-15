# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/skills.py
========================================
Danh mục skill: liệt kê, nạp lại, bật/tắt (một hoặc nhiều), tạo skill tuỳ
biến, chạy trực tiếp một skill (RBAC + HITL).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


_REGISTRY_PATH = Path(settings.PROJECT_ROOT) / "skills" / "registry.json"


class SkillToggleRequest(BaseModel):
    """Payload for POST /api/v1/skills/toggle."""
    name: str = Field(..., min_length=1)
    enabled: bool = Field(...)


class SkillCreateRequest(BaseModel):
    """Payload for POST /api/v1/skills/create."""
    name: str = Field(..., min_length=2, max_length=64)
    description: str = Field(..., min_length=5, max_length=500)
    python_code: str = Field(..., min_length=5)
    parameters: Optional[Dict[str, Any]] = None


class SkillExecuteRequest(BaseModel):
    """Payload for POST /api/v1/skills/execute."""
    name: str = Field(..., min_length=1, description="Tên kỹ năng")
    arguments: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Tham số đầu vào")


class BatchSkillToggleRequest(BaseModel):
    """Payload for POST /api/v1/skills/batch-toggle."""
    enabled: bool = Field(..., description="Bật hoặc tắt tất cả")


@router.get(
    "/api/v1/skills",
    summary="Get skills registry",
    tags=["Skills"],
)
async def get_skills_registry(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """
    Return the live skills registry from plugin_manager (preferred).
    Nếu registry sống ít hơn registry.json trên đĩa, tự động hot-reload
    để hiển thị đúng skills mới nhất (kể cả computer_use_skills).
    """
    try:
        from core.plugin_manager import plugin_manager

        # Đọc disk registry để so sánh
        disk_count = 0
        disk_registry: Dict[str, Any] = {}
        if _REGISTRY_PATH.exists():
            try:
                disk_registry = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
                disk_count = len(disk_registry)
            except Exception:
                pass

        with plugin_manager._lock:
            live_count = len(plugin_manager._registry)

        # Hot-reload nếu registry sống lạc hậu so với đĩa
        if disk_count > live_count:
            try:
                plugin_manager.load_plugins()
                logger.info("[Skills API] Hot-reload triggered: disk=%d > live=%d", disk_count, live_count)
            except Exception as reload_err:
                logger.warning("[Skills API] Hot-reload failed (non-critical): %s", reload_err)

        with plugin_manager._lock:
            live_registry: Dict[str, Any] = {
                name: {
                    "module":  entry["module"],
                    "attr":    entry["attr"],
                    "meta":    entry["meta"],
                    "enabled": entry.get("enabled", True),
                }
                for name, entry in plugin_manager._registry.items()
            }
        return live_registry if live_registry else disk_registry
    except Exception:  # pylint: disable=broad-except
        pass

    if _REGISTRY_PATH.exists():
        try:
            return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"registry.json malformed: {exc}")

    raise HTTPException(status_code=404, detail="skills/registry.json not found.")


@router.post(
    "/api/v1/skills/reload",
    summary="Hot-reload toàn bộ skill modules từ disk",
    tags=["Skills"],
)
async def reload_skills(
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Quét lại thư mục skills/, reimport tất cả module có @export_skill
    và cập nhật registry.json. Không cần restart server.
    """
    from core.plugin_manager import plugin_manager
    try:
        count = await asyncio.get_event_loop().run_in_executor(None, plugin_manager.load_plugins)
        return {
            "success": True,
            "skills_loaded": count,
            "message": f"Đã hot-reload {count} kỹ năng từ đĩa thành công.",
        }
    except Exception as exc:
        logger.error("[Skills Reload] %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Reload thất bại: {exc}")


@router.post(
    "/api/v1/skills/toggle",
    summary="Bật hoặc tắt một kỹ năng trong runtime",
    tags=["Skills"],
)
async def toggle_skill(
    payload: SkillToggleRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Bật hoặc tắt kỹ năng. Trạng thái được lưu vĩnh viễn vào registry.json.
    Khi kỹ năng bị tắt, LLM sẽ không được cấp tool đó.
    """
    from core.plugin_manager import plugin_manager
    try:
        new_state = plugin_manager.toggle_skill(payload.name, payload.enabled)
        return {"success": True, "name": payload.name, "enabled": new_state}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy kỹ năng '{payload.name}'")


@router.post(
    "/api/v1/skills/create",
    summary="Thêm kỹ năng mới bằng tay",
    tags=["Skills"],
)
async def create_custom_skill(
    payload: SkillCreateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Định nghĩa và đăng ký kỹ năng mới vào skills/custom_skills.py.
    Tự động biên dịch, kiểm tra cú pháp và hot-load vào runtime.
    """
    from core.plugin_manager import plugin_manager
    try:
        res = plugin_manager.register_custom_skill(
            name=payload.name,
            description=payload.description,
            python_code=payload.python_code,
            parameters=payload.parameters,
        )
        return {
            "success": True,
            "message": f"Kỹ năng '{payload.name}' đã được tạo và kích hoạt thành công!",
            "skill": res,
        }
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Lỗi thêm kỹ năng: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Không thể tạo kỹ năng: {exc}")


@router.post(
    "/api/v1/skills/execute",
    summary="Thực thi trực tiếp một kỹ năng và nhận kết quả tức thời",
    tags=["Skills"],
)
async def execute_skill_endpoint(
    payload: SkillExecuteRequest,
    request: Request,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Thực thi một kỹ năng trong registry với tham số được cung cấp.
    Đo thời gian thực thi chính xác và trả về kết quả 100% thời gian thực.

    Zero-Trust: đây là cổng gọi skill TRỰC TIẾP có thể chạy PowerShell, ghi/xoá
    file, tạo skill mới... Trước đây chỉ kiểm tra "đã đăng nhập", nên tài khoản
    role `viewer` (chỉ được xem) vẫn chạy được skill nguy hiểm. Nay đã nối vào
    RBAC của SecurityGuard — cùng cổng kiểm tra mà luồng chat/LLM dùng.
    """
    from core.plugin_manager import plugin_manager
    from mateai.application.security.security_guard import security_guard
    from mateai.application.security.zero_trust import execute_with_hitl

    # RBAC + rủi ro + L5 + từ khoá cấm: một hàm `policy_engine.authorize()` bên
    # trong `execute_with_hitl` (trước đây router tự gọi RBAC rồi mới qua cổng duyệt).
    t0 = time.perf_counter()
    args = payload.arguments or {}
    source_ip = request.client.host if request.client else "unknown"

    # ── Zero-Trust HITL: tác vụ rủi ro Level 3-5 phải chờ CEO duyệt ─────────
    # RBAC (SecurityGuard) chỉ kiểm tra VAI TRÒ, không kiểm tra RỦI RO. Nên
    # trước đây admin gọi `run_powershell_command` / `delete_database` chạy thẳng.
    # Đây là cổng duy nhất mọi skill đi qua, nên đặt gate ở đây là đủ.
    async def _run() -> Dict[str, Any]:
        return await plugin_manager.execute_skill(payload.name, args)

    gate = await execute_with_hitl(
        action_name=payload.name,
        params=args,
        executor=_run,
        requested_by=user.get("username", "unknown"),
        description=f"Skill '{payload.name}' được gọi qua API bởi {user.get('username', '?')}",
    )

    if gate.get("status") == "denied":
        logger.warning("[Policy] Chặn gọi skill '%s' từ '%s': %s", payload.name, user.get("username"), gate.get("message"))
        raise HTTPException(status_code=403, detail=gate.get("message"))
    if gate.get("status") == "awaiting_approval":
        security_guard.audit_tool_execution(
            tool_name=payload.name,
            execution_status="awaiting_hitl_approval",
            employee_id=user.get("username"),
            payload=args,
            source_ip=source_ip,
        )
        raise HTTPException(status_code=202, detail=gate["message"])

    res = gate["result"] if isinstance(gate.get("result"), dict) else {"result": gate.get("result")}
    duration_ms = int((time.perf_counter() - t0) * 1000)
    res["latency_ms"] = duration_ms
    res["skill_name"] = payload.name

    security_guard.audit_tool_execution(
        tool_name=payload.name,
        execution_status="success",
        employee_id=user.get("username"),
        payload=payload.arguments or {},
        source_ip=source_ip,
    )
    return res


@router.post(
    "/api/v1/skills/batch-toggle",
    summary="Bật hoặc tắt toàn bộ kỹ năng cùng lúc",
    tags=["Skills"],
)
async def batch_toggle_skills(
    payload: BatchSkillToggleRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Bật hoặc tắt tất cả các kỹ năng đã đăng ký.
    """
    from core.plugin_manager import plugin_manager
    count = 0
    with plugin_manager._lock:
        for name in list(plugin_manager._registry.keys()):
            plugin_manager.toggle_skill(name, payload.enabled)
            count += 1
    return {"success": True, "count": count, "enabled": payload.enabled}


@router.get("/api/v1/skills/catalog", summary="Danh mục kỹ năng: nhóm, chủ sở hữu, kiểm thử, có trong phiên bản này không",
            tags=["Skills"])
async def get_skills_catalog(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """skills/catalog.json (scripts/skill_catalog.py) + trạng thái thật: bị chặn ở phiên bản, thiếu nguồn dữ liệu (thẩm định #3)."""
    from mateai.application.skills.tool_readiness import ready_reason
    from mateai.config import edition
    path = Path(settings.PROJECT_ROOT) / "skills" / "catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    items = []
    for name, c in sorted(catalog.items()):
        items.append({"name": name, **c, "blocked_in_edition": edition.blocked_reason(name) or None,
                      "source_missing": ready_reason(name) or None})
    return {"edition": edition.current(), "count": len(items),
            "untested_in_commercial": sum(1 for i in items if i["commercial"] and not i["tested"]), "skills": items}
