# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/users.py
=======================================
Quản trị tài khoản người dùng (CRUD + đặt lại mật khẩu). Chỉ admin.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateUserRequest(BaseModel):
    """Payload for POST /api/v1/users."""
    username: str = Field(..., min_length=3, description="Tên đăng nhập")
    password: str = Field(..., min_length=6, description="Mật khẩu (tối thiểu 6 ký tự)")
    full_name: Optional[str] = Field(default="", description="Họ và tên người dùng")
    role: Optional[str] = Field(default="viewer", description="Vai trò (admin, manager, viewer)")


class UpdateUserRequest(BaseModel):
    """Payload for PUT /api/v1/users/{user_id}."""
    full_name: Optional[str] = Field(default=None, description="Họ và tên người dùng")
    role: Optional[str] = Field(default=None, description="Vai trò (admin, manager, viewer)")
    #: ABAC (prompt cuối §35, §64): phòng ban ERP ("" = bỏ gán) và cấp bảo mật 1–4 (null = theo vai trò).
    department: Optional[str] = Field(default=None, max_length=120, description="Tên phòng ban ERP")
    clearance_level: Optional[int] = Field(default=None, ge=1, le=4, description="Cấp bảo mật 1–4")


class ChangeUserPasswordRequest(BaseModel):
    """Payload for PUT /api/v1/users/{user_id}/password."""
    new_password: str = Field(..., min_length=6, description="Mật khẩu mới (tối thiểu 6 ký tự)")


@router.get(
    "/api/v1/users",
    summary="Lấy danh sách tất cả tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def list_users_endpoint(
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Trả về danh sách tài khoản người dùng an toàn (không kèm password hash)."""
    users = auth_manager.get_all_users()
    return {
        "status": "success",
        "total": len(users),
        "users": users,
    }


@router.post(
    "/api/v1/users",
    summary="Tạo tài khoản người dùng mới (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def create_user_endpoint(
    payload: CreateUserRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Tạo người dùng mới với mật khẩu được mã hóa bcrypt an toàn."""
    try:
        user_data = payload.dict()
        new_user = auth_manager.create_user(user_data)
        return {
            "status": "success",
            "message": "Đã tạo tài khoản người dùng thành công.",
            "user": new_user,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi không mong muốn khi tạo tài khoản: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể tạo tài khoản do lỗi máy chủ.",
        )


@router.put(
    "/api/v1/users/{user_id}",
    summary="Cập nhật thông tin/vai trò người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def update_user_endpoint(
    user_id: str,
    payload: UpdateUserRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Cập nhật họ tên hoặc vai trò của tài khoản theo user_id."""
    try:
        update_data = payload.dict(exclude_unset=True)
        updated_user = auth_manager.update_user(user_id, update_data,
                                                actor=str(admin_user.get("username") or "admin"))
        return {
            "status": "success",
            "message": "Đã cập nhật thông tin tài khoản thành công.",
            "user": updated_user,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi cập nhật tài khoản '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật tài khoản do lỗi máy chủ.",
        )


@router.delete(
    "/api/v1/users/{user_id}",
    summary="Xóa tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def delete_user_endpoint(
    user_id: str,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Xóa hoàn toàn tài khoản khỏi hệ thống."""
    try:
        auth_manager.delete_user(user_id)
        return {
            "status": "success",
            "message": f"Đã xóa tài khoản '{user_id}' thành công.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi xóa tài khoản '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xóa tài khoản do lỗi máy chủ.",
        )


@router.put(
    "/api/v1/users/{user_id}/password",
    summary="Đặt lại mật khẩu cho tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def change_password_endpoint(
    user_id: str,
    payload: ChangeUserPasswordRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Mã hóa và ghi đè mật khẩu mới cho người dùng. Đặt lại cho NGƯỜI KHÁC: người đó phải đổi ở lần đăng nhập tới (admin
    không nên biết mật khẩu đang dùng của người khác)."""
    try:
        target = auth_manager.get_user(user_id) or {}
        me = str(admin_user.get("username") or "").lower()
        must_change = str(target.get("username") or "").lower() != me
        auth_manager.change_user_password(user_id, payload.new_password, must_change=must_change)
        try:  # yêu cầu quên mật khẩu đang mở của tài khoản này coi như đã xử lý + kiểm toán việc admin cấp lại
            from mateai.application.security import password_reset
            password_reset.close_for_user(str(target.get("username") or user_id), me or "admin")
            password_reset._audit(me or "admin", "PASSWORD_SET_BY_ADMIN", str(target.get("username") or user_id), True,
                                  {"must_change": must_change})
        except Exception:  # noqa: BLE001
            logger.warning("Không đóng được yêu cầu quên mật khẩu của %s", user_id)
        return {
            "status": "success",
            "message": f"Đã đặt lại mật khẩu cho tài khoản '{user_id}' thành công.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi đặt lại mật khẩu cho '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể đặt lại mật khẩu do lỗi máy chủ.",
        )
