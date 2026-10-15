# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/auth_manager.py
====================
Hệ thống Xác Thực & Phân Quyền (Auth & RBAC) cho VN-MateAI Web Portal.

Quản lý danh sách người dùng, băm mật khẩu, sinh/giải mã JWT token,
và cung cấp các FastAPI Dependencies để bảo vệ REST API theo vai trò (RBAC).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
import jwt
from passlib.context import CryptContext

from mateai.infrastructure.database.db_manager import db_manager

logger = logging.getLogger("mateai.application.security.auth_manager")

# ---------------------------------------------------------------------------
# Cấu hình Token & Mật khẩu
# ---------------------------------------------------------------------------
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 giờ

# Thư mục gốc dự án (đúng cả bản đóng gói) — không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402

JWT_SECRET_FILE = Path(_settings.PROJECT_ROOT) / "certs" / "jwt_secret.key"


def _load_jwt_secret() -> str:
    """
    Nạp khóa ký JWT theo thứ tự ưu tiên:

      1. Biến môi trường ``VNMATEAI_JWT_SECRET`` (chuẩn cho production).
      2. File ``certs/jwt_secret.key`` — tự sinh ngẫu nhiên ở lần chạy đầu.
      3. Nếu không sinh/lưu được file, dừng khởi động (fail-closed).

    Lý do đổi: trước đây có fallback hardcode
    ``"vnmateai-enterprise-secret-key-2026-auth"`` được commit vào git. Ai đọc được
    mã nguồn cũng tự ký được token admin hợp lệ — phủ nhận hoàn toàn Zero-Trust.
    """
    env_secret = os.getenv("VNMATEAI_JWT_SECRET", "").strip()
    if env_secret:
        return env_secret

    try:
        if JWT_SECRET_FILE.exists():
            existing = JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        JWT_SECRET_FILE.write_text(generated, encoding="utf-8")
        try:
            os.chmod(JWT_SECRET_FILE, 0o600)
        except OSError:
            pass
        logger.warning(
            "Đã sinh khóa ký JWT mới tại %s. Đặt biến môi trường VNMATEAI_JWT_SECRET "
            "để quản lý tập trung trong production.",
            JWT_SECRET_FILE,
        )
        return generated
    except Exception as exc:
        raise RuntimeError(
            "Không thể nạp/sinh khóa ký JWT. Hãy đặt biến môi trường "
            "VNMATEAI_JWT_SECRET, hoặc đảm bảo thư mục 'certs/' ghi được. "
            f"Chi tiết: {exc}"
        ) from exc


JWT_SECRET_KEY = _load_jwt_secret()

pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")


# ---------------------------------------------------------------------------
# AuthManager Class
# ---------------------------------------------------------------------------
class AuthManager:
    """Quản trị danh tính, xác thực và lưu trữ người dùng."""

    """Tài khoản nằm DUY NHẤT trong bảng users của SQLite (mateai.infrastructure.database.db_manager)."""

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Kiểm tra mật khẩu nhập vào khớp với chuỗi băm hay không (hỗ trợ bcrypt và pbkdf2 legacy)."""
        try:
            if not hashed_password:
                return False
            if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
                pwd_bytes = plain_password.encode("utf-8")[:72]
                return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as exc:
            logger.error("Lỗi xác minh mật khẩu: %s", exc)
            return False

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Tạo chuỗi băm bcrypt an toàn cho mật khẩu."""
        pwd_bytes = password.encode("utf-8")[:72]
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Tìm người dùng theo tên đăng nhập hoặc ID (SQLite — kho duy nhất)."""
        return db_manager.get_user_by_username_or_id(username)

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Xác thực người dùng và mật khẩu."""
        user = self.get_user(username)
        if not user:
            return None
        h = user.get("password_hash") or user.get("hashed_password") or ""
        if not self.verify_password(password, h):
            return None
        return user

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Danh sách người dùng (KHÔNG kèm password hash)."""
        return db_manager.get_all_users()

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Mã hoá mật khẩu bằng bcrypt và lưu user mới. Lỗi nếu trùng username / mật khẩu không hợp lệ."""
        created = db_manager.create_user(data)
        logger.info("Đã tạo người dùng mới: %s (role: %s, id: %s)", created["username"], created["role"], created["id"])
        return created

    def update_user(self, user_id: str, data: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
        """Cập nhật họ tên / vai trò / phòng ban / cấp bảo mật. Trả bản ghi KHÔNG có hash.

        Phòng ban phải là phòng ban ERP đang có ("" = bỏ gán). Đổi vai trò, phòng ban hay cấp
        bảo mật là thay đổi quyền -> ghi audit (prompt cuối §149)."""
        dept = data.get("department")
        if dept:
            from mateai.infrastructure.database.erp_database import erp_db
            names = {str(d.get("name") or "").strip().lower() for d in erp_db.get_structure_tree()}
            if dept.strip().lower() not in names:
                raise ValueError(f"Phòng ban '{dept}' không có trong cơ cấu tổ chức ERP.")
        before = db_manager.public_user(db_manager.get_user_by_username_or_id(user_id))
        updated = db_manager.update_user(user_id, data)
        logger.info("Đã cập nhật người dùng: %s (id: %s)", updated.get("username"), user_id)
        changed = {k: {"before": before.get(k), "after": updated.get(k)}
                   for k in ("role", "department", "clearance_level") if before.get(k) != updated.get(k)}
        if changed:
            try:
                from mateai.application.security.safety_guard import security_engine
                security_engine.log_audit(actor, "user_access_change", "USER", "SUCCESS",
                                          {"user": updated.get("username"), "changes": changed})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Không ghi được audit đổi quyền người dùng: %s", exc)
        return updated

    def delete_user(self, user_id: str) -> bool:
        """Xoá user theo user_id hoặc username."""
        db_manager.delete_user(user_id)
        logger.info("Đã xóa người dùng khỏi hệ thống: %s", user_id)
        return True

    def change_user_password(self, user_id: str, new_password: str, must_change: bool = False) -> bool:
        """Mã hoá và lưu mật khẩu mới (`must_change`: người dùng phải đổi lại ở lần đăng nhập tới)."""
        db_manager.change_user_password(user_id, new_password, must_change=must_change)
        logger.info("Đã đổi mật khẩu cho người dùng: %s", user_id)
        return True

    @staticmethod
    def password_problem(new_password: str, username: str = "", old_hash: str = "") -> str:
        """Chính sách mật khẩu khi người dùng TỰ đặt ("" = đạt): ≥ 10 ký tự, có chữ và số, khác tên đăng nhập, khác mật khẩu cũ,
        không phải mật khẩu dev công khai."""
        pwd = str(new_password or "")
        if len(pwd) < 10:
            return "Mật khẩu mới phải có ít nhất 10 ký tự."
        if not any(c.isalpha() for c in pwd) or not any(c.isdigit() for c in pwd):
            return "Mật khẩu mới phải có cả chữ và số."
        if username and username.strip().lower() in pwd.lower():
            return "Mật khẩu mới không được chứa tên đăng nhập."
        if pwd.lower() in {"admin123", "manager123", "viewer123", "password123", "1234567890", "qwerty12345"}:
            return "Mật khẩu này quá phổ biến — chọn mật khẩu khác."
        if old_hash and AuthManager.verify_password(pwd, old_hash):
            return "Mật khẩu mới phải khác mật khẩu hiện tại."
        return ""

    @staticmethod
    def create_access_token(
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Sinh JWT token có hạn sử dụng và chứa claim người dùng."""
        to_encode = data.copy()
        now = datetime.utcnow()
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire, "iat": now})
        return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    @staticmethod
    def decode_access_token(token: str, allow_scopes: Tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
        """Giải mã và xác thực tính hợp lệ của JWT token.

        Token CÓ claim `scope` (bước trung gian: `mfa` = đã đúng mật khẩu nhưng chưa nhập mã, `mfa_enroll` = chỉ được cài MFA)
        KHÔNG phải token truy cập: trả None trừ khi nơi gọi liệt kê đúng scope đó trong `allow_scopes`. Nhờ vậy mọi đường dùng
        cũ (middleware, dependency, WebSocket) tự từ chối chúng mà không phải sửa từng nơi."""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            if payload.get("scope") and payload.get("scope") not in allow_scopes:
                return None
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token đã hết hạn sử dụng.")
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT token không hợp lệ: %s", exc)
            return None


auth_manager = AuthManager()
