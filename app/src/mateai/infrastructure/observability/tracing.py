# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/observability/tracing.py
==============================================
Trace OpenTelemetry (prompt cuối §93). Một nơi dựng tracer + exporter:

  OTEL_EXPORTER = "none"    (mặc định) không xuất — span vẫn tạo được nhưng bị bỏ, gần như 0 chi phí
                  "console" in ra stdout (gỡ lỗi)
                  "otlp"    gửi tới collector (OTEL_EXPORTER_OTLP_ENDPOINT, mặc định localhost:4317)
                  "memory"  giữ trong RAM (test)

Đường thoại không đổi hot path: span con dựng LẠI từ các mốc `VoiceTurnTrace` đã đo
(`emit_voice_turn`). Tool và HTTP dùng span sống (`span(...)`).
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import Any, Dict, Iterator, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

logger = logging.getLogger(__name__)

_PROVIDER: Optional[TracerProvider] = None
_MEMORY_EXPORTER: Any = None
_TRACER_NAME = "vn-mateai"


def setup_tracing(exporter: Optional[str] = None) -> Any:
    """Dựng TracerProvider một lần (gọi lại với exporter khác thì thêm processor tương ứng).
    Trả exporter "memory" để test đọc span."""
    global _PROVIDER, _MEMORY_EXPORTER
    kind = (exporter or os.environ.get("VNMATEAI_OTEL_EXPORTER") or _configured() or "none").lower()
    if _PROVIDER is None:
        _PROVIDER = TracerProvider(resource=Resource.create({"service.name": "vn-mateai"}))
        trace.set_tracer_provider(_PROVIDER)
    if kind == "memory":
        if _MEMORY_EXPORTER is None:
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
            _MEMORY_EXPORTER = InMemorySpanExporter()
            _PROVIDER.add_span_processor(SimpleSpanProcessor(_MEMORY_EXPORTER))
        return _MEMORY_EXPORTER
    if kind == "console":
        _PROVIDER.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif kind == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        _PROVIDER.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        logger.info("[Tracing] Xuất trace OTLP tới %s", endpoint)
    return None


def _configured() -> str:
    try:
        from mateai.config.loader import settings
        return str(settings.OTEL_EXPORTER)
    except Exception:  # noqa: BLE001
        return "none"


def tracer() -> trace.Tracer:
    if _PROVIDER is None:
        setup_tracing()
    return trace.get_tracer(_TRACER_NAME)


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Span sống quanh một khối việc. Thuộc tính None bị bỏ (OTel không nhận None)."""
    with tracer().start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
        yield sp


def set_attrs(sp: Any, **attrs: Any) -> None:
    for k, v in attrs.items():
        if v is not None:
            sp.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))


#: (tên span con, mốc bắt đầu, mốc kết thúc) — ms tính từ đầu lượt, lấy từ VoiceTurnTrace.finish.
_VOICE_STAGES = (
    ("voice.routing", None, "router_ms"),
    ("voice.llm_first_token", None, "llm_first_token_ms"),
    ("voice.first_text", None, "ttft_ms"),
    ("voice.first_audio", None, "ttfa_answer_ms"),
)


def emit_voice_turn(data: Dict[str, Any]) -> None:
    """Dựng span `voice.turn` + span con từ số đo của một lượt đã xong."""
    try:
        ttl = data.get("ttl_ms") or 0
        end_ns = time.time_ns()
        start_ns = end_ns - int(ttl * 1_000_000)
        t = tracer()
        root = t.start_span("voice.turn", start_time=start_ns)
        set_attrs(root, **{f"voice.{k}": v for k, v in data.items()
                           if k != "status_steps" and isinstance(v, (str, int, float, bool))})
        ctx = trace.set_span_in_context(root)
        for name, _start, key in _VOICE_STAGES:
            ms = data.get(key)
            if isinstance(ms, (int, float)):
                child = t.start_span(name, context=ctx, start_time=start_ns)
                child.end(end_time=start_ns + int(ms * 1_000_000))
        if isinstance(data.get("agent_ms"), (int, float)):
            child = t.start_span("voice.agent", context=ctx, start_time=start_ns)
            child.end(end_time=min(end_ns, start_ns + int(data["agent_ms"] * 1_000_000)))
        root.end(end_time=end_ns)
    except Exception as exc:  # noqa: BLE001 — trace hỏng không làm hỏng lượt
        logger.debug("[Tracing] Không dựng được span lượt thoại: %s", exc)
