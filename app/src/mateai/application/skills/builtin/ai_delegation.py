# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/ai_delegation.py
============================
Phase 40: Dual-LLM Orchestration (Gemini Router & Claude Specialist).

Cung cấp công cụ 'Hỏi chuyên gia' (delegate_to_specialist):
  - Cho phép Gemini (Lễ tân giao tiếp) ủy quyền các tác vụ phức tạp
    (phân tích log chuyên sâu, viết/sửa mã nguồn, tìm Root Cause, lập kế hoạch kiến trúc)
    cho Claude (Chuyên gia hệ thống cấp cao) thông qua 9router.
  - Kích hoạt câu đệm tức thì từ Audio Cache khi bắt đầu ủy quyền để tối ưu trải nghiệm đàm thoại.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict


logger = logging.getLogger(__name__)

# System prompt quy chuẩn cho Claude Chuyên gia
CLAUDE_SPECIALIST_SYSTEM_PROMPT = (
    "Bạn là Chuyên gia Hệ thống cấp cao (Claude). "
    "Nhiệm vụ của bạn là giải quyết các bài toán phức tạp do Lễ tân AI (Gemini) chuyển tới. "
    "Hãy phân tích sâu, viết code hoặc tìm Root Cause dựa trên ngữ cảnh được cấp. "
    "Trả lời trực tiếp vào vấn đề."
)

DELEGATION_FILLER_PHRASE = (
    "Ca này hơi sâu, anh chờ em một lát để em đẩy dữ liệu qua hệ thống phân tích chuyên sâu nhé."
)

# Robust export_skill decorator
try:
    from core.plugin_manager import export_skill
except Exception:
    def export_skill(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


def _get_llm_config() -> Dict[str, Any]:
    """
    Retrieve active LLM config (base_url, api_key, specialist_model, specialist_models).

    `specialist_models` (danh sách dự phòng) trước đây **không được trả về**,
    trong khi `analyse_and_plan()` và `_analyse_sync()` lại đọc
    `cfg.get("specialist_models")` — nên vòng lặp "thử model này hỏng thì thử
    model kia" thực ra chỉ có đúng **một** model, vòng dự phòng là ảo. Hàm
    bịa ra vẻ ngoài sức chịu lỗi mà thực tế không có.

    Nay trả cả danh sách, và chuẩn hoá *từng* phần tử (không chỉ model
    chính) để một model dự phòng viết sai tiền tố cũng không làm hỏng cả
    chuỗi dự phòng.
    """
    try:
        from mateai.config.loader import settings
        base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1")
        api_key = getattr(settings.llm, "api_key", "sk-dummy")
        specialist_model = (
            getattr(settings.llm, "specialist_model", None)
            or getattr(settings.llm, "model_name", "ag/claude-sonnet-4-6")
        )
        raw_models = getattr(settings.llm, "specialist_models", None)
    except Exception as exc:
        logger.warning("[AIDelegation] Could not read settings, falling back to defaults: %s", exc)
        base_url = "http://localhost:20128/v1"
        api_key = "sk-dummy"
        specialist_model = "ag/claude-sonnet-4-6"
        raw_models = None

    # Normalize base_url
    if base_url and not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    # Normalize specialist model name for 9router if needed
    if specialist_model and not specialist_model.startswith("ag/") and not specialist_model.startswith("mimo/"):
        # If user specified claude-3-5-sonnet-20240620 or claude-sonnet-4-6 without prefix on 9router
        if "claude" in specialist_model.lower():
            specialist_model = "ag/claude-sonnet-4-6"

    # Chuẩn hoá danh sách dự phòng: bỏ rỗng, khử trùng lặp, giữ thứ tự cấu hình
    # (model ưu tiên đứng đầu) và luôn đặt `specialist_model` ở vị trí đầu.
    models: list[str] = []
    for m in ([specialist_model] if specialist_model else []) + list(raw_models or []):
        if not isinstance(m, str) or not m.strip():
            continue
        m = m.strip()
        if m not in models:
            models.append(m)
    if not models:
        models = ["ag/claude-sonnet-4-6"]

    return {
        "base_url": base_url,
        "api_key": api_key,
        "specialist_model": specialist_model,
        "specialist_models": models,
    }


def trigger_delegation_reflex() -> None:
    """
    Phát âm thanh câu đệm từ Audio Cache và cập nhật HUD/Widget
    ngay khi quyết định ủy quyền cho Claude.
    """
    try:
        from mateai.interfaces.desktop.voice_controller import voice_controller
        if voice_controller:
            voice_controller.notify_delegation_started()
    except Exception as exc:
        logger.debug("[AIDelegation] Non-fatal voice reflex notification error: %s", exc)


async def delegate_to_specialist_async(
    task_description: str,
    context_data: str = "",
    target_client: str = "master",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Async implementation of specialist delegation via 9router (Claude).
    """
    t_start = time.monotonic()
    cfg = _get_llm_config()
    model = cfg["specialist_model"]
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]

    logger.info(
        "[AIDelegation] 🚀 Delegating task to Specialist Model '%s' via %s (target_client=%s)",
        model, base_url, target_client,
    )

    # Phát thông báo reflex tức thì
    trigger_delegation_reflex()

    # Xây dựng nội dung yêu cầu gửi Claude
    user_payload_parts = [
        f"### YÊU CẦU PHÂN TÍCH & XỬ LÝ CHUYÊN SÂU:\n{task_description.strip()}"
    ]
    if context_data and context_data.strip():
        user_payload_parts.append(f"### DỮ LIỆU & NGỮ CẢNH HỆ THỐNG ĐÍNH KÈM:\n{context_data.strip()}")
    if target_client and target_client.lower() not in ("master", "local", "cục bộ"):
        user_payload_parts.append(f"### MÁY TRẠM LIÊN QUAN:\n{target_client}")

    full_user_content = "\n\n".join(user_payload_parts)

    messages = [
        {"role": "system", "content": CLAUDE_SPECIALIST_SYSTEM_PROMPT},
        {"role": "user", "content": full_user_content},
    ]

    # Phase 5: thử model + nhớ model hỏng do provider chung đảm nhận
    # (mateai.infrastructure.llm.llm_provider). Client tạo trong event loop hiện tại vì
    # delegate_to_specialist() đồng bộ chạy hàm này trong một loop riêng.
    from mateai.infrastructure.llm.llm_provider import NineRouterLLMProvider, make_llm_client
    models = cfg["specialist_models"]
    # Phân tích chuyên sâu có thể lâu: timeout 180 s.
    client = make_llm_client(base_url, api_key, timeout=180.0, max_retries=1)
    provider = NineRouterLLMProvider(client, model, models)

    analysis_text = None
    used_model = model
    last_error = None
    try:
        resp = await provider.complete(
            messages=messages,
            temperature=0.2,  # Độ chính xác cao cho chẩn đoán mã nguồn / lỗi
            max_tokens=4096,
            brain_role="specialist",
            timeout=180.0,
            extra_body=None,
        )
        analysis_text = resp.choices[0].message.content or ""
        used_model = getattr(resp, "model", None) or model
    except Exception as e:
        last_error = e

    duration = round(time.monotonic() - t_start, 2)
    if analysis_text is not None:
        logger.info(
            "[AIDelegation] ✅ Specialist (%s) returned analysis in %.2fs (length=%d chars)",
            used_model, duration, len(analysis_text),
        )
        return {
            "status": "success",
            "specialist_model": used_model,
            "duration_seconds": duration,
            "task_description": task_description,
            "analysis": analysis_text,
            "result": analysis_text,
        }
    else:
        fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
        logger.error("[AIDelegation] ❌ Tất cả specialist model %s đều thất bại: %s", models, last_error)
        return {
            "status": "error",
            "specialist_model": "fallback",
            "duration_seconds": duration,
            "error": "ALL_SPECIALIST_MODELS_FAILED",
            "analysis": fallback_msg,
            "result": fallback_msg,
        }


@export_skill(
    name="delegate_to_specialist",
    description="Ủy quyền (delegate) bài toán phức tạp cho Chuyên gia Hệ thống cấp cao (Claude) phân tích sâu, viết mã nguồn, chẩn đoán nguyên nhân gốc rễ (Root Cause) hoặc lập kế hoạch kiến trúc.",
    parameters_schema={
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Mô tả chi tiết bài toán, lỗi hoặc yêu cầu kỹ thuật cần chuyên gia Claude xử lý.",
            },
            "context_data": {
                "type": "string",
                "description": "Ngữ cảnh chi tiết, file log, đoạn mã nguồn, cấu hình hoặc dữ liệu điều tra liên quan.",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm liên quan nếu có (mặc định: 'master').",
            },
        },
        "required": ["task_description"],
    },
)
def delegate_to_specialist(
    task_description: str,
    context_data: str = "",
    target_client: str = "master",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Synchronous entry point for delegate_to_specialist skill.
    Can be invoked from synchronous executor threads safely.
    """
    try:
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:  # thread worker: không có loop đang chạy
            on_loop = False
        if on_loop:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    delegate_to_specialist_async(
                        task_description=task_description,
                        context_data=context_data,
                        target_client=target_client,
                        **kwargs,
                    ),
                )
                return future.result(timeout=180.0)
        else:
            return asyncio.run(
                delegate_to_specialist_async(
                    task_description=task_description,
                    context_data=context_data,
                    target_client=target_client,
                    **kwargs,
                )
            )
    except Exception as exc:
        # Phase 5: bỏ vòng thử model thứ ba (client đồng bộ) — việc thử model và
        # nhớ model hỏng đã nằm trong provider chung mà bản async dùng.
        logger.error("[AIDelegation] Không chạy được ủy quyền chuyên gia: %s", exc)
        fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
        return {
            "status": "error",
            "specialist_model": "fallback",
            "error": "ALL_SPECIALIST_MODELS_FAILED",
            "analysis": fallback_msg,
            "result": fallback_msg,
        }
