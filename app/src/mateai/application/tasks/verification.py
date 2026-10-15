# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/tasks/verification.py
========================================
Kiểm chứng sau hành động (prompt Supervisor §29–§30, §151).

"Tool trả về đã chạy" KHÔNG phải là "đã xong". Mức kiểm chứng theo rủi ro:

  rủi ro 1  NONE      — tool chỉ đọc; đạt nếu tool không báo lỗi
  rủi ro 2  BASIC     — kết quả đúng dạng, không báo lỗi
  rủi ro >= 3 STANDARD — đọc lại trạng thái THẬT sau hành động bằng bộ kiểm chứng
                         riêng của tool; tool chưa có bộ kiểm chứng -> `not_verifiable`
                         (sổ tác vụ chuyển ESCALATED: cần người xác nhận, không báo xong)

Bộ kiểm chứng chỉ chạy cho tác vụ trên máy chủ (`master`); tác vụ trên máy trạm
không đọc lại được từ máy chủ -> `not_verifiable`, nói đúng như vậy.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

NONE, BASIC, STANDARD = "NONE", "BASIC", "STANDARD"
PASSED, FAILED, NOT_VERIFIABLE, PENDING = "passed", "failed", "not_verifiable", "pending"
_LOCAL = ("master", "local", "server", "chính", "cục bộ")


def tool_outcome(result: Any) -> Dict[str, Any]:
    """Trạng thái thật của một kết quả tool: `status` (success / error / need_confirm /
    awaiting_approval / budget_exceeded / done…), `message`, `approval_id`.

    Trình chạy plugin bọc kết quả skill: {"success": true, "data": {"success": false,
    "error": "..."}} — lớp ngoài chỉ nói "đã gọi được skill". Đọc lớp TRONG."""
    res = result if isinstance(result, dict) else {}
    inner = res.get("data")
    if isinstance(inner, dict):
        if "error" in inner and inner["error"]:
            res = {**res, "error": inner["error"], "success": False}
        elif inner.get("success") is False:
            res = {**res, "success": False, "error": inner.get("error") or inner.get("message")}
        elif inner.get("status") in ("error", "failed", "need_confirm", "awaiting_approval", "budget_exceeded"):
            res = {**res, "status": inner["status"], "error": inner.get("error") or inner.get("message")}
        elif inner.get("success") is True and not res.get("error"):
            res = {**res, "success": True}

    raw_status = res.get("status")
    if not isinstance(raw_status, str):
        raw_status = None

    if raw_status:
        st_lower = raw_status.strip().lower()
        if st_lower in ("need_confirm", "awaiting_approval", "pending"):
            status = st_lower
        elif st_lower in ("error", "failed", "failure", "timeout", "budget_exceeded", "repeated_failure"):
            status = "error"
        elif st_lower in ("success", "done", "ok", "passed", "completed"):
            status = "success"
        elif res.get("error") or res.get("success") is False:
            status = "error"
        else:
            status = "success" if res.get("success") is True or not res.get("error") else "error"
    elif res.get("error") or res.get("success") is False:
        status = "error"
    else:
        status = "success" if res.get("success") is True else "done"

    message = str(res.get("message") or res.get("error") or "")[:300]
    return {"status": status, "message": message, "approval_id": res.get("approval_id")}


def level_for(risk: int) -> str:
    return NONE if risk <= 1 else BASIC if risk == 2 else STANDARD


# ── Bộ kiểm chứng riêng (STANDARD) ──────────────────────────────────────────

def _verify_kill_process(args: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    import psutil
    try:
        pid = int(args.get("pid"))
    except (TypeError, ValueError):
        return {"status": NOT_VERIFIABLE, "checks": ["không có PID hợp lệ để kiểm lại"]}
    alive = psutil.pid_exists(pid)
    return {"status": FAILED if alive else PASSED,
            "checks": [f"psutil.pid_exists({pid}) = {alive}"]}


def _verify_write_file(args: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    from mateai.application.skills.builtin.file_system import _resolve_path
    raw = args.get("file_path")
    if not raw:
        return {"status": NOT_VERIFIABLE, "checks": ["không có đường dẫn"]}
    path = _resolve_path(str(raw))
    if not path.is_file():
        return {"status": FAILED, "checks": [f"{path} không tồn tại sau khi ghi"]}
    content = str(args.get("content") or "").encode("utf-8")
    data = path.read_bytes()
    mode = str(args.get("mode") or "w").lower()
    ok = data == content if mode == "w" else data.endswith(content)
    digest = hashlib.sha256(data).hexdigest()[:16]
    return {"status": PASSED if ok else FAILED,
            "checks": [f"{path.name}: {len(data)} byte, sha256 {digest}, khớp nội dung = {ok}"]}


VERIFIERS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = {
    "kill_process": _verify_kill_process,
    "write_file": _verify_write_file,
}


def verify(tool: str, args: Dict[str, Any], result: Any, risk: int, target: str = "master") -> Dict[str, Any]:
    """Kết quả: {level, status, checks[], summary}."""
    level = level_for(risk)
    out = tool_outcome(result)
    st = out["status"]
    if st in ("need_confirm", "awaiting_approval", "pending"):
        return {"level": level, "status": PENDING, "checks": ["chờ phê duyệt — chưa thực thi"], "summary": "chờ duyệt"}
    if st not in ("success", "done", "ok", "passed", "completed"):
        return {"level": level, "status": FAILED, "checks": [f"tool báo: {st} {out['message']}".strip()],
                "summary": out["message"] or st}
    if level in (NONE, BASIC):
        return {"level": level, "status": PASSED, "checks": [f"tool báo {st}, không có lỗi"], "summary": "đạt"}
    fn = VERIFIERS.get(str(tool))
    if fn is None or str(target or "master").strip().lower() not in _LOCAL:
        why = ("chưa có bộ kiểm chứng riêng cho tool này" if fn is None
               else f"tác vụ chạy trên máy trạm '{target}' — máy chủ không đọc lại được")
        return {"level": level, "status": NOT_VERIFIABLE, "checks": [why], "summary": why}
    try:
        res = fn(dict(args or {}), result if isinstance(result, dict) else {})
    except Exception as exc:  # noqa: BLE001 — kiểm chứng lỗi: không coi là đạt
        logger.warning("[Verify] %s lỗi: %s", tool, exc)
        res = {"status": NOT_VERIFIABLE, "checks": [f"kiểm chứng lỗi: {type(exc).__name__}"]}
    checks: List[str] = list(res.get("checks") or [])
    return {"level": level, "status": res["status"], "checks": checks, "summary": "; ".join(checks)[:300]}
