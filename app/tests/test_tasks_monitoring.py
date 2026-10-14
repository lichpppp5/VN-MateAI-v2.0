# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tasks_monitoring.py
==============================
Tab Portal #tasks — giám sát giao việc (2026-10-05). Trước đây:

  - máy trạm gửi `task_response` với task_id BẤT KỲ đều được ghi: tự bịa đầu việc
    "hoàn thành" vào KPI, hoặc đóng việc của máy khác; trạng thái là chuỗi tuỳ ý;
  - popup lỗi (không in gì) -> Agent báo "completed"; đóng cửa sổ -> "vướng mắc";
  - gửi lỗi -> việc treo "pending" mãi; người giao là ô chữ tự khai ("Ban Giám Đốc");
  - không có hạn chót, thời gian phản hồi, KPI theo máy; số liệu KPI gộp cả công việc ERP;
  - Viewer import / tạo / XOÁ được phòng ban ERP.
Không gọi mạng; DB tạm (conftest), CSV KPI ghi vào tmp_path.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.devices import task_manager as tm_mod
from mateai.application.devices.task_manager import build_board, task_manager
from mateai.infrastructure.database.db_manager import db_manager

ROOT = Path(__file__).resolve().parents[1]
TS = "%Y-%m-%d %H:%M:%S"


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, t):
        self.sent.append(t)


@pytest.fixture
def lan(monkeypatch, tmp_path):
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    ws = {"pc-01": FakeWS(), "pc-02": FakeWS()}
    for cid, w in ws.items():
        monkeypatch.setitem(orchestrator._clients, cid, {"websocket": w})
    monkeypatch.setattr(orchestrator, "is_client_online", lambda cid: cid in ws)
    monkeypatch.setattr(task_manager, "csv_path", tmp_path / "kpi.csv")
    monkeypatch.setattr(task_manager, "_tts_notifier", None)
    return ws


async def test_response_only_for_own_pending_task(lan):
    r = await task_manager.dispatch_task("pc-01", "Nộp báo cáo", "Sếp", dispatched_by="boss", due_minutes=30)
    tid = r["task_id"]
    row = db_manager.get_task(tid)
    assert row["dispatched_by"] == "boss" and row["due_at"] and row["status"] == "pending"

    # Máy khác không đóng được; task_id bịa không tạo dòng mới; trạng thái lạ -> issue.
    assert task_manager.handle_task_response("pc-02", tid, "completed")["status"] == "ignored"
    assert task_manager.handle_task_response("pc-01", "task_bia", "completed")["status"] == "ignored"
    assert db_manager.get_task("task_bia") is None
    assert task_manager.handle_task_response("pc-01", tid, "<b>xong</b>")["kpi_status"] == "issue"
    row = db_manager.get_task(tid)
    assert row["status"] == "issue" and row["responded_at"]
    # Đã đóng -> phản hồi lặp lại bị bỏ qua (không ghi đè).
    assert task_manager.handle_task_response("pc-01", tid, "completed")["status"] == "ignored"


async def test_dismissed_keeps_task_pending_and_cancel_blocks_late_reply(lan):
    tid = (await task_manager.dispatch_task("pc-01", "Kiểm tra kho", dispatched_by="boss"))["task_id"]
    assert task_manager.handle_task_response("pc-01", tid, "dismissed")["status"] == "dismissed"
    row = db_manager.get_task(tid)
    assert row["status"] == "pending" and "đóng cửa sổ" in row["resolution_notes"]
    assert (await task_manager.remind_task(tid))["status"] == "success"
    assert len(lan["pc-01"].sent) == 2
    assert task_manager.cancel_task(tid, "boss")["status"] == "success"
    assert task_manager.handle_task_response("pc-01", tid, "completed")["status"] == "ignored"
    assert db_manager.get_task(tid)["status"] == "cancelled"


async def test_send_failure_is_recorded_not_left_pending(lan, monkeypatch):
    async def boom(*a, **k):
        raise ConnectionError("mất kết nối")

    monkeypatch.setattr(lan["pc-01"], "send_text", boom)
    r = await task_manager.dispatch_task("pc-01", "Việc lỗi", dispatched_by="boss")
    assert r["status"] == "error"
    rows = [t for t in db_manager.list_micro_tasks() if t["task_message"] == "Việc lỗi"]
    assert rows and rows[0]["status"] == "failed"


def test_board_math():
    now = time.time()

    def ts(off):
        return datetime.fromtimestamp(now + off).strftime(TS)

    rows = [
        {"task_id": "a", "client_id": "pc-01", "status": "completed", "timestamp": ts(-600), "responded_at": ts(-540)},
        {"task_id": "b", "client_id": "pc-01", "status": "issue", "timestamp": ts(-600), "responded_at": ts(-480)},
        {"task_id": "c", "client_id": "pc-02", "status": "pending", "timestamp": ts(-3600), "due_at": ts(-60)},
        {"task_id": "d", "client_id": "pc-02", "status": "pending", "timestamp": ts(-60), "due_at": ts(600)},
        {"task_id": "e", "client_id": "pc-02", "status": "failed", "timestamp": ts(-60)},
    ]
    b = build_board(rows, now=now, online={"pc-01"})
    t = b["totals"]
    assert (t["sent"], t["completed"], t["issue"], t["pending"], t["overdue"], t["failed"]) == (5, 1, 1, 2, 1, 1)
    assert t["completion_rate"] == 33.3                 # 1 / (1 hoàn thành + 1 vướng + 1 quá hạn)
    assert t["avg_response_s"] == 90.0                  # (60 + 120) / 2
    m = {x["client_id"]: x for x in b["machines"]}
    assert m["pc-02"]["overdue"] == 1 and m["pc-02"]["completion_rate"] == 0.0 and not m["pc-02"]["online"]
    assert b["machines"][0]["client_id"] == "pc-02"     # máy có việc quá hạn lên đầu
    d = next(x for x in b["tasks"] if x["task_id"] == "d")
    assert d["overdue"] is False and 590 <= d["due_in_s"] <= 600
    assert build_board([], now=now)["totals"]["completion_rate"] is None


def test_board_excludes_erp_tasks():
    db_manager.add_or_update_task({"task_id": "erp_x", "client_id": "erp", "task_message": "ERP", "status": "pending"})
    db_manager.add_or_update_task({"task_id": "imp_x", "client_id": "master", "task_message": "Import", "status": "pending"})
    ids = {t["task_id"] for t in db_manager.list_micro_tasks()}
    assert "erp_x" not in ids and "imp_x" not in ids


def _client(role, router):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_api_roles_multi_dispatch_and_export(lan):
    import mateai.interfaces.http.routers.tasks as tasks
    viewer = _client("viewer", tasks.router)
    assert viewer.get("/api/v1/tasks/board").status_code == 200
    assert viewer.post("/api/v1/tasks/send", json={"client_id": "pc-01", "message": "x"}).status_code == 403
    assert viewer.get("/api/v1/tasks/export.csv").status_code == 403

    mgr = _client("manager", tasks.router)
    r = mgr.post("/api/v1/tasks/send", json={"client_ids": ["pc-01", "pc-02", "pc-09"],
                                              "message": "=HYPERLINK(\"x\")", "due_minutes": 15}).json()
    assert r["sent"] == 2 and r["failed"] == 1
    board = mgr.get("/api/v1/tasks/board?days=1").json()
    mine = [t for t in board["tasks"] if t["task_message"].startswith("=HYPERLINK")]
    assert len(mine) == 2 and {t["dispatched_by"] for t in mine} == {"manager_u"}
    assert set(board["online_clients"]) == {"pc-01", "pc-02"}
    csv_text = mgr.get("/api/v1/tasks/export.csv?days=1").text
    assert "'=HYPERLINK" in csv_text                    # chặn công thức Excel
    assert mgr.post(f"/api/v1/tasks/{mine[0]['task_id']}/cancel").status_code == 200
    assert mgr.post(f"/api/v1/tasks/{mine[0]['task_id']}/cancel").status_code == 409


def test_erp_writes_need_manager_and_delete_needs_admin():
    import mateai.interfaces.http.api_erp as erp
    v = _client("viewer", erp.router)
    assert v.get("/api/erp/structure").status_code == 200
    assert v.post("/api/erp/department", json={"name": "Phòng thử"}).status_code == 403
    assert v.delete("/api/erp/department/1").status_code == 403
    assert _client("manager", erp.router).delete("/api/erp/department/1").status_code == 403


def test_agent_no_fake_completion():
    agent = (ROOT / "client_agent" / "agent.py").read_text(encoding="utf-8")
    popup = (ROOT / "client_agent" / "popup_ui.py").read_text(encoding="utf-8")
    assert 'or "completed"' not in agent and '"dismissed"' in agent
    assert 'result = "dismissed"' in popup


def test_portal_tab_is_live_and_honest():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    tab = html[html.index('id="tab-tasks"'):html.index('id="tab-logs"')]
    for el in ("task-board-body", "task-machines-body", "task-targets", "task-due", "tb-overdue", "tb-resp"):
        assert f'id="{el}"' in tab, el
    assert 'value="Ban Giám Đốc"' not in tab            # người giao = tài khoản, không tự khai
    assert "event === 'task_update'" in app and "scheduleTaskBoardReload()" in app
    loader = app[app.index("if (tabId === 'tasks') {"):app.index("if (tabId === 'security') {")]
    assert "loadTaskBoard()" in loader and "loadKpiLogs" not in loader
    fn = app[app.index("function renderTaskBoard("):app.index("function renderTaskMachines(")]
    assert "_esc(t.task_message" in fn and "_esc(t.client_id)" in fn
