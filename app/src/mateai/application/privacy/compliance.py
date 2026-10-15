# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/privacy/compliance.py
========================================
Tuân thủ dữ liệu cá nhân (thẩm định thương mại 14/10/2026 #24; Luật Bảo vệ dữ liệu cá nhân 2025, Nghị định 356/2025/NĐ-CP).

  - `REGISTER`: sổ hoạt động xử lý dữ liệu cá nhân của hệ thống (loại dữ liệu, mục đích, nơi lưu, thời hạn, bên nhận) — một
    nguồn cho API `/api/v1/privacy/register` và tài liệu docs/compliance/so-hoat-dong-xu-ly.md;
  - `lookup(subject)`: yêu cầu truy cập của chủ thể — mọi bản ghi lưu bền có tên đăng nhập / email / SĐT / họ tên đó;
  - `erase(subject, actor)`: yêu cầu xoá — ẩn danh hồ sơ nhân sự (giữ khoá để không vỡ liên kết), xoá trace thoại và lịch sử
    hội thoại có định danh đó, xoá tài khoản (trừ admin cuối cùng). `audit_logs` KHÔNG xoá: nhật ký bất biến có chuỗi băm
    là nghĩa vụ an toàn thông tin — kết quả ghi rõ để trả lời chủ thể. Mọi lần tra / xoá vào audit.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

ERASED = "[đã xoá theo yêu cầu]"

REGISTER: List[Dict[str, Any]] = [
    {"activity": "Tài khoản đăng nhập", "data": ["tên đăng nhập", "họ tên", "vai trò", "phòng ban", "băm mật khẩu", "MFA"],
     "subjects": "nhân viên dùng hệ thống", "purpose": "xác thực, phân quyền", "legal_basis": "thực hiện hợp đồng lao động / dịch vụ",
     "storage": "PostgreSQL vnmate.users", "retention": "đến khi xoá tài khoản", "recipients": "không chia sẻ"},
    {"activity": "Danh bạ nhân sự (đồng bộ AD / ERP)", "data": ["họ tên", "email", "SĐT", "chức danh", "phòng ban", "máy tính"],
     "subjects": "nhân viên", "purpose": "tra cứu nội bộ, giao việc, hỗ trợ IT", "legal_basis": "lợi ích hợp pháp của tổ chức",
     "storage": "PostgreSQL hr.employees, hr.computers, vnmate.employees", "retention": "theo đồng bộ AD; ẩn danh khi có yêu cầu xoá",
     "recipients": "LLM bên ngoài chỉ thấy bản ĐÃ CHE (SĐT, email, CCCD…) ở bản thương mại hoặc khi bật privacy.external_llm_mask_pii"},
    {"activity": "Hội thoại với trợ lý AI", "data": ["câu hỏi", "câu trả lời", "kết quả công cụ"],
     "subjects": "người dùng", "purpose": "trả lời yêu cầu", "legal_basis": "thực hiện yêu cầu của người dùng",
     "storage": "RAM (cửa sổ trượt theo phiên) — không lưu bền", "retention": "đến khi khởi động lại / hết phiên",
     "recipients": "nhà cung cấp LLM qua gateway (9Router / model tự host) — cần hợp đồng DPA"},
    {"activity": "Trace lượt thoại (đo hiệu năng)", "data": ["câu hỏi (rút gọn)", "kênh", "thời gian từng pha"],
     "subjects": "người dùng", "purpose": "đo chất lượng, SLA", "legal_basis": "lợi ích hợp pháp",
     "storage": "PostgreSQL vnmate.voice_traces", "retention": "observability.voice_trace_retention_days (mặc định 400 ngày)",
     "recipients": "không chia sẻ"},
    {"activity": "Giọng nói", "data": ["âm thanh micro"], "subjects": "người nói với HUD / robot",
     "purpose": "nhận dạng lệnh", "legal_basis": "đồng ý khi dùng tính năng giọng nói",
     "storage": "không lưu âm thanh; chuyển văn bản bằng STT", "retention": "không lưu",
     "recipients": "dịch vụ STT (Google) khi không dùng STT tại chỗ"},
    {"activity": "Nhật ký kiểm toán", "data": ["người thực hiện", "hành động", "tham số (đã che)", "IP"],
     "subjects": "người dùng, thiết bị", "purpose": "an toàn thông tin, truy vết", "legal_basis": "nghĩa vụ pháp lý / an toàn hệ thống",
     "storage": "PostgreSQL vnmate.audit_logs (chỉ ghi thêm, chuỗi băm)", "retention": "theo chính sách lưu trữ của tổ chức — không xoá từng dòng",
     "recipients": "kiểm toán viên (báo cáo xuất)"},
    {"activity": "Tác vụ vận hành AI", "data": ["mục tiêu tác vụ", "người yêu cầu", "kết quả"],
     "subjects": "người dùng", "purpose": "sổ tác vụ, kiểm chứng", "legal_basis": "lợi ích hợp pháp",
     "storage": "PostgreSQL vnmate.op_tasks", "retention": "đến khi quản trị viên dọn", "recipients": "không chia sẻ"},
    {"activity": "Tệp log ứng dụng", "data": ["sự kiện hệ thống", "tên người dùng"], "subjects": "người dùng",
     "purpose": "vận hành, xử lý sự cố", "legal_basis": "lợi ích hợp pháp",
     "storage": "logs/vnmateai.log (đã che token / mật khẩu)", "retention": "xoay vòng 20 MB × 10 tệp", "recipients": "không chia sẻ"},
]


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _like(value: str) -> str:
    return "%" + value.replace("%", "").replace("_", "") + "%"


def _identifier_ok(subject: str) -> bool:
    s = subject.strip()
    return len(s) >= 3 and not re.fullmatch(r"[%_\s*]+", s)


def lookup(subject: str) -> Dict[str, Any]:
    """Mọi bản ghi lưu bền gắn với `subject` (tên đăng nhập, email, SĐT hoặc họ tên đầy đủ)."""
    if not _identifier_ok(subject):
        raise ValueError("Định danh quá ngắn — nhập tên đăng nhập, email, SĐT hoặc họ tên đầy đủ.")
    s = subject.strip()
    out: Dict[str, Any] = {"subject": s, "found": {}}
    from mateai.infrastructure.database.db_manager import db_manager
    u = db_manager.get_user_by_username_or_id(s)
    if u:
        out["found"]["users"] = [{k: u.get(k) for k in ("id", "username", "full_name", "role", "department", "created_at")}]
    out["found"]["hr.employees"] = _rows_hr(s)
    out["found"]["employees"] = _rows_erp(s)
    out["found"]["voice_traces"] = _count_traces(s, (u or {}).get("username"))
    out["kept_by_law"] = {"audit_logs": "nhật ký kiểm toán bất biến — giữ theo chính sách lưu trữ, không xoá từng dòng"}
    return out


def _rows_hr(s: str) -> List[Dict[str, Any]]:
    from mateai.infrastructure.database import governance_repo
    return governance_repo.hr_people(s)


def _rows_erp(s: str) -> List[Dict[str, Any]]:
    from mateai.infrastructure.database import governance_repo
    return governance_repo.erp_people(s)


def _trace_terms(s: str, username: Any) -> List[str]:
    return [t for t in {s, str(username or "")} if t and len(t) >= 3]


def _count_traces(s: str, username: Any) -> int:
    from mateai.infrastructure.database import governance_repo
    return governance_repo.count_traces_mentioning(_trace_terms(s, username))


def erase(subject: str, actor: str) -> Dict[str, Any]:
    """Thực hiện yêu cầu xoá. Trả bảng việc đã làm / việc giữ lại kèm lý do (để trả lời chủ thể)."""
    found = lookup(subject)
    s = found["subject"]
    done: Dict[str, Any] = {}
    from mateai.infrastructure.database import governance_repo
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.infrastructure.database.erp_database import erp_db

    done["hr.employees_anonymised"] = governance_repo.anonymise_hr([r["id"] for r in found["found"]["hr.employees"]], ERASED)
    done["employees_anonymised"] = governance_repo.anonymise_erp([r["id"] for r in found["found"]["employees"]], ERASED)

    users = found["found"].get("users") or []
    username = users[0]["username"] if users else None
    done["voice_traces_deleted"] = governance_repo.delete_traces_mentioning(_trace_terms(s, username))

    from mateai.application.conversation import memory_manager as mm
    cleared = 0
    with mm._lock:
        for sid in [k for k in list(mm.CHAT_HISTORY) if username and _norm(username) in _norm(k)]:
            mm.CHAT_HISTORY.pop(sid, None)
            cleared += 1
    done["chat_sessions_cleared"] = cleared

    kept: Dict[str, str] = dict(found["kept_by_law"])
    if users:
        u = users[0]
        admins = [x for x in db_manager.get_all_users() if x.get("role") == "admin"]
        if u.get("role") == "admin" and len(admins) <= 1:
            kept["users"] = "tài khoản admin cuối cùng — tạo admin khác trước rồi xoá lại"
        else:
            db_manager.delete_user(u["id"])
            done["user_deleted"] = u["username"]

    erp_db.log_audit_action(action_type="PRIVACY_ERASURE", employee_id=actor, status="success",
                            payload=json.dumps({"subject_hash": _hash(s),                 # audit không lưu lại định danh đã xoá
                                                "done": {k: (bool(v) if k == "user_deleted" else v) for k, v in done.items()},
                                                "kept": list(kept)}, ensure_ascii=False))
    return {"subject": s, "done": done, "kept": kept}


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]


def audit_lookup(subject: str, actor: str) -> None:
    from mateai.infrastructure.database.erp_database import erp_db
    erp_db.log_audit_action(action_type="PRIVACY_LOOKUP", employee_id=actor, status="success",
                            payload=json.dumps({"subject_hash": _hash(subject)}))
