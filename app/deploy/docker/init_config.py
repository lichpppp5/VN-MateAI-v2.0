# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Tạo config.json LẦN ĐẦU cho máy chủ mới (bản chuyển giao sạch) từ config.example.json + biến môi trường (.env).

Không mang bí mật của máy nào khác: khoá API LLM lấy từ .env; token Telegram / Vbee / ElevenLabs… nhập sau trên Portal
(Quản Lý Trợ Lý AI). Khoá bí mật trong config.json được ứng dụng tự mã hoá ở lần khởi động đầu (certs/config_secret.key).

Dùng: python init_config.py <config.example.json> <đích config.json>
Biến: VNMATE_LLM_BASE_URL, VNMATE_LLM_API_KEY, VNMATE_LLM_MODEL, VNMATE_LLM_FALLBACK_MODELS (cách nhau dấu phẩy),
      VNMATE_ASSISTANT_NAME, VNMATE_WAKE_WORD, VNMATE_HTTPS_PORT_INTERNAL (mặc định 8443).
"""
from __future__ import annotations

import json
import os
import sys

PLACEHOLDER = "YOUR_"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _scrub(obj):
    """Ô giữ chỗ "YOUR_…_HERE" → rỗng (không để chuỗi mẫu bị coi là khoá thật)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [x for x in (_scrub(v) for v in obj) if not (isinstance(x, str) and x == "")]
    if isinstance(obj, str) and obj.startswith(PLACEHOLDER):
        return ""
    return obj


def build(example: dict) -> dict:
    cfg = _scrub(json.loads(json.dumps(example)))
    base = _env("VNMATE_LLM_BASE_URL").rstrip("/")
    key = _env("VNMATE_LLM_API_KEY")
    model = _env("VNMATE_LLM_MODEL")
    fallbacks = [m.strip() for m in _env("VNMATE_LLM_FALLBACK_MODELS").split(",") if m.strip()]
    name = _env("VNMATE_ASSISTANT_NAME", "Ly Ly")
    wake = _env("VNMATE_WAKE_WORD", f"Hey {name}")

    llm = cfg.setdefault("llm", {})
    llm.update({"base_url": base, "api_key": key, "model_name": model,
                "router_models": [model, *fallbacks] if model else fallbacks,
                "specialist_models": fallbacks[:3]})
    cfg.update({"BASE_URL": base, "API_KEY": key, "MODEL_NAME": model})
    for block in ("routing", "router"):
        prim = (cfg.get(block) or {}).get("primary")
        if isinstance(prim, dict):
            prim.update({"provider_model": model, "api_key": key, "api_base": base})

    persona = cfg.setdefault("persona", {})
    persona.update({"ai_name": name, "wake_word": wake, "ai_pronoun": "em", "user_pronoun": "anh/chị"})
    cfg.update({"AI_NAME": name, "ASSISTANT_NAME": name, "WAKE_WORD": wake})

    # Container: cổng trong 8443 (user thường không mở được 443) — compose ánh xạ ra 443 của máy chủ.
    cfg.update({"HOST": "0.0.0.0", "PORT": int(_env("VNMATE_HTTPS_PORT_INTERNAL", "8443")), "IOT_PORT": 8000,
                "MIC_AUTO_START": False})
    tg = cfg.setdefault("telegram", {})
    tg.update({"enabled": False, "bot_token": "", "admin_chat_ids": []})
    return cfg


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        cfg = build(json.load(f))
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, dst)
    missing = [n for n, v in (("VNMATE_LLM_BASE_URL", cfg["llm"]["base_url"]), ("VNMATE_LLM_MODEL", cfg["llm"]["model_name"]))
               if not v]
    print(f"[init_config] Đã tạo {dst} (trợ lý: {cfg['AI_NAME']})"
          + (f" — CHƯA có {', '.join(missing)}: nhập ở Portal → Quản Lý Trợ Lý AI" if missing else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
