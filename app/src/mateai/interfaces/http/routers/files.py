# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/files.py
=======================================
Thao tác tệp trên máy chủ hoặc máy trạm (liệt kê / đọc / ghi / xoá).

Chỉ admin. Mọi thao tác đi qua cổng tool duy nhất
(`mateai.application.agent.tool_gate.run_tool_with_policy`): Zero-Trust,
HITL, RBAC và audit giống hệt khi agent gọi cùng tool. Ghi và xoá cần phê
duyệt qua hàng đợi HITL; request KHÔNG tự xác nhận được (trước đây trường
`confirmed` trong body cho phép bỏ qua bước duyệt).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mateai.application.agent.tool_gate import run_tool_with_policy
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class _FsTarget(BaseModel):
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsListRequest(_FsTarget):
    path: str = Field(default=".", description="Đường dẫn thư mục")


class FsReadRequest(_FsTarget):
    file_path: str = Field(..., description="Đường dẫn file cần đọc")
    lines: int = Field(default=500, description="Số dòng tối đa từ cuối file")


class FsWriteRequest(_FsTarget):
    file_path: str = Field(..., description="Đường dẫn file cần ghi")
    content: str = Field(..., description="Nội dung file")
    mode: str = Field(default="w", description="Chế độ 'w' hoặc 'a'")


class FsDeleteRequest(_FsTarget):
    path: str = Field(..., description="Đường dẫn file hoặc thư mục cần xóa")
    is_folder: bool = Field(default=False, description="True nếu là thư mục")


# Kênh gọi ghi vào pending action / audit. Không chứa từ khoá kênh quản trị
# của tool_gate nên tác vụ NEED_CONFIRM luôn vào hàng đợi phê duyệt.
_SOURCE_DEVICE = "http:fs"


async def _run(tool: str, args: Dict[str, Any], req: _FsTarget, user: Dict[str, Any], query: str) -> Dict[str, Any]:
    target = req.target_client_id or req.target_client or "master"
    gate = await run_tool_with_policy(
        tool,
        {**args, "target_client": target},
        caller=str(user.get("username") or "admin"),
        source_device=_SOURCE_DEVICE,
        query=query,
    )
    result = gate["result"]
    # Skill cục bộ chạy qua plugin_manager trả phong bì {success, data, error};
    # API này trước nay trả thẳng kết quả của skill — giữ nguyên hợp đồng đó.
    if isinstance(result, dict) and "success" in result and isinstance(result.get("data"), dict):
        return result["data"] if result["success"] else {"status": "error", "error": result.get("error")}
    return result


@router.post(
    "/api/v1/fs/list",
    summary="List directory contents",
    tags=["Native File System"],
)
async def fs_list_endpoint(
    req: FsListRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    return await _run("list_directory", {"path": req.path}, req, current_user, f"Liệt kê thư mục: {req.path}")


@router.post(
    "/api/v1/fs/read",
    summary="Read file text/log (last N lines)",
    tags=["Native File System"],
)
async def fs_read_endpoint(
    req: FsReadRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    return await _run("read_file", {"file_path": req.file_path, "lines": req.lines}, req, current_user,
                      f"Đọc tệp tin: {req.file_path}")


@router.post(
    "/api/v1/fs/write",
    summary="Write file (Zero-Trust approval via HITL queue)",
    tags=["Native File System"],
)
async def fs_write_endpoint(
    req: FsWriteRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    return await _run("write_file", {"file_path": req.file_path, "content": req.content, "mode": req.mode},
                      req, current_user, f"Ghi tệp tin: {req.file_path}")


@router.post(
    "/api/v1/fs/delete",
    summary="Delete file or folder (Zero-Trust approval via HITL queue)",
    tags=["Native File System"],
)
async def fs_delete_endpoint(
    req: FsDeleteRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    return await _run("delete_item", {"path": req.path, "is_folder": req.is_folder}, req, current_user,
                      f"Xóa {'thư mục' if req.is_folder else 'tệp tin'}: {req.path}")
