# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/llmops/usage.py
==================================
Đo mức dùng AI (token THẬT do nhà cung cấp báo) theo ngày / người / model và áp NGÂN SÁCH (docs/ai/llmops.md).

- `record()` ghi một dòng cho mỗi lần gọi model có báo usage. Không có usage thì không ghi, không đoán.
- Tiền chỉ tính cho model đã khai giá trong `llm.model_registry`; phần chưa có giá được báo riêng (`unpriced_tokens`).
- `check()` trả lý do từ chối khi chế độ `block` và đã VƯỢT hạn mức (trước khi gọi model); `warn` chỉ cảnh báo qua kênh cảnh báo.
- Không đo được (DB lỗi) thì KHÔNG chặn người dùng — nhưng ghi log; ngân sách là lớp kiểm soát chi phí, không phải lớp an ninh.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODES = ("off", "warn", "block")
_alerted: Dict[str, bool] = {}


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _cfg():
    from mateai.config.loader import settings
    return settings.llmops


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _month_start() -> str:
    return datetime.now().strftime("%Y-%m-01")


# ── ghi nhận ────────────────────────────────────────────────────────────────

def record(principal: Optional[str], model: str, prompt_tokens: int, completion_tokens: int, task_id: Optional[str] = None) -> None:
    """Ghi một lần gọi model. Không được làm hỏng lượt hội thoại: mọi lỗi chỉ ghi log."""
    try:
        from mateai.infrastructure.llm.llm_provider import estimate_cost
        pin, pout = max(0, int(prompt_tokens or 0)), max(0, int(completion_tokens or 0))
        if not pin and not pout:
            return
        cost, _ = estimate_cost({str(model): {"prompt_tokens": pin, "completion_tokens": pout}})
        now = datetime.now()
        _db().dev_insert("llm_usage", {"usage_id": uuid.uuid4().hex, "ts": now.strftime("%Y-%m-%d %H:%M:%S"), "day": now.strftime("%Y-%m-%d"),
                                       "principal": str(principal or "unknown")[:120], "model": str(model or "unknown")[:120],
                                       "prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout, "cost": cost, "task_id": task_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LLMOps] Không ghi được mức dùng AI: %s", exc)
        return
    try:
        _maybe_alert(principal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LLMOps] Kiểm tra ngân sách lỗi: %s", exc)


# ── ngân sách ───────────────────────────────────────────────────────────────

def _checks(principal: Optional[str]) -> List[Dict[str, Any]]:
    c = _cfg()
    db = _db()
    today = db.llm_usage_sum(_today())
    month = db.llm_usage_sum(_month_start())
    rows = [("daily_tokens", "Token trong ngày", today["tokens"], c.daily_token_limit),
            ("daily_cost", "Chi phí trong ngày", today["cost"], c.daily_cost_limit),
            ("monthly_cost", "Chi phí trong tháng", month["cost"], c.monthly_cost_limit)]
    if principal and c.user_daily_token_limit:
        rows.append(("user_daily_tokens", f"Token trong ngày của {principal}", db.llm_usage_sum(_today(), principal)["tokens"], c.user_daily_token_limit))
    if principal:
        from mateai.application.agent_gateway import directory
        agent_limit = directory.daily_limit(principal)            # agent ngoài: hạn mức riêng trong Danh bạ Agent
        if agent_limit:
            rows.append(("agent_daily_tokens", f"Token trong ngày của {principal}", db.llm_usage_sum(_today(), principal)["tokens"], agent_limit))
    out = []
    for key, label, used, limit in rows:
        if not limit:
            continue
        ratio = used / limit
        out.append({"key": key, "label": label, "used": round(used, 6), "limit": limit, "ratio": round(ratio, 4),
                    "state": "exceeded" if ratio >= 1 else "warn" if ratio >= c.warn_ratio else "ok"})
    return out


def check(principal: Optional[str]) -> Optional[str]:
    """None = cho phép. Chỉ trả lý do khi chế độ `block` và có hạn mức đã vượt."""
    try:
        if _cfg().budget_mode != "block":
            return None
        over = [x for x in _checks(principal) if x["state"] == "exceeded"]
    except Exception as exc:  # noqa: BLE001 — không đo được thì không chặn
        logger.warning("[LLMOps] Không kiểm tra được ngân sách: %s", exc)
        return None
    if not over:
        return None
    return ("Dạ, hạn mức dùng AI đã hết (" + "; ".join(f"{x['label']} {x['used']:g}/{x['limit']:g}" for x in over)
            + "). Quản trị viên cần nâng ngân sách hoặc chờ sang kỳ mới.")


def _maybe_alert(principal: Optional[str]) -> None:
    if _cfg().budget_mode == "off":
        return
    for x in _checks(principal):
        if x["state"] == "ok":
            continue
        key = f"{_today()}:{x['key']}:{x['state']}:{principal if x['key'].startswith(('user', 'agent')) else ''}"
        if _alerted.get(key):
            continue
        _alerted[key] = True
        if len(_alerted) > 500:
            _alerted.clear()
        title = ("Vượt ngân sách AI: " if x["state"] == "exceeded" else "Sắp hết ngân sách AI: ") + x["label"]
        msg = f"Đã dùng {x['used']:g} / {x['limit']:g} ({x['ratio']:.0%}). Chế độ: {_cfg().budget_mode}."
        try:
            from mateai.application.operations import alert_dispatcher
            asyncio.get_running_loop().create_task(
                alert_dispatcher.dispatch(title, msg, severity="critical" if x["state"] == "exceeded" else "warning", category="llm_budget"))
        except RuntimeError:
            pass          # không có vòng lặp (gọi từ luồng đồng bộ): bỏ qua cảnh báo, số liệu vẫn được ghi


def status(principal: Optional[str] = None) -> Dict[str, Any]:
    c = _cfg()
    return {"mode": c.budget_mode, "warn_ratio": c.warn_ratio, "limits": {
        "daily_token_limit": c.daily_token_limit, "user_daily_token_limit": c.user_daily_token_limit,
        "daily_cost_limit": c.daily_cost_limit, "monthly_cost_limit": c.monthly_cost_limit}, "checks": _checks(principal)}


def summary(days: int = 30) -> Dict[str, Any]:
    days = max(1, min(int(days), 365))
    since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    db = _db()
    total = db.llm_usage_sum(since)
    return {"since": since, "days": days, "total": total,
            "by_day": db.llm_usage_group(since, "day", 400), "by_model": db.llm_usage_group(since, "model", 50),
            "by_user": db.llm_usage_group(since, "principal", 50), "budget": status(),
            "note": ("Chi phí chỉ gồm model đã khai giá trong llm.model_registry; " f"{total['unpriced_tokens']} token thuộc model chưa khai giá không được quy ra tiền."
                     if total["unpriced_tokens"] else "Token là số nhà cung cấp báo; chi phí theo giá khai trong llm.model_registry.")}


def save_budget(actor: str, values: Dict[str, Any]) -> Dict[str, Any]:
    from mateai.application.administration import config_governance as gov
    from mateai.config.loader import LlmOpsConfig
    allowed = {"budget_mode", "daily_token_limit", "user_daily_token_limit", "daily_cost_limit", "monthly_cost_limit", "warn_ratio", "eval_max_cases"}
    updates = {k: v for k, v in (values or {}).items() if k in allowed}
    if not updates:
        raise ValueError("Không có thay đổi nào")
    if "budget_mode" in updates and updates["budget_mode"] not in MODES:
        raise ValueError(f"Chế độ ngân sách phải thuộc {', '.join(MODES)}")

    def _apply(cfg: Dict[str, Any]) -> None:
        section = {**dict(cfg.get("llmops") or {}), **updates}
        LlmOpsConfig(**section)              # kiểm kiểu / biên trước khi ghi (pydantic ném lỗi nếu sai)
        cfg["llmops"] = section
    try:
        gov.save_config(actor, _apply, "Ngân sách AI", None)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "ValidationError":
            raise ValueError("Giá trị không hợp lệ: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
        raise
    return status()
