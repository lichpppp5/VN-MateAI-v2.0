# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/task_manager.py
====================
Lean Micro-Tasking & KPI Tracking Engine for VN-MateAI.

Responsibilities:
  - Keep track of active/pending tasks dispatched to LAN clients in memory (RAM).
  - Push interactive popup payloads to Client Agents via WebSocket.
  - Handle task responses ('completed' or 'issue') from workers.
  - Append audit records to logs/kpi_logs.csv (Timestamp, Client_ID, Task_Message, Status).
  - Trigger instant TTS voice alerts to Xiaozhi audio nodes and Master speakers.
  - Provide fast KPI statistics and monthly summaries for AI skills and Web Portal.
"""

from __future__ import annotations

import csv
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mateai.infrastructure.database.db_manager import db_manager

logger = logging.getLogger(__name__)

# Base directory for logs
# Thư mục gốc dự án (đúng cả bản đóng gói) — không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
_LOGS_DIR = _PROJECT_ROOT / "logs"
_KPI_CSV_PATH = _LOGS_DIR / "kpi_logs.csv"
_CSV_HEADERS = ["Timestamp", "Client_ID", "Task_Message", "Status"]
_TS = "%Y-%m-%d %H:%M:%S"
#: Trạng thái máy trạm được phép báo (nút trên popup). Giá trị khác -> "issue".
_RESPONSE_STATUSES = ("completed", "issue")


def _parse_ts(value: Any) -> Optional[float]:
    try:
        return datetime.strptime(str(value), _TS).timestamp()
    except (TypeError, ValueError):
        return None


def _notify_portal(task_id: str, client_id: str, status: str) -> None:
    """Báo Portal làm mới bảng giám sát (không gửi nội dung việc)."""
    try:
        import asyncio
        from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui
        asyncio.get_running_loop().create_task(broadcast_portal_ui(
            "task_update", {"task_id": task_id, "client_id": client_id, "status": status}))
    except Exception:  # noqa: BLE001 — không có loop (CLI/test) thì thôi
        pass


def build_board(rows: List[Dict[str, Any]], now: Optional[float] = None,
                online: Optional[set] = None) -> Dict[str, Any]:
    """Thống kê + danh sách cho bảng giám sát (thuần — test được).

    Tỷ lệ hoàn thành = hoàn thành / (hoàn thành + vướng mắc + quá hạn): việc còn
    trong hạn chưa tính là trượt. Thời gian phản hồi = responded_at - lúc giao.
    """
    now = now or time.time()
    online = online or set()
    totals = {"sent": 0, "completed": 0, "issue": 0, "pending": 0, "overdue": 0, "failed": 0, "cancelled": 0}
    per: Dict[str, Dict[str, Any]] = {}
    resp: List[float] = []
    items: List[Dict[str, Any]] = []
    for r in rows:
        st = str(r.get("status") or "").lower()
        sent = _parse_ts(r.get("timestamp"))
        due = _parse_ts(r.get("due_at"))
        answered = _parse_ts(r.get("responded_at"))
        overdue = st == "pending" and due is not None and now > due
        rsec = round(answered - sent, 1) if (answered is not None and sent is not None) else None
        cid = str(r.get("client_id") or "")
        m = per.setdefault(cid, {"client_id": cid, "online": cid in online, "sent": 0, "completed": 0,
                                 "issue": 0, "pending": 0, "overdue": 0, "_resp": []})
        totals["sent"] += 1
        m["sent"] += 1
        key = st if st in totals else "pending"
        totals[key] += 1
        if key in m:
            m[key] += 1
        if overdue:
            totals["overdue"] += 1
            m["overdue"] += 1
        if rsec is not None and st in _RESPONSE_STATUSES:
            resp.append(rsec)
            m["_resp"].append(rsec)
        items.append({**r, "status": st, "overdue": overdue, "response_s": rsec,
                      "online": cid in online,
                      "due_in_s": (round(due - now) if (st == "pending" and due is not None) else None)})

    def rate(c: int, i: int, o: int) -> Optional[float]:
        d = c + i + o
        return round(c * 100.0 / d, 1) if d else None

    def avg(v: List[float]) -> Optional[float]:
        return round(sum(v) / len(v), 1) if v else None

    machines = []
    for m in per.values():
        r_ = m.pop("_resp")
        m["avg_response_s"] = avg(r_)
        m["completion_rate"] = rate(m["completed"], m["issue"], m["overdue"])
        machines.append(m)
    machines.sort(key=lambda x: (-x["overdue"], -x["issue"], -x["sent"]))
    resp.sort()
    return {
        "totals": {**totals, "completion_rate": rate(totals["completed"], totals["issue"], totals["overdue"]),
                   "avg_response_s": avg(resp),
                   "median_response_s": (resp[len(resp) // 2] if resp else None)},
        "machines": machines,
        "tasks": items,
        "generated_at": now,
    }


class TaskManager:
    """
    Lightweight, high-speed task coordinator and KPI tracker.
    Prioritizes < 0.5s round-trip latency without database overhead.
    """

    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self.csv_path = csv_path or _KPI_CSV_PATH
        # In-memory RAM cache for pending and recent tasks: task_id -> dict
        self._pending_tasks: Dict[str, Dict[str, Any]] = {}
        self._recent_history: List[Dict[str, Any]] = []
        # Callback to trigger TTS notification on Xiaozhi audio nodes
        self._tts_notifier: Optional[Callable[[str], Any]] = None
        self._init_csv()

    def _init_csv(self) -> None:
        """Ensure logs directory and kpi_logs.csv exist with headers."""
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
                with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(_CSV_HEADERS)
                logger.info("Đã khởi tạo file nhật ký KPI: %s", self.csv_path)
        except Exception as exc:
            logger.error("Lỗi khởi tạo file KPI CSV: %s", exc)

    def set_tts_notifier(self, notifier_func: Callable[[str], Any]) -> None:
        """Register a callback to broadcast TTS voice announcements."""
        self._tts_notifier = notifier_func

    # ------------------------------------------------------------------
    # Dispatching Tasks
    # ------------------------------------------------------------------

    async def dispatch_task(
        self,
        client_id: str,
        message: str,
        sender: str = "Ban Giám Đốc",
        timeout: float = 300.0,
        *,
        dispatched_by: Optional[str] = None,
        due_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Gửi popup nhắc việc tới một máy trạm. `sender` là nhãn hiển thị trên popup;
        `dispatched_by` là tài khoản thật đã giao (lưu + audit). `due_minutes`: hạn
        phản hồi — quá hạn mà máy chưa trả lời thì bảng giám sát báo "quá hạn".
        Gửi lỗi -> task ghi "failed" (trước đây treo "pending" mãi mãi).
        """
        from mateai.interfaces.websocket.client_orchestrator import orchestrator

        if not orchestrator.is_client_online(client_id):
            return {
                "status": "error",
                "client_id": client_id,
                "message": f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
            }

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        now_ts = time.time()
        now_iso = datetime.now().strftime(_TS)
        due_at = (datetime.fromtimestamp(now_ts + int(due_minutes) * 60).strftime(_TS)
                  if due_minutes else None)

        self._pending_tasks[task_id] = {
            "task_id": task_id, "client_id": client_id, "message": message, "sender": sender,
            "status": "pending", "dispatched_at": now_ts, "dispatched_at_iso": now_iso,
        }
        try:
            db_manager.add_or_update_task({
                "task_id": task_id, "timestamp": now_iso, "client_id": client_id,
                "task_message": message, "sender": sender, "status": "pending",
                "dispatched_by": dispatched_by or "ai", "due_at": due_at,
            })
        except Exception as dbe:
            logger.warning("TaskManager: Lỗi lưu task vào SQLite: %s", dbe)

        try:
            await self._push_popup(client_id, task_id, message, sender, now_ts)
        except Exception as exc:
            logger.error("Lỗi khi gửi task tới máy trạm [%s]: %s", client_id, exc)
            self._pending_tasks.pop(task_id, None)
            self._close(task_id, "failed", note=f"Gửi popup lỗi: {exc}")
            return {
                "status": "error",
                "client_id": client_id,
                "message": f"Lỗi gửi tin nhắn qua WebSocket tới máy trạm '{client_id}': {exc}",
            }

        logger.info("TaskManager: Đã đẩy nhắc việc tới [%s] (Task ID: %s)", client_id, task_id)
        _notify_portal(task_id, client_id, "pending")
        return {
            "status": "success",
            "task_id": task_id,
            "client_id": client_id,
            "message": f"Đã gửi nhiệm vụ tới máy trạm '{client_id}' thành công.",
            "dispatched_at": now_iso,
            "due_at": due_at,
        }

    async def _push_popup(self, client_id: str, task_id: str, message: str, sender: str, ts: float) -> None:
        import json
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        session = orchestrator._clients.get(client_id)
        if not session or not session.get("websocket"):
            raise ConnectionError(f"Phiên kết nối của máy trạm '{client_id}' không tồn tại.")
        await session["websocket"].send_text(json.dumps({
            "action": "task_popup", "task_id": task_id, "message": message,
            "sender": sender, "timestamp": ts,
        }, ensure_ascii=False))

    async def remind_task(self, task_id: str) -> Dict[str, Any]:
        """Gửi lại popup của một việc còn chờ (cùng task_id — máy trả lời lúc nào cũng được tính)."""
        task = db_manager.get_task(task_id)
        if not task or str(task.get("status")).lower() != "pending":
            return {"status": "error", "message": "Chỉ nhắc lại được việc đang chờ phản hồi."}
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        cid = str(task.get("client_id"))
        if not orchestrator.is_client_online(cid):
            return {"status": "error", "message": f"Máy trạm '{cid}' đang ngoại tuyến."}
        try:
            await self._push_popup(cid, task_id, str(task.get("task_message") or ""),
                                   str(task.get("sender") or ""), time.time())
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"Gửi lại lỗi: {exc}"}
        return {"status": "success", "message": f"Đã nhắc lại việc trên máy '{cid}'."}

    def cancel_task(self, task_id: str, by: str) -> Dict[str, Any]:
        """Huỷ việc còn chờ. Phản hồi muộn của máy trạm sau đó bị bỏ qua."""
        if not self._close(task_id, "cancelled", note=f"Huỷ bởi {by}"):
            return {"status": "error", "message": "Chỉ huỷ được việc đang chờ phản hồi."}
        self._pending_tasks.pop(task_id, None)
        task = db_manager.get_task(task_id) or {}
        _notify_portal(task_id, str(task.get("client_id") or ""), "cancelled")
        return {"status": "success", "message": "Đã huỷ việc."}

    @staticmethod
    def _close(task_id: str, status: str, **kw: Any) -> bool:
        try:
            return db_manager.close_pending_task(task_id, status, **kw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Không cập nhật được task %s -> %s: %s", task_id, status, exc)
            return False

    # ------------------------------------------------------------------
    # Handling Responses & Recording KPI
    # ------------------------------------------------------------------

    def handle_task_response(
        self,
        client_id: str,
        task_id: str,
        status: str,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Phản hồi của máy trạm ('completed' / 'issue').

        Chỉ nhận khi task_id là việc ĐANG CHỜ đã giao cho CHÍNH máy này. Trước đây
        mọi task_id đều được ghi: một máy trạm có thể tự bịa đầu việc "hoàn thành"
        vào KPI, hoặc đóng việc của máy khác; trạng thái là chuỗi tuỳ ý từ máy trạm.
        """
        st = str(status or "").strip().lower()
        timestamp_iso = datetime.now().strftime(_TS)
        if st == "dismissed":
            # Nhân viên đóng popup không chọn: việc VẪN CHỜ (Portal thấy ghi chú, nhắc lại được).
            if self._close(task_id, "pending", client_id=client_id,
                           note=f"Nhân viên đóng cửa sổ lúc {timestamp_iso} — chưa trả lời"):
                _notify_portal(task_id, client_id, "dismissed")
                return {"status": "dismissed", "task_id": task_id, "client_id": client_id}
            return {"status": "ignored", "task_id": task_id, "client_id": client_id}
        st = st if st in _RESPONSE_STATUSES else "issue"
        if not self._close(task_id, st, client_id=client_id, responded_at=timestamp_iso,
                           note=(str(error)[:300] if error else None)):
            logger.warning("Bỏ qua phản hồi task [%s] từ [%s]: không phải việc đang chờ của máy này.",
                           task_id, client_id)
            return {"status": "ignored", "task_id": task_id, "client_id": client_id}

        task_info = self._pending_tasks.pop(task_id, {})
        task = db_manager.get_task(task_id) or {}
        task_msg = str(task.get("task_message") or task_info.get("message") or message or "Công việc được giao")

        record = {"timestamp": timestamp_iso, "client_id": client_id, "task_id": task_id,
                  "task_message": task_msg, "status": st}
        self._recent_history.insert(0, record)
        del self._recent_history[200:]

        # Nguồn KPI là bảng `tasks` (status + responded_at, ghi trong giao dịch ở _close). Trước 2026-10-14 còn ghi song song
        # logs/kpi_logs.csv — tệp phẳng không giao dịch, không phân quyền (thẩm định thương mại §6 "Không đạt").

        if st == "completed":
            announcement = f"Báo cáo Sếp, máy {client_id} vừa báo cáo đã hoàn thành công việc: {task_msg}."
        else:
            announcement = f"Cảnh báo, máy {client_id} báo cáo đang gặp vướng mắc với công việc: {task_msg}."
        if self._tts_notifier:
            try:
                self._tts_notifier(announcement)
            except Exception as exc:
                logger.warning("Không thể phát TTS thông báo KPI: %s", exc)
        _notify_portal(task_id, client_id, st)

        return {
            "status": "success",
            "task_id": task_id,
            "client_id": client_id,
            "kpi_status": st,
            "timestamp": timestamp_iso,
            "announcement": announcement,
        }

    # ------------------------------------------------------------------
    # Bảng giám sát (Portal #tasks)
    # ------------------------------------------------------------------

    def board(self, days: int = 30, now: Optional[float] = None, online: Optional[set] = None) -> Dict[str, Any]:
        """Số liệu THẬT của các việc giao cho máy trạm trong `days` ngày gần nhất."""
        return build_board(db_manager.list_micro_tasks(
            since=datetime.fromtimestamp((now or time.time()) - days * 86400).strftime(_TS)),
            now=now, online=online)

    # ------------------------------------------------------------------
    # KPI Analytics & Summaries
    # ------------------------------------------------------------------

    def get_kpi_logs(
        self,
        limit: int = 100,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Đọc nhật ký công việc thực tế từ SQLite database (hoặc kpi_logs.csv) và tính toán KPI metrics.
        """
        try:
            # 1. Thử lấy từ SQLite database
            db_tasks = db_manager.get_tasks(limit=limit, client_id=client_id, status=status)
            if db_tasks:
                counts = db_manager.count_tasks()
                total = counts.get("total", len(db_tasks))
                completed = counts.get("completed", 0)
                issues = counts.get("issues", 0)
                rate = round((completed / total * 100), 1) if total > 0 else 0.0
                return {
                    "status": "success",
                    "source": "sqlite",
                    "total": total,
                    "completed": completed,
                    "issue": issues,
                    "completion_rate": rate,
                    "logs": db_tasks,
                }
        except Exception as dbe:
            logger.warning("Không thể đọc tasks từ SQLite, chuyển sang đọc CSV: %s", dbe)

        # 2. Fallback sang đọc file logs/kpi_logs.csv
        logs: List[Dict[str, str]] = []
        total_tasks = 0
        completed_count = 0
        issue_count = 0

        if not self.csv_path.exists():
            return {
                "status": "success",
                "source": "empty",
                "total": 0,
                "completed": 0,
                "issue": 0,
                "completion_rate": 0.0,
                "logs": [],
            }

        try:
            with open(self.csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r_cid = row.get("Client_ID", "").strip()
                    r_status = row.get("Status", "").strip().lower()

                    if client_id and client_id.lower() != r_cid.lower():
                        continue
                    if status and status.lower() != r_status:
                        continue

                    total_tasks += 1
                    if r_status == "completed":
                        completed_count += 1
                    elif r_status in ("issue", "error"):
                        issue_count += 1

                    item = {
                        "timestamp": row.get("Timestamp", ""),
                        "client_id": r_cid,
                        "task_message": row.get("Task_Message", ""),
                        "status": r_status,
                    }
                    logs.append(item)
                    # Sync to SQLite
                    try:
                        db_manager.add_or_update_task(item)
                    except Exception:
                        pass

            logs.reverse()
            logs = logs[:limit]

            rate = round((completed_count / total_tasks * 100), 1) if total_tasks > 0 else 0.0

            return {
                "status": "success",
                "source": "csv",
                "total": total_tasks,
                "completed": completed_count,
                "issue": issue_count,
                "completion_rate": rate,
                "logs": logs,
            }
        except Exception as exc:
            logger.error("Lỗi đọc file kpi_logs.csv: %s", exc)
            return {
                "status": "error",
                "error": str(exc),
                "logs": [],
            }

    def summarize_monthly(
        self,
        client_id: Optional[str] = None,
        month: Optional[str] = None,
    ) -> str:
        """
        Calculate KPI completion counts for a specific month.
        Format of month: 'YYYY-MM' or 'MM' or string (default: current month).
        Returns a friendly Vietnamese text summary for the AI assistant.
        """
        now = datetime.now()
        target_month_prefix = ""

        if month:
            clean_m = "".join(c for c in month if c.isdigit())
            if len(clean_m) == 1:
                clean_m = f"0{clean_m}"
            if len(clean_m) == 2:
                target_month_prefix = f"{now.year}-{clean_m}"
            elif len(clean_m) >= 6:
                target_month_prefix = f"{clean_m[:4]}-{clean_m[4:6]}"
            else:
                target_month_prefix = month.strip()
        else:
            target_month_prefix = now.strftime("%Y-%m")

        completed_by_client: Dict[str, int] = {}
        issue_by_client: Dict[str, int] = {}
        total_completed = 0
        total_issues = 0

        try:
            for row in db_manager.task_responses_in_month(target_month_prefix):   # bảng tasks (trước: kpi_logs.csv)
                cid = str(row.get("client_id") or "").strip()
                st = str(row.get("status") or "").strip().lower()
                if client_id and client_id.lower() != cid.lower():
                    continue
                if st == "completed":
                    completed_by_client[cid] = completed_by_client.get(cid, 0) + 1
                    total_completed += 1
                elif st in ("issue", "error"):
                    issue_by_client[cid] = issue_by_client.get(cid, 0) + 1
                    total_issues += 1

            if client_id:
                cnt = completed_by_client.get(client_id, 0)
                iss = issue_by_client.get(client_id, 0)
                return (
                    f"Trong tháng {target_month_prefix}, máy trạm {client_id} đã hoàn thành "
                    f"{cnt} đầu việc (vướng mắc {iss} việc)."
                )

            if total_completed == 0 and total_issues == 0:
                return f"Hệ thống chưa ghi nhận đầu việc nào hoàn thành trong tháng {target_month_prefix}."

            lines = [f"Tổng hợp KPI tháng {target_month_prefix}: toàn hệ thống đã hoàn thành {total_completed} đầu việc ({total_issues} vướng mắc)."]
            for cid, c_cnt in completed_by_client.items():
                i_cnt = issue_by_client.get(cid, 0)
                lines.append(f"- Máy {cid}: hoàn thành {c_cnt} việc (vướng mắc: {i_cnt}).")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("Lỗi khi tổng hợp KPI tháng: %s", exc)
            return f"Không thể đọc dữ liệu KPI: {exc}"


# Module-level singleton
task_manager = TaskManager()
