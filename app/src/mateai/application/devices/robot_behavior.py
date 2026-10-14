# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devices/robot_behavior.py
============================================
Cử động tự nhiên của robot: lúc rảnh (đèn thở, ngó quanh, lắc bánh nhẹ), lúc nói (cử chỉ theo
nội dung câu). Máy chủ quyết định CẤU HÌNH + cử chỉ theo ngữ cảnh; robot tự chạy nhịp lúc rảnh
(không cần mạng cho từng cử động).

Cấu hình `robot_behavior` trong config.json (mọi khoá tuỳ chọn):

    enabled          true   tắt = robot đứng yên, chỉ khuôn mặt OLED
    idle_motion      true   cử động lúc rảnh (servo + đèn)
    idle_wheels      true   lắc bánh nhẹ lúc rảnh (trái/phải, tiến rồi lùi — luôn về chỗ cũ)
    speech_gestures  true   cử chỉ theo nội dung câu trả lời + gật nhẹ theo nhịp khi nói
    led              true   đèn RGB theo trạng thái / cảm xúc
    idle_min_s       30     khoảng nghỉ ngẫu nhiên giữa hai cử động lúc rảnh (giây)
    idle_max_s       120
    wheel_ms         120    độ dài một nhịp bánh (ms, 60–300) — ngắn = nhẹ
    quiet_hours      ""     vd "22:00-07:00": trong khung này không cử động, không đèn

Máy chủ gửi khung {"type": "behavior", ...} khi robot bắt tay và mỗi khi giá trị đổi
(gồm lúc vào / ra giờ yên lặng).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from mateai.application.voice.emotion import _fold

DEFAULTS: Dict[str, Any] = {
    "enabled": True, "idle_motion": True, "idle_wheels": True, "speech_gestures": True, "led": True,
    "idle_min_s": 30, "idle_max_s": 120, "wheel_ms": 120, "quiet_hours": "",
}

#: Cử chỉ firmware biết (MotionCore): giữ khớp `handleIncomingJson` trong esp32_firmware/src/main.cpp.
GESTURES = ("wave_hand", "nod_head", "look_around", "excited", "sad")

_GREETING = ("xin chao", "chao anh", "chao chi", "chao ban", "chao em", "chao moi nguoi", "chao buoi",
             "tam biet", "hen gap lai")
_AFFIRM = ("vang", "da", "duoc a", "dong y", "chac chan roi", "dung vay", "dung roi")
_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def in_quiet_hours(spec: str, now: datetime) -> bool:
    """"22:00-07:00" (qua nửa đêm) hoặc "12:00-13:30". Rỗng / sai dạng = không có giờ yên lặng."""
    m = _HHMM.match(str(spec or ""))
    if not m:
        return False
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    start, end, cur = h1 * 60 + m1, h2 * 60 + m2, now.hour * 60 + now.minute
    if start == end:
        return False
    return start <= cur < end if start < end else (cur >= start or cur < end)


def settings(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cấu hình đã chuẩn hoá (mặc định + giới hạn an toàn)."""
    if raw is None:
        from mateai.config.loader import get_config_section
        raw = get_config_section("robot_behavior") or {}
    s = {**DEFAULTS, **{k: v for k, v in dict(raw).items() if k in DEFAULTS}}
    for k in ("enabled", "idle_motion", "idle_wheels", "speech_gestures", "led"):
        s[k] = bool(s[k])
    s["idle_min_s"] = _int(s["idle_min_s"], 30, 5, 3600)
    s["idle_max_s"] = max(s["idle_min_s"], _int(s["idle_max_s"], 120, 5, 3600))
    s["wheel_ms"] = _int(s["wheel_ms"], 120, 60, 300)
    s["quiet_hours"] = str(s["quiet_hours"] or "")
    return s


def behavior_frame(raw: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Khung gửi xuống robot. Giờ yên lặng hoặc `enabled=false` -> tắt hết cử động và đèn."""
    s = settings(raw)
    quiet = in_quiet_hours(s["quiet_hours"], now or datetime.now())
    on = s["enabled"] and not quiet
    return {
        "type": "behavior",
        "idle": on and s["idle_motion"],
        "idle_wheels": on and s["idle_motion"] and s["idle_wheels"],
        "speech_gestures": on and s["speech_gestures"],
        "led": on and s["led"],
        "idle_min_s": s["idle_min_s"], "idle_max_s": s["idle_max_s"], "wheel_ms": s["wheel_ms"],
        "quiet": quiet,
    }


def gesture_for_text(text: str, emotion: str = "neutral") -> Optional[str]:
    """Cử chỉ cho câu robot sắp nói. Chào / tạm biệt -> vẫy tay; theo cảm xúc của câu
    (emotion_for_text): phấn khích -> excited, buồn / khóc -> sad, ngạc nhiên -> ngó quanh;
    xác nhận ("vâng", "dạ", "đồng ý") hoặc vui -> gật đầu. Không rõ -> None (để nhịp gật tự nhiên)."""
    folded = _fold(text)
    if any(f" {k} " in folded for k in _GREETING):
        return "wave_hand"
    if emotion == "excited":
        return "excited"
    if emotion in ("sad", "cry"):
        return "sad"
    if emotion == "wow":
        return "look_around"
    if emotion in ("happy", "love") or any(folded.startswith(f" {k} ") for k in _AFFIRM):
        return "nod_head"
    return None
