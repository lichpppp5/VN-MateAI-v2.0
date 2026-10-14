# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/administration/config_service.py
===================================================
Nghiệp vụ của màn hình Cấu Hình (`/api/v1/config`). Chuyển từ `routers/config.py`
(Supervisor Phase 10, §198: router chỉ nhận request, gọi use case, trả kết quả).

  - `legacy_view`   : khối `llm` + các bí danh tương thích ngược cho giao diện cũ;
  - `prepare_save`  : chuẩn hoá payload (llm / routing / MODEL_NAME), ghép SÂU vào cấu
                      hình đang lưu, kiểm tra hợp lệ — chưa ghi gì;
  - `router_model_pool`: model router đang phục vụ (dùng làm danh sách dự phòng).

Che / khôi phục bí mật là việc của tầng HTTP (`secret_masking`) và PHẢI làm trước
`prepare_save` (ký hiệu che là chuỗi khác rỗng, sẽ thắng nhánh `or existing...`).
Ghi file: `config_governance.save_config` (một đường ghi, có lịch sử + audit).
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Any, Dict, List, Tuple

from mateai.application.administration import config_governance as gov
from mateai.config.loader import LLMConfig, settings

logger = logging.getLogger(__name__)

#: Mặc định của khối llm lấy từ MỘT nơi (schema cấu hình), không viết lại ở đây.
DEFAULT_BASE_URL: str = LLMConfig.model_fields["base_url"].default


class ConfigInvalid(Exception):
    """Cấu hình gửi lên không hợp lệ — chưa ghi gì. `detail` trả nguyên cho giao diện."""

    def __init__(self, detail: Dict[str, Any]):
        super().__init__(detail.get("message", "invalid"))
        self.detail = detail


def deep_merge(base: Any, incoming: Any) -> Any:
    """
    Ghép `incoming` vào `base`, xuống từng khối con thay vì chỉ một tầng.

    `{**existing, **payload}` ghép NÔNG: giao diện gửi `{"telegram": {"admin_chat_ids":
    [...]}}` thì khối telegram bị thay TRỌN — khoá bot, cờ `enabled` mất lặng lẽ (lỗi đã
    xảy ra thật: bấm "Lưu" ở tab Cấu Hình xoá token bot và tắt gateway Telegram).
    Danh sách THAY thế, không nối — "danh sách admin này là danh sách này".
    """
    if isinstance(base, dict) and isinstance(incoming, dict):
        out = dict(base)
        for k, v in incoming.items():
            out[k] = deep_merge(base.get(k), v) if k in base else v
        return out
    return incoming


def legacy_view(data: Dict[str, Any]) -> Dict[str, Any]:
    """Bản xem cho GET /api/v1/config: luôn có `llm`, `auto_execute`, `routing`/`router`
    và bí danh MODEL_NAME / API_KEY / BASE_URL. CHƯA che bí mật — tầng HTTP che ở cửa cuối
    (các nhánh dưới nhân bản cùng một khoá ra nhiều đường dẫn, che từng nhánh dễ sót)."""
    # Phase 22: Ensure 'llm' block is present
    if "llm" not in data or not isinstance(data["llm"], dict):
        old_primary = data.get("routing", {}).get("primary", {})
        data["llm"] = {
            "base_url": old_primary.get("api_base") or data.get("BASE_URL", DEFAULT_BASE_URL),
            "model_name": old_primary.get("provider_model") or data.get("MODEL_NAME", ""),
            "api_key": old_primary.get("api_key") or data.get("API_KEY", "sk-dummy"),
        }

    llm = data["llm"]
    data["MODEL_NAME"] = llm.get("model_name", "")
    data["API_KEY"] = llm.get("api_key", "")
    data["BASE_URL"] = llm.get("base_url", "")
    data["auto_execute"] = data.get("auto_execute", data.get("AUTO_EXECUTE_UNVERIFIED_CODE", False))

    # Backward compatibility for legacy UI expecting 'routing' or 'router'
    if "routing" not in data:
        data["routing"] = {
            "primary": {
                "provider_model": llm.get("model_name", ""),
                "api_key": llm.get("api_key", ""),
                "api_base": llm.get("base_url", ""),
                "api_keys": [llm.get("api_key", "")] if llm.get("api_key") else [],
            },
            "fallback_1": {"provider_model": "", "api_key": "", "api_base": "", "api_keys": []},
            "fallback_2": {"provider_model": "", "api_key": "", "api_base": "", "api_keys": []},
        }
    data["router"] = data["routing"]
    return {k: v for k, v in data.items() if not k.startswith("_")}


def fetch_router_models() -> List[str]:
    """
    Hỏi router đang phục vụ model nào (ĐỒNG BỘ — gọi qua `router_model_pool`).

    Router là nguồn sự thật: chỉ liệt kê model tới được từ provider đang bật và còn hạn
    mức. Hardcode tên model nghĩa là chỉ đúng vào một thời điểm. Bỏ combo do người dùng
    đặt (tên không có "/"): đưa vào danh sách dự phòng thì gọi lại chính nó.
    """
    base = (getattr(settings.llm, "base_url", "") if settings else "") or ""
    if not base:
        return []
    try:
        root = base.rsplit("/v1", 1)[0] if "/v1" in base else base.rstrip("/")
        key = (getattr(settings.llm, "api_key", "") if settings else "") or ""
        req = urllib.request.Request(
            f"{root}/v1/models",
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[Config] Không đọc được danh sách model từ router: %s", exc)
        return []

    out: List[str] = []
    for entry in payload.get("data") or []:
        mid = entry.get("id") if isinstance(entry, dict) else None
        if mid and "/" in mid and mid not in out:
            out.append(mid)
    return out


async def router_model_pool() -> List[str]:
    """Trước Phase 10 `urlopen` chạy thẳng trong hàm async: mỗi lần Lưu chặn event loop
    tới 5 s — mọi kênh voice / WebSocket đứng theo."""
    return await asyncio.to_thread(fetch_router_models)


def _normalize(payload: Dict[str, Any], existing: Dict[str, Any], pool: List[str]) -> None:
    """Chuẩn hoá payload TẠI CHỖ: llm / routing cũ / MODEL_NAME → khối `llm`."""
    # Phase 68: danh sách dự phòng lấy TỪ ROUTER; router không trả lời thì để trống
    # (không đoán, không ghi model chết).
    fallbacks = list(pool)
    if not pool:
        logger.warning("[Config] Router không trả danh sách model — bỏ trống danh sách dự phòng "
                       "thay vì ghi model chết.")
    # Phase 73: không tự điền "sk-dummy" — khoá giả ghi xuống đĩa trông y hệt khoá thật.
    if "llm" in payload and isinstance(payload["llm"], dict):
        existing_llm = existing.get("llm", {})
        new_model = payload["llm"].get("model_name", existing_llm.get("model_name", "")) or ""
        r_models = payload["llm"].get("router_models", existing_llm.get("router_models", []))
        if not isinstance(r_models, list) or not r_models:
            r_models = fallbacks
        if new_model and new_model not in r_models:
            r_models = [new_model] + [m for m in r_models if m != new_model]
        s_models = payload["llm"].get("specialist_models", existing_llm.get("specialist_models", fallbacks))
        # Giữ MỌI trường khác form gửi (tri_brain_enabled, controller/voice/ops_model…).
        payload["llm"] = {
            **payload["llm"],
            "base_url": payload["llm"].get("base_url", existing_llm.get("base_url", DEFAULT_BASE_URL)),
            "model_name": new_model,
            "api_key": payload["llm"].get("api_key") or existing_llm.get("api_key") or "",
            "router_models": r_models,
            "specialist_models": s_models,
            # Phase 91: Dual-mode routing fields
            "routing_mode": payload["llm"].get("routing_mode", existing_llm.get("routing_mode", "router")),
            "direct_url": payload["llm"].get("direct_url", existing_llm.get("direct_url", "")),
            "direct_model": payload["llm"].get("direct_model", existing_llm.get("direct_model", "")),
            # Không ghi khoá giả; client direct tự dùng khoá giữ chỗ khi rỗng.
            "direct_api_key": payload["llm"].get("direct_api_key") or existing_llm.get("direct_api_key") or "",
        }
    elif "routing" in payload and isinstance(payload["routing"], dict):
        primary = payload["routing"].get("primary", {})
        new_model = primary.get("provider_model", "") or ""
        payload["llm"] = {
            "base_url": primary.get("api_base", DEFAULT_BASE_URL),
            "model_name": new_model,
            "api_key": primary.get("api_key") or existing.get("llm", {}).get("api_key") or "",
            "router_models": [new_model] + [m for m in fallbacks if m != new_model],
            "specialist_models": fallbacks,
        }
    elif "MODEL_NAME" in payload or "BASE_URL" in payload:
        existing_llm = existing.get("llm", {})
        new_model = payload.get("MODEL_NAME", existing_llm.get("model_name", "")) or ""
        payload["llm"] = {
            "base_url": payload.get("BASE_URL", existing_llm.get("base_url", DEFAULT_BASE_URL)),
            "model_name": new_model,
            "api_key": payload.get("API_KEY") or existing_llm.get("api_key") or "",
            "router_models": [new_model] + [m for m in fallbacks if m != new_model],
            "specialist_models": fallbacks,
        }

    # auto_execute và AUTO_EXECUTE_UNVERIFIED_CODE luôn khớp nhau
    if "auto_execute" in payload:
        payload["AUTO_EXECUTE_UNVERIFIED_CODE"] = bool(payload["auto_execute"])
    elif "AUTO_EXECUTE_UNVERIFIED_CODE" in payload:
        payload["auto_execute"] = bool(payload["AUTO_EXECUTE_UNVERIFIED_CODE"])


def prepare_save(payload: Dict[str, Any], existing: Dict[str, Any], pool: List[str],
                 force_models: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(cấu hình sau khi ghép, payload đã chuẩn hoá). Ném `ConfigInvalid` — chưa ghi gì.

    Chỉ kiểm các mục form này gửi (cấu hình cũ ở mục khác có lỗi thì không chặn lưu).
    `force_models`: giao diện đã hỏi và người dùng vẫn muốn lưu model không có trên router.
    """
    _normalize(payload, existing, pool)
    comment_keys = {k: v for k, v in existing.items() if k.startswith("_")}
    merged = deep_merge({**existing, **comment_keys}, payload)
    touched = {k: merged[k] for k in payload if k in merged}
    errors, unknown = gov.validate(touched, pool, check_models=("llm" in payload and not force_models))
    if errors or unknown:
        raise ConfigInvalid({"message": "Cấu hình chưa hợp lệ — chưa lưu gì.",
                             "errors": errors, "unknown_models": unknown})
    return merged, payload


def save(actor: str, merged: Dict[str, Any], note: str, mask=None,
         audit_action: str = "config_change") -> None:
    """Thay TOÀN BỘ cấu hình bằng `merged` qua đường ghi duy nhất (lịch sử + audit)."""
    def _apply(cfg: Dict[str, Any]) -> None:
        cfg.clear()
        cfg.update(merged)

    gov.save_config(actor, _apply, note, mask, audit_action=audit_action)


def prepare_restore(snapshot: Dict[str, Any], current: Dict[str, Any], restore_secrets) -> Dict[str, Any]:
    """Phiên bản cũ + khoá bí mật HIỆN TẠI (lịch sử không lưu khoá thật) + chú thích `_`."""
    restored = restore_secrets(snapshot, current)
    restored.update({k: v for k, v in current.items() if str(k).startswith("_")})
    errors, _unknown = gov.validate(restored, None, check_models=False)
    if errors:
        raise ConfigInvalid({"message": "Phiên bản này không hợp lệ với hệ thống hiện tại.", "errors": errors})
    return restored


__all__ = ["ConfigInvalid", "DEFAULT_BASE_URL", "deep_merge", "fetch_router_models", "legacy_view",
           "prepare_restore", "prepare_save", "router_model_pool", "save"]


# ── Model registry (prompt cuối §79) ─────────────────────────────────────────

def model_registry_view(pool: List[str]) -> Dict[str, Any]:
    """Mọi model đang biết (router phục vụ + đã đăng ký + đang được cấu hình dùng) kèm trạng thái."""
    from mateai.infrastructure.llm.llm_provider import model_status
    registry = dict(getattr(settings.llm, "model_registry", None) or {})
    in_use: Dict[str, List[str]] = {}
    for path, label in gov.MODEL_FIELDS + (("llm.direct_model", "Model kết nối trực tiếp"),):
        m = str(getattr(settings.llm, path.split(".", 1)[1], "") or "").strip()
        if m:
            in_use.setdefault(m, []).append(label)
    names = sorted(set(pool) | set(registry) | set(in_use))
    return {"models": [{"model": m, "status": model_status(m), "in_router": m in pool,
                        "used_as": in_use.get(m, []), **{k: (registry.get(m) or {}).get(k)
                                                          for k in ("privacy", "use_cases", "note",
                                                                    "price_in_per_1m", "price_out_per_1m")}}
                       for m in names],
            "statuses": ["APPROVED", "EXPERIMENTAL", "DEPRECATED", "BLOCKED"]}


def update_model_registry(actor: str, changes: Dict[str, Any], reason: str = "", mask=None) -> List[str]:
    """Ghi `llm.model_registry` qua đường ghi cấu hình chuẩn (lịch sử + audit). Giá trị None =
    gỡ đăng ký. Trả cảnh báo (vd vừa BLOCK model đang là model chính -> sẽ dùng dự phòng)."""
    def _apply(cfg: Dict[str, Any]) -> None:
        reg = dict((cfg.setdefault("llm", {}) or {}).get("model_registry") or {})
        for model, entry in changes.items():
            if entry is None:
                reg.pop(model, None)
            else:
                reg[model] = {k: v for k, v in entry.items() if v not in (None, "", [])}
        cfg["llm"]["model_registry"] = reg

    gov.save_config(actor, _apply, "Model registry" + (f": {reason}" if reason else ""), mask)
    warnings = []
    for path, label in gov.MODEL_FIELDS:
        m = str(getattr(settings.llm, path.split(".", 1)[1], "") or "").strip()
        if m and str((changes.get(m) or {}).get("status", "")).upper() == "BLOCKED":
            warnings.append(f"{label} '{m}' vừa bị BLOCKED — hệ thống sẽ chỉ dùng model dự phòng cho tới khi đổi.")
    return warnings
