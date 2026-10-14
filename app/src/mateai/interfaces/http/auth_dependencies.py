# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/auth_dependencies.py
===========================================
Dependency FastAPI cho xác thực HTTP: lấy người dùng từ JWT (header Bearer hoặc
?token=) và kiểm tra role. Tầng giao diện — logic JWT/mật khẩu nằm ở
mateai.application.security.auth_manager.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mateai.application.security.auth_manager import auth_manager

logger = logging.getLogger(__name__)

http_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# FastAPI Security Dependencies
# ---------------------------------------------------------------------------
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    token_query: Optional[str] = Query(default=None, alias="token"),
) -> Dict[str, Any]:
    """
    FastAPI dependency trích xuất và kiểm tra người dùng hiện tại từ:
    1. Header Authorization: Bearer <token>
    2. Query param ?token=<token> (hỗ trợ phát audio trực tiếp hoặc xem tệp)

    Zero-Trust: KHÔNG có bất kỳ fallback nào. Mọi request đều phải mang JWT hợp lệ.
    (Trước đây từng tự cấp quyền admin cho localhost và cho Referer chứa "/hud" —
     cả hai đều dễ bị giả mạo và đã bị gỡ bỏ.)
    """
    raw_token = None
    if credentials and credentials.credentials:
        raw_token = credentials.credentials
    elif token_query:
        raw_token = token_query

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực tài khoản (Thiếu Bearer Token).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = auth_manager.decode_access_token(raw_token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload.get("sub")
    user = auth_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản người dùng không tồn tại.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Trả về thông tin an toàn (bỏ password_hash)
    return {
        "id": user.get("id", f"usr_{user['username']}"),
        "username": user["username"],
        "full_name": user.get("full_name", user["username"]),
        "role": user.get("role", "viewer"),
        "created_at": user.get("created_at"),
    }


def require_roles(allowed_roles: List[str]):
    """
    Dependency factory kiểm tra quyền của người dùng (RBAC).
    Ví dụ: Depends(require_roles(["admin", "manager"]))
    """
    async def role_checker(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        user_role = current_user.get("role", "viewer")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyền hạn '{user_role}' không được phép thực hiện tác vụ này. Yêu cầu một trong các quyền: {allowed_roles}.",
            )
        return current_user

    return role_checker
