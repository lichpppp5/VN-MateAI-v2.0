# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/skills/tool_readiness.py
===========================================
Công cụ chỉ đưa cho LLM khi NGUỒN DỮ LIỆU của nó đã cấu hình (thẩm định thương mại 14/10/2026 §7: phần lớn lượt thất bại
là AI gọi công cụ Prometheus / AWS / eInvoice… trên máy chưa khai báo nguồn — 7/7 mục "Cần chú ý" là thất bại).

  - `ready_reason(tool)`: "" khi dùng được, ngược lại lý do (nguồn nào chưa cấu hình).
  - `ready_tools(tools)`: lọc danh mục trước khi gửi LLM (llm_engine._enrich_tools_with_target_client — điểm chung mọi đường).
  - Kết quả kiểm tra lưu đệm `_TTL_S` giây: đọc cấu hình / registry nguồn không chạy ở mỗi lượt.

Công cụ không có trong `REQUIREMENTS` luôn sẵn sàng. Kịch bản (run_playbook) dùng `ready_reason` cho từng bước để từ chối
chạy khi nguồn của bước chưa cấu hình (báo cáo §5.3).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Iterable, List, Tuple

_TTL_S = 30.0


def _monitor(kind: str) -> Callable[[], str]:
    def check() -> str:
        from mateai.application.monitoring.infra_monitor import monitor_sources
        if any(s.get("monitor_type") == kind for s in monitor_sources(include_secrets=False)):
            return ""
        return f"chưa khai báo nguồn {kind.capitalize()} (Tích hợp hệ thống → Kết nối → thêm nguồn {kind})"
    return check


def _any_monitor() -> str:
    from mateai.application.monitoring.infra_monitor import monitor_sources
    return "" if monitor_sources(include_secrets=False) else \
        "chưa khai báo nguồn giám sát (Prometheus / Grafana) trong Tích hợp hệ thống"


def _connector(name: str) -> Callable[[], str]:
    def check() -> str:
        from mateai.infrastructure.connectors.base_connector import load_connector_settings, missing_required_fields
        cfg = load_connector_settings(name)
        if not bool(cfg.get("enabled", True)):
            return f"connector {name} đang tắt"
        missing = missing_required_fields(name)
        return f"connector {name} thiếu cấu hình: {', '.join(missing)}" if missing else ""
    return check


def _custom_sources() -> str:
    from mateai.infrastructure.connectors import custom_registry
    return "" if custom_registry.list_sources(include_secrets=False) else \
        "chưa có nguồn dữ liệu doanh nghiệp nào (Tích hợp hệ thống → Kết nối)"


def _telegram() -> str:
    from mateai.config.loader import settings
    tg = settings.telegram
    return "" if (getattr(tg, "enabled", False) and getattr(tg, "bot_token", "")) else "Telegram chưa bật hoặc chưa có token bot"


#: Công cụ → kiểm tra nguồn dữ liệu.
REQUIREMENTS: Dict[str, Callable[[], str]] = {
    "get_infra_status": _any_monitor, "get_infra_alerts": _any_monitor,
    "query_prometheus": _monitor("prometheus"), "list_grafana_dashboards": _monitor("grafana"),
    "check_aws_cost": _connector("aws"), "check_aws_instances": _connector("aws"),
    "check_oci_instances": _connector("oci"), "check_oci_metrics": _connector("oci"),
    "search_paperless_documents": _connector("paperless"), "get_paperless_document": _connector("paperless"),
    "download_paperless_document": _connector("paperless"),
    "check_einvoice_daily": _connector("einvoice"), "search_einvoices": _connector("einvoice"),
    "get_einvoice_details": _connector("einvoice"),
    "fetch_data_source": _custom_sources, "run_data_source_action": _custom_sources,
    "prepare_data_source_export": _custom_sources,
    "send_telegram_message": _telegram,
}

_cache: Dict[str, Tuple[float, str]] = {}
_lock = threading.Lock()


def ready_reason(tool: str) -> str:
    check = REQUIREMENTS.get(str(tool or ""))
    if check is None:
        return ""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(tool)
        if hit and now - hit[0] < _TTL_S:
            return hit[1]
    try:
        reason = check()
    except Exception as exc:  # noqa: BLE001 — không kiểm được thì không ẩn (tránh mất công cụ vì lỗi kiểm tra)
        reason = ""
        import logging
        logging.getLogger(__name__).debug("[ToolReadiness] Không kiểm được %s: %s", tool, exc)
    with _lock:
        _cache[tool] = (now, reason)
    return reason


def ready_tools(tools: Iterable[dict]) -> List[dict]:
    out = []
    for t in tools or []:
        name = ((t or {}).get("function") or {}).get("name") or (t or {}).get("name")
        if not ready_reason(str(name or "")):
            out.append(t)
    return out


def unready() -> Dict[str, str]:
    """Mọi công cụ đang bị ẩn và lý do — cho màn hình kỹ năng / chẩn đoán."""
    return {t: r for t in REQUIREMENTS if (r := ready_reason(t))}


def clear_cache() -> None:
    with _lock:
        _cache.clear()
