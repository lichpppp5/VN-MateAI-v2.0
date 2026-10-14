# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/enrollment.py
====================================
Secret ghi danh (enrollment) cho worker agent và thiết bị ESP32 / Xiaozhi.

Hai lớp tin cậy khác nhau nên hai secret riêng: worker được nhận lệnh thực thi
skill (nguy hiểm hơn nhiều), thiết bị chỉ stream âm thanh. Mỗi secret tự sinh ở
lần chạy đầu, lưu trong `certs/` (đã git-ignore), quyền 0600.

Gọi qua module (`enrollment.get_device_enrollment_secret()`) để test thay được
ở một chỗ cho mọi nơi dùng.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

WORKER_SECRET_FILE = Path(settings.PROJECT_ROOT) / "certs" / "worker_secret.key"
DEVICE_SECRET_FILE = Path(settings.PROJECT_ROOT) / "certs" / "device_secret.key"


def _load_or_create_secret(path: Path, label: str) -> str:
    """Đọc secret trong `path`; chưa có thì sinh mới. Lỗi → "" (không ai khớp được)."""
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        path.parent.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_urlsafe(32)
        path.write_text(new_secret, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("Đã sinh %s enrollment secret mới tại %s", label, path)
        return new_secret
    except Exception as exc:
        logger.error("Không thể tạo/đọc %s enrollment secret: %s", label, exc)
        return ""


def get_worker_enrollment_secret() -> str:
    """
    Secret đăng ký LAN worker agent vào /ws/client.

    Worker là tiến trình headless không có tài khoản người dùng, nên không thể đăng
    nhập bằng JWT. Mỗi bản agent được phát secret này khi tải về từ
    /api/v1/download-agent.
    """
    return _load_or_create_secret(WORKER_SECRET_FILE, "worker")


def get_device_enrollment_secret() -> str:
    """Secret để thiết bị ESP32 / Xiaozhi đăng ký vào /api/v1/xiaozhi/ws và /ws/audio-stream."""
    return _load_or_create_secret(DEVICE_SECRET_FILE, "device")
