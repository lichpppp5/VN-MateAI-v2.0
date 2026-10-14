# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/hr_guard.py
=======================================
Ranh giới tự chủ cho QUYẾT ĐỊNH VỀ CON NGƯỜI (đạo đức AI, điểm 4 — 2026-10-12).

AI không tự quyết định nhân sự: tuyển / loại ứng viên, sa thải, kỷ luật, thăng / giáng chức, chấm điểm / xếp hạng / đánh giá
nhân viên, đổi lương / thưởng. Công cụ làm việc đó (kể cả kỹ năng AI TỰ TẠO sau này — nhận theo tên) luôn:
  - rủi ro tối thiểu L3 → người có thẩm quyền duyệt (risk_engine.assess_risk);
  - KHÔNG được "duyệt một lần, nhớ 30 ngày" (policy_engine): mỗi quyết định về một người được duyệt riêng.
Công cụ CHỈ ĐỌC (get / list / query… — bảng xếp hạng, chấm công, báo cáo) không bị ảnh hưởng.
"""
from __future__ import annotations

import re
import unicodedata

#: Hành động về con người — một từ là đủ (tiếng Anh + tiếng Việt không dấu viết liền).
_STRONG = {"hire", "fire", "terminate", "dismiss", "discipline", "disciplinary", "promote", "demote", "payroll", "recruit",
           "layoff", "sack", "reprimand", "penalize", "penalise", "kyluat", "tuyendung", "sathai", "thangchuc",
           "giangchuc", "thoiviec", "chamdiem"}
#: Động từ chỉ là quyết định khi đi với đối tượng là NGƯỜI (rank_servers thì không).
_VERBS = {"evaluate", "appraise", "rate", "rank", "score", "assess", "review", "reject", "shortlist", "screen", "select",
          "approve", "adjust", "set", "change", "update", "raise", "cut", "reduce", "grade", "classify", "danhgia", "xephang"}
_PEOPLE = {"employee", "employees", "staff", "candidate", "candidates", "applicant", "applicants", "personnel", "worker",
           "workers", "nhanvien", "ungvien", "salary", "salaries", "bonus", "wage", "luong", "thuong", "performance", "cv",
           "resume", "hoso"}
#: Động từ chỉ đọc ở đầu tên → không phải quyết định (xem dữ liệu để người quyết).
_READ_ONLY = {"get", "list", "read", "query", "search", "check", "lookup", "show", "count", "view", "summarize", "summarise"}


def _words(name: str) -> "list[str]":
    t = unicodedata.normalize("NFD", re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name or "")))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D").lower()
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


def is_people_decision(tool_name: str) -> bool:
    """Tool này có ra QUYẾT ĐỊNH về một con người không (theo tên). Công cụ chỉ đọc → False."""
    words = _words(tool_name)
    if not words or words[0] in _READ_ONLY:
        return False
    joined = {"".join(words[i:i + 2]) for i in range(len(words))} | set(words)
    if joined & _STRONG:
        return True
    return bool(joined & _VERBS) and bool(joined & _PEOPLE)
