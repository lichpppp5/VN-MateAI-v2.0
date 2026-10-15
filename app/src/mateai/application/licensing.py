# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/licensing.py
===============================
Bản quyền sử dụng + đo lượng sử dụng (thẩm định thương mại 14/10/2026 #23). Chạy NGOẠI TUYẾN: tệp bản quyền ký Ed25519 bởi nhà
cung cấp (mateai.infrastructure.security.signing), máy khách hàng chỉ kiểm chữ ký bằng khoá công khai đi kèm gói cài.

Tệp `certs/license.json`:  {"license": {customer, license_id, edition, max_users, max_workstations, expires, issued_at},
                            "signature": "<base64 Ed25519 của JSON license dạng chuẩn>"}
Cấp: `python scripts/issue_license.py --customer "Công ty A" --users 50 --workstations 200 --expires 2027-12-31`.

Áp dụng:
  - bản `internal`: không giới hạn (máy chủ dự án);
  - bản `commercial` có bản quyền hợp lệ: theo hạn mức trong tệp;
  - bản `commercial` không có / sai chữ ký / hết hạn: hạn mức DÙNG THỬ (5 người dùng, 5 máy trạm) + cảnh báo.
Không bao giờ khoá đăng nhập hay xoá dữ liệu — chỉ chặn TẠO THÊM người dùng / đăng ký thêm máy trạm vượt hạn mức.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

TRIAL = {"max_users": 5, "max_workstations": 5}
LICENSE_REL = Path("certs") / "license.json"
_FIELDS = ("customer", "license_id", "edition", "max_users", "max_workstations", "expires", "issued_at")


def canonical(lic: Dict[str, Any]) -> bytes:
    """Bytes được ký: JSON sắp xếp khoá, không khoảng trắng — hai phía tính ra giống hệt nhau."""
    return json.dumps({k: lic.get(k) for k in _FIELDS}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def license_path() -> Path:
    from mateai.config.loader import settings
    return Path(settings.PROJECT_ROOT) / LICENSE_REL


def _load() -> Optional[Dict[str, Any]]:
    p = license_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"_broken": True}


def check(doc: Optional[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """Trạng thái của một tài liệu bản quyền: valid | expired | invalid | missing."""
    from mateai.infrastructure.security import signing
    if not doc:
        return {"state": "missing"}
    lic = doc.get("license") if isinstance(doc, dict) else None
    if not isinstance(lic, dict):
        return {"state": "invalid", "reason": "tệp bản quyền hỏng"}
    try:
        ok = signing.verify(canonical(lic), signing.unb64(doc.get("signature", "")))
    except ValueError:
        ok = False
    if not ok:
        return {"state": "invalid", "reason": "chữ ký không hợp lệ (tệp bị sửa hoặc không do nhà cung cấp cấp)"}
    today = today or date.today()
    try:
        expires = datetime.strptime(str(lic.get("expires")), "%Y-%m-%d").date()
    except ValueError:
        return {"state": "invalid", "reason": "ngày hết hạn sai định dạng"}
    out = {"license": {k: lic.get(k) for k in _FIELDS}, "days_left": (expires - today).days}
    out["state"] = "valid" if expires >= today else "expired"
    return out


def status() -> Dict[str, Any]:
    from mateai.config import edition
    if not edition.is_commercial():
        return {"edition": "internal", "state": "internal", "limits": None, "message": "Bản nội bộ — không giới hạn."}
    st = check(_load())
    if st["state"] == "valid":
        lic = st["license"]
        limits = {"max_users": int(lic.get("max_users") or 0), "max_workstations": int(lic.get("max_workstations") or 0)}
        msg = f"Bản quyền của {lic.get('customer')} — còn {st['days_left']} ngày."
        if st["days_left"] <= 30:
            msg += " Sắp hết hạn: liên hệ nhà cung cấp để gia hạn."
    else:
        limits = dict(TRIAL)
        reason = {"missing": "chưa có bản quyền", "expired": "bản quyền đã hết hạn",
                  "invalid": st.get("reason", "bản quyền không hợp lệ")}[st["state"]]
        msg = f"Dùng thử ({reason}): tối đa {TRIAL['max_users']} người dùng, {TRIAL['max_workstations']} máy trạm."
    return {"edition": "commercial", **st, "limits": limits, "message": msg}


def usage() -> Dict[str, Any]:
    """Đo lượng sử dụng hiện tại (đếm đúng thứ bị giới hạn + lượng dùng AI tháng này)."""
    from mateai.infrastructure.database import governance_repo
    month = datetime.now().strftime("%Y-%m")
    counts = governance_repo.usage_counts(month)
    st = status()
    return {"month": month, **counts, "limits": st.get("limits"), "state": st["state"], "message": st["message"]}


def ensure_can_add(kind: str) -> None:
    """Gọi TRƯỚC khi tạo người dùng (`users`) / cấp mã đăng ký máy trạm (`workstations`). Vượt hạn mức → ValueError."""
    st = status()
    limits = st.get("limits")
    if not limits:
        return
    u = usage()
    if kind == "users" and u["users"] >= limits["max_users"]:
        raise ValueError(f"Đã đạt hạn mức {limits['max_users']} người dùng — {st['message']}")
    if kind == "workstations" and u["workstations"] >= limits["max_workstations"]:
        raise ValueError(f"Đã đạt hạn mức {limits['max_workstations']} máy trạm — {st['message']}")


def install(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Lưu tệp bản quyền mới sau khi kiểm chữ ký (Portal tải lên)."""
    st = check(doc)
    if st["state"] not in ("valid", "expired"):
        raise ValueError(st.get("reason") or "bản quyền không hợp lệ")
    p = license_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return status()
