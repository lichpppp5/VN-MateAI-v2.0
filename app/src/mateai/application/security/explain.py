# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/explain.py
======================================
Giải thích phiếu phê duyệt (đạo đức AI, điểm 2 — 2026-10-12): người duyệt phải biết AI muốn LÀM GÌ, theo YÊU CẦU NÀO của AI
(ai), và VÌ SAO hệ thống cần người duyệt — trước đây phiếu chỉ có tên công cụ + câu lệnh gốc.

Dùng ở MỘT chỗ (tool_gate khi sinh phiếu) → hiện ở HUD, portal, Telegram, nhật ký kiểm toán như nhau. Tham số hiển thị qua
`pii_guard` (người duyệt không nhất thiết được xem dữ liệu cá nhân trong tham số).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

#: Ý nghĩa mức rủi ro — câu người duyệt đọc được, không phải "Level 4/5".
RISK_MEANING = {
    1: "chỉ đọc dữ liệu",
    2: "thao tác thường ngày",
    3: "thay đổi dữ liệu / hệ thống — cần người có thẩm quyền xác nhận",
    4: "thay đổi quan trọng hoặc cài mã mới vào hệ thống — cần người có thẩm quyền xác nhận",
    5: "có thể gây mất dữ liệu / không đảo ngược được",
}


def _tool_summary(action_name: str) -> str:
    """Câu đầu trong mô tả công cụ (đã đăng ký) — nói AI định làm gì bằng lời."""
    try:
        from core.plugin_manager import plugin_manager
        entry = (plugin_manager._registry or {}).get(action_name) or {}
        desc = str((entry.get("meta") or {}).get("description") or "").strip()
    except Exception:  # noqa: BLE001
        desc = ""
    if not desc:
        return f"chạy công cụ '{action_name}'"
    first = desc.split(". ")[0].split("\n")[0].strip().rstrip(".")
    return first[:160]


def _params_summary(params: Dict[str, Any], limit: int = 4) -> str:
    from mateai.application.security.pii_guard import mask_obj
    shown, _ = mask_obj(dict(params or {}))
    parts = []
    for k, v in list(shown.items())[:limit]:
        text = v if isinstance(v, str) else str(v)
        parts.append(f"{k}={text[:60]}{'…' if len(text) > 60 else ''}")
    more = len(shown) - limit
    return ", ".join(parts) + (f" (+{more} tham số)" if more > 0 else "")


def explain_approval(action_name: str, params: Optional[Dict[str, Any]], risk: Optional[int],
                     reasons: Iterable[str] = (), query: str = "", requested_by: str = "",
                     target: str = "master") -> Dict[str, Any]:
    """Giải thích có cấu trúc + bản chữ nhiều dòng cho HUD / Telegram / portal."""
    from mateai.application.security.hr_guard import is_people_decision
    level = int(risk or 0)
    why = [f"Mức rủi ro {level}/5: {RISK_MEANING.get(level, 'cần xác nhận')}."]
    if is_people_decision(action_name):
        why.append("Đây là quyết định về CON NGƯỜI — AI không tự quyết, mỗi lần phải được duyệt riêng.")
    why += [str(r) for r in reasons if r and "rủi ro" not in str(r)][:2]
    action = _tool_summary(action_name)
    where = "" if str(target or "master").lower() in ("master", "local", "localhost", "") else f" trên máy {target}"
    params_text = _params_summary(params or {})
    out = {
        "action": f"{action}{where}",
        "tool": action_name,
        "params": params_text,
        "request": (query or "").strip()[:200],
        "requested_by": requested_by or "?",
        "why": why,
    }
    lines = [f"AI muốn: {out['action']}" + (f" ({params_text})" if params_text else "")]
    if out["request"]:
        lines.append(f"Theo yêu cầu của {out['requested_by']}: “{out['request']}”")
    else:
        lines.append(f"Người yêu cầu: {out['requested_by']}")
    lines.append("Vì sao cần duyệt: " + " ".join(why))
    out["text"] = "\n".join(lines)
    return out
