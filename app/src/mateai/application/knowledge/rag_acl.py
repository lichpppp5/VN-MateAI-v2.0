# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/knowledge/rag_acl.py
=======================================
Phân quyền đọc cho kho tri thức (RAG / GraphRAG): mỗi TÀI LIỆU có mức phân loại (PUBLIC / INTERNAL / CONFIDENTIAL / RESTRICTED) và,
tuỳ chọn, danh sách phòng ban được xem. Người hỏi được xem khi cấp bảo mật ≥ mức của tài liệu VÀ (không giới hạn phòng ban HOẶC
đúng phòng ban). Admin xem tất cả.

- Tài liệu chưa khai báo = INTERNAL, không giới hạn phòng ban (giữ hành vi cũ: mọi người có tài khoản đều xem).
- Người hỏi lấy từ `CURRENT_PRINCIPAL` (tool_gate đặt) hoặc `use_reader()` (HTTP). Không rõ người hỏi → cấp 1, không phòng ban
  (thấy INTERNAL trở xuống, không thấy tài liệu giới hạn phòng ban): KHÔNG BAO GIỜ rơi về quyền admin.
- Lọc diễn ra TRƯỚC khi nội dung đến AI / người dùng; kết quả chỉ báo SỐ đoạn bị ẩn, không lộ tên tài liệu.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

CLASSIFICATIONS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 3, "RESTRICTED": 4}
DEFAULT_CLASSIFICATION = "INTERNAL"

_READER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("rag_reader", default=None)


class AclError(ValueError):
    pass


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(actor: str, action: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "RAG_ACL", "SUCCESS", details)
    except Exception:  # noqa: BLE001
        pass


# ── khai báo quyền theo tài liệu ────────────────────────────────────────────

def _row_to_acl(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        depts = json.loads(row.get("departments") or "[]")
    except (TypeError, ValueError):
        depts = []
    return {"doc_name": row["doc_name"], "classification": row["classification"], "departments": depts,
            "updated_by": row.get("updated_by"), "updated_at": row.get("updated_at")}


def get_acl(doc_name: str) -> Dict[str, Any]:
    row = _db().dev_get("rag_doc_acl", doc_name)
    if row:
        return _row_to_acl(row)
    return {"doc_name": doc_name, "classification": DEFAULT_CLASSIFICATION, "departments": [],
            "updated_by": None, "updated_at": None, "default": True}


def list_acls() -> Dict[str, Dict[str, Any]]:
    return {r["doc_name"]: _row_to_acl(r) for r in _db().dev_list("rag_doc_acl", limit=5000)}


def set_acl(doc_name: str, classification: str, departments: Optional[List[str]], actor: str) -> Dict[str, Any]:
    doc_name = str(doc_name or "").strip()
    cls = str(classification or "").strip().upper()
    if not doc_name:
        raise AclError("Thiếu tên tài liệu")
    if cls not in CLASSIFICATIONS:
        raise AclError(f"Mức phân loại phải là một trong: {', '.join(CLASSIFICATIONS)}")
    depts: List[str] = []
    for d in departments or []:
        d = str(d).strip()
        if d and d.lower() not in [x.lower() for x in depts]:
            depts.append(d[:80])
    if len(depts) > 50:
        raise AclError("Tối đa 50 phòng ban")
    db = _db()
    db.dev_delete("rag_doc_acl", doc_name)
    db.dev_insert("rag_doc_acl", {"doc_name": doc_name, "classification": cls, "departments": json.dumps(depts, ensure_ascii=False),
                                  "updated_by": actor, "updated_at": _now()})
    _audit(actor, "rag_acl_set", {"doc_name": doc_name, "classification": cls, "departments": depts})
    return get_acl(doc_name)


def reset_acl(doc_name: str, actor: str) -> bool:
    n = _db().dev_delete("rag_doc_acl", doc_name)
    if n:
        _audit(actor, "rag_acl_reset", {"doc_name": doc_name})
    return bool(n)


# ── người hỏi ───────────────────────────────────────────────────────────────

@contextlib.contextmanager
def use_reader(username: Optional[str]) -> Iterator[None]:
    """Đặt người đang hỏi cho các lời gọi RAG trong khối (HTTP). Context được `to_thread` sao chép sang luồng chạy."""
    tok = _READER.set(str(username) if username else None)
    try:
        yield
    finally:
        _READER.reset(tok)


_ANON = {"id": None, "role": "viewer", "department": None, "clearance": 1, "all_departments": False}


def current_reader() -> Dict[str, Any]:
    ident = _READER.get()
    if ident is None:
        try:
            from mateai.application.security.security_guard import CURRENT_PRINCIPAL
            ident = CURRENT_PRINCIPAL.get()
        except Exception:  # noqa: BLE001
            ident = None
    if not ident:
        return dict(_ANON)
    try:
        from mateai.application.security.security_guard import security_guard
        return security_guard.principal(ident)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.warning("Không xác định được người hỏi RAG '%s': %s", ident, exc)
        return {**_ANON, "id": ident}


def can_read(acl: Dict[str, Any], reader: Dict[str, Any]) -> bool:
    if reader.get("role") == "admin":
        return True
    if int(reader.get("clearance") or 0) < CLASSIFICATIONS.get(acl.get("classification"), CLASSIFICATIONS["RESTRICTED"]):
        return False
    depts = [d.lower() for d in acl.get("departments") or []]
    if depts:
        return str(reader.get("department") or "").strip().lower() in depts
    return True


class Filter:
    """Bộ lọc dựng một lần cho một lượt truy vấn (một lần đọc bảng ACL)."""

    def __init__(self, reader: Optional[Dict[str, Any]] = None) -> None:
        self.reader = reader or current_reader()
        try:
            self._acls: Optional[Dict[str, Dict[str, Any]]] = list_acls()
        except Exception as exc:  # noqa: BLE001 — không đọc được ACL thì không biết tài liệu nào bị hạn chế: chặn hết (fail-closed)
            logger.error("Không đọc được ACL của RAG: %s", exc)
            self._acls = None
        self.hidden = 0

    @property
    def active(self) -> bool:
        """Có khai báo nào cần lọc không (để biết có nên lấy dư kết quả)."""
        if self.reader.get("role") == "admin":
            return False
        return self._acls is None or bool(self._acls) or int(self.reader.get("clearance") or 0) < CLASSIFICATIONS[DEFAULT_CLASSIFICATION]

    def allows(self, doc_name: str) -> bool:
        if self.reader.get("role") == "admin":
            return True
        if self._acls is None:
            return False
        acl = self._acls.get(doc_name) or {"classification": DEFAULT_CLASSIFICATION, "departments": []}
        return can_read(acl, self.reader)

    def classification_of(self, doc_name: str) -> str:
        return ((self._acls or {}).get(doc_name) or {}).get("classification", DEFAULT_CLASSIFICATION)
