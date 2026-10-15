# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/config/edition.py
========================
Phiên bản sản phẩm — MỘT nơi quyết định khác biệt giữa bản nội bộ và bản thương mại (thẩm định thương mại 14/10/2026, P0-2/3/4).

  internal   (mặc định) : máy của chủ dự án — giữ nguyên các quyết định vận hành đã chốt (AI tự tạo kỹ năng, auto_execute…).
  commercial            : bản giao cho khách hàng — các mục dưới đây BẬT cứng, cấu hình không nới được:
      - AI không tự sinh / cài mã: `create_new_skill`, `install_skill_from_url`, `reload_all_skills` bị chặn ở cổng chính
        sách (mọi người gọi) và ẩn khỏi danh mục; `auto_execute` luôn tắt;
      - ẩn kỹ năng demo / dữ liệu mô phỏng / cá nhân (`DEMO_SKILLS`) — chỉ bật lại bằng `edition_allow_skills`;
      - không dùng model miễn phí (tên chứa `:free`) — thường không cam kết không huấn luyện trên dữ liệu khách hàng;
      - che dữ liệu cá nhân trong kết quả công cụ TRƯỚC khi gửi lên LLM bên ngoài, kể cả với admin
        (`privacy.external_llm_mask_pii`, mặc định bật ở bản này);
      - tài liệu API (/docs, /redoc, /openapi.json) tắt.

Chọn: `"edition": "commercial"` trong config.json, hoặc biến môi trường `VNMATEAI_EDITION` (ưu tiên — bộ cài Docker đặt).
"""
from __future__ import annotations

import os
from typing import FrozenSet, Iterable

INTERNAL = "internal"
COMMERCIAL = "commercial"

#: AI sinh / cài / nạp lại mã chạy trong tiến trình lõi.
CODEGEN_TOOLS: FrozenSet[str] = frozenset({"create_new_skill", "install_skill_from_url", "reload_all_skills"})

#: Kỹ năng demo, dữ liệu mô phỏng hoặc cá nhân — không thuộc sản phẩm vận hành CNTT (thẩm định §9 "Bỏ").
DEMO_SKILLS: FrozenSet[str] = frozenset({
    # dữ liệu mô phỏng / số giả
    "simulate_incoming_customer_email", "check_cashflow_predictive_health", "get_financial_summary",
    "record_income", "record_expense", "get_executive_leaderboard", "record_attendance_skill", "get_attendance_report",
    # cá nhân / giải trí
    "open_youtube", "auto_play_music", "media_control", "auto_get_hanoi_weather", "get_chrome_tabs_info",
    "append_genealogy_record", "open_text_file_notepad",
    # robot ESP32 và Dev Fleet: bỏ khỏi v1 (§9)
    "animate_robot", "move_robot",
    "create_dev_fleet_task", "cancel_dev_fleet_task", "get_dev_fleet_briefing", "get_dev_fleet_status",
    "get_dev_fleet_task", "list_dev_fleet_workers",
    # điều khiển GUI tự trị trên worker riêng — chưa đủ kiểm soát cho bản bán
    "tool_execute_gui_task", "capture_worker_screen", "get_computer_use_status",
})


#: Kỹ năng chạy lệnh / sửa tệp / điều khiển tiến trình — trên MÁY CHỦ chúng chạy cùng quyền tiến trình lõi (thẩm định §8
#: mức Cao). Bản thương mại: chỉ chạy trên máy trạm qua Agent (target_client), không chạy trong tiến trình lõi.
HOST_EXEC_TOOLS: FrozenSet[str] = frozenset({
    "run_powershell_command", "delete_item", "write_file", "kill_process", "run_local_sql_check",
    "manage_windows_service", "open_application", "set_clipboard", "get_clipboard", "set_system_volume",
    "capture_screen_base64", "search_files", "read_file", "list_directory",
})


def current() -> str:
    env = os.environ.get("VNMATEAI_EDITION", "").strip().lower()
    if env:
        return COMMERCIAL if env == COMMERCIAL else INTERNAL
    try:
        from mateai.config.loader import settings
        value = str(getattr(settings, "edition", "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        value = ""
    return COMMERCIAL if value == COMMERCIAL else INTERNAL


def is_commercial() -> bool:
    return current() == COMMERCIAL


def _allowed_override() -> FrozenSet[str]:
    try:
        from mateai.config.loader import settings
        return frozenset(str(s) for s in (getattr(settings, "edition_allow_skills", None) or []))
    except Exception:  # noqa: BLE001
        return frozenset()


def blocked_reason(tool: str) -> str:
    """Lý do tool bị chặn ở phiên bản hiện tại ("" = được dùng)."""
    if not is_commercial():
        return ""
    name = str(tool or "").strip()
    if name in CODEGEN_TOOLS:
        return f"Bản thương mại không cho AI sinh hay cài mã ('{name}'). Kỹ năng mới cài qua bản phát hành đã kiểm duyệt."
    if name in DEMO_SKILLS and name not in _allowed_override():
        return f"Kỹ năng '{name}' là demo / cá nhân, không có trong bản thương mại."
    return ""


def server_exec_blocked_reason(tool: str) -> str:
    """Lý do không chạy `tool` NGAY TRÊN MÁY CHỦ ở phiên bản này ("" = được)."""
    if is_commercial() and str(tool or "").strip() in HOST_EXEC_TOOLS:
        return (f"Bản thương mại không chạy '{tool}' trên máy chủ (cùng quyền tiến trình lõi). "
                "Chọn máy trạm đang online (list_online_workstations rồi gọi lại với target_client).")
    return ""


def visible_tools(tools: Iterable[dict]) -> list:
    """Danh mục công cụ cho LLM / giao diện — bỏ tool bị chặn ở phiên bản này."""
    out = []
    for t in tools:
        name = ((t or {}).get("function") or {}).get("name") or (t or {}).get("name")
        if not blocked_reason(str(name or "")):
            out.append(t)
    return out


def auto_execute_allowed() -> bool:
    return not is_commercial()


def model_allowed(model: str) -> bool:
    """Bản thương mại không gửi dữ liệu tới model miễn phí (`…:free`)."""
    return not (is_commercial() and ":free" in str(model or "").lower())


def mask_pii_for_llm() -> bool:
    """Che dữ liệu cá nhân trước khi gửi LLM bên ngoài, bất kể vai trò. Bản thương mại: luôn bật; nội bộ: theo
    `privacy.external_llm_mask_pii` (mặc định tắt — giữ hành vi đã có)."""
    if is_commercial():
        return True
    try:
        from mateai.config.loader import get_config_section
        return bool(get_config_section("privacy").get("external_llm_mask_pii", False))
    except Exception:  # noqa: BLE001
        return False


def api_docs_enabled() -> bool:
    """/docs, /redoc, /openapi.json: tắt mặc định ở mọi phiên bản (lộ toàn bộ API); bản nội bộ bật bằng
    `security.public_api_docs: true` hoặc VNMATEAI_API_DOCS=1. Bản thương mại: luôn tắt."""
    if is_commercial():
        return False
    if os.environ.get("VNMATEAI_API_DOCS", "").strip() in ("1", "true", "yes"):
        return True
    try:
        from mateai.config.loader import get_config_section
        return bool(get_config_section("security").get("public_api_docs", False))
    except Exception:  # noqa: BLE001
        return False
