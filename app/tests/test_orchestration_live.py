# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Lõi điều phối đầu-cuối với LLM THẬT (gateway trong config.json) — chỉ chạy khi đặt VNMATEAI_LIVE_LLM=1:

    VNMATEAI_LIVE_LLM=1 python -m pytest -q tests/test_orchestration_live.py -s

Vai nội bộ "Quản trị hệ thống" chỉ có `get_system_info`, nhận một phiếu thật; agent tự gọi công cụ; AI thẩm định thật chấm; phép thử
chỉ đọc kiểm độc lập. CSDL vẫn là CSDL cách ly của bộ test."""
from __future__ import annotations

import os

import pytest

from mateai.application.orchestration import registry as R
from mateai.application.orchestration import service as S
from mateai.application.tasks import ledger
from mateai.infrastructure.database.db_manager import db_manager

pytestmark = pytest.mark.skipif(os.environ.get("VNMATEAI_LIVE_LLM") != "1", reason="cần VNMATEAI_LIVE_LLM=1 (gọi LLM thật)")


async def test_real_llm_role_completes_a_real_order_with_independent_verification():
    import asyncio
    import uuid
    with db_manager._get_connection() as conn:
        conn.execute("DELETE FROM orch_orders;")
        conn.execute("DELETE FROM orch_executors;")
        conn.commit()
    boss = f"live-{uuid.uuid4().hex[:6]}"
    db_manager.create_user({"username": boss, "password": "Matkhau#2026", "role": "admin", "full_name": "Thử thật"})
    R.save("sysadmin", {"kind": "local", "name": "Quản trị hệ thống", "capabilities": ["sysadmin"], "tools": ["get_system_info"],
                        "description": "Đọc tình trạng máy chủ (CPU, RAM, ổ đĩa) và báo cáo ngắn gọn bằng tiếng Việt."}, "admin")
    o = await S.create({"objective": "Báo cáo tình trạng tài nguyên máy chủ hiện tại (CPU, RAM, ổ đĩa) kèm nhận xét có cần xử lý không",
                        "capability": "sysadmin", "deliverables": ["Số liệu CPU, RAM, ổ đĩa", "Nhận xét có cần xử lý không"],
                        "done_criteria": [{"kind": "evidence", "min": 1}, {"kind": "contains", "text": "RAM"},
                                          {"kind": "probe", "tool": "get_system_info", "args": {}, "retries": 1,
                                           "expect": {"path": "result", "op": "exists"}},
                                          "Có nhận xét rõ ràng có cần xử lý hay không"],
                        "risk": 1, "deadline_s": 180}, requested_by=boss)
    for _ in range(600):
        cur = S.get(o["order_id"])
        if cur["status"] not in ("PLANNED", "DISPATCHED", "REVIEWING") and o["order_id"] not in S._tasks:
            break
        await asyncio.sleep(0.5)
    cur = S.get(o["order_id"])
    print("\nTRẠNG THÁI:", cur["status"], "| lượt:", cur["attempt"], "| lỗi:", cur.get("error"))
    print("KẾT QUẢ AGENT:", (cur.get("report") or {}).get("result", "")[:600])
    for c in (cur.get("verification") or {}).get("checks", []):
        print(f"  [{c['ok']}] [{c['kind']}] {c['criterion']} — {c['detail']}")
    t = ledger.get_task(cur["task_id"])
    print("SỔ TÁC VỤ:", t["status"], t.get("verification_status"), "| bước:", [s["tool"] for s in t["steps"]])
    assert cur["status"] == "COMPLETED", cur
    assert t["status"] == "COMPLETED" and any(s["tool"] == "get_system_info" for s in t["steps"])
