# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/pii_guard.py
========================================
Che DỮ LIỆU CÁ NHÂN trong kết quả công cụ theo quyền của người được phục vụ (2026-10-12).

Vì sao ở cổng công cụ (`tool_gate`): mọi dữ liệu doanh nghiệp tới AI qua kết quả công cụ. Che TRƯỚC khi AI đọc thì AI không
thể nói / hiện / gửi Telegram / ghi báo cáo thứ nó chưa từng thấy — một chỗ che, mọi kênh (HUD, robot đọc to, portal, Telegram).
Khác `safety_guard.mask_sensitive_data` (che bí mật KỸ THUẬT: IP nội bộ, mật khẩu, khoá API trước khi gửi lên LLM).

Ai xem đầy đủ (cấu hình `privacy` trong config.json, mặc định):
  - vai trò trong `pii_full_roles` (mặc định: admin);
  - hoặc người thuộc phòng ban trong `pii_full_departments` (Nhân sự / HR / HCNS) có cấp bảo mật ≥ `pii_min_clearance` (3).
  Thiết bị (robot đọc to trong phòng), agent ngoài, dịch vụ: LUÔN bản đã che, bất kể vai trò.
Che gì: theo TÊN TRƯỜNG (điện thoại, email, CCCD/CMND, hộ chiếu, tài khoản ngân hàng, lương / thu nhập, địa chỉ, ngày sinh,
mã số thuế cá nhân, BHXH) và theo MẪU trong văn bản (số điện thoại VN, CCCD 12 số, email; STK / lương / CMND khi có từ khoá).
Căn cứ: Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân — dữ liệu cá nhân chỉ hiện cho người cần biết.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

MASK = "***"

#: Từ (sau khi tách tên trường thành từ) cho biết trường chứa dữ liệu cá nhân.
_PII_TOKENS = {
    "phone": "phone", "mobile": "phone", "tel": "phone", "sdt": "phone", "dienthoai": "phone",
    "email": "email", "mail": "email",
    "cccd": "id", "cmnd": "id", "passport": "id", "hochieu": "id", "identity": "id", "nationalid": "id", "idnumber": "id",
    "stk": "bank", "iban": "bank", "bankaccount": "bank", "accountnumber": "bank", "sotaikhoan": "bank",
    "salary": "money", "luong": "money", "income": "money", "thunhap": "money", "wage": "money", "bonus": "money",
    "address": "address", "diachi": "address",
    "dob": "dob", "birthday": "dob", "birthdate": "dob", "ngaysinh": "dob", "dateofbirth": "dob",
    "taxcode": "id", "masothue": "id", "mst": "id", "bhxh": "id", "socialinsurance": "id",
}
#: Tên trường có các từ này KHÔNG phải dữ liệu cá nhân (địa chỉ IP / MAC / máy chủ, email hệ thống…).
_NOT_PII = {"ip", "mac", "server", "host", "url", "web", "smtp", "sender", "noreply", "system", "count", "total", "domain"}


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def pii_kind(key: Any) -> Optional[str]:
    """Loại dữ liệu cá nhân của một tên trường (None nếu không phải): phone, email, id, bank, money, address, dob."""
    raw = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key or ""))
    words = [w for w in re.split(r"[^a-z0-9]+", _fold(raw)) if w]
    if not words or _NOT_PII & set(words):
        return None
    joined = "".join(words)
    for i in range(len(words)):                    # ghép các từ liền nhau: so_dien_thoai → sodienthoai, bank_account …
        for j in range(i + 1, min(len(words), i + 4) + 1):
            kind = _PII_TOKENS.get("".join(words[i:j]))
            if kind:
                return kind
    return _PII_TOKENS.get(joined)


def _keep(digits: str, head: int, tail: int) -> str:
    d = re.sub(r"\D", "", digits)
    if len(d) <= head + tail:
        return MASK
    return d[:head] + "*" * (len(d) - head - tail) + d[-tail:]


def _mask_email(e: str) -> str:
    user, _, dom = e.partition("@")
    return (user[:1] + MASK + "@" + dom) if dom else MASK


def mask_value(kind: str, value: Any) -> Any:
    """Che một giá trị theo loại: giữ vài ký tự đầu / cuối để người dùng vẫn nhận ra (vd 0912***678)."""
    if value is None or value == "" or isinstance(value, bool):
        return value
    s = str(value)
    if kind == "phone":
        return _keep(s, 4, 3)
    if kind == "email":
        return _mask_email(s) if "@" in s else MASK
    if kind == "id":
        return _keep(s, 3, 3) if sum(ch.isdigit() for ch in s) >= 8 else MASK
    if kind == "bank":
        return "****" + re.sub(r"\D", "", s)[-4:] if sum(ch.isdigit() for ch in s) >= 6 else MASK
    if kind == "dob":
        m = re.search(r"(19|20)\d{2}", s)
        return f"**/**/{m.group(0)}" if m else MASK
    return MASK                                     # money, address


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)[35789](?:[ .]?\d){8}(?!\d)")
_CCCD = re.compile(r"(?<!\d)0\d{11}(?!\d)")
_CMND = re.compile(r"(?i)(cmnd|chứng minh(?: nhân dân)?)([^\d\n]{0,20})(\d{9})(?!\d)")
_BANK = re.compile(r"(?i)(stk|số tài khoản|tài khoản ngân hàng|account (?:number|no\.?))([:\s#]*)(\d[\d -]{5,22}\d)")
_MONEY = re.compile(r"(?i)(lương|mức lương|thu nhập|salary|thưởng)([^\d\n]{0,25}?)(\d[\d.,]*\s*(?:triệu|tr|nghìn|k|đ|vnđ|vnd|đồng|usd|\$)?)")


def mask_text(text: str) -> Tuple[str, int]:
    """Che dữ liệu cá nhân trong văn bản tự do. Trả (văn bản đã che, số chỗ đã che)."""
    if not text or not isinstance(text, str):
        return text, 0
    n = 0

    def sub(pattern, fn, s):
        nonlocal n

        def _f(m):
            nonlocal n
            n += 1
            return fn(m)
        return pattern.sub(_f, s)

    out = sub(_EMAIL, lambda m: _mask_email(m.group(0)), text)
    out = sub(_CMND, lambda m: m.group(1) + m.group(2) + _keep(m.group(3), 3, 3), out)
    out = sub(_BANK, lambda m: m.group(1) + m.group(2) + mask_value("bank", m.group(3)), out)
    out = sub(_MONEY, lambda m: m.group(1) + m.group(2) + MASK, out)
    out = sub(_CCCD, lambda m: _keep(m.group(0), 3, 3), out)
    out = sub(_PHONE, lambda m: _keep(m.group(0), 4, 3), out)
    return out, n


def mask_obj(obj: Any, _kind: Optional[str] = None) -> Tuple[Any, int]:
    """Che đệ quy dict / list / chuỗi (theo tên trường + mẫu văn bản). Trả (bản đã che, số chỗ đã che). Không sửa bản gốc."""
    if isinstance(obj, dict):
        out, n = {}, 0
        for k, v in obj.items():
            kind = pii_kind(k)
            if kind and not isinstance(v, (dict, list)):
                masked = mask_value(kind, v)
                n += masked != v
                out[k] = masked
            else:
                out[k], c = mask_obj(v, kind)
                n += c
        return out, n
    if isinstance(obj, list):
        out_l, n = [], 0
        for v in obj:
            if _kind and not isinstance(v, (dict, list)):         # danh sách số điện thoại / email …
                masked = mask_value(_kind, v)
                n += masked != v
                out_l.append(masked)
            else:
                m, c = mask_obj(v)
                n += c
                out_l.append(m)
        return out_l, n
    if isinstance(obj, str):
        return mask_text(obj)
    return obj, 0


# ── ai được xem đầy đủ ──────────────────────────────────────────────────────────────────────────────────────────────

_DEFAULTS = {"mask_pii": True, "pii_full_roles": ["admin"],
             "pii_full_departments": ["Nhân sự", "Hành chính nhân sự", "HCNS", "HR", "Human Resources"],
             "pii_min_clearance": 3}
#: Danh tính KHÔNG phải con người — luôn bản đã che (robot đọc to trong phòng, agent ngoài, dịch vụ).
_NON_PERSON_PREFIXES = ("device:", "agent:", "service:", "worker:", "robot")


def privacy_settings() -> Dict[str, Any]:
    try:
        from mateai.config.loader import get_config_section
        return {**_DEFAULTS, **(get_config_section("privacy") or {})}
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


def can_view_pii(caller: Any) -> bool:
    """Người này được xem dữ liệu cá nhân ĐẦY ĐỦ không. Fail-closed: không xác định được → không."""
    cfg = privacy_settings()
    if not cfg.get("mask_pii", True):
        return True
    who = str(caller or "").strip()
    if not who or who.lower().startswith(_NON_PERSON_PREFIXES):
        return False
    try:
        from mateai.application.security.security_guard import security_guard
        p = security_guard.principal(who)
    except Exception:  # noqa: BLE001
        return False
    if str(p.get("role") or "") in set(cfg.get("pii_full_roles") or []):
        return True
    dept = _fold(p.get("department") or "")
    hr = {_fold(d) for d in cfg.get("pii_full_departments") or []}
    return bool(dept) and dept in hr and int(p.get("clearance") or 0) >= int(cfg.get("pii_min_clearance", 3))


def guard_result(result: Any, caller: Any) -> Tuple[Any, int]:
    """Kết quả công cụ cho người gọi `caller`: nguyên bản nếu được xem đầy đủ, ngược lại bản đã che."""
    if can_view_pii(caller):
        return result, 0
    return mask_obj(result)
