# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/risk_engine.py
==========================================
Risk Engine chuẩn (prompt Supervisor §15, `docs/autonomy/risk-model.md`).

Một bản duy nhất: bảng rủi ro theo tên tool + heuristic theo từ trong tên + khai
báo của nơi đăng ký tool (chỉ nâng) + luật tham số + danh sách "phải duyệt" trong
cấu hình (`security.require_confirmation_actions`, trước đây KHÔNG được cổng tool
áp dụng). Chuyển nguyên từ `zero_trust.HumanInTheLoopManager.get_risk_level`
(hàm đó nay chỉ gọi sang đây).

Không dựa vào LLM: đầu vào là tên tool, tham số, khai báo registry và cấu hình.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Risk level mappings
RISK_LEVEL_MAP: Dict[str, int] = {
    # Level 1: Read-only & informational
    "list_directory": 1,
    "read_file": 1,
    "get_system_metrics": 1,
    "search_past_incidents": 1,
    "get_memory_stats": 1,
    "ping": 1,
    "read_excel_file": 1,
    "query_local_db": 1,
    "read_audit_logs": 1,
    "query_company_policy": 1,
    "query_enterprise_graph_rag": 1,
    "get_financial_summary": 1,
    "get_attendance_report": 1,
    "get_executive_leaderboard": 1,
    "get_executive_standup_briefing": 1,
    # Lõi điều phối: xem năng lực / phiếu là chỉ đọc; GIAO việc tự xin duyệt theo rủi ro của phiếu (orchestration/service.py).
    "list_agent_capabilities": 1,
    "get_work_order_status": 1,
    "dispatch_work_order": 2,

    # Quản lý kỹ năng: liệt kê chỉ đọc; nạp lại chỉ chạy mã đã có trên đĩa;
    # TẠO kỹ năng là cài mã Python mới vào tiến trình máy chủ (đã qua kiểm toán
    # AST) — cần duyệt khi lệnh không đến từ kênh tin cậy.
    "list_available_skills": 1,
    # Phím media / âm lượng trên máy chủ: không đọc / ghi dữ liệu.
    "media_control": 1,
    "reload_all_skills": 2,
    "create_new_skill": 4,
    # Tải mã kỹ năng từ URL rồi cài: mã từ bên ngoài, không qua MetaArchitect.
    "install_skill_from_url": 5,

    # Level 2: Routine operational creation
    "record_attendance_skill": 2,
    "assign_task_intelligently": 2,
    "run_proactive_task_audit": 2,
    "simulate_incoming_customer_email": 2,
    "check_cashflow_predictive_health": 2,

    # Level 3: Moderate modifications
    "record_income": 3,
    "write_excel_file": 3,
    "write_file": 3,
    # Cấp danh tính cho nhân viên mới (hồ sơ ERP + workspace + tài khoản AD
    # dự kiến). Briefing BƯỚC 5 xếp "Xóa user AD" vào nhóm 3-5, nên việc CẤP
    # tài khoản cũng phải ở Level 4 — trước đây để Level 3 nên chạy tự động.
    "zero_touch_onboard_employee": 4,
    # Điều phối đa tác nhân: hạ từ Level 3 xuống Level 2 ("thao tác
    # thường", cùng mức với assign_task_intelligently mà nó kích hoạt). Trước
    # đây MỌI câu hỏi Multi-Agent — kể cả chỉ đọc ("doanh thu tháng trước?"),
    # tra chính sách, chấm công — đều phải CEO duyệt, nên CEO nhận tin nhắn
    # mời duyệt liên tục cho việc không quan trọng. Các thao tác thật sự nguy
    # hiểm (record / delete / run_powershell...) vẫn giữ cổng HITL riêng ở
    # đúng điểm chạy của chúng.
    "delegate_to_multi_agent": 2,

    # Level 4: High risk system actions
    "record_expense": 4,
    "manage_windows_service": 4,
    "run_powershell_command": 4,
    "kill_process": 4,
    "deploy_skill": 4,
    "backup_vector_db": 4,
    "restore_vector_db": 4,

    # Level 5: Critical & destructive
    "delete_item": 5,
    "delete_records": 5,
    "drop_database": 5,
    "wipe_system": 5,
    "execute_financial_transfer": 5,
}


#: Ngưỡng bắt đầu phải có người duyệt (L3).
APPROVAL_THRESHOLD = 3

#: Động từ đầu tên tool cho biết chỉ đọc (khi tên không có trong bảng và không
#: chứa từ phá huỷ / sửa đổi — các luật đó xét trước).
_READ_ONLY_VERBS = {"get", "list", "read", "query", "search", "check", "lookup", "show", "count"}


def _config_requires_confirmation(name: str) -> bool:
    try:
        from mateai.config.loader import settings
        return name in {str(x).strip().lower() for x in
                        (getattr(settings.security, "require_confirmation_actions", None) or [])}
    except Exception:  # noqa: BLE001
        return False


def assess_risk(
    action_name: str,
    params: Optional[Dict[str, Any]] = None,
    declared_risk_level: Optional[int] = None,
) -> int:
    """
    Đánh giá Risk Level từ 1 đến 5 cho bất kỳ tác vụ nào.

    `declared_risk_level` là mức rủi ro do chính nơi ĐĂNG KÝ tác vụ công
    bố (vd `ToolDefinition.risk_level` của Plugin Registry). Giá trị đó
    được lấy theo phép `max()` — có thể nâng lên, KHÔNG bao giờ hạ xuống.

    Vì sao cần tham số này
    ---------------------
    Trước đây hàm chỉ tra bảng `RISK_LEVEL_MAP` + heuristic theo TÊN tác
    vụ, mặc định 2. `PluginRegistry` thì mang rủi ro riêng trên mỗi tool
    và dùng nó để quyết định "có vào nhánh HITL không", nhưng bên trong
    nhánh đó lại hỏi `requires_approval(tên_tool)` — một phán đoán hoàn toàn
    khác và không biết gì về khai báo của registry. Hệ quả: một tool khai
    `risk_level=5` nhưng tên không chứa từ khoá nguy hiểm ("check_*",
    "export_*") vẫn chạy thẳng, không ai duyệt. Rủi ro khai trong registry
    và rủi ro thực sự áp dụng phải là MỘT.
    """

    clean = str(action_name or "").strip().lower()

    # Đánh giá theo tên trước (bảng + heuristic), rồi áp khai báo của registry.
    computed = 2
    if clean in RISK_LEVEL_MAP:
        computed = RISK_LEVEL_MAP[clean]
    else:
        # So theo TỪ trong tên (tách bởi _ - . khoảng trắng), từ bắt đầu bằng
        # từ khoá. Trước đây so CHUỖI CON: "s-KILL-s" khớp "kill" nên mọi
        # tool có chữ "skill" (list_available_skills, create_new_skill, kỹ
        # năng AI tạo) thành Level 5 như kill_process; "de-SCRIPT-ion" khớp "script".
        words = [w for w in re.split(r"[^a-z0-9]+", clean) if w]

        def _has(*keys: str) -> bool:
            return any(w.startswith(k) for w in words for k in keys)

        if _has("delete", "remove", "drop", "wipe", "format", "kill", "transfer", "destroy"):
            computed = 5
        elif _has("modify", "update", "write", "exec", "script", "service", "admin"):
            computed = 4
        elif words and words[0] in _READ_ONLY_VERBS:
            # Tool chỉ đọc (get_*, list_*…) = L0: vẫn chạy khi bật kill switch (§96).
            computed = 1

    # Kiểm tra chi tiêu lớn: nếu record_expense có số tiền > 50,000,000 VND thì nâng lên Level 5
    if clean == "record_expense" and params:
        try:
            amt = float(params.get("amount", 0))
            if amt >= 50_000_000:
                computed = 5
        except Exception:
            pass

    # Danh sách "phải duyệt" do quản trị viên cấu hình trên Portal: tối thiểu L3.
    if _config_requires_confirmation(clean):
        computed = max(computed, APPROVAL_THRESHOLD)

    # Quyết định về CON NGƯỜI (tuyển / sa thải / kỷ luật / chấm điểm nhân viên / đổi lương…): AI không tự quyết — tối thiểu
    # L3, kể cả kỹ năng AI tự tạo sau này (nhận theo tên). Xem security/hr_guard.py.
    from mateai.application.security.hr_guard import is_people_decision
    if is_people_decision(str(action_name or "")):   # tên gốc: adjustSalary → adjust + salary
        computed = max(computed, APPROVAL_THRESHOLD)

    if declared_risk_level is not None:
        try:
            declared = int(declared_risk_level)
        except (TypeError, ValueError):
            return computed
        if not 1 <= declared <= 5:
            logger.warning(
                "[RiskEngine] risk_level khai sai (%r) cho '%s' — bỏ qua, dùng %d",
                declared_risk_level, action_name, computed,
            )
            return computed
        return max(computed, declared)

    return computed
