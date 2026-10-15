# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/config/secret_box.py
===========================
Mã hoá khoá bí mật NẰM TRONG config.json (API key 9Router / Groq / ElevenLabs,
token Telegram, mật khẩu SMTP, URL webhook…) — yêu cầu 2026-10-05.

Trước đây mọi khoá nằm dạng chữ thường trong config.json: ai đọc được file (bản
sao lưu, ổ đĩa mang đi sửa, lỡ commit) là có toàn bộ khoá.

- Fernet (AES-128-CBC + HMAC-SHA256, thư viện `cryptography`). Giá trị mã hoá có
  dạng "enc:v1:<token>"; giá trị chưa mã hoá vẫn đọc được (chuyển đổi dần).
- Khoá giải mã: biến môi trường VNMATEAI_CONFIG_KEY (khuyên dùng cho máy chủ
  thật — khoá không nằm trên đĩa), nếu không có thì certs/config_secret.key (tự
  sinh lần đầu, quyền 0600, thư mục certs/ không commit).
- Mã hoá / giải mã CHỈ ở cổng đọc-ghi config.json (mateai.config.loader, RULE-013):
  phần còn lại của hệ thống vẫn thấy khoá thật như cũ.
- Tắt: VNMATEAI_CONFIG_ENCRYPTION=off.

MẤT FILE KHOÁ = mất các khoá trong config.json (phải nhập lại). Sao lưu
certs/config_secret.key cùng với config.json.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"

#: Tên trường bí mật — MỘT nguồn cho cả mã hoá (đây) lẫn che khi trả về giao diện
#: (interfaces/http/secret_masking.py). So KHỚP CHÍNH XÁC tên viết thường: không dò
#: chuỗi con, vì `security.forbidden_keywords` chứa chữ "key" mà là chính sách.
SECRET_FIELD_NAMES = frozenset({
    "api_key", "api_keys", "apikey", "api_token", "access_key_id", "secret",
    "secret_access_key", "client_secret", "private_key", "token", "bot_token", "password", "metrics_token",
    # Khoá dịch vụ ở khối phẳng, tên viết HOA kiểu cũ.
    "groq_api_key", "direct_api_key",
    # Kênh cảnh báo: URL webhook Teams / Slack chứa chữ ký truy cập.
    "webhook_url", "hmac_secret",
    # Âm thanh
    "elevenlabs_api_key", "vbee_token",
    # Hạ tầng (prompt cuối §67): URL Redis chứa mật khẩu.
    "redis_url", "database_url",
    # OAuth (Zalo OA…): token truy cập / làm mới + khoá ứng dụng — 14/10/2026 (trước đó access_token của kênh Zalo lưu chữ thường).
    "access_token", "refresh_token", "app_secret", "secret_key",
})

_lock = threading.Lock()
_fernet: Any = None


def enabled() -> bool:
    return os.environ.get("VNMATEAI_CONFIG_ENCRYPTION", "").strip().lower() not in ("off", "0", "false")


def _key_file() -> Path:
    from mateai.config.loader import CONFIG_PATH
    return Path(CONFIG_PATH).resolve().parent / "certs" / "config_secret.key"


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        from cryptography.fernet import Fernet
        env_key = os.environ.get("VNMATEAI_CONFIG_KEY", "").strip()
        if env_key:
            _fernet = Fernet(env_key.encode())
            return _fernet
        kf = _key_file()
        if kf.exists() and kf.read_text(encoding="utf-8").strip():
            key = kf.read_text(encoding="utf-8").strip().encode()
        else:
            kf.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            kf.write_bytes(key)
            try:
                os.chmod(kf, 0o600)
            except OSError:
                pass
            logger.warning("Đã sinh khoá mã hoá cấu hình mới tại %s — HÃY SAO LƯU file này cùng config.json.", kf)
        _fernet = Fernet(key)
        return _fernet


def reset_cache() -> None:
    """Chỉ dùng trong test (đổi khoá / thư mục)."""
    global _fernet
    with _lock:
        _fernet = None


def is_secret(name: Any) -> bool:
    return str(name).lower() in SECRET_FIELD_NAMES


def encrypt_value(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    try:
        return _get_fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — sai khoá: coi như CHƯA cấu hình, không sập
        logger.error("Không giải mã được một khoá trong config.json (%s) — sai / mất "
                     "certs/config_secret.key? Khoá đó coi như trống, cần nhập lại.", type(exc).__name__)
        return ""


def _walk(obj: Any, fn, in_secret: bool = False) -> Any:
    if isinstance(obj, dict):
        return {k: _walk(v, fn, is_secret(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn, in_secret) for v in obj]
    if in_secret and isinstance(obj, str):
        return fn(obj)
    return obj


def encrypt_tree(cfg: Any) -> Any:
    """Bản sao với mọi trường bí mật đã mã hoá (để GHI ra đĩa)."""
    if not enabled():
        return cfg
    return _walk(cfg, encrypt_value)


def decrypt_tree(cfg: Any) -> Any:
    """Bản sao với mọi giá trị "enc:v1:" đã giải mã (sau khi ĐỌC từ đĩa). Luôn chạy,
    kể cả khi đã tắt mã hoá — để đọc được file đã mã hoá từ trước."""
    def has_cipher(o: Any) -> bool:
        if isinstance(o, dict):
            return any(has_cipher(v) for v in o.values())
        if isinstance(o, list):
            return any(has_cipher(v) for v in o)
        return isinstance(o, str) and o.startswith(PREFIX)
    if not has_cipher(cfg):
        return cfg
    return _walk(cfg, decrypt_value)


def has_plaintext_secrets(cfg: Any, _in_secret: bool = False) -> bool:
    if isinstance(cfg, dict):
        return any(has_plaintext_secrets(v, is_secret(k)) for k, v in cfg.items())
    if isinstance(cfg, list):
        return any(has_plaintext_secrets(v, _in_secret) for v in cfg)
    return bool(_in_secret and isinstance(cfg, str) and cfg and not cfg.startswith(PREFIX))


def key_fingerprint() -> Optional[str]:
    """8 ký tự đầu SHA-256 của khoá đang dùng (để đối chiếu bản sao lưu, không lộ khoá)."""
    import hashlib
    try:
        f = _get_fernet()
        raw = f._signing_key + f._encryption_key  # noqa: SLF001
        return hashlib.sha256(raw).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        return None


def _b64(x: bytes) -> str:  # pragma: no cover - tiện ích gỡ lỗi
    return base64.urlsafe_b64encode(x).decode()
