# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/administration/config_governance.py
======================================================
Quản trị cấu hình hệ thống (config.json) — yêu cầu 2026-10-05:

  - KIỂM TRA trước khi ghi: giá trị sai (tốc độ đọc ngoài khoảng, URL hỏng, model
    không có trên 9Router, engine không tồn tại…) bị từ chối kèm lý do tiếng Việt.
    Trước đây chỉ giao diện cảnh báo; gọi API thẳng là ghi được mọi thứ.
  - LỊCH SỬ: mỗi lần lưu là một phiên bản (ai lưu, lúc nào, đổi những gì) và
    KHÔI PHỤC được về bất kỳ phiên bản nào.

Bản chụp lưu trong lịch sử đã CHE khoá bí mật (tầng HTTP che trước khi gọi vào
đây); khi khôi phục, khoá được lấy lại từ cấu hình hiện tại — lịch sử không bao
giờ chứa khoá thật.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mateai.infrastructure.database.db_manager import db_manager

TTS_ENGINES = {"edge-tts", "elevenlabs", "vbee"}
ASR_ENGINES = {"google", "groq", "whisper", "local_whisper"}
ROUTING_MODES = {"router", "direct"}
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)
MAX_PROMPT_CHARS = 8000

#: Trường model được dùng khi chạy qua 9Router (đường "router").
MODEL_FIELDS = (("llm.model_name", "Model chính"), ("llm.controller_model", "Não Điều phối"),
                ("llm.voice_model", "Não Giao tiếp"), ("llm.ops_model", "Não Vận hành"))


def _get(cfg: Dict[str, Any], path: str) -> Any:
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def validate(cfg: Dict[str, Any], router_models: Optional[Iterable[str]] = None,
             check_models: bool = True) -> Tuple[List[str], List[str]]:
    """Trả (lỗi, model_không_có). Lỗi rỗng = hợp lệ. Chỉ kiểm khoá có mặt."""
    errors: List[str] = []

    for path, label in (("llm.base_url", "Địa chỉ 9Router"), ("llm.direct_url", "Địa chỉ Direct LLM"),
                        ("GROQ_BASE_URL", "Địa chỉ Groq")):
        v = _get(cfg, path)
        if v not in (None, "") and not _URL_RE.match(str(v)):
            errors.append(f"{label} không phải URL http(s) hợp lệ: {v!r}")

    mode = _get(cfg, "llm.routing_mode")
    if mode is not None and mode not in ROUTING_MODES:
        errors.append(f"Chế độ kết nối LLM phải là 'router' hoặc 'direct', nhận {mode!r}")
    if (mode or "router") == "router" and _get(cfg, "llm") is not None and not str(_get(cfg, "llm.model_name") or "").strip():
        errors.append("Chưa chọn model chính cho 9Router")

    for path, label, lo, hi in (("audio.speech_rate", "Tốc độ đọc (%)", -50, 100),
                                ("audio.volume", "Âm lượng (%)", 0, 100)):
        v = _get(cfg, path)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            errors.append(f"{label} phải là số, nhận {v!r}")
            continue
        if not lo <= n <= hi:
            errors.append(f"{label} phải trong khoảng {lo}..{hi}, nhận {n}")

    v = _get(cfg, "audio.tts_engine")
    if v is not None and v not in TTS_ENGINES:
        errors.append(f"Bộ máy đọc (TTS) không được hỗ trợ: {v!r} (chỉ {', '.join(sorted(TTS_ENGINES))})")
    v = _get(cfg, "audio.asr_engine")
    if v is not None and v not in ASR_ENGINES:
        errors.append(f"Bộ nhận dạng giọng nói không được hỗ trợ: {v!r} (chỉ {', '.join(sorted(ASR_ENGINES))})")
    if _get(cfg, "audio.tts_engine") == "elevenlabs" and not (
            _get(cfg, "audio.elevenlabs_api_key") and _get(cfg, "audio.elevenlabs_voice_id")):
        errors.append("Chọn ElevenLabs thì phải có API key và Voice ID")
    if _get(cfg, "audio.tts_engine") == "vbee" and not (
            _get(cfg, "audio.vbee_app_id") and _get(cfg, "audio.vbee_token")):
        errors.append("Chọn Vbee thì phải có App ID và Token (tạo ở studio.vbee.vn/apps)")

    name = _get(cfg, "persona.ai_name")
    if name is not None and not (1 <= len(str(name).strip()) <= 40):
        errors.append("Tên trợ lý phải có 1–40 ký tự")
    prompt = _get(cfg, "persona.system_prompt")
    if prompt is not None and len(str(prompt)) > MAX_PROMPT_CHARS:
        errors.append(f"Chỉ thị cá tính dài {len(str(prompt))} ký tự — tối đa {MAX_PROMPT_CHARS}")

    # Model registry (prompt cuối §79): không lưu cấu hình dùng model đã bị BLOCKED.
    registry = _get(cfg, "llm.model_registry")
    if registry is None:
        try:
            from mateai.config.loader import settings
            registry = settings.llm.model_registry
        except Exception:  # noqa: BLE001
            registry = {}
    blocked = {m for m, e in (registry or {}).items() if str((e or {}).get("status", "")).upper() == "BLOCKED"}
    for path, label in MODEL_FIELDS + (("llm.direct_model", "Model kết nối trực tiếp"),):
        m = str(_get(cfg, path) or "").strip()
        if m and m in blocked:
            errors.append(f"{label} '{m}' đang bị BLOCKED trong model registry")

    missing: List[str] = []
    known = set(router_models or [])
    if check_models and known and (mode or "router") == "router":
        for path, label in MODEL_FIELDS:
            m = str(_get(cfg, path) or "").strip()
            if m and m not in known:
                missing.append(f"{label}: {m}")
    return errors, missing


# ── So sánh thay đổi ────────────────────────────────────────────────────────

def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = obj
    return out


def diff(old: Dict[str, Any], new: Dict[str, Any], secret_marker: str = "") -> List[Dict[str, Any]]:
    """Danh sách thay đổi {path, old, new}. Trường bí mật: chỉ báo "đã đổi" (old/new = None)
    — so trên giá trị THẬT nên đổi khoá vẫn được ghi nhận, nhưng không lưu / trả khoá."""
    from mateai.config.secret_box import is_secret
    a, b = _flatten(old or {}), _flatten(new or {})
    changes = []
    for path in sorted(set(a) | set(b)):
        va, vb = a.get(path), b.get(path)
        if va == vb:
            continue
        secret = any(is_secret(seg) for seg in path.split(".")) or bool(secret_marker) and secret_marker in (
            json.dumps(va, ensure_ascii=False) + json.dumps(vb, ensure_ascii=False))
        changes.append({"path": path, "old": None if secret else va, "new": None if secret else vb,
                        "secret": secret})
    return changes


# ── Lịch sử ─────────────────────────────────────────────────────────────────

def record(before: Dict[str, Any], after: Dict[str, Any], saved_by: str, note: str, mask) -> Optional[int]:
    """Ghi một phiên bản (trạng thái SAU khi lưu). `before`/`after` là cấu hình THẬT để
    phát hiện cả việc đổi khoá; chỉ bản đã che (`mask`) được lưu. Lần đầu ghi thêm
    bản gốc trước đó."""
    before_masked, after_masked = mask(before), mask(after)
    changes = diff(before, after)
    if not changes:
        return None
    if note == "Lưu":
        # Giao diện gửi cả cấu hình: ghi chú chỉ nêu những mục THẬT SỰ đổi.
        sections = sorted({c["path"].split(".")[0] for c in changes})
        note = "Lưu: " + ", ".join(sections[:8]) + (f" (+{len(sections) - 8})" if len(sections) > 8 else "")
    if db_manager.count_config_history() == 0:
        db_manager.add_config_history("hệ thống", "Trạng thái trước lần lưu đầu tiên được ghi lại", "[]",
                                      json.dumps(before_masked, ensure_ascii=False))
    return db_manager.add_config_history(saved_by, note, json.dumps(changes, ensure_ascii=False),
                                         json.dumps(after_masked, ensure_ascii=False))


def history(limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for r in db_manager.list_config_history(limit):
        changes = json.loads(r.pop("changes_json") or "[]")
        r["change_count"] = len(changes)
        r["changes"] = changes[:12]
        out.append(r)
    return out


def snapshot(entry_id: int) -> Optional[Dict[str, Any]]:
    row = db_manager.get_config_history(entry_id)
    return json.loads(row["snapshot_json"]) if row else None


# ── Một đường ghi cấu hình cho mọi màn hình (Supervisor Phase 10, §70, §128) ──

def save_config(actor: str, mutate, note: str, mask=None, audit_action: str = "config_change",
                audit: bool = True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Đọc – sửa – ghi `config.json` NGUYÊN TỬ (cùng khoá với loader), nạp lại cấu hình,
    ghi lịch sử phiên bản (khôi phục được) và audit (chỉ tên khoá đã đổi, không giá trị).

    `mutate(cfg)` sửa tại chỗ bản sao cấu hình. Không có thay đổi thì không ghi gì.
    Trước đây 5 màn hình (Telegram — kể cả danh sách chat admin —, AD sync, mẫu báo cáo,
    danh sách từ khoá bảo mật) tự ghi `config.json`: không lịch sử, phần lớn không audit.
    Trả (trước, sau)."""
    import copy
    from mateai.config.loader import _CONFIG_LOCK, read_raw_config, reload_settings, write_raw_config

    with _CONFIG_LOCK:
        before = read_raw_config(strict=True)
        after = copy.deepcopy(before)
        mutate(after)
        if after == before:
            return before, after
        write_raw_config(after)
    reload_settings()
    changes = diff(before, after)
    try:
        record(before, after, actor, note, mask or (lambda x: x))
    except Exception as exc:  # noqa: BLE001 — lịch sử hỏng không chặn việc lưu
        import logging
        logging.getLogger(__name__).warning("Không ghi được lịch sử cấu hình: %s", exc)
    if audit:
        try:
            from mateai.application.security.safety_guard import security_engine
            security_engine.log_audit(actor, audit_action, "CONFIG", "SUCCESS",
                                      {"note": note, "paths": [c["path"] for c in changes][:50]})
        except Exception:  # noqa: BLE001
            pass
    return before, after
