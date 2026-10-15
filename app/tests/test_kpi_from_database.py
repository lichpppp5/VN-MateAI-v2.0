# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""KPI giao việc máy trạm đọc từ bảng `tasks` (thẩm định thương mại §6: logs/kpi_logs.csv "Không đạt" — không giao dịch,
không phân quyền). Phản hồi mới không còn ghi CSV."""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime

from mateai.application.devices.task_manager import TaskManager, task_manager
from mateai.infrastructure.database.db_manager import db_manager


def _task(client, status, when):
    tid = f"t-{uuid.uuid4().hex[:8]}"
    with db_manager._get_connection() as conn:
        conn.execute("INSERT INTO tasks (id, timestamp, client_id, task_message, status, created_at, updated_at, responded_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?);", (tid, when, client, "Nộp báo cáo", status, when, when, when))
        conn.commit()


def test_monthly_summary_comes_from_tasks_table():
    month = datetime.now().strftime("%Y-%m")
    client = f"PC-{uuid.uuid4().hex[:4]}"
    _task(client, "completed", f"{month}-02 09:00:00")
    _task(client, "completed", f"{month}-03 09:00:00")
    _task(client, "issue", f"{month}-04 09:00:00")
    _task(client, "completed", "2001-01-05 09:00:00")                 # tháng khác: không tính
    text = task_manager.summarize_monthly(client_id=client)
    assert "hoàn thành 2 đầu việc" in text and "vướng mắc 1" in text


def test_responses_no_longer_written_to_csv():
    src = inspect.getsource(TaskManager.handle_task_response)
    assert "csv.writer" not in src and "open(self.csv_path" not in inspect.getsource(TaskManager.summarize_monthly)
