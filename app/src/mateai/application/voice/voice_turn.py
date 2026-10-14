# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/voice_turn.py
==================
Xử lý MỘT lượt nói — implementation duy nhất cho mọi kênh voice (Phase 3).

Trước Phase 3 có 5 đường riêng (portal WS, HUD, ESP32/XiaoZhi, mic máy chủ,
REST), mỗi đường tự ghép lệnh nhanh / LLM / tool / TTS / lịch sử theo cách
riêng. Nay mỗi kênh chỉ còn phần transport + một `VoiceSink` (đầu ra), còn
nghiệp vụ nằm ở đây:

    lệnh nhanh (FastCommandRouter, không qua LLM)
      └─ không khớp →  câu đệm cho tác vụ cần tool (cache, trước khi gọi LLM)
                    →  LLMEngine.stream_voice_response
                         · trò chuyện: stream từng câu
                         · cần tool: vòng agent đầy đủ (ask_async) qua cổng
                           run_tool_with_policy (Zero-Trust, HITL, RBAC, audit)
                    →  StreamingTTSWorkerPipeline (TTS gối đầu, đúng thứ tự, huỷ được)
                    →  sink.on_audio(...)

Lịch sử hội thoại: memory_manager, khoá = session_id (ghi trong LLM layer cho
đường LLM, ghi ở đây cho lệnh nhanh).

Huỷ (barge-in): bên gọi chạy hàm này trong một task và `task.cancel()`; mọi
task TTS / lời đệm được dọn trong `finally`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class VoiceSink:
    """Đầu ra của một kênh. Mặc định không làm gì — kênh chỉ ghi đè cái cần."""

    async def on_status(self, status: str, **info: Any) -> None:
        """status: "thinking" | "speaking" | "done". `speaking` kèm reasoning khi có."""

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        """Một câu sẵn sàng (đã làm sạch). `display_text`: toàn bộ chữ để hiển thị tới lúc này."""

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        """Audio MP3 theo đúng thứ tự câu. kind: "speech" | "ack" | "filler".

        Với "speech", `audio` RỖNG khi TTS câu đó lỗi/timeout — vẫn được gọi để
        kênh hiển thị chữ đúng lúc (HUD: chữ bám theo tiếng)."""


@dataclass
class VoiceTurnResult:
    reply_text: str = ""
    display_text: str = ""
    reasoning: str = ""
    fast_command: Optional[str] = None
    used_agent: bool = False
    sentences: List[str] = field(default_factory=list)
    filler_played: bool = False
    pipeline_metrics: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    #: Từ vòng agent (khi lượt có gọi tool) — REST trả lại cho client.
    tool_calls_made: List[Dict[str, Any]] = field(default_factory=list)
    requires_confirmation: bool = False


# ── Đo độ trễ một lượt (Phase 1 realtime) ──────────────────────────────────
#
# Một bản trace cho MỌI kênh (portal, HUD, robot, mic máy chủ). Trước đây chỉ
# portal có trace (`realtime_voice_ws.VoiceRequestTrace`), và hai số chính bị
# đo sai nghĩa: "TTFT" là lúc câu ĐẦU được đọc chứ không phải token đầu của
# LLM; "TTFA" tính cả câu xác nhận từ cache (≈ 6 ms) nên che mất thời gian tới
# tiếng của CÂU TRẢ LỜI.
#
# Mốc (ms, tính từ lúc nhận lệnh; perf_counter — trên Windows monotonic chỉ
# phân giải 15,6 ms):
#   ttfd_ms              sự kiện trạng thái đầu tiên gửi ra kênh
#   router_ms            đã quyết định lệnh nhanh / không
#   ack_audio_ms         tiếng câu xác nhận / lời đệm đầu tiên
#   llm_first_token_ms   token (hoặc lời gọi tool) đầu tiên từ LLM
#   ttft_ms              câu chữ đầu tiên sẵn sàng hiển thị/đọc
#   ttfa_ms              tiếng đầu tiên bất kỳ (kể cả câu xác nhận)
#   ttfa_answer_ms       tiếng đầu tiên của CÂU TRẢ LỜI
#   agent_ms             thời gian vòng agent (tool) nếu có
#   ttl_ms               kết thúc lượt
# stt_ms do kênh có STT phía máy chủ (robot, mic) truyền vào.

_RECENT_TRACES: "deque[Dict[str, Any]]" = deque(maxlen=500)
_TRACES_LOADED = False


def _ensure_traces_loaded() -> None:
    """Nạp lại 500 lượt gần nhất từ DB lần đầu cần số đo (trước: chỉ RAM — số đo trống
    sau mỗi lần khởi động lại, đã gặp ngày 2026-10-05)."""
    global _TRACES_LOADED
    if _TRACES_LOADED:
        return
    _TRACES_LOADED = True
    try:
        from mateai.infrastructure.database.db_manager import db_manager
        saved = db_manager.recent_voice_traces(_RECENT_TRACES.maxlen or 500)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VoiceTrace] Không nạp được trace đã lưu: %s", exc)
        return
    current = list(_RECENT_TRACES)
    _RECENT_TRACES.clear()
    seen = {t.get("trace_id") for t in current}
    _RECENT_TRACES.extend([t for t in saved if t.get("trace_id") not in seen] + current)


def _persist_trace(data: Dict[str, Any]) -> None:
    def _write() -> None:
        try:
            from mateai.infrastructure.database.db_manager import db_manager
            db_manager.add_voice_trace(data)
        except Exception as exc:  # noqa: BLE001 — lưu trace hỏng không làm hỏng lượt thoại
            logger.warning("[VoiceTrace] Không lưu được trace: %s", exc)
    try:
        asyncio.get_running_loop().run_in_executor(None, _write)
    except RuntimeError:
        _write()


#: Mốc của lượt thoại hiện trên trang giám sát: tên mốc -> (nguồn, đích, nhãn).
#: "@channel" = thành phần của kênh (HUD / portal / robot …).
_TRACE_STEPS = {
    "router": ("@channel", "voice", "định tuyến (lệnh nhanh / LLM)"),
    "ack_audio": ("tts", "@channel", "câu xác nhận"),
    "first_text": ("llm", "voice", "LLM trả câu đầu"),
    "first_answer_audio": ("tts", "@channel", "tiếng câu trả lời đầu tiên"),
}


def _channel_node(channel: Optional[str]) -> str:
    """Tên kênh của lượt -> id thành phần trên sơ đồ topology."""
    c = str(channel or "")
    if c in ("hud", "portal", "telegram"):
        return c
    if c in ("web", "rest"):
        return "portal"
    if c in ("server_mic", "bench", ""):
        return "core"
    if c.startswith("telegram"):
        return "telegram"
    return f"robot:{c}"


def _topology(kind: str, **kw: Any) -> None:
    try:
        from mateai.application.operations.topology_events import publish
        publish(kind, **kw)
    except Exception:  # noqa: BLE001 — giám sát không được làm hỏng lượt thoại
        pass


@dataclass
class VoiceTurnTrace:
    session_id: str
    channel: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:12]}")
    stt_ms: Optional[float] = None
    t0: float = field(default_factory=time.perf_counter)
    marks: Dict[str, float] = field(default_factory=dict)
    statuses: List[str] = field(default_factory=list)
    tts_first_latency_ms: Optional[int] = None
    turn: Dict[str, Any] = field(default_factory=dict)

    def _ms(self, t: float) -> int:
        return int(round((t - self.t0) * 1000))

    def mark(self, name: str) -> None:
        """Ghi mốc lần ĐẦU tiên (các lần sau bỏ qua) và báo trang giám sát."""
        if name in self.marks:
            return
        self.marks[name] = time.perf_counter()
        step = _TRACE_STEPS.get(name)
        if step:
            source, target, label = step
            _topology(
                "turn", stage=name, trace_id=self.trace_id, channel=self.channel,
                source=_channel_node(self.channel) if source == "@channel" else source,
                target=_channel_node(self.channel) if target == "@channel" else target,
                ms=self._ms(self.marks[name]), detail=label,
            )

    def mark_status(self, status: str) -> None:
        self.mark("first_status")
        self.statuses.append(f"{status}@{self._ms(time.perf_counter())}ms")

    def finish(self, outcome: str, result: Optional["VoiceTurnResult"] = None) -> Dict[str, Any]:
        end = time.perf_counter()
        turn = self.turn or {}

        def at(name: str) -> Optional[int]:
            t = self.marks.get(name)
            return self._ms(t) if t is not None else None

        def turn_at(key: str) -> Optional[int]:
            t = turn.get(key)
            return self._ms(t) if isinstance(t, (int, float)) else None

        agent_start, agent_end = turn_at("agent_start_at"), turn_at("agent_end_at")
        data: Dict[str, Any] = {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "channel": self.channel,
            "outcome": outcome,
            "stt_ms": None if self.stt_ms is None else int(round(self.stt_ms)),
            "ttfd_ms": at("first_status"),
            "router_ms": at("router"),
            "ack_audio_ms": at("ack_audio"),
            "llm_first_token_ms": turn_at("llm_first_token_at"),
            "ttft_ms": at("first_text"),
            "ttfa_ms": at("first_audio"),
            "ttfa_answer_ms": at("first_answer_audio"),
            "agent_ms": (agent_end - agent_start) if agent_start is not None and agent_end is not None else None,
            "tts_first_latency_ms": self.tts_first_latency_ms,
            "ttl_ms": self._ms(end),
            "sentences": len(result.sentences) if result else 0,
            "fast_command": result.fast_command if result else None,
            "used_agent": bool(result.used_agent) if result else False,
            "prompt_chars": turn.get("prompt_chars"),
            "tools_offered": turn.get("tools_offered"),
            "system_chars": turn.get("system_chars"),
            "history_chars": turn.get("history_chars"),
            "tools_chars": turn.get("tools_chars"),
            "brain": turn.get("brain"),
            "model": turn.get("model"),
            "model_requested": turn.get("model_requested"),
            "agent_prefetched": turn.get("agent_prefetched"),
            "status_steps": list(self.statuses),
        }
        _ensure_traces_loaded()
        _RECENT_TRACES.append(data)
        _persist_trace(data)
        from mateai.infrastructure.observability.tracing import emit_voice_turn
        emit_voice_turn(data)            # OpenTelemetry (§93) — dựng lại từ số đo, không chạm hot path
        summary = " · ".join(p for p in (
            outcome,
            f"LLM {data['llm_first_token_ms']} ms" if data.get("llm_first_token_ms") is not None else "",
            f"tiếng đầu {data['ttfa_answer_ms']} ms" if data.get("ttfa_answer_ms") is not None else "",
            "có gọi tool" if data.get("used_agent") else "",
        ) if p)
        _topology("turn", stage="end", trace_id=self.trace_id, channel=self.channel,
                  node="voice", source="voice", target=_channel_node(self.channel),
                  ms=data.get("ttl_ms"), detail=summary,
                  status={"error": "error", "cancelled": "cancelled"}.get(outcome, "ok"))
        logger.info("[VoiceTrace] %s", json.dumps({k: v for k, v in data.items() if k != "status_steps"},
                                                   ensure_ascii=False))
        return data


def recent_traces(limit: int = 100, channel: Optional[str] = None) -> List[Dict[str, Any]]:
    """Các lượt gần nhất (mới nhất trước) — bộ đệm 500 lượt, nạp lại từ DB sau khởi động."""
    _ensure_traces_loaded()
    items = [t for t in reversed(_RECENT_TRACES) if channel is None or t.get("channel") == channel]
    return items[: max(1, limit)]


_TRACE_METRICS = ("stt_ms", "ttfd_ms", "router_ms", "ack_audio_ms", "llm_first_token_ms", "ttft_ms",
                  "ttfa_ms", "ttfa_answer_ms", "agent_ms", "tts_first_latency_ms", "ttl_ms")


def trace_stats(channel: Optional[str] = None) -> Dict[str, Any]:
    """p50 / p95 / p99 từng mốc trên các lượt gần nhất, tách theo kiểu lượt."""
    def pct(values: List[float], p: float) -> float:
        s = sorted(values)
        k = (len(s) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)

    rows = recent_traces(len(_RECENT_TRACES) or 1, channel)
    out: Dict[str, Any] = {"turns": len(rows), "by_outcome": {}}
    for outcome in sorted({r["outcome"] for r in rows}):
        group = [r for r in rows if r["outcome"] == outcome]
        metrics = {}
        for m in _TRACE_METRICS:
            vals = [r[m] for r in group if isinstance(r.get(m), (int, float))]
            if vals:
                metrics[m] = {"n": len(vals), "p50": pct(vals, .5), "p95": pct(vals, .95), "p99": pct(vals, .99)}
        out["by_outcome"][outcome] = {"turns": len(group), "metrics": metrics}
    return out


def model_stats() -> Dict[str, Any]:
    """Hiệu năng theo NÃO + MODEL thực tế trên các lượt gần nhất (bộ đệm 500 lượt):
    số lượt, lỗi, số lần model yêu cầu không trả lời phải chuyển dự phòng, p50/p95
    chữ đầu của LLM / tiếng trả lời đầu / cả lượt. Lượt lệnh nhanh (không gọi LLM) bỏ qua."""
    def pct(values: List[float], p: float) -> Optional[float]:
        if not values:
            return None
        s = sorted(values)
        k = (len(s) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)

    _ensure_traces_loaded()
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for t in _RECENT_TRACES:
        if not t.get("brain"):
            continue
        groups.setdefault((t["brain"], t.get("model") or t.get("model_requested") or "?"), []).append(t)
    rows = []
    for (brain, model), ts in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        def vals(k: str) -> List[float]:
            return [float(t[k]) for t in ts if isinstance(t.get(k), (int, float))]
        rows.append({
            "brain": brain, "model": model, "turns": len(ts),
            "errors": sum(1 for t in ts if t.get("outcome") == "error"),
            "fallbacks": sum(1 for t in ts if t.get("model") and t.get("model_requested")
                             and t["model"] != t["model_requested"]),
            "llm_first_token_ms": {"p50": pct(vals("llm_first_token_ms"), .5), "p95": pct(vals("llm_first_token_ms"), .95)},
            "ttfa_answer_ms": {"p50": pct(vals("ttfa_answer_ms"), .5), "p95": pct(vals("ttfa_answer_ms"), .95)},
            "ttl_ms": {"p50": pct(vals("ttl_ms"), .5), "p95": pct(vals("ttl_ms"), .95)},
        })
    return {"turns_total": len(_RECENT_TRACES), "rows": rows}


class _TracingSink(VoiceSink):
    """Bọc sink của kênh: chuyển tiếp nguyên vẹn, ghi mốc thời gian vào trace."""

    def __init__(self, inner: VoiceSink, trace: VoiceTurnTrace) -> None:
        self._inner = inner
        self._trace = trace

    def __getattr__(self, name: str) -> Any:  # thuộc tính riêng của sink kênh
        return getattr(self._inner, name)

    async def on_status(self, status: str, **info: Any) -> None:
        self._trace.mark_status("fast_path" if info.get("fast_path") and status == "speaking" else status)
        await self._inner.on_status(status, **info)

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        self._trace.mark("first_text")
        await self._inner.on_sentence(seq, text, display_text, **info)

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        if audio:
            self._trace.mark("first_audio")
            if kind in ("ack", "filler"):
                self._trace.mark("ack_audio")
            elif "first_answer_audio" not in self._trace.marks:
                self._trace.mark("first_answer_audio")
                if info.get("tts_latency_ms") is not None:
                    self._trace.tts_first_latency_ms = int(info["tts_latency_ms"])
        await self._inner.on_audio(seq, audio, text, kind, **info)


async def process_voice_turn(
    query: str,
    *,
    sink: VoiceSink,
    session_id: str,
    source_device: Optional[str] = None,
    caller: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    fast_path: bool = True,
    pre_ack: bool = True,
    filler_after_s: Optional[float] = None,
    filler_text: Optional[Callable[[str], str]] = None,
    request_id: Optional[str] = None,
    stt_ms: Optional[float] = None,
) -> VoiceTurnResult:
    """
    Xử lý một lượt nói và đẩy kết quả ra `sink`; mọi lượt được đo (VoiceTurnTrace)
    — kết quả đo ở `result.trace`, log `[VoiceTrace]` và `recent_traces()`.

    history=None → lấy từ memory_manager theo session_id.
    caller → danh tính RBAC/audit khi lượt cần chạy tool (mặc định source_device).
    pre_ack=False → không câu xác nhận nào: cả câu đầu lượt lẫn câu "để em xử
    lý" khi model gọi tool (REST: một phản hồi duy nhất; mic máy chủ: đã phát
    lời đệm riêng trước lượt).
    filler_after_s → phát một lời đệm nếu chưa có câu trả lời sau ngần ấy giây
    (HUD 1s, mic máy chủ 18s); filler_text(query) chọn câu, mặc định câu đệm
    theo ngữ cảnh. request_id → id do kênh đặt (portal); stt_ms → thời gian STT
    phía máy chủ (robot, mic).
    """
    # Mic máy chủ gọi với source_device=None (không đổi được: nó quyết định ngữ
    # cảnh thiết bị trong prompt và RBAC) — trace ghi tên kênh "server_mic".
    trace = VoiceTurnTrace(session_id=session_id, channel=source_device or "server_mic",
                           stt_ms=stt_ms, **({"request_id": request_id} if request_id else {}))
    _topology("turn", stage="start", trace_id=trace.trace_id, channel=trace.channel,
              source=_channel_node(trace.channel), target="voice", status="running",
              detail=f"nhận câu: {query}" + (f" (STT {round(stt_ms)} ms)" if stt_ms is not None else ""))
    result: Optional[VoiceTurnResult] = None
    outcome = "error"
    # Giới hạn tần suất (prompt cuối §97) — theo người / thiết bị; quá ngưỡng thì không chạy
    # pipeline (không LLM, không TTS), báo rõ thời gian chờ.
    from mateai.application.security import rate_limit
    wait = rate_limit.hit(f"voice:{caller or source_device or session_id}",
                          rate_limit.limit("voice_turns_per_min", 30), 60.0)
    if wait:
        msg = rate_limit.busy_message(wait)
        result = VoiceTurnResult(reply_text=msg, display_text=msg, sentences=[msg])
        try:
            await sink.on_sentence(0, msg, msg)
        finally:
            result.trace = trace.finish("rate_limited", result)
        return result
    try:
        result = await _run_voice_turn(
            query, sink=_TracingSink(sink, trace), trace=trace, session_id=session_id,
            source_device=source_device, caller=caller, history=history, fast_path=fast_path,
            pre_ack=pre_ack, filler_after_s=filler_after_s, filler_text=filler_text,
        )
        outcome = "fast_path" if result.fast_command else ("agent" if result.used_agent else "llm")
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        data = trace.finish(outcome, result)
        if result is not None:
            result.trace = data


async def _run_voice_turn(
    query: str,
    *,
    sink: VoiceSink,
    trace: VoiceTurnTrace,
    session_id: str,
    source_device: Optional[str] = None,
    caller: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    fast_path: bool = True,
    pre_ack: bool = True,
    filler_after_s: Optional[float] = None,
    filler_text: Optional[Callable[[str], str]] = None,
) -> VoiceTurnResult:
    """Thân một lượt nói (đã được `process_voice_turn` bọc đo đạc)."""
    from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine, _get_tts_voice
    from mateai.application.voice.speech_text import shorten_for_speech
    from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes

    result = VoiceTurnResult()
    t0 = time.perf_counter()

    # ── 1. Lệnh nhanh tất định ───────────────────────────────────────────
    if fast_path:
        from mateai.application.commands.fast_command_router import fast_command_router
        fast_res = await fast_command_router.dispatch(query, synthesize_audio=False)
        trace.mark("router")
        if fast_res and fast_res.is_matched:
            reply = fast_res.reply_text
            result.fast_command = fast_res.command_name
            result.reply_text = result.display_text = reply
            result.sentences = [reply]
            await sink.on_status("speaking", fast_path=True)
            await sink.on_sentence(1, reply, reply, fast_path=True)
            audio = get_cached_audio_bytes(reply) or await get_tts_engine().synthesise(reply)
            if audio:
                await sink.on_audio(1, audio, reply, "speech", fast_path=True)
            from mateai.application.conversation.memory_manager import memory_manager
            memory_manager.add_turn(session_id, query, reply)
            await sink.on_status("done", fast_path=True)
            logger.info("[VoiceTurn] Lệnh nhanh '%s' xong sau %.0fms", fast_res.command_name,
                        (time.perf_counter() - t0) * 1000)
            return result

    await sink.on_status("thinking")

    # ── 2. Câu đệm cho tác vụ cần tool (từ cache, trước khi gọi LLM) ───────
    # MỘT câu xác nhận mỗi lượt: câu đệm đầu lượt, lời đệm khi chậm và câu xác
    # nhận lúc model gọi tool dùng chung cờ `turn["acked"]`. Trước đây lời đệm
    # (sau 1s) và câu xác nhận khi gọi tool cùng phát — "em đang xử lý" hai lần.
    from mateai.application.agent.llm_engine import llm_engine
    turn: Dict[str, Any] = {}
    trace.turn = turn
    trace.mark("router")
    acked = False
    if pre_ack:
        intent = llm_engine.classify_intent(query)
        turn["intent"] = intent  # stream_voice_response dùng lại, không phân loại lần hai
        if intent.get("ack_needed"):
            from mateai.infrastructure.tts.acoustic_ack_catalog import select_acoustic_ack
            from mateai.infrastructure.tts.acoustic_ack import get_acoustic_ack_audio
            ack_phrase = select_acoustic_ack(query, domain=intent.get("target_brain"))
            ack_audio = await get_acoustic_ack_audio(phrase=ack_phrase)
            if ack_audio:
                await sink.on_audio(0, ack_audio, ack_phrase, "ack")
                acked = True
                turn["acked"] = True

    # ── 3. Lời đệm khi LLM chậm ───────────────────────────────────────────
    first_sentence = asyncio.Event()
    filler_task: Optional[asyncio.Task] = None

    async def _filler() -> None:
        await asyncio.sleep(filler_after_s or 0)
        if first_sentence.is_set():
            return
        if filler_text is not None:
            phrase = filler_text(query)
        else:
            from mateai.interfaces.desktop.voice_controller import get_contextual_filler
            phrase = get_contextual_filler(query)
        audio = get_cached_audio_bytes(phrase) or await get_tts_engine().synthesise(phrase)
        if audio and not first_sentence.is_set() and not turn.get("acked"):
            turn["acked"] = True
            result.filler_played = True
            await sink.on_audio(0, audio, phrase, "filler")
            logger.info("[VoiceTurn] Phát lời đệm sau %.1fs: %s", filler_after_s, phrase)

    if filler_after_s is not None and not acked:
        filler_task = asyncio.create_task(_filler())

    # ── 4. LLM (stream / agent) → TTS gối đầu → sink ─────────────────────
    from mateai.infrastructure.tts.tts_queue_pipeline import StreamingTTSWorkerPipeline
    pipeline = StreamingTTSWorkerPipeline(voice=_get_tts_voice(), num_workers=2)
    pipeline.start()

    # Lớp che thứ hai (lớp chính ở tool_gate): người không có quyền xem dữ liệu cá nhân → che SĐT / CCCD / STK / lương…
    # trong từng câu trước khi đọc / hiện / gửi — phòng dữ liệu tới AI theo đường khác công cụ (lịch sử, người dùng nói ra).
    from mateai.application.security.pii_guard import can_view_pii, mask_text
    _full_pii = can_view_pii(caller or source_device)

    async def _produce() -> None:
        seq = 0
        try:
            async for sentence in llm_engine.stream_voice_response(
                query=query,
                history=history,
                source_device=source_device,
                session_id=session_id,
                turn=turn,
                tool_ack=pre_ack and not acked,
                caller=caller,
            ):
                # Mảnh chỉ có dấu câu ("!", "--") không đọc được — TTS sẽ lỗi.
                if not sentence or not any(ch.isalnum() for ch in sentence):
                    continue
                if not first_sentence.is_set():
                    first_sentence.set()
                    if filler_task is not None and not filler_task.done():
                        filler_task.cancel()
                    await sink.on_status("speaking", reasoning=turn.get("reasoning", ""))
                seq += 1
                if not _full_pii:
                    sentence = mask_text(sentence)[0]
                result.sentences.append(sentence)
                display = turn.get("display_text") or " ".join(result.sentences)
                if not _full_pii:
                    display = mask_text(display)[0]
                    if turn.get("display_text"):
                        turn["display_text"] = display
                await sink.on_sentence(seq, sentence, display)
                # Câu dài (thường là kết quả vòng agent) được rút gọn khi ĐỌC —
                # chữ hiển thị vẫn đầy đủ. Trước Phase 3 chỉ ESP32/HUD làm vậy.
                await pipeline.push_sentence(
                    sequence=seq, text=shorten_for_speech(sentence), request_id=session_id,
                )
        finally:
            await pipeline.mark_complete(seq)

    async def _consume() -> None:
        async for item in pipeline.iterate_audio_results():
            await sink.on_audio(item.sequence, item.audio_bytes or b"", item.text, "speech",
                                tts_latency_ms=item.tts_latency_ms)

    try:
        await asyncio.gather(_produce(), _consume())
    finally:
        pipeline.cancel()
        if filler_task is not None and not filler_task.done():
            filler_task.cancel()

    result.reply_text = " ".join(result.sentences)
    result.display_text = turn.get("display_text") or result.reply_text
    from mateai.application.ethics.refusals import record_refusal
    record_refusal(result.reply_text, query, caller or source_device, channel=str(source_device or ""))
    result.reasoning = turn.get("reasoning", "")
    result.used_agent = bool(turn.get("used_agent"))
    result.tool_calls_made = list(turn.get("tool_calls_made") or [])
    result.requires_confirmation = bool(turn.get("requires_confirmation"))
    result.pipeline_metrics = dict(getattr(pipeline, "metrics", {}) or {})
    await sink.on_status("done")
    logger.info("[VoiceTurn] Xong lượt %s sau %.2fs (%d câu, agent=%s)", session_id,
                time.perf_counter() - t0, len(result.sentences), result.used_agent)
    return result
