# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/agent/state_manager.py
=========================================
Ký ức ngắn hạn về tác vụ đã được DUYỆT và chạy xong.

Khi người dùng hỏi lại ("xong chưa?", "đồng ý" lần nữa) sau khi tác vụ đã
chạy, trợ lý cần biết kết quả để trả lời đúng. Mỗi bản ghi chỉ trả cho đúng
người đã yêu cầu tác vụ đó — trước đây nếu không khớp người hỏi thì trả về tác
vụ gần nhất của BẤT KỲ ai, kèm kết quả.

Hàng đợi chờ duyệt KHÔNG nằm ở đây: hàng đợi duy nhất là
`mateai.application.security.zero_trust.hitl_manager` (trước đây module này
giữ một hàng đợi thứ hai cho tác vụ từ hội thoại/portal).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Cửa sổ khôi phục tác vụ đã duyệt từ audit_logs khi khởi động.
_RESTORE_WINDOW_S = 7200.0
_MAX_COMPLETED = 20


class StateManager:
    """Ký ức tác vụ đã duyệt + chạy xong (tối đa 20, mới nhất trước)."""

    def __init__(self) -> None:
        self._completed_actions: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self.load_from_audit_logs()

    def load_from_audit_logs(self) -> None:
        """Nạp lại tác vụ đã được duyệt và thực thi trong 2 giờ qua (bản ghi HITL_APPROVED_*)."""
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            rows = erp_db.get_audit_logs(limit=500, action_type="HITL_APPROVED_")
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[StateManager] Lỗi khi nạp từ audit log: %s", exc)
            return

        now = time.time()
        restored: List[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row.get("payload") or "{}")
                ts = _parse_ts(row.get("timestamp"))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or not payload.get("executed") or now - ts > _RESTORE_WINDOW_S:
                continue
            ctx = payload.get("context") or {}
            tool = str(payload.get("action") or "")
            restored.append({
                "id": payload.get("approval_id"),
                "tool_name": tool,
                "arguments": payload.get("params") or {},
                "target_client": ctx.get("target_client") or "master",
                "query": ctx.get("query") or f"Lệnh {tool}",
                "user_id": payload.get("requested_by") or "",
                "source_device": ctx.get("source_device") or "",
                "result": payload.get("result"),
                "reply": f"Tác vụ '{tool}' đã được phê duyệt và hoàn tất.",
                "timestamp": ts,
            })
        with self._lock:
            self._completed_actions = sorted(restored, key=lambda a: a["timestamp"], reverse=True)[:_MAX_COMPLETED]

    def record_completed_action(self, action_data: Dict[str, Any], result: Any, reply: str = "") -> None:
        """Ghi nhận một tác vụ đã duyệt và chạy xong (`action_data` = `tool_gate.pending_view`)."""
        record = {
            "id": action_data.get("id"),
            "tool_name": action_data.get("tool_name"),
            "arguments": action_data.get("arguments"),
            "target_client": action_data.get("target_client", "master"),
            "query": action_data.get("query", ""),
            "user_id": action_data.get("user_id") or action_data.get("requested_by") or "",
            "source_device": action_data.get("source_device") or "",
            "result": result,
            "reply": reply,
            "timestamp": time.time(),
        }
        with self._lock:
            self._completed_actions.insert(0, record)
            self._completed_actions = self._completed_actions[:_MAX_COMPLETED]
        logger.info(
            "[StateManager] Ghi nhận tác vụ hoàn thành '%s' (query='%s', target='%s')",
            record.get("tool_name"), record.get("query"), record.get("target_client"),
        )

    def get_recent_completed_action(self, user_id: Optional[str] = None, max_age: float = 1800.0) -> Optional[Dict[str, Any]]:
        """Tác vụ hoàn tất gần nhất CỦA `user_id` (theo danh tính hoặc kênh) trong `max_age` giây."""
        if not user_id:
            return None
        now = time.time()
        with self._lock:
            for act in self._completed_actions:
                if now - act.get("timestamp", 0) > max_age:
                    continue
                if str(user_id) in (str(act.get("user_id") or ""), str(act.get("source_device") or "")):
                    return act
        return None

    def list_completed_actions(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._completed_actions[:limit])


def _parse_ts(value: Any) -> float:
    """`timestamp` của audit_logs (ISO, UTC) → epoch."""
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


state_manager = StateManager()
