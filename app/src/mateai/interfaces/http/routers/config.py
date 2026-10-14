# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/config.py
=========================================
Cấu hình hệ thống: đọc (đã che bí mật) / ghi config.json, tên trợ lý, danh sách model của router, thử kết nối LLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.application.administration import config_service
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.http.secret_masking import (
    _SECRET_MASK,
    _is_secret_field,
    _mask_secrets,
    _restore_masked_secrets,
)
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class ConfigSaveResponse(BaseModel):
    """Response for POST /api/v1/config."""
    success: bool
    message: str


class LLMTestRequest(BaseModel):
    """Payload for POST /api/v1/llm/test."""
    base_url: Optional[str] = Field(default=None, description="URL proxy 9router (VD: http://localhost:20128/v1)")
    model_name: Optional[str] = Field(default=None, description="Tên mô hình cần kiểm tra")
    api_key: Optional[str] = Field(default=None, description="Khóa API")
    tier: Optional[str] = Field(default="primary", description="Tên cấp (backward compat)")
    provider_model: Optional[str] = Field(default=None, description="Tên mô hình (backward compat)")
    api_base: Optional[str] = Field(default=None, description="Base URL (backward compat)")


@router.get(
    "/api/v1/config/assistant-name",
    summary="Get current AI assistant name (Public lightweight)",
    tags=["Config"],
)
async def get_assistant_name_endpoint() -> Dict[str, str]:
    """Trả về tên định danh hiện tại của trợ lý AI (không yêu cầu token)."""
    return {"assistant_name": get_assistant_name()}


@router.post(
    "/api/v1/llm/test",
    summary="Kiểm tra kết nối và đo độ trễ mô hình LLM qua 9router",
    tags=["LLM Router"],
)
async def test_llm_endpoint(payload: LLMTestRequest, user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:

    """
    Kiểm tra kết nối mô hình LLM trực tiếp qua 9router bằng thư viện openai chuẩn.
    """
    import time
    from mateai.infrastructure.llm.llm_provider import make_llm_client, probe_model
    from mateai.config.loader import settings

    cfg_llm = getattr(settings, "llm", None)
    default_base = getattr(cfg_llm, "base_url", "") if cfg_llm else ""
    default_base = default_base or config_service.DEFAULT_BASE_URL
    default_model = (getattr(cfg_llm, "model_name", "") if cfg_llm else "") or ""
    default_key = getattr(cfg_llm, "api_key", "sk-dummy") if cfg_llm else "sk-dummy"

    base_url = (payload.base_url or payload.api_base or default_base).strip()
    model_name = (payload.model_name or payload.provider_model or default_model).strip()
    api_key = payload.api_key if payload.api_key is not None else default_key
    if not api_key or (isinstance(api_key, str) and api_key.strip() == _SECRET_MASK):
        # Đang thử URL direct (LM Studio / Ollama / DeepSeek) thì dùng khoá direct,
        # không gửi khoá 9Router sang một máy chủ khác.
        _norm = lambda u: (u or "").strip().rstrip("/").removesuffix("/v1")  # noqa: E731
        direct_url = getattr(cfg_llm, "direct_url", "") if cfg_llm else ""
        if direct_url and _norm(base_url) == _norm(direct_url):
            api_key = (getattr(cfg_llm, "direct_api_key", "") or "lm-studio")
        else:
            api_key = getattr(cfg_llm, "api_key", "sk-dummy") if cfg_llm else "sk-dummy"
    api_key = (api_key or "sk-dummy").strip()

    if not model_name:
        return {"success": False, "error": "Chưa chọn hoặc nhập tên mô hình."}

    start_time = time.perf_counter()
    client = make_llm_client(base_url, api_key, timeout=15.0)

    primary_error = None
    try:
        reply = await probe_model(client, model_name)
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "success": True,
            "fallback_triggered": False,
            "tier": payload.tier or "llm",
            "requested_model": model_name,
            "resolved_model": model_name,
            "reply": reply,
            "latency_ms": latency_ms,
            "message": f"Kết nối thành công! Phản hồi trong {latency_ms}ms.",
        }
    except Exception as exc:
        primary_error = str(exc)

    # ═════════════════════════════════════════════════════════════════════
    # Phase 46.3: Auto-Fallback Demonstration in Diagnostic Test
    # ═════════════════════════════════════════════════════════════════════
    # Phase 68: hỏi router thay vì ghi cứng. Danh sách ghi cứng từng khiến
    # màn hình chẩn đoán báo "dự phòng OK" cho những model không tồn tại.
    fallback_candidates = [m for m in await _router_model_pool() if m != model_name][:4]
    for fb_model in fallback_candidates:
        if fb_model == model_name:
            continue
        try:
            fb_start = time.perf_counter()
            reply = await probe_model(client, fb_model)
            latency_ms = int((time.perf_counter() - fb_start) * 1000)
            return {
                "success": True,
                "fallback_triggered": True,
                "tier": payload.tier or "llm",
                "requested_model": model_name,
                "resolved_model": fb_model,
                "primary_error": primary_error,
                "reply": reply,
                "latency_ms": latency_ms,
                "message": (
                    f"⚡ Auto-Fallback đã kích hoạt thành công!\n"
                    f"Model chính '{model_name}' gặp lỗi nhưng hệ thống đã tự động chuyển đổi sang '{fb_model}'."
                ),
            }
        except Exception:
            continue

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    err_msg = primary_error or "Tất cả mô hình đều không phản hồi."
    suggestion = None
    if "404" in err_msg or "not found" in err_msg.lower() or "no active credentials" in err_msg.lower():
        suggestion = (
            f"Model '{model_name}' không tìm thấy trên proxy 9router. "
            "Hãy kiểm tra lại danh sách model mà router đang phục vụ."
        )
    elif "401" in err_msg or "invalid" in err_msg.lower() or "api_key" in err_msg.lower():
        suggestion = "Khóa API không hợp lệ. Kiểm tra lại API Key trong 9router."
    elif "connection" in err_msg.lower() or "refused" in err_msg.lower() or "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        suggestion = f"Không thể kết nối tới {base_url}. Hãy chắc chắn rằng 9router đang chạy."
    elif "unsupported model" in err_msg.lower() or "400" in err_msg:
        suggestion = (
            f"Mô hình '{model_name}' không được nhà cung cấp hỗ trợ hoặc đã ngừng cung cấp. "
            "👉 Khuyên dùng: chọn một model trong danh sách router đang phục vụ."
        )
    return {
        "success": False,
        "fallback_triggered": False,
        "tier": payload.tier or "llm",
        "requested_model": model_name,
            "resolved_model": model_name,
            "error": err_msg,
            "suggestion": suggestion,
            "latency_ms": latency_ms,
        }


class ProxyModelsRequest(BaseModel):
    """Payload for POST /api/v1/llm/proxy-models."""
    base_url: str = Field(..., description="URL proxy, ví dụ http://localhost:20128/v1")
    api_key: Optional[str] = Field(default=None, description="API Key của proxy (nếu cần)")


@router.post(
    "/api/v1/llm/proxy-models",
    summary="Lấy danh sách model từ proxy OpenAI-compatible (9router/LMStudio/Ollama)",
    tags=["LLM Router"],
)
async def proxy_models_endpoint(
    payload: ProxyModelsRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Gọi {base_url}/models để lấy danh sách model từ proxy.
    Trả về danh sách id model khả dụng.
    """
    import httpx

    base = payload.base_url.rstrip("/")
    # Đảm bảo có /v1
    if not base.endswith("/v1"):
        base = base + "/v1"
    models_url = f"{base}/models"
    headers: Dict[str, str] = {"Accept": "application/json"}
    api_key = payload.api_key
    if not api_key or api_key == _SECRET_MASK:
        try:
            from mateai.config.loader import read_raw_config
            cfg_raw = read_raw_config(strict=True)
            api_key = cfg_raw.get("llm", {}).get("api_key") or cfg_raw.get("API_KEY") or None
        except Exception:
            api_key = None
    elif api_key:
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError:
            return {
                "success": False,
                "error": (
                    "Mã bảo mật có ký tự không hợp lệ (chữ tiếng Việt hoặc "
                    "ký hiệu che). Mã bảo mật chỉ gồm ký tự ASCII — thường "
                    "bắt đầu bằng sk-... . Để trống ô này nếu 9router không "
                    "yêu cầu mã."
                ),
                "models": [],
            }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(models_url, headers=headers)
        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"Proxy trả về HTTP {resp.status_code}: {resp.text[:200]}",
                "models": [],
            }
        data = resp.json()
        model_ids = [m["id"] for m in data.get("data", []) if "id" in m]
        return {
            "success": True,
            "count": len(model_ids),
            "models": model_ids,
        }
    except UnicodeEncodeError:
        # Chặn sẵn ở trên, nhưng để nguyên `except Exception` bên dưới thì lỗi
        # codec vẫn lọt ra khi chuỗi lọt qua chỗ khác (ví dụ URL có ký tự lạ).
        # Người dùng không biết sửa gì từ "'ascii' codec can't encode".
        return {
            "success": False,
            "error": (
                "Địa chỉ proxy hoặc mã bảo mật có ký tự không hợp lệ — "
                "HTTP header chỉ nhận ký tự ASCII. Kiểm tra lại ô địa chỉ và "
                "ô mã bảo mật."
            ),
            "models": [],
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Không thể kết nối proxy {base}: {exc}",
            "models": [],
        }


#: Giữ tên cũ (test + người gọi cũ) — nghiệp vụ ở config_service.
_deep_merge = config_service.deep_merge


@router.get(
    "/api/v1/config",
    summary="Read system configuration",
    tags=["Config"],
)
async def get_config(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """
    Read and return the current config.json as JSON.

    Trường bí mật (API key, bot token) KHÔNG trả giá trị thật — thay bằng
    `_SECRET_MASK`. Trước đây endpoint này trả khoá thật cho mọi tài khoản
    `manager`/`admin`, nên chỉ cần token của một tài khoản đó là đủ để lấy
    khoá 9router và token bot Telegram. Cần sửa bí mật thì nhập lại: ô trống
    hoặc ký hiệu khi Lưu nghĩa là giữ nguyên giá trị đang lưu
    (xem `_restore_masked_secrets`).

    Đảm bảo khối 'llm' và cờ 'auto_execute' luôn có mặt.
    """
    try:
        from mateai.config.loader import read_raw_config
        data = config_service.legacy_view(await run_blocking(lambda: read_raw_config(strict=True)))

        # Che bí mật ở CỬA CUỐI cùng: mọi nhánh tương thích ngược phía trên
        # (`API_KEY`, `routing.*`, `router.*`) đều nhân bản cùng một khoá ra
        # nhiều đường dẫn, và che từng nhánh thì dễ sót. Ở đây một lần là xong.
        return _mask_secrets({k: v for k, v in data.items() if not k.startswith("_")})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="config.json not found.")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"config.json is malformed: {exc}")


async def _router_model_pool() -> List[str]:
    """Model router đang phục vụ (dự phòng) — `config_service.router_model_pool`, chạy
    ngoài event loop. Giữ tên này: test thay nó để không gọi mạng."""
    return await config_service.router_model_pool()


@router.get(
    "/api/v1/config/models",
    summary="Danh sách model router đang phục vụ",
    tags=["Config"],
)
async def list_available_models(
    user: dict = Depends(require_roles(["admin", "operator"])),
) -> Dict[str, Any]:
    """
    Trả về model mà router thực sự phục vụ, để giao diện không phải ghi cứng.

    Trước đây danh sách gợi ý trong UI ghi cứng tên model của một provider.
    Khi provider đó hết tiền, giao diện vẫn hiện các nút bấm chết và người
    dùng bấm vào rồi mới biết là chết — trải nghiệm rất tệ. Nay danh sách lấy
    từ router mỗi lần mở, nên bấm là chạy.
    """
    from mateai.config.loader import settings
    models = await _router_model_pool()
    return {
        "models": models,
        "count": len(models),
        "base_url": (getattr(settings.llm, "base_url", "") if settings else "") or "",
    }


@router.post(
    "/api/v1/config",
    response_model=ConfigSaveResponse,
    summary="Save system configuration",
    tags=["Config"],
)
@router.put(
    "/api/v1/config",
    response_model=ConfigSaveResponse,
    include_in_schema=False,
)
async def save_config(
    payload: Dict[str, Any],
    user: dict = Depends(require_roles(["admin"])),
) -> ConfigSaveResponse:
    """
    Receive a config dict from the Web Portal, validate, and persist to config.json.
    Supports Thin Client 'llm' schema and legacy routing parameters.
    Re-initialises in-memory settings so changes take effect without a restart.
    """
    try:
        from mateai.config.loader import read_raw_config
        # strict: config.json hỏng thì báo lỗi, KHÔNG ghi đè bằng bản chỉ có payload.
        existing: Dict[str, Any] = await run_blocking(lambda: read_raw_config(strict=True))
        # Giao diện đã hỏi người dùng và họ vẫn muốn lưu model không có trên 9Router.
        force_models = bool(payload.pop("_force_models", False)) if isinstance(payload, dict) else False

        # Thay ký hiệu chỗ trống bằng giá trị đang lưu TRƯỚC KHI chuẩn hoá: ký hiệu
        # "••••••••" là chuỗi khác rỗng nên sẽ thắng nhánh `or existing...` và khoá thật
        # bị ghi đè bằng ký hiệu (người dùng chỉ bấm "Lưu" một trường khác là hỏng khoá LLM).
        payload = _restore_masked_secrets(payload, existing)

        pool = await _router_model_pool()
        try:
            merged, payload = config_service.prepare_save(payload, existing, pool, force_models)
        except config_service.ConfigInvalid as bad:
            raise HTTPException(status_code=400, detail=bad.detail)

        by = user.get("username") if isinstance(user, dict) else "api"
        await run_blocking(lambda: config_service.save(str(by), merged, "Lưu", _mask_secrets))
        logger.info("config.json updated via Web Portal.")

        await _apply_written_config(merged, payload)
        return ConfigSaveResponse(success=True, message="Cấu hình hệ thống và điểm nối 9router đã được lưu thành công.")

    except OSError as exc:
        logger.error("Failed to write config.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể ghi config.json: {exc}")


#: Giữ tham chiếu task làm nóng cache giọng đọc (asyncio chỉ giữ tham chiếu yếu — task có thể bị thu gom giữa chừng).
_WARMUP_TASKS: set = set()


async def _apply_written_config(merged: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Sau khi ghi config.json: nạp lại runtime, báo HUD, nạp lại connector / Telegram.
    Dùng chung cho Lưu và Khôi phục phiên bản."""
    # Hot-reload in-memory settings
    try:
        from mateai.config.loader import reload_settings
        reload_settings()
        # Broadcast updated assistant name to all connected HUD displays in real-time
        updated_ai_name = get_assistant_name()
        await broadcast_hud({
            "type": "assistant_name_updated",
            "assistant_name": updated_ai_name,
            "timestamp": datetime.utcnow().isoformat(),
        })
        # Broadcast updated audio config (TTS rate/voice/volume) so HUD applies immediately
        audio_block = merged.get("audio", {})
        tts_rate_top = merged.get("TTS_RATE", "+15%")
        tts_voice_top = merged.get("TTS_VOICE", "vi-VN-HoaiMyNeural")
        await broadcast_hud({
            "type": "audio_config_updated",
            "tts_voice": audio_block.get("tts_voice") or tts_voice_top,
            "tts_rate": tts_rate_top,
            "speech_rate_num": audio_block.get("speech_rate", 15),
            "volume": audio_block.get("volume", 80),
            "timestamp": datetime.utcnow().isoformat(),
        })
        # Đổi nguồn / giọng đọc (vd sang Vbee): làm nóng ngay các câu cố định (lời đệm, "Anh còn cần…", lời chào) bằng
        # GIỌNG MỚI — cache theo giọng, nếu không thì câu đầu của mỗi loại phải chờ tổng hợp (Vbee Batch ~3 s).
        if "audio" in payload:
            from mateai.application.voice.voice_session import FAREWELL_PHRASE, FOLLOW_UP_PHRASE, GOODBYE_PHRASE
            from mateai.infrastructure.tts.acoustic_ack import warmup_acoustic_ack_cache
            from asyncio import create_task as _create_task   # hàm này có `import asyncio` cục bộ phía dưới
            task = _create_task(warmup_acoustic_ack_cache(
                extra_phrases=(FOLLOW_UP_PHRASE, FAREWELL_PHRASE, GOODBYE_PHRASE)), name="tts-warmup-after-config")
            _WARMUP_TASKS.add(task)
            task.add_done_callback(_WARMUP_TASKS.discard)
    except Exception as hot_err:  # pylint: disable=broad-except
        logger.warning("Hot-reload settings failed (non-critical): %s", hot_err)

    # Phase 59: Nạp lại cấu hình cho connector nào vừa được sửa.
    # Không có bước này, thông số lưu từ giao diện chỉ nằm trong file
    # mà connector vẫn giữ giá trị cũ tới lần restart server.
    touched = [k for k in ("aws", "oci", "paperless", "einvoice") if k in payload]
    if touched:
        try:
            from mateai.infrastructure.connectors import CONNECTOR_REGISTRY

            # reload_config() đọc config.json MỚI qua config_loader (không còn cache).
            for name in touched:
                connector = CONNECTOR_REGISTRY.get(name)
                if connector is not None:
                    connector.reload_config()
        except Exception as conn_err:  # pylint: disable=broad-except
            logger.warning("Connector reload sau khi lưu config thất bại: %s", conn_err)

    # If telegram config was included, ensure gateway reflects changes
    if "telegram" in payload:
        # Đọc token từ CẤU HÌNH ĐÃ GHÉP, không phải từ payload.
        #
        # Phase 80: client không còn gửi `bot_token` (ô để trống nghĩa là
        # giữ), nên `payload["telegram"].get("bot_token", "")` luôn rỗng →
        # nhánh `if tg_token` không bao giờ chạy → gateway bị stop() rồi
        # không khởi động lại, trong khi cờ `enabled` vẫn là true. Cấu
        # hình và trạng thái thực lệch nhau.
        tg_block = merged.get("telegram", {})
        tg_token = tg_block.get("bot_token", "") if isinstance(tg_block, dict) else ""
        tg_enabled = bool(tg_block.get("enabled")) if isinstance(tg_block, dict) else False
        try:
            from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
            if not tg_enabled:
                telegram_gateway.stop()
            elif tg_token and not getattr(telegram_gateway, "is_running", False):
                # Chỉ khởi động lại khi CẦN. Gateway đang chạy và cấu hình
                # không đổi thì không đụng tới — stop() rồi start() lúc
                # người dùng chỉ lưu một trường khác là mất kết nối thật.
                import asyncio; await asyncio.sleep(0.5)
                telegram_gateway.start()
        except Exception as gw_err:
            logger.warning("Telegram gateway restart in save_config: %s", gw_err)


# ── Lịch sử cấu hình + khôi phục ────────────────────────────────────────────

@router.get("/api/v1/config/history", summary="Lịch sử các lần lưu cấu hình", tags=["Config"])
async def config_history(limit: int = Query(30, ge=1, le=200),
                         user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.administration import config_governance as gov
    return {"status": "success", "history": await run_blocking(lambda: gov.history(limit))}


@router.get("/api/v1/config/history/{entry_id}/diff", summary="So sánh một phiên bản với cấu hình hiện tại",
            tags=["Config"])
async def config_history_diff(entry_id: int, user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.administration import config_governance as gov
    from mateai.config.loader import read_raw_config
    snap = await run_blocking(lambda: gov.snapshot(entry_id))
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Không có phiên bản #{entry_id}")
    current = _mask_secrets(read_raw_config(strict=True))
    # "Khôi phục bản này" sẽ đổi: hiện tại -> phiên bản
    return {"status": "success", "id": entry_id, "changes": gov.diff(current, snap, _SECRET_MASK)}


@router.post("/api/v1/config/history/{entry_id}/restore", summary="Khôi phục cấu hình về một phiên bản",
             tags=["Config"])
async def config_history_restore(entry_id: int, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Khoá bí mật lấy từ cấu hình HIỆN TẠI (lịch sử không lưu khoá thật)."""
    from mateai.application.administration import config_governance as gov
    from mateai.config.loader import read_raw_config
    snap = await run_blocking(lambda: gov.snapshot(entry_id))
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Không có phiên bản #{entry_id}")
    current = await run_blocking(lambda: read_raw_config(strict=True))
    try:
        restored = config_service.prepare_restore(snap, current, _restore_masked_secrets)
    except config_service.ConfigInvalid as bad:
        raise HTTPException(status_code=400, detail=bad.detail)
    await run_blocking(lambda: config_service.save(str(user.get("username")), restored,
                                                   f"Khôi phục phiên bản #{entry_id}", _mask_secrets,
                                                   audit_action="config_restore"))
    await _apply_written_config(restored, {k: restored[k] for k in restored})
    logger.info("Cấu hình đã khôi phục về phiên bản #%s bởi %s", entry_id, user.get("username"))
    return {"status": "success", "message": f"Đã khôi phục cấu hình về phiên bản #{entry_id}."}


# ── Model registry (prompt cuối §79) ────────────────────────────────────────

class ModelRegistryEntry(BaseModel):
    status: Literal["APPROVED", "EXPERIMENTAL", "DEPRECATED", "BLOCKED"]
    privacy: Optional[Literal["external", "local"]] = None
    use_cases: Optional[List[str]] = None
    note: Optional[str] = Field(default=None, max_length=300)
    #: Giá USD / 1 triệu token (vào / ra) — để tính chi phí từng tác vụ. Bỏ trống = chưa có giá.
    price_in_per_1m: Optional[float] = Field(default=None, ge=0)
    price_out_per_1m: Optional[float] = Field(default=None, ge=0)


class ModelRegistryUpdate(BaseModel):
    #: model -> mục đăng ký; null = gỡ đăng ký.
    models: Dict[str, Optional[ModelRegistryEntry]]
    reason: str = Field(default="", max_length=300)


@router.get("/api/v1/llm/model-registry", summary="Danh sách model + trạng thái phê duyệt", tags=["LLM Router"])
async def get_model_registry(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    return {"status": "success", **config_service.model_registry_view(await _router_model_pool())}


@router.put("/api/v1/llm/model-registry", summary="Đổi trạng thái model (chỉ admin, có lịch sử + audit)",
            tags=["LLM Router"])
async def put_model_registry(payload: ModelRegistryUpdate,
                             user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    changes = {m.strip(): (e.model_dump() if e else None) for m, e in payload.models.items() if m.strip()}
    if not changes:
        raise HTTPException(status_code=400, detail="Không có thay đổi nào.")
    warnings = await run_blocking(config_service.update_model_registry, actor=str(user.get("username")),
                                  changes=changes, reason=payload.reason, mask=_mask_secrets)
    return {"status": "success", "warnings": warnings,
            **config_service.model_registry_view(await _router_model_pool())}


# ── Thử trước khi lưu ───────────────────────────────────────────────────────

class LLMPreviewRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    model: Optional[str] = Field(default=None, description="Model muốn thử (bỏ trống: model chính đang chạy)")
    system_prompt: Optional[str] = Field(default=None, max_length=8000,
                                         description="Chỉ thị cá tính muốn thử (None: đang lưu, '': không có)")
    ai_name: Optional[str] = Field(default=None, max_length=40)


@router.post("/api/v1/llm/preview", summary="Thử một câu với model / chỉ thị cá tính CHƯA lưu", tags=["LLM Router"])
async def llm_preview(payload: LLMPreviewRequest, user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Một lượt trò chuyện (không tool, không lưu lịch sử) để so sánh trước khi bấm Lưu."""
    import time
    from mateai.application.agent.llm_engine import build_system_prompt
    from mateai.infrastructure.llm.llm_provider import complete_once, make_llm_client
    from mateai.config.loader import settings
    cfg = settings.llm
    direct = getattr(cfg, "routing_mode", "router") == "direct"
    base = (cfg.direct_url if direct else cfg.base_url) or cfg.base_url
    key = (cfg.direct_api_key if direct else cfg.api_key) or "sk-dummy"
    model = (payload.model or (cfg.direct_model if direct else "") or cfg.model_name or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Chưa có model để thử.")
    system = build_system_prompt(source_device="portal", conversation=True,
                                 persona_override=payload.system_prompt, name_override=payload.ai_name)
    t0 = time.perf_counter()
    try:
        reply = await complete_once(make_llm_client(base, key, timeout=45.0), model,
                                    [{"role": "system", "content": system},
                                     {"role": "user", "content": payload.message}])
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "model": model, "error": str(exc)[:300],
                "latency_ms": int((time.perf_counter() - t0) * 1000)}
    return {"success": True, "model": model, "reply": reply,
            "latency_ms": int((time.perf_counter() - t0) * 1000), "system_chars": len(system)}


@router.get(
    "/api/v1/routing",
    summary="Get 3-tier AI routing configuration",
    tags=["Config"],
)
async def get_routing_endpoint(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Trả về cấu hình định tuyến AI 3 tầng (primary, fallback_1, fallback_2)."""
    cfg = await get_config()
    return {
        "status": "success",
        "routing": cfg.get("routing", {}),
    }


@router.post(
    "/api/v1/routing",
    summary="Update 3-tier AI routing configuration",
    tags=["Config"],
)
async def update_routing_endpoint(
    payload: Dict[str, Any],
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Cập nhật cấu hình định tuyến AI 3 tầng, lưu vào config.json và hot-reload runtime."""
    routing_data = payload.get("routing") if "routing" in payload else payload
    # Truyền người dùng thật: trước Phase 10 thiếu `user`, lịch sử + audit ghi "api".
    await save_config({"routing": routing_data}, user=user)
    return {
        "status": "success",
        "message": "Đã cập nhật và kích hoạt cấu hình định tuyến AI 3 tầng thành công.",
        "routing": routing_data,
    }
