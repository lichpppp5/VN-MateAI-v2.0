# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/playbook_tools.py
========================
Công cụ cho AI Ly Ly dùng KỊCH BẢN vận hành (docs/integrations/playbooks.md). AI không tự chế chuỗi lệnh: chỉ chạy kịch bản người quản trị
đã viết, đã kiểm tra, và mọi bước vẫn qua chính sách / duyệt như lời gọi công cụ thường. Danh tính áp RBAC là NGƯỜI đang được phục vụ.

`list_*` / `get_*` là chỉ đọc (L0, dùng được cả khi bật kill switch). `run_playbook` chỉ admin; kế hoạch có bước cần duyệt thì xin MỘT phiếu
cho cả kế hoạch và NÓI RÕ chưa chạy.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


def _eng():
    from mateai.application.playbooks import engine
    return engine


def _caller() -> str:
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    return str(CURRENT_PRINCIPAL.get() or "AI_Agent")


def _fail(exc: Exception) -> Dict[str, Any]:
    return {"success": False, "error": str(exc) if exc.__class__.__name__ == "PlaybookError" else f"{type(exc).__name__}: {exc}"}


@export_skill(
    name="list_playbooks",
    description="Liệt kê kịch bản vận hành đã được quản trị viên viết (mã, tên, mô tả, tham số). Gọi trước khi chạy để biết đúng mã và tham số.",
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def list_playbooks() -> Dict[str, Any]:
    items = [{"id": p["id"], "name": p["name"], "description": p["definition"].get("description", ""), "params": p["definition"]["params"],
              "steps": len(p["definition"]["steps"]), "has_verification": bool(p["definition"]["verify"])}
             for p in _eng().list_playbooks() if p["enabled"]]
    return {"success": True, "count": len(items), "playbooks": items,
            "hint": None if items else "Chưa có kịch bản nào — quản trị viên tạo ở tab Kịch bản."}


@export_skill(
    name="get_playbook_plan",
    description=("CHẠY THỬ một kịch bản (không thực thi gì): từng bước sẽ được cho phép / cần duyệt / bị chặn, mức rủi ro, có hoàn tác không. "
                 "Dùng để báo người dùng trước khi chạy thật."),
    parameters_schema={"type": "object", "properties": {"playbook_id": {"type": "string"}, "params": {"type": "object"}}, "required": ["playbook_id"]},
)
async def get_playbook_plan(playbook_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    eng = _eng()
    pb = eng.get_playbook(playbook_id)
    if not pb:
        return {"success": False, "error": f"Không có kịch bản «{playbook_id}»"}
    try:
        p = eng.plan(pb["definition"], params, caller=_caller(), human=False)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"success": True, "playbook": pb["name"], "blocked": p["blocked"], "denied": p["denied"], "needs_approval": p["needs_approval"],
            "max_risk": p["max_risk"], "notes": p["notes"],
            "steps": [{k: s[k] for k in ("title", "tool", "decision", "risk", "has_rollback")} for s in p["steps"]]}


@export_skill(
    name="run_playbook",
    description=("CHẠY một kịch bản vận hành (chỉ admin). Mọi bước vẫn qua chính sách. Nếu kết quả có `awaiting_approval` thì CHƯA chạy gì: nói người "
                 "dùng duyệt phiếu `approval_id`. Nếu `running`, dùng get_playbook_run để xem tiến độ — KHÔNG nói đã xong khi chưa kiểm chứng."),
    parameters_schema={"type": "object", "properties": {"playbook_id": {"type": "string"}, "params": {"type": "object"},
                                                        "idempotency_key": {"type": "string", "description": "Chống chạy trùng khi gọi lại"}},
                       "required": ["playbook_id"]},
)
async def run_playbook(playbook_id: str, params: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    try:
        out = await _eng().start(playbook_id, params, caller=_caller(), human=False, idempotency_key=idempotency_key)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    res = {"success": out["status"] in ("running", "awaiting_approval", "duplicate"), "status": out["status"], "run_id": out["run_id"]}
    if out["status"] == "awaiting_approval":
        res.update(awaiting_approval=True, approval_id=out["approval_id"], message="CHƯA chạy: cần người có thẩm quyền duyệt kế hoạch. Nói rõ điều này với người dùng.")
    if out["status"] == "blocked":
        res.update(denied=out["plan"]["denied"], message="Kế hoạch bị chính sách chặn — không chạy.")
    return res


@export_skill(
    name="get_playbook_run",
    description="Xem một lượt chạy kịch bản: trạng thái, từng bước đã làm / lỗi, kiểm chứng đạt chưa. SUCCEEDED_UNVERIFIED = chưa kiểm chứng, đừng báo là xong.",
    parameters_schema={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
)
async def get_playbook_run(run_id: str) -> Dict[str, Any]:
    run = _eng().get_run(run_id)
    if not run:
        return {"success": False, "error": f"Không có lượt chạy «{run_id}»"}
    return {"success": True, "run_id": run_id, "status": run["status"], "error": run.get("error"),
            "steps": [{k: s.get(k) for k in ("title", "tool", "status", "error")} for s in run["steps"]], "verify": run["verify"]}
