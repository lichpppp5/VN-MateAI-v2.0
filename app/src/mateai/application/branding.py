# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Thương hiệu hiển thị của portal (white-label — thẩm định thương mại, mục giao diện).

Doanh nghiệp đổi tên sản phẩm, dòng mô tả, logo, màu chủ đạo trong Cấu hình; màn hình đăng nhập đọc được trước khi đăng
nhập nên GET là công khai (chỉ các trường dưới đây). Dòng bản quyền "VN-MateAI © Dương Thanh Lịch" KHÔNG đổi theo
(LICENSE / NOTICE). Logo chỉ nhận ảnh data URL (PNG / JPEG / WebP / SVG) ≤ ~150 KB, hiển thị qua <img> — SVG trong <img>
không chạy script. Lưu ở mục `branding` của config.json.
"""
from __future__ import annotations

import re
from typing import Any, Dict

DEFAULTS: Dict[str, str] = {
    "product_name": "VN-MateAI",
    "tagline": "Hệ Thống Trí Tuệ AI & Quản Trị Doanh Nghiệp",
    "primary_color": "",
    "logo_data_url": "",
}
MAX_NAME, MAX_TAGLINE, MAX_LOGO = 40, 90, 200_000
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO = re.compile(r"^data:image/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")


def _clean_text(v: Any, limit: int, field: str) -> str:
    s = str(v or "").strip()
    if _CTRL.search(s):
        raise ValueError(f"{field}: không được chứa ký tự điều khiển hay dấu < >.")
    if len(s) > limit:
        raise ValueError(f"{field}: tối đa {limit} ký tự.")
    return s


def _valid(section: Dict[str, Any]) -> Dict[str, str]:
    """Bỏ qua giá trị hỏng trong config (sửa tay) — trả mặc định thay vì để giao diện vỡ."""
    out = dict(DEFAULTS)
    for key, limit in (("product_name", MAX_NAME), ("tagline", MAX_TAGLINE)):
        try:
            v = _clean_text(section.get(key), limit, key)
            if v:
                out[key] = v
        except ValueError:
            pass
    c = str(section.get("primary_color") or "")
    out["primary_color"] = c if _COLOR.match(c) else ""
    logo = str(section.get("logo_data_url") or "")
    out["logo_data_url"] = logo if len(logo) <= MAX_LOGO and _LOGO.match(logo) else ""
    return out


def get() -> Dict[str, Any]:
    from mateai.config.loader import get_config_section
    data = _valid(get_config_section("branding") or {})
    data["customized"] = any(data[k] != DEFAULTS[k] for k in DEFAULTS)
    return data


def update(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Kiểm từng trường; chuỗi rỗng = về mặc định. Sai → ValueError (thông báo tiếng Việt)."""
    from mateai.config.loader import update_config_section
    clean: Dict[str, str] = {}
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Trường không hỗ trợ: {', '.join(sorted(unknown))}.")
    if "product_name" in changes:
        clean["product_name"] = _clean_text(changes["product_name"], MAX_NAME, "Tên sản phẩm")
    if "tagline" in changes:
        clean["tagline"] = _clean_text(changes["tagline"], MAX_TAGLINE, "Dòng mô tả")
    if "primary_color" in changes:
        c = str(changes["primary_color"] or "").strip()
        if c and not _COLOR.match(c):
            raise ValueError("Màu chủ đạo phải dạng #RRGGBB.")
        clean["primary_color"] = c.lower()
    if "logo_data_url" in changes:
        logo = str(changes["logo_data_url"] or "").strip()
        if logo and len(logo) > MAX_LOGO:
            raise ValueError("Logo quá lớn (tối đa khoảng 150 KB).")
        if logo and not _LOGO.match(logo):
            raise ValueError("Logo phải là ảnh PNG, JPEG, WebP hoặc SVG.")
        clean["logo_data_url"] = logo
    update_config_section("branding", clean)
    return get()
