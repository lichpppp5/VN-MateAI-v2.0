# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/llm_provider.py
====================
Phase 2: LLM Streaming & Provider Abstraction (Realtime Voice Revamp).

Kiến trúc:
  - BaseLLMProvider (ABC) định nghĩa interface chuẩn cho mọi Provider:
      async for chunk in provider.stream(messages, tools, ...):
          ...
      async for token in provider.stream_tokens(messages, tools, ...):
          ...
  - DirectLLMProvider: Kết nối trực tiếp (LM Studio / Ollama / DeepSeek / vLLM) bỏ qua proxy.
  - NineRouterLLMProvider: Kết nối qua 9router OpenAI-compatible proxy kèm Auto-Fallback thông minh.
  - TriBrainLLMProvider: Điều phối 3 Bộ Não (Supervisor, Voice, Operations) theo vai trò.
  - LLMStreamEventBus: Kênh phát sóng Token Stream đồng thời tới:
      (1) UI WebSocket (text_delta events)
      (2) SentenceBuffer / TTS Queue
"""

from __future__ import annotations

import abc
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple, Union

import openai

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models for Streaming
# ---------------------------------------------------------------------------

@dataclass
class LLMStreamChunk:
    """Đại diện cho một gói token hoặc tool call delta từ LLM."""
    content: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    model: str = ""
    role: str = "assistant"
    reasoning: Optional[str] = None
    ttft_ms: Optional[int] = None
    #: Token thật của cả lần stream (chunk cuối, khi nhà cung cấp hỗ trợ `stream_options.include_usage`).
    usage: Optional[Dict[str, int]] = None


def _stream_usage_option(kwargs_api: Dict[str, Any]) -> None:
    """Xin nhà cung cấp báo token ở cuối stream (chuẩn OpenAI). Tắt bằng `llmops.stream_usage` nếu router từ chối tham số này."""
    try:
        from mateai.config.loader import settings
        if settings.llmops.stream_usage:
            kwargs_api["stream_options"] = {"include_usage": True}
    except Exception:  # noqa: BLE001
        pass


def _chunk_usage(chunk: Any) -> Optional[Dict[str, int]]:
    u = getattr(chunk, "usage", None)
    if not u:
        return None
    pin, pout = int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)
    return {"prompt_tokens": pin, "completion_tokens": pout} if (pin or pout) else None


# ---------------------------------------------------------------------------
# Sức khoẻ model (dùng chung mọi provider / mọi phiên trong tiến trình)
# ---------------------------------------------------------------------------

#: Model lỗi / quá hạn bị xếp xuống cuối danh sách trong ngần ấy giây. Trước
#: Phase 5 mỗi lượt thử lại từ đầu cả danh sách: 6 model hỏng × tới 5s mỗi cái
#: = chữ đầu tiên sau ~22s (đo 2026-10-01).
MODEL_COOLDOWN_S = 120.0

#: Tổng thời gian tối đa cho chuỗi thử model dự phòng của MỘT lời gọi (realtime P5).
#: Bench 2026-10-03: lệnh vận hành p95 40,7 s — mọi model trong danh sách lần lượt
#: chờ 5 s rồi mới tới câu "quá tải". Hết ngân sách thì dừng, tầng trên trả lời dự phòng.
STREAM_FAILOVER_BUDGET_S = 12.0
COMPLETE_FAILOVER_BUDGET_S = 24.0
#: complete() (vòng agent): mỗi model được chờ tới ngần ấy giây, nhưng quá HEDGE_AFTER_S chưa xong thì gửi THÊM sang
#: model kế tiếp (không huỷ model đang chạy) và lấy kết quả về trước. Đo thật 2026-10-15 với 93 công cụ: model tốt trả
#: lời 2,5–9,5 s, model hết hạn mức treo tới 45 s — giới hạn cũ 8 s/model, 16 s/chuỗi cắt mất câu 9,5 s và để một model
#: treo ăn nửa ngân sách (đánh giá 14/10: 22/128 lượt hỏng cả chuỗi).
COMPLETE_MODEL_TIMEOUT_S = 20.0
HEDGE_AFTER_S = 6.0
#: Model nhà cung cấp báo "đã ngừng" không tự sống lại — xếp cuối lâu hơn để
#: mỗi lượt không mất thêm một vòng gọi vô ích (đo được: 3 model ngừng + timeout
#: làm một lượt REST mất 88 s). Vẫn thử lại sau đó phòng khi cấu hình đổi model.
MODEL_RETIRED_COOLDOWN_S = 3600.0
_model_down_until: Dict[str, float] = {}

#: Giá trị mẫu còn sót trong config (vd. YOUR_MODEL_NAME_HERE) — không phải model.
_PLACEHOLDER_RE = re.compile(r"^YOUR_[A-Z0-9_]*_HERE$", re.IGNORECASE)


# Một số cổng (9Router) báo model đã ngừng bằng một câu trả lời BÌNH THƯỜNG
# (HTTP 200), vd. "Gemini 3.5 Flash is no longer available. Please switch to
# ...". Không nhận ra thì câu đó được đọc nguyên văn cho người dùng. Chỉ so ở
# đầu câu trả lời và chỉ các cụm của nhà cung cấp (tiếng Anh).
_RETIRED_MODEL_RE = re.compile(
    r"\b(?:is|are|has been|have been) (?:no longer (?:available|supported)|deprecated|retired|discontinued)\b"
    r"|\bplease (?:switch|upgrade|migrate) to\b",
    re.IGNORECASE,
)
#: Số ký tự đầu của stream được giữ lại để kiểm tra trước khi phát ra.
RETIRED_PROBE_CHARS = 60


def looks_like_retired_model_reply(text: str) -> bool:
    """Câu trả lời là thông báo "model đã ngừng" của nhà cung cấp, không phải nội dung."""
    return bool(text) and bool(_RETIRED_MODEL_RE.search(text[:240]))


_QUOTA_RE = re.compile(r"RESOURCE_EXHAUSTED|QUOTA_EXHAUSTED|quota reached", re.IGNORECASE)
#: Cổng báo lúc model dùng lại được, vd. 9Router "Unavailable (reset after 122h 12m 17s)" / "reset after 2m".
_RESET_AFTER_RE = re.compile(r"reset(?:s)? (?:after|in) (?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.IGNORECASE)
#: Trần cho thời gian gác theo gợi ý của cổng — vẫn thử lại định kỳ phòng khi hạn mức được nâng / cấu hình đổi.
MODEL_RESET_HINT_MAX_S = 6 * 3600.0


def reset_hint_seconds(reason: Any) -> Optional[float]:
    m = _RESET_AFTER_RE.search(str(reason))
    if not m or not any(m.groups()):
        return None
    h, mi, se = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mi * 60 + se)


def _mark_model_failed(model: str, reason: Any, cooldown_s: float = MODEL_COOLDOWN_S) -> None:
    # Hết quota (vd. "Resets in 101h") không tự khỏi sau 120 s: xếp cuối lâu như model đã ngừng.
    if _QUOTA_RE.search(str(reason)):
        cooldown_s = max(cooldown_s, MODEL_RETIRED_COOLDOWN_S)
    # Cổng nói rõ khi nào dùng lại được: gác đúng chừng đó (đo 15/10: model chính hết hạn mức 122 h vẫn bị thử lại mỗi 2 phút).
    hint = reset_hint_seconds(reason)
    if hint is not None:
        cooldown_s = max(cooldown_s, min(hint, MODEL_RESET_HINT_MAX_S))
    _model_down_until[model] = time.monotonic() + cooldown_s
    logger.warning("[LLMProvider] Tạm xếp cuối model '%s' trong %.0fs: %s", model, cooldown_s, reason)


def _mark_model_ok(model: str) -> None:
    _model_down_until.pop(model, None)


MODEL_STATUSES = ("APPROVED", "EXPERIMENTAL", "DEPRECATED", "BLOCKED")


def model_status(model: str) -> str:
    """Trạng thái trong `llm.model_registry` (prompt cuối §79); chưa đăng ký -> UNREGISTERED."""
    try:
        from mateai.config.loader import settings
        entry = (getattr(settings.llm, "model_registry", None) or {}).get(str(model or "").strip()) or {}
    except Exception:  # noqa: BLE001
        entry = {}
    st = str((entry.get("status") if isinstance(entry, dict) else "") or "").upper()
    return st if st in MODEL_STATUSES else "UNREGISTERED"


def model_price(model: str) -> Optional[Tuple[float, float]]:
    """(giá / 1 triệu token vào, giá / 1 triệu token ra) khai trong `llm.model_registry`
    (`price_in_per_1m`, `price_out_per_1m`). Chưa khai -> None: không đoán giá."""
    try:
        from mateai.config.loader import settings
        entry = (getattr(settings.llm, "model_registry", None) or {}).get(str(model or "").strip()) or {}
        pin, pout = entry.get("price_in_per_1m"), entry.get("price_out_per_1m")
        if pin is None or pout is None:
            return None
        return float(pin), float(pout)
    except (AttributeError, TypeError, ValueError):
        return None


def estimate_cost(by_model: Dict[str, Dict[str, int]]) -> Tuple[Optional[float], int]:
    """Chi phí từ token THẬT theo từng model. Trả (tiền của phần có giá — None khi không phần nào
    có giá, số token thuộc model chưa khai giá). Không quy đổi phần chưa có giá."""
    cost: Optional[float] = None
    unpriced = 0
    for model, u in (by_model or {}).items():
        pin_out = model_price(model)
        tokens_in, tokens_out = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        if pin_out is None:
            unpriced += tokens_in + tokens_out
            continue
        cost = (cost or 0.0) + (tokens_in * pin_out[0] + tokens_out * pin_out[1]) / 1_000_000
    return (round(cost, 6) if cost is not None else None), unpriced


def model_health() -> Dict[str, float]:
    """Model đang bị xếp cuối -> số giây còn lại (cho chẩn đoán)."""
    now = time.monotonic()
    return {m: round(t - now, 1) for m, t in _model_down_until.items() if t > now}


# ---------------------------------------------------------------------------
# Abstract Base Provider
# ---------------------------------------------------------------------------

class BaseLLMProvider(abc.ABC):
    """Lớp cơ sở trừu tượng cho mọi LLM Provider."""

    @abc.abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream chunks từ model từng token một."""
        pass

    async def stream_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream chỉ lấy token văn bản thô (text delta) để phát ra loa hoặc render UI."""
        async for chunk in self.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        ):
            if chunk.content:
                yield chunk.content

    @abc.abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        """Thực hiện gọi non-streaming thông thường (khi cần chạy tool loop sâu)."""
        pass


# ---------------------------------------------------------------------------
# Direct Mode Provider (Local LM Studio / Ollama / Direct endpoints)
# ---------------------------------------------------------------------------

class DirectLLMProvider(BaseLLMProvider):
    """
    Gọi thẳng vào endpoint cục bộ (LM Studio, Ollama, vLLM) không qua 9router proxy.
    Giảm ~150-300ms độ trễ mạng cho chế độ Voice Realtime.
    """

    def __init__(self, direct_client: openai.AsyncOpenAI, direct_model: str, direct_url: str) -> None:
        self.client = direct_client
        self.model = direct_model
        self.url = direct_url

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        if model_status(self.model) == "BLOCKED":       # model registry §79
            raise RuntimeError(f"Model '{self.model}' bị BLOCKED trong model registry — không gọi.")
        from mateai.config import edition
        if not edition.model_allowed(self.model):
            raise RuntimeError(f"Model '{self.model}' là model miễn phí — bản thương mại không gửi dữ liệu tới.")
        kwargs_api: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        _stream_usage_option(kwargs_api)
        if tools:
            kwargs_api["tools"] = tools
            kwargs_api["tool_choice"] = "auto"

        logger.info("[DirectLLMProvider] Mở stream trực tiếp tới %s (model=%s)", self.url, self.model)
        t0 = time.monotonic()
        t_first_token: Optional[float] = None

        stream = await asyncio.wait_for(
            self.client.chat.completions.create(**kwargs_api),
            timeout=5.0,
        )

        async for chunk in stream:
            usage = _chunk_usage(chunk)
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                if usage:
                    yield LLMStreamChunk(model=self.model, usage=usage)
                continue

            delta = choice.delta
            token = getattr(delta, "content", "") or ""
            reasoning = getattr(delta, "reasoning", "") or getattr(delta, "reasoning_content", "") or None
            tool_calls = None
            if getattr(delta, "tool_calls", None):
                tool_calls = [
                    {
                        "index": tc.index,
                        "id": getattr(tc, "id", ""),
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                    }
                    for tc in delta.tool_calls
                ]

            ttft = None
            if token and t_first_token is None:
                t_first_token = time.monotonic()
                ttft = int((t_first_token - t0) * 1000)

            yield LLMStreamChunk(
                content=token,
                tool_calls=tool_calls,
                finish_reason=getattr(choice, "finish_reason", None),
                model=self.model,
                reasoning=reasoning,
                ttft_ms=ttft,
                usage=usage,
            )

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        if model_status(self.model) == "BLOCKED":       # model registry §79
            raise RuntimeError(f"Model '{self.model}' bị BLOCKED trong model registry — không gọi.")
        from mateai.config import edition
        if not edition.model_allowed(self.model):
            raise RuntimeError(f"Model '{self.model}' là model miễn phí — bản thương mại không gửi dữ liệu tới.")
        kwargs_api: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            kwargs_api["tools"] = tools
            kwargs_api["tool_choice"] = "auto"

        return await asyncio.wait_for(
            self.client.chat.completions.create(**kwargs_api),
            timeout=8.0,
        )


# ---------------------------------------------------------------------------
# 9Router Provider (OpenAI-compatible Multi-model Fallback)
# ---------------------------------------------------------------------------

class NineRouterLLMProvider(BaseLLMProvider):
    """
    Gọi qua 9router OpenAI-compatible proxy với danh sách dự phòng (Auto-Fallback).
    Tự động Fast-Failover trong 5s nếu model đầu tiên bị rate-limit hoặc nghẽn.
    """

    def __init__(self, client: openai.AsyncOpenAI, primary_model: str, router_models: List[str]) -> None:
        self.client = client
        self.primary_model = primary_model
        self.router_models = [m for m in router_models if m]

    def _resolve_candidate_models(self, preferred_model: Optional[str] = None) -> List[str]:
        """Thứ tự thử: model ưu tiên -> danh sách dự phòng; model vừa hỏng xếp cuối.
        Model registry (§79): BLOCKED bị loại hẳn, DEPRECATED xếp sau mọi model khác."""
        ordered: List[str] = []
        for m in [preferred_model or self.primary_model, *self.router_models]:
            clean = str(m or "").strip()
            if clean and clean not in ordered and not _PLACEHOLDER_RE.match(clean):
                ordered.append(clean)
        status = {m: model_status(m) for m in ordered}
        blocked = [m for m in ordered if status[m] == "BLOCKED"]
        if blocked:
            logger.warning("[ModelRegistry] Bỏ model BLOCKED khỏi danh sách thử: %s", blocked)
        ordered = [m for m in ordered if status[m] != "BLOCKED"]
        from mateai.config import edition
        free = [m for m in ordered if not edition.model_allowed(m)]
        if free:                                    # bản thương mại: không gửi dữ liệu khách hàng tới model miễn phí
            logger.warning("[Edition] Bỏ model miễn phí khỏi danh sách thử: %s", free)
            ordered = [m for m in ordered if edition.model_allowed(m)]
        now = time.monotonic()
        healthy = [m for m in ordered if _model_down_until.get(m, 0.0) <= now and status[m] != "DEPRECATED"]
        cooling = [m for m in ordered if _model_down_until.get(m, 0.0) > now and status[m] != "DEPRECATED"]
        return healthy + cooling + [m for m in ordered if status[m] == "DEPRECATED"]

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        models = self._resolve_candidate_models(kwargs.get("model"))
        if not models:
            raise RuntimeError("Không có model nào được phép gọi (chưa cấu hình, hoặc đều BLOCKED trong model registry).")

        stream = None
        used_model = models[0]
        last_err = None
        t0 = time.monotonic()
        primed: List[Any] = []

        for model_name in models:
            remaining = STREAM_FAILOVER_BUDGET_S - (time.monotonic() - t0)
            if remaining <= 0.5:
                last_err = TimeoutError(f"hết {STREAM_FAILOVER_BUDGET_S:.0f}s thử model dự phòng")
                logger.warning("[NineRouterLLMProvider] Dừng thử model: %s", last_err)
                break
            kwargs_api: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "extra_body": {"thinking": {"budget_tokens": 0}},
            }
            _stream_usage_option(kwargs_api)
            if tools:
                kwargs_api["tools"] = tools
                kwargs_api["tool_choice"] = "auto"

            try:
                logger.info("[NineRouterLLMProvider] Thử kết nối stream: role=%s, model=%s", brain_role, model_name)
                candidate = await asyncio.wait_for(
                    self.client.chat.completions.create(**kwargs_api),
                    timeout=min(5.0, remaining),
                )
                iterator = candidate.__aiter__()  # duyệt MỘT lần: đọc trước rồi đọc tiếp
                primed, retired_text = await self._probe_retired(iterator, candidate)
                if retired_text is not None:
                    last_err = RuntimeError(f"Model {model_name} đã ngừng: {retired_text[:120]}")
                    _mark_model_failed(model_name, f"đã ngừng: {retired_text[:120]}", MODEL_RETIRED_COOLDOWN_S)
                    continue
                stream = iterator
                used_model = model_name
                break
            except asyncio.TimeoutError:
                logger.warning("[NineRouter TIMEOUT] Model %s không phản hồi sau 5s → Fast Failover!", model_name)
                last_err = TimeoutError(f"Model {model_name} timed out after 5s")
                _mark_model_failed(model_name, "timeout 5s")
                continue
            except Exception as exc:
                logger.warning("[NineRouter FALLBACK] Model %s lỗi: %s → Thử model dự phòng...", model_name, exc)
                last_err = exc
                _mark_model_failed(model_name, exc)
                continue

        if stream is not None:
            _mark_model_ok(used_model)
        if stream is None:
            raise RuntimeError(f"Tất cả model {models} đều không phản hồi streaming: {last_err}")

        t_first_token: Optional[float] = None

        async def _chunks():
            for c in primed:
                yield c
            async for c in stream:  # cùng iterator đã đọc trước — không lặp lại chunk
                yield c

        async for chunk in _chunks():
            usage = _chunk_usage(chunk)
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                if usage:
                    yield LLMStreamChunk(model=used_model, usage=usage)
                continue

            delta = choice.delta
            token = getattr(delta, "content", "") or ""
            reasoning = getattr(delta, "reasoning", "") or getattr(delta, "reasoning_content", "") or None
            tool_calls = None
            if getattr(delta, "tool_calls", None):
                tool_calls = [
                    {
                        "index": tc.index,
                        "id": getattr(tc, "id", ""),
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                    }
                    for tc in delta.tool_calls
                ]

            ttft = None
            if token and t_first_token is None:
                t_first_token = time.monotonic()
                ttft = int((t_first_token - t0) * 1000)

            yield LLMStreamChunk(
                content=token,
                tool_calls=tool_calls,
                finish_reason=getattr(choice, "finish_reason", None),
                model=used_model,
                reasoning=reasoning,
                ttft_ms=ttft,
                usage=usage,
            )

    @staticmethod
    async def _probe_retired(iterator: Any, stream: Any) -> "tuple[List[Any], Optional[str]]":
        """
        Đọc trước vài chunk đầu (tới RETIRED_PROBE_CHARS ký tự, tool call, hoặc
        hết stream) để nhận ra câu "model đã ngừng". Trả (chunk đã đọc, text nếu
        là model đã ngừng). Chunk đã đọc được phát lại nguyên vẹn cho người gọi.
        """
        buffered: List[Any] = []
        text = ""
        while True:
            try:
                chunk = await iterator.__anext__()
            except StopAsyncIteration:
                break
            buffered.append(chunk)
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            if getattr(choice.delta, "tool_calls", None):
                return buffered, None
            text += getattr(choice.delta, "content", "") or ""
            if len(text) >= RETIRED_PROBE_CHARS or getattr(choice, "finish_reason", None):
                break
        if looks_like_retired_model_reply(text):
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # pylint: disable=broad-except
                    pass
            return buffered, text
        return buffered, None

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        models = self._resolve_candidate_models(kwargs.get("model"))
        if not models:
            raise RuntimeError("Không có model nào được phép gọi (chưa cấu hình, hoặc đều BLOCKED trong model registry).")
        # timeout / extra_body chỉnh được cho tác vụ dài (vd. chuyên gia phân tích sâu 180s) — khi đó thử LẦN LƯỢT
        # (không gửi song song một tác vụ đắt). Mặc định: gửi song song có trễ (HEDGE_AFTER_S), tắt "thinking".
        long_task = "timeout" in kwargs
        timeout_s = float(kwargs["timeout"]) if long_task else COMPLETE_MODEL_TIMEOUT_S
        hedge_after = timeout_s if long_task else HEDGE_AFTER_S
        extra_body = kwargs.get("extra_body", {"thinking": {"budget_tokens": 0}})
        budget_s = max(COMPLETE_FAILOVER_BUDGET_S, timeout_s)
        t0 = time.monotonic()
        last_err: Optional[BaseException] = None

        async def attempt(model_name: str) -> Any:
            kwargs_api: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            if extra_body:
                kwargs_api["extra_body"] = extra_body
            if tools:
                kwargs_api["tools"] = tools
                kwargs_api["tool_choice"] = "auto"
            remaining = budget_s - (time.monotonic() - t0)
            from mateai.infrastructure.observability.tracing import set_attrs, span
            with span("llm.complete", **{"llm.model": model_name, "llm.brain": brain_role}) as _sp:
                try:
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(**kwargs_api),
                        timeout=max(0.1, min(timeout_s, remaining)),
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Model {model_name} timed out after {timeout_s:.0f}s") from None
                _u = getattr(response, "usage", None)
                set_attrs(_sp, **{"llm.total_tokens": getattr(_u, "total_tokens", None)})
            if not getattr(response, "choices", None):
                # Cổng trả 200 nhưng không có câu trả lời (bộ lọc an toàn của nhà cung cấp…) — đánh giá 15/10: vòng agent
                # chết vì `choices[0]` trên None. Coi như model lỗi, thử model khác.
                raise RuntimeError(f"Model {model_name} trả về không có câu trả lời (choices rỗng)")
            msg = response.choices[0].message
            content = getattr(msg, "content", "") or ""
            if msg is not None and not getattr(msg, "tool_calls", None) and looks_like_retired_model_reply(content):
                raise _RetiredModel(f"Model {model_name} đã ngừng: {content[:120]}")
            return response

        queue = list(models)
        running: Dict["asyncio.Task[Any]", str] = {}
        try:
            while queue or running:
                remaining = budget_s - (time.monotonic() - t0)
                if remaining <= 0.5:
                    last_err = TimeoutError(f"hết {budget_s:.0f}s thử model dự phòng")
                    logger.warning("[NineRouterLLMProvider] Dừng thử model: %s", last_err)
                    break
                if queue and not running:
                    m = queue.pop(0)
                    running[asyncio.ensure_future(attempt(m))] = m
                done, _ = await asyncio.wait(running, timeout=min(hedge_after, remaining) if queue else remaining,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if queue:                    # model đang chạy chậm: gửi thêm sang model kế, vẫn chờ cả hai
                        m = queue.pop(0)
                        logger.info("[NineRouterLLMProvider] Quá %.0fs chưa xong — gửi song song thêm %s", hedge_after, m)
                        running[asyncio.ensure_future(attempt(m))] = m
                    continue
                for task in done:
                    model_name = running.pop(task)
                    exc = task.exception()
                    if exc is None:
                        _mark_model_ok(model_name)
                        return task.result()
                    last_err = exc
                    if isinstance(exc, _RetiredModel):
                        _mark_model_failed(model_name, str(exc).split(": ", 1)[-1], MODEL_RETIRED_COOLDOWN_S)
                    elif isinstance(exc, TimeoutError):
                        _mark_model_failed(model_name, f"timeout {timeout_s:.0f}s")
                    else:
                        _mark_model_failed(model_name, exc)
        finally:
            for task in running:                  # model thua / hết ngân sách: huỷ, không đánh dấu hỏng
                task.cancel()
        raise RuntimeError(f"Tất cả model {models} đều thất bại: {last_err}")


class _RetiredModel(RuntimeError):
    """Cổng trả lời bình thường nhưng nội dung báo model đã ngừng."""


def make_llm_client(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 60.0,
    max_retries: int = 0,
    http_client: Any = None,
) -> "openai.AsyncOpenAI":
    """MỘT nơi dựng client OpenAI-compatible (RULE-011, realtime P6 / D7).

    Trước đây llm_engine (2 chỗ), ai_delegation và màn hình chẩn đoán cấu hình
    tự dựng client với tham số riêng — timeout / pool có thể lệch nhau.
    """
    kwargs: Dict[str, Any] = {"base_url": base_url, "api_key": api_key,
                              "timeout": timeout, "max_retries": max_retries}
    if http_client is not None:
        kwargs["http_client"] = http_client
    return openai.AsyncOpenAI(**kwargs)


async def probe_model(client: Any, model: str, *, max_tokens: int = 20) -> str:
    """Một lời gọi thử ĐÚNG model này (không thử model dự phòng, không ghi nhận
    sức khoẻ model) — cho màn hình chẩn đoán cấu hình. Ném lỗi của API nếu hỏng."""
    res = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "1+1=? Trả lời số duy nhất."}],
        max_tokens=max_tokens,
        temperature=0.3,
        stream=False,
        extra_body={"thinking": {"budget_tokens": 0}},
    )
    return str(res.choices[0].message.content or "").strip()


async def complete_once(client: Any, model: str, messages: List[Dict[str, Any]], *,
                        max_tokens: int = 400, temperature: float = 0.6) -> str:
    """Một lượt trả lời (không tool, không stream, không thử model dự phòng) — cho
    màn hình "Thử trước khi lưu": phải thấy đúng model đang thử trả lời gì."""
    res = await client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=False,
    )
    return str(res.choices[0].message.content or "").strip()


def complete_text_blocking(
    base_url: str,
    api_key: str,
    models: List[str],
    messages: List[Dict[str, Any]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout: float = 60.0,
) -> "tuple[str, str]":
    """
    Cầu nối ĐỒNG BỘ tới provider chung cho code chạy trong thread worker
    (skill, MetaArchitect, analytics). Trả (nội dung, model đã trả lời); ném
    RuntimeError nếu mọi model đều hỏng. Dùng chung vòng thử model + trí nhớ
    model hỏng/đã ngừng của NineRouterLLMProvider — không tự tạo client riêng.

    Chỉ gọi từ thread KHÔNG có event loop đang chạy (xem plugin_manager.run_blocking).
    """
    async def _run() -> "tuple[str, str]":
        client = make_llm_client(base_url, api_key or "sk-dummy", timeout=timeout)
        try:
            provider = NineRouterLLMProvider(client, models[0], models[1:])
            resp = await provider.complete(
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                timeout=timeout, extra_body=None,
            )
        finally:
            await client.close()
        text = (resp.choices[0].message.content or "") if getattr(resp, "choices", None) else ""
        return text, getattr(resp, "model", None) or models[0]

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tri-Brain Dispatcher Provider (Phân luồng Bộ Não Chuyên Biệt)
# ---------------------------------------------------------------------------

class TriBrainLLMProvider(BaseLLMProvider):
    """
    Bộ điều phối Tri-Brain Provider cấp cao:
      - Quản lý DirectProvider hoặc NineRouterProvider dựa theo routing_mode ('direct', 'router', 'auto').
      - Phân luồng ý định (Supervisor Controller) sang đúng model của vai trò:
          * voice: Model giao tiếp siêu tốc, 0 tool overhead.
          * ops: Model chuyên gia hệ thống, nạp kỹ năng.
    """

    def __init__(self, direct_provider: Optional[DirectLLMProvider], router_provider: NineRouterLLMProvider) -> None:
        self.direct_provider = direct_provider
        self.router_provider = router_provider

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        routing_mode: str = "router",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        # Nếu cấu hình direct hoặc auto, thử direct trước
        if routing_mode in ("direct", "auto") and self.direct_provider:
            try:
                async for chunk in self.direct_provider.stream(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    brain_role=brain_role,
                    **kwargs,
                ):
                    yield chunk
                return
            except Exception as direct_err:
                logger.warning("[TriBrainProvider] Direct stream thất bại: %s → Fallback sang router", direct_err)
                if routing_mode == "direct":
                    raise

        # Sử dụng router provider
        async for chunk in self.router_provider.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        ):
            yield chunk

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        routing_mode: str = "router",
        **kwargs: Any,
    ) -> Any:
        if routing_mode in ("direct", "auto") and self.direct_provider:
            try:
                return await self.direct_provider.complete(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    brain_role=brain_role,
                    **kwargs,
                )
            except Exception as direct_err:
                logger.warning("[TriBrainProvider] Direct complete thất bại: %s → Fallback sang router", direct_err)
                if routing_mode == "direct":
                    raise

        return await self.router_provider.complete(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# LLM Stream Event Bus (Phát sóng Token Stream song song)
# ---------------------------------------------------------------------------

class LLMStreamEventBus:
    """
    Event Bus đa kênh cho LLM Token Stream:
      Token Stream từ LLM
           │
      ┌────┴────────────────────────┐
      ▼                             ▼
    Kênh 1: UI Stream             Kênh 2: Sentence Buffer
    (Phát text_delta ra WS)       (Gom câu chuẩn bị cho TTS)
    """

    def __init__(self) -> None:
        self._listeners: List[Callable[[str], Any]] = []

    def subscribe(self, callback: Callable[[str], Any]) -> None:
        """Đăng ký listener nhận token delta."""
        self._listeners.append(callback)

    async def publish_token(self, token: str) -> None:
        """Phát token tới tất cả listeners đồng thời."""
        if not token:
            return
        for listener in self._listeners:
            try:
                res = listener(token)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.debug("[EventBus] Lỗi listener: %s", exc)
