# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/llmops_tools.py
======================
Công cụ CHỈ ĐỌC cho AI Ly Ly trả lời câu hỏi về mức dùng AI, ngân sách và kết quả đánh giá chất lượng (docs/ai/llmops.md).

Không có công cụ đặt ngân sách hay chạy đánh giá: hai việc đó tốn tiền / đổi cấu hình nên chỉ admin làm qua portal hoặc API.
Số liệu là số THẬT nhà cung cấp báo; chi phí chỉ tính cho model đã khai giá — AI phải nói rõ phần chưa khai giá, không tự đoán tiền.
"""
from __future__ import annotations

from typing import Any, Dict

from core.plugin_manager import export_skill


@export_skill(
    name="get_ai_usage",
    description=("Xem mức dùng AI thật (token, số lượt, chi phí theo giá đã khai) trong N ngày gần nhất, theo model và theo người. "
                 "Cách hỏi: 'tháng này dùng AI hết bao nhiêu', 'tốn bao nhiêu token', 'ai dùng AI nhiều nhất', 'chi phí AI'. "
                 "Nếu có token chưa khai giá thì phải nói rõ phần đó chưa quy ra tiền."),
    parameters_schema={"type": "object", "properties": {"days": {"type": "integer", "description": "Số ngày gần nhất (1–365), mặc định 30"}},
                       "required": []},
)
async def get_ai_usage(days: int = 30) -> Dict[str, Any]:
    from mateai.application.llmops import usage
    try:
        s = usage.summary(days)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"success": True, "since": s["since"], "days": s["days"], "total": s["total"],
            "by_model": s["by_model"][:10], "top_users": s["by_user"][:10], "budget_mode": s["budget"]["mode"],
            "budget_checks": s["budget"]["checks"], "note": s["note"]}


@export_skill(
    name="get_ai_budget_status",
    description=("Xem ngân sách AI: chế độ (off / warn / block), hạn mức token / chi phí theo ngày, tháng, người và mức đã dùng. "
                 "Cách hỏi: 'còn bao nhiêu hạn mức AI', 'ngân sách AI hôm nay', 'sao AI báo hết hạn mức'."),
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def get_ai_budget_status() -> Dict[str, Any]:
    from mateai.application.llmops import usage
    try:
        st = usage.status()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"success": True, **st,
            "hint": None if st["checks"] else "Chưa đặt hạn mức nào (0 = không giới hạn). Quản trị viên đặt ở Quản lý Trợ lý AI → Chi phí & Đánh giá."}


@export_skill(
    name="get_ai_eval_results",
    description=("Xem kết quả các lần chạy bộ đánh giá chất lượng AI: tỉ lệ đạt, so với lần trước, ca nào bị TỤT (đạt → hỏng). "
                 "Truyền run_id để xem chi tiết một lần chạy. Đạt nghĩa là thỏa các điều kiện quản trị viên đã viết, không chứng minh gì thêm."),
    parameters_schema={"type": "object", "properties": {"set_id": {"type": "string", "description": "Lọc theo bộ kiểm"},
                                                        "run_id": {"type": "string", "description": "Chi tiết một lần chạy"}},
                       "required": []},
)
async def get_ai_eval_results(set_id: str = "", run_id: str = "") -> Dict[str, Any]:
    from mateai.application.llmops import evaluation as ev
    if run_id:
        r = ev.get_run(run_id)
        if not r:
            return {"success": False, "error": f"Không có lần chạy «{run_id}»"}
        return {"success": True, **{k: r[k] for k in ("run_id", "set_id", "status", "passed", "total", "pass_rate", "models", "error",
                                                      "created_at", "finished_at", "comparison")},
                "failed_cases": [{"id": c["id"], "error": c.get("error"), "failed_checks": [x for x in c["checks"] if not x["ok"]]}
                                 for c in r["results"] if not c["passed"]]}
    sets = ev.list_sets()
    if set_id:
        sets = [s for s in sets if s["set_id"] == set_id]
    return {"success": True, "sets": [{"set_id": s["set_id"], "name": s["name"], "cases": s["cases"], "last_run": s["last_run"]} for s in sets],
            "hint": None if sets else "Chưa có bộ kiểm nào — quản trị viên tạo ở Quản lý Trợ lý AI → Chi phí & Đánh giá."}
