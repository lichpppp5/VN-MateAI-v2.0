# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/mfa.py
==================================
Xác thực hai lớp (MFA) bằng TOTP (RFC 6238 / RFC 4226: HMAC-SHA1, bước 30 s, 6 chữ số) — dùng được với Google Authenticator,
Microsoft Authenticator, Authy, 1Password… Tự cài bằng thư viện chuẩn (hmac, hashlib) nên không thêm phụ thuộc.

An toàn:
  - mã bí mật mã hoá khi lưu (secret_box) và KHÔNG bao giờ trả lại sau lần cài đặt đầu;
  - chấp nhận lệch ±1 bước (đồng hồ lệch ~30 s); mỗi bước chỉ dùng MỘT lần (chống phát lại, `mfa_last_step`);
  - so sánh hằng thời gian; 10 mã khôi phục dùng một lần, lưu dạng băm;
  - bật MFA chỉ khi người dùng chứng minh đã cài đúng (nhập một mã hợp lệ).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

STEP = 30
DIGITS = 6
ISSUER = "VN-MateAI"
RECOVERY_COUNT = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # không có 0 O 1 I để khỏi đọc nhầm


def new_secret() -> str:
    """20 byte ngẫu nhiên (160 bit) dạng base32 không đệm."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _now() -> float:
    return time.time()


def totp(secret: str, at: Optional[float] = None, step: int = STEP, digits: int = DIGITS) -> str:
    return hotp(secret, int((_now() if at is None else at) // step), digits)


def verify(secret: str, code: str, *, at: Optional[float] = None, window: int = 1, last_step: Optional[int] = None) -> Optional[int]:
    """Trả BƯỚC thời gian khớp (để ghi lại chống phát lại) hoặc None. Bước phải lớn hơn `last_step`."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now_step = int((_now() if at is None else at) // STEP)
    hit: Optional[int] = None
    for delta in range(-window, window + 1):               # luôn duyệt hết các bước để thời gian xử lý không lộ bước nào khớp
        step = now_step + delta
        if hmac.compare_digest(hotp(secret, step), code) and hit is None and (last_step is None or step > last_step):
            hit = step
    return hit


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def group(secret: str) -> str:
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ── mã khôi phục ────────────────────────────────────────────────────────────

def new_recovery_codes(n: int = RECOVERY_COUNT) -> List[str]:
    return ["".join(secrets.choice(_ALPHABET) for _ in range(5)) + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(n)]


def _norm(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def hash_recovery(code: str) -> str:
    return hashlib.sha256(("vnmateai-recovery:" + _norm(code)).encode("utf-8")).hexdigest()


def consume_recovery(stored_json: Optional[str], code: str) -> Tuple[bool, Optional[str]]:
    """(đúng?, danh sách băm còn lại dạng JSON). Mã đúng bị gỡ khỏi danh sách (dùng một lần)."""
    try:
        hashes: List[str] = json.loads(stored_json or "[]")
    except (TypeError, ValueError):
        hashes = []
    if len(_norm(code)) != 10:
        return False, stored_json
    probe = hash_recovery(code)
    found = None
    for h in hashes:
        if hmac.compare_digest(h, probe):
            found = h
    if found is None:
        return False, stored_json
    hashes.remove(found)
    return True, json.dumps(hashes)


# ── mã hoá bí mật khi lưu ───────────────────────────────────────────────────

def seal(secret: str) -> str:
    from mateai.config import secret_box
    return secret_box.encrypt_value(secret) if secret_box.enabled() else secret


def unseal(stored: Optional[str]) -> str:
    from mateai.config import secret_box
    return secret_box.decrypt_value(stored) if stored else ""


# ── nghiệp vụ trên tài khoản ────────────────────────────────────────────────

class MfaError(ValueError):
    pass


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def status(username: str) -> Dict[str, Any]:
    user = _db().get_user_by_username_or_id(username)
    if not user:
        raise MfaError("Không tìm thấy tài khoản")
    sec = _db().user_security_get(user["id"])
    try:
        left = len(json.loads(sec.get("mfa_recovery") or "[]"))
    except ValueError:
        left = 0
    return {"enabled": bool(sec.get("mfa_enabled")), "pending_setup": bool(sec.get("mfa_secret")) and not sec.get("mfa_enabled"),
            "recovery_codes_left": left if sec.get("mfa_enabled") else None, "auth_source": sec.get("auth_source") or "local"}


def begin_setup(username: str) -> Dict[str, Any]:
    """Sinh mã bí mật MỚI (chưa bật). Gọi lại sẽ thay mã chưa xác nhận. Đã bật rồi thì phải tắt trước."""
    user = _db().get_user_by_username_or_id(username)
    if not user:
        raise MfaError("Không tìm thấy tài khoản")
    sec = _db().user_security_get(user["id"])
    if sec.get("auth_source") == "sso":
        raise MfaError("Tài khoản đăng nhập qua SSO dùng MFA của hệ thống định danh (IdP), không cài ở đây")
    if sec.get("mfa_enabled"):
        raise MfaError("MFA đã bật — hãy tắt trước khi cài lại")
    secret = new_secret()
    _db().user_security_set(user["id"], mfa_secret=seal(secret), mfa_enabled=0, mfa_recovery=None, mfa_last_step=None)
    return {"secret": secret, "secret_grouped": group(secret), "otpauth_uri": otpauth_uri(secret, user["username"])}


def confirm_enable(username: str, code: str) -> List[str]:
    """Xác nhận mã đầu tiên -> bật MFA và trả 10 mã khôi phục (CHỈ hiện lần này)."""
    user = _db().get_user_by_username_or_id(username)
    if not user:
        raise MfaError("Không tìm thấy tài khoản")
    sec = _db().user_security_get(user["id"])
    if sec.get("mfa_enabled"):
        raise MfaError("MFA đã bật")
    secret = unseal(sec.get("mfa_secret"))
    if not secret:
        raise MfaError("Chưa bắt đầu cài MFA — hãy tạo mã bí mật trước")
    step = verify(secret, code)
    if step is None:
        raise MfaError("Mã không đúng hoặc đã hết hạn")
    codes = new_recovery_codes()
    _db().user_security_set(user["id"], mfa_enabled=1, mfa_recovery=json.dumps([hash_recovery(c) for c in codes]), mfa_last_step=step)
    return codes


def check_login_code(username: str, code: str) -> bool:
    """Kiểm mã TOTP (một lần cho mỗi bước) hoặc mã khôi phục. Đúng thì ghi nhận đã dùng."""
    user = _db().get_user_by_username_or_id(username)
    if not user:
        return False
    sec = _db().user_security_get(user["id"])
    if not sec.get("mfa_enabled"):
        return False
    secret = unseal(sec.get("mfa_secret"))
    step = verify(secret, code, last_step=sec.get("mfa_last_step")) if secret else None
    if step is not None:
        _db().user_security_set(user["id"], mfa_last_step=step)
        return True
    ok, remaining = consume_recovery(sec.get("mfa_recovery"), code)
    if ok:
        _db().user_security_set(user["id"], mfa_recovery=remaining)
    return ok


def disable(username: str) -> None:
    user = _db().get_user_by_username_or_id(username)
    if not user:
        raise MfaError("Không tìm thấy tài khoản")
    _db().user_security_set(user["id"], mfa_secret=None, mfa_enabled=0, mfa_recovery=None, mfa_last_step=None)
