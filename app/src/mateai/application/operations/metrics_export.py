# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/operations/metrics_export.py
===============================================
Xuất số đo của CHÍNH VN-MateAI theo định dạng văn bản Prometheus (`GET /metrics`) để Prometheus / Grafana của doanh nghiệp
giám sát lại hệ thống AI: tác vụ, hành động bị chặn, chờ duyệt, sự cố, token / chi phí, độ trễ giọng nói, phần cứng,
Dev Fleet, tình trạng giám sát hạ tầng.

Nguyên tắc: số nào không đo được thì KHÔNG xuất (không xuất 0 giả); một khối lỗi không làm mất cả lần thu thập
(`vnmateai_scrape_error{section}` = 1 cho khối lỗi). Chỉ đọc dữ liệu có sẵn; không gọi ra ngoài.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

Sample = Tuple[str, Dict[str, str], float]


def _esc(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        f = float(value)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


class _Registry:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self._declared: set = set()

    def add(self, name: str, help_: str, mtype: str, value: Any, **labels: Any) -> None:
        num = _num(value)
        if num is None:
            return
        if name not in self._declared:
            self._declared.add(name)
            self.lines += [f"# HELP {name} {help_}", f"# TYPE {name} {mtype}"]
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        self.lines.append(f"{name}{{{lab}}} {num:g}" if lab else f"{name} {num:g}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _section_core(r: _Registry) -> None:
    import psutil
    r.add("vnmateai_up", "1 khi tiến trình VN-MateAI đang phục vụ.", "gauge", 1)
    r.add("vnmateai_process_uptime_seconds", "Thời gian tiến trình đã chạy (giây).", "gauge", time.time() - psutil.Process().create_time())
    from mateai.config.loader import settings
    r.add("vnmateai_kill_switch", "1 khi công tắc dừng khẩn cấp AI đang bật (chỉ còn tác vụ chỉ đọc).", "gauge", settings.autonomy.kill_switch)
    r.add("vnmateai_disabled_agents", "Số tác nhân AI đang bị tắt.", "gauge", len(settings.autonomy.disabled_agents or []))


def _section_ledger(r: _Registry) -> None:
    from mateai.application.tasks import ledger
    from mateai.infrastructure.database.db_manager import db_manager
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    st = db_manager.op_stats(since)
    for status, n in (st.get("tasks_by_status") or {}).items():
        r.add("vnmateai_tasks_24h", "Tác vụ AI tạo trong 24 giờ qua, theo trạng thái.", "gauge", n, status=status)
    for kind, n in (st.get("tasks_by_kind") or {}).items():
        r.add("vnmateai_tasks_by_kind_24h", "Tác vụ AI trong 24 giờ qua, theo loại.", "gauge", n, kind=kind)
    for decision, n in (st.get("steps_by_decision") or {}).items():
        r.add("vnmateai_tool_actions_24h", "Hành động công cụ trong 24 giờ qua, theo quyết định chính sách.", "gauge", n, decision=decision)
    for ver, n in (st.get("steps_by_verification") or {}).items():
        r.add("vnmateai_tool_verifications_24h", "Kết quả kiểm chứng hành động trong 24 giờ qua.", "gauge", n, result=ver or "none")
    r.add("vnmateai_llm_tokens_24h", "Token LLM dùng trong 24 giờ qua.", "gauge", st.get("tokens"))
    r.add("vnmateai_llm_calls_24h", "Số lần gọi LLM trong 24 giờ qua.", "gauge", st.get("llm_calls"))
    r.add("vnmateai_llm_cost_24h", "Chi phí LLM ước tính 24 giờ qua (chỉ phần có đơn giá; thiếu thì không xuất).", "gauge", st.get("llm_cost"))
    r.add("vnmateai_llm_unpriced_tokens_24h", "Token chưa có đơn giá nên chưa tính vào chi phí.", "gauge", st.get("unpriced_tokens"))
    incidents = [t for t in ledger.list_tasks(kind="incident", limit=500) if t.get("status") not in ("COMPLETED", "FAILED", "CANCELLED")]
    r.add("vnmateai_incidents_open", "Sự cố đang mở (chưa đóng bằng bằng chứng).", "gauge", len(incidents))


def _section_approvals(r: _Registry) -> None:
    from mateai.application.security.zero_trust import hitl_manager
    pending = hitl_manager.get_pending_list()
    r.add("vnmateai_approvals_pending", "Yêu cầu duyệt (HITL) đang chờ người quyết định.", "gauge", len(pending))
    for level in sorted({int(p.get("risk_level") or 0) for p in pending}):
        r.add("vnmateai_approvals_pending_by_risk", "Yêu cầu duyệt đang chờ theo mức rủi ro.", "gauge",
              sum(1 for p in pending if int(p.get("risk_level") or 0) == level), risk=level)


def _section_voice(r: _Registry) -> None:
    from mateai.application.voice.voice_turn import trace_stats
    stats = trace_stats()
    r.add("vnmateai_voice_turns", "Số lượt thoại trong bộ đệm gần nhất.", "gauge", stats.get("turns"))
    for outcome, grp in (stats.get("by_outcome") or {}).items():
        r.add("vnmateai_voice_turns_by_outcome", "Lượt thoại theo kết cục.", "gauge", grp.get("turns"), outcome=outcome)
        for metric, q in (grp.get("metrics") or {}).items():
            for quant in ("p50", "p95"):
                r.add("vnmateai_voice_stage_ms", "Độ trễ từng mốc của lượt thoại (ms) theo phân vị.", "gauge", q.get(quant),
                      outcome=outcome, stage=metric, quantile=quant)


def _section_hardware(r: _Registry) -> None:
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
    hw = dict(SYSTEM_HEALTH_CACHE.get("hardware") or {})
    for key in ("cpu_percent", "ram_percent", "disk_percent"):
        r.add(f"vnmateai_host_{key}", "Tải của máy chủ chạy VN-MateAI (%), từ health_monitor.", "gauge", hw.get(key))


def _section_devfleet(r: _Registry) -> None:
    from mateai.infrastructure.database.db_manager import db_manager
    runs = db_manager.dev_list("dev_runs", limit=1000)
    counts: Dict[str, int] = {}
    for run in runs:
        counts[run["status"]] = counts.get(run["status"], 0) + 1
    for status, n in counts.items():
        r.add("vnmateai_devfleet_runs", "Lượt chạy Dev Fleet theo trạng thái (1000 lượt gần nhất).", "gauge", n, status=status)


def _section_monitoring(r: _Registry) -> None:
    from mateai.application.monitoring import infra_monitor
    snap = infra_monitor._cache.get("data")                 # chỉ dùng bản đã thu thập — scrape không kích hoạt gọi ra ngoài
    if not snap or not snap.get("configured"):
        return
    s = snap["summary"]
    for state in ("HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"):
        r.add("vnmateai_infra_health", "Tình trạng hạ tầng theo Prometheus/Grafana (1 ở trạng thái hiện tại).", "gauge",
              1 if s["health"] == state else 0, state=state.lower())
    r.add("vnmateai_infra_alerts_firing", "Cảnh báo hạ tầng đang bắn.", "gauge", s["alerts_firing"])
    r.add("vnmateai_infra_alerts_critical", "Cảnh báo hạ tầng mức nghiêm trọng đang bắn.", "gauge", s["alerts_critical"])
    r.add("vnmateai_infra_targets_down", "Target Prometheus không phản hồi.", "gauge", s["targets_down"])
    r.add("vnmateai_infra_sources_reachable", "Nguồn giám sát liên lạc được.", "gauge", s["sources_reachable"])


SECTIONS: List[Tuple[str, Callable[[_Registry], None]]] = [
    ("core", _section_core), ("ledger", _section_ledger), ("approvals", _section_approvals), ("voice", _section_voice),
    ("hardware", _section_hardware), ("devfleet", _section_devfleet), ("monitoring", _section_monitoring),
]


def render() -> str:
    reg = _Registry()
    t0 = time.perf_counter()
    for name, fn in SECTIONS:
        try:
            fn(reg)
            reg.add("vnmateai_scrape_error", "1 nếu khối số đo này lỗi trong lần thu thập vừa rồi.", "gauge", 0, section=name)
        except Exception as exc:  # noqa: BLE001 — một khối hỏng không làm mất cả lần thu thập
            logger.warning("[Metrics] khối %s lỗi: %s", name, exc)
            reg.add("vnmateai_scrape_error", "1 nếu khối số đo này lỗi trong lần thu thập vừa rồi.", "gauge", 1, section=name)
    reg.add("vnmateai_scrape_duration_seconds", "Thời gian thu thập số đo.", "gauge", time.perf_counter() - t0)
    return reg.text()
