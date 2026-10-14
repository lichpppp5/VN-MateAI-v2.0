# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devices/worker_enrollment.py
===============================================
Mã đăng ký RIÊNG TỪNG MÁY TRẠM (yêu cầu 2026-10-05).

Trước đây mọi Agent dùng chung một enrollment secret: lộ một gói tải về là ai
cũng nối được làm máy trạm, và muốn chặn một máy phải đổi secret cho TẤT CẢ.

Luồng mới:
  1. Mỗi lần tải gói Agent (hoặc admin bấm "Tạo mã"), máy chủ sinh một MÃ ĐĂNG KÝ
     dùng MỘT LẦN, hết hạn sau `ttl_days` ngày, nhúng vào config.json của gói.
  2. Lần chạy đầu, Agent đổi mã lấy KHOÁ THIẾT BỊ riêng (POST /api/v1/agent/enroll).
     Mã bị đánh dấu đã dùng — dùng lại / chép sang máy khác đều bị từ chối.
  3. Các lần sau Agent nối /ws/client bằng khoá thiết bị; máy chủ gán đúng
     client_id của máy đó (Agent không tự xưng tên máy khác được).
  4. Thu hồi một máy: chỉ khoá của máy đó hết hiệu lực, kết nối đang mở bị cắt.

Máy chủ chỉ lưu SHA-256 của mã và khoá.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mateai.infrastructure.database.db_manager import db_manager

CODE_TTL_DAYS = 7
_CLIENT_ID_RE = re.compile(r"[^a-z0-9-]+")


class EnrollError(Exception):
    """Mã đăng ký sai / hết hạn / đã dùng."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_enroll_code(created_by: str, label: str = "", ttl_days: int = CODE_TTL_DAYS) -> Tuple[str, Dict[str, Any]]:
    """Sinh mã dùng một lần. Trả (mã — chỉ hiện MỘT lần, thông tin lưu)."""
    code = "VNM-" + secrets.token_urlsafe(24)
    expires = (datetime.utcnow() + timedelta(days=max(1, min(int(ttl_days), 90)))).isoformat()
    db_manager.add_worker_enroll_code(_sha256(code), (label or "")[:80], created_by, expires)
    return code, {"code_ref": _sha256(code)[:12], "label": label, "expires_at": expires}


def _new_client_id(hostname: str) -> str:
    base = _CLIENT_ID_RE.sub("-", (hostname or "may-tram").lower()).strip("-")[:40] or "may-tram"
    candidate = base
    while db_manager.worker_client_id_exists(candidate):
        candidate = f"{base}-{secrets.token_hex(2)}"
    return candidate


def enroll(code: str, hostname: str, platform: str = "", package: str = "", agent_version: str = "") -> Dict[str, str]:
    """Đổi mã đăng ký lấy khoá thiết bị. Trả {client_id, device_token} — khoá chỉ trả MỘT lần."""
    code = (code or "").strip()
    if not code.startswith("VNM-") or len(code) > 128:
        raise EnrollError("Mã đăng ký không hợp lệ.")
    client_id = _new_client_id(hostname)
    used = db_manager.consume_worker_enroll_code(_sha256(code), client_id)
    if used is None:
        raise EnrollError("Mã đăng ký sai, đã hết hạn hoặc đã được dùng cho máy khác.")
    token = secrets.token_urlsafe(32)
    db_manager.add_worker_device(
        client_id, _sha256(token), used.get("label") or "", (hostname or "")[:120], (platform or "")[:120],
        (package or "")[:40], (agent_version or "")[:20], used.get("created_by") or "")
    return {"client_id": client_id, "device_token": token}


def authenticate_device_token(token: str) -> Optional[Dict[str, Any]]:
    """Máy trạm còn hiệu lực có khoá này (None nếu sai / đã thu hồi)."""
    if not token or len(token) > 256:
        return None
    return db_manager.get_worker_device_by_token(_sha256(token))


def touch(client_id: str, agent_version: Optional[str] = None, package: Optional[str] = None) -> None:
    db_manager.touch_worker_device(client_id, agent_version, package)


def list_devices() -> List[Dict[str, Any]]:
    return db_manager.list_worker_devices()


def list_codes() -> List[Dict[str, Any]]:
    now = datetime.utcnow().isoformat()
    out = []
    for c in db_manager.list_worker_enroll_codes():
        c["state"] = "used" if c.get("used_at") else ("expired" if c["expires_at"] <= now else "waiting")
        out.append(c)
    return out


def delete_code(code_ref: str) -> bool:
    return db_manager.delete_worker_enroll_code(code_ref) > 0


def revoke(client_id: str, revoked_by: str) -> bool:
    return db_manager.revoke_worker_device(client_id, revoked_by)
