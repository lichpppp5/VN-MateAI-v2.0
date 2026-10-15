# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Quên mật khẩu — quy trình qua quản trị viên (portal cài nội bộ, tài khoản không có email):

  1. Màn đăng nhập → "Quên mật khẩu?": người dùng nhập tên đăng nhập + ghi chú (số điện thoại / phòng ban để admin gọi xác
     minh). Máy chủ LUÔN trả cùng một câu → người ngoài không dò được tài khoản nào tồn tại. Có giới hạn theo IP (router).
  2. Tài khoản có thật (đăng nhập cục bộ, không phải SSO) → lưu yêu cầu, báo admin qua kênh cảnh báo + hộp thư header.
     Một tài khoản chỉ có một yêu cầu mở (gửi lại không tạo thêm, không báo lại).
  3. Admin xác minh người thật rồi "Cấp mật khẩu tạm" (PUT /api/v1/users/{id}/password) → người dùng phải đổi ở lần đăng
     nhập tới; yêu cầu tự đóng. Hoặc "Bỏ qua" nếu không xác minh được.
  4. Chính admin quên: scripts/reset_password.py chạy trên máy chủ (cần quyền vào máy chủ).
Mọi bước vào nhật ký kiểm toán. SQL ở infrastructure/database/governance_repo.py (RULE-024).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NEUTRAL_MESSAGE = ("Nếu tài khoản tồn tại, yêu cầu đã được gửi tới quản trị viên. Quản trị viên sẽ liên hệ để xác minh và "
                   "cấp mật khẩu tạm; bạn sẽ đặt mật khẩu mới ở lần đăng nhập sau.")
MAX_USERNAME, MAX_NOTE = 80, 300
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _audit(actor: str, event: str, target: str, ok: bool, extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(actor)[:80], "password_reset", "AUTH", "SUCCESS" if ok else "REJECTED",
                                  {"event": event, "target": str(target)[:80], **(extra or {})})
    except Exception:  # noqa: BLE001 — kiểm toán hỏng không được chặn luồng
        logger.warning("Không ghi được kiểm toán %s cho %s", event, target)


def _clean(v: Any, limit: int) -> str:
    return _CTRL.sub(" ", str(v or "")).strip()[:limit]


def submit(username: str, note: str, ip: str) -> Dict[str, Any]:
    """Ghi yêu cầu (nếu tài khoản có thật và chưa có yêu cầu mở). Trả {"message", "created": id|None} — `created` CHỈ dùng
    nội bộ (gửi thông báo admin); router không trả nó ra ngoài."""
    from mateai.infrastructure.database import governance_repo as repo
    from mateai.infrastructure.database.db_manager import db_manager
    name = _clean(username, MAX_USERNAME)
    text = _clean(note, MAX_NOTE)
    created: Optional[int] = None
    user = db_manager.get_user_by_username_or_id(name) if name else None
    if not user:
        _audit(name or "?", "PASSWORD_RESET_UNKNOWN_USER", name, False, {"source_ip": ip})
    elif (user.get("auth_source") or "local") == "sso":
        _audit(user["username"], "PASSWORD_RESET_SSO_USER", user["username"], False, {"source_ip": ip})
    else:
        if repo.pwreset_open_id(user["username"]) is None:
            created = repo.pwreset_insert(user["username"], text, ip, _now())
        _audit(user["username"], "PASSWORD_RESET_REQUESTED", user["username"], True, {"source_ip": ip, "new": bool(created)})
    return {"message": NEUTRAL_MESSAGE, "created": created, "username": user["username"] if user else None}


def list_open(limit: int = 50) -> List[Dict[str, Any]]:
    from mateai.infrastructure.database import governance_repo as repo
    from mateai.infrastructure.database.db_manager import db_manager
    out = []
    for r in repo.pwreset_list("open", limit):
        u = db_manager.get_user_by_username_or_id(r["username"]) or {}
        out.append({**r, "user_id": u.get("id") or r["username"], "full_name": u.get("full_name") or "", "role": u.get("role") or ""})
    return out


def close(request_id: int, by: str, status: str) -> bool:
    from mateai.infrastructure.database import governance_repo as repo
    if status not in ("done", "rejected"):
        raise ValueError("Trạng thái phải là done hoặc rejected.")
    n = repo.pwreset_close("id = ?", (int(request_id),), status, by, _now())
    if n:
        _audit(by, "PASSWORD_RESET_REQUEST_" + status.upper(), str(request_id), True)
    return bool(n)


def close_for_user(username: str, by: str) -> int:
    """Admin vừa cấp mật khẩu mới cho tài khoản → đóng mọi yêu cầu mở của tài khoản đó."""
    from mateai.infrastructure.database import governance_repo as repo
    return repo.pwreset_close("username = ?", (str(username),), "done", by, _now())
