# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/secret_masking.py
========================================
Che / khôi phục giá trị bí mật (khoá LLM, token bot, mật khẩu...) khi cấu hình
đi ra và vào qua API: GET trả ký hiệu thay cho giá trị thật; POST gửi lại ký
hiệu thì giữ giá trị đang lưu. Dùng chung cho /api/v1/config, /api/v1/telegram,
bộ lọc log và các nút "thử kết nối".
"""
from __future__ import annotations

from typing import Any


# Ký hiệu thay cho giá trị thật của một trường bí mật khi trả ra ngoài.
#
# Dùng đúng ký hiệu mà giao diện đã dùng cho ô mật khẩu connector, nên
# frontend nhận về cùng một giá trị quen thuộc ở mọi nơi có bí mật.
_SECRET_MASK = "••••••••"

# Tên trường được coi là bí mật. So KHỚP CHÍNH XÁC tên đã quy đổi chữ thường,
# không dò chuỗi con — vì `security.forbidden_keywords` chứa chữ "key" mà
# là CHÍNH SÁCH chặn lệnh nguy hiểm, không phải bí mật; che nhầm sẽ làm
# mất cấu hình bảo mật mà không ai hiểu vì sao.
#
# `api_keys` (số nhiều) là danh sách khoá của lớp định tuyến cũ.
# Danh sách thật: mateai.config.secret_box.SECRET_FIELD_NAMES (nhập bên dưới).


# Một nguồn với lớp mã hoá config.json (trước đây hai danh sách riêng — danh sách
# này thiếu `elevenlabs_api_key`, nên GET /api/v1/config trả khoá ElevenLabs nguyên văn).
from mateai.config.secret_box import SECRET_FIELD_NAMES as _SECRET_FIELD_NAMES  # noqa: E402


def _is_secret_field(name: str) -> bool:
    return str(name).lower() in _SECRET_FIELD_NAMES


def _mask_secrets(value: Any) -> Any:
    """
    Trả về bản sao của `value` với mọi trường bí mật đã thay bằng ký hiệu.

    - Chỉ che khi trường CÓ giá trị. Rỗng vẫn hiện rỗng, để phân biệt
      "chưa cấu hình" với "đã lưu nhưng không tiện hiện".
    - Trường bí mật kiểu DANH SÁCH (`api_keys` của lớp định tuyến cũ) che TỪNG
      phần tử và giữ nguyên kiểu list. Thay cả danh sách bằng một chuỗi là đổi
      kiểu dữ liệu: bên đọc không còn biết có bao nhiêu khoá, và lúc ghi lại
      cũng khôi phục không đúng số phần tử.
    - `bool` không bị che: đó là cờ bật/tắt, che thành ký hiệu sẽ làm hỏng
      công tắc trên giao diện.
    """
    if isinstance(value, dict):
        return {k: _mask_secret_field(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_secrets(v) for v in value]
    return value


def _mask_secret_field(name: str, value: Any) -> Any:
    """Che đúng một trường, giữ nguyên kiểu dữ liệu của nó."""
    if not _is_secret_field(name):
        return _mask_secrets(value)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append({k: _mask_secret_field(k, v) for k, v in item.items()})
            elif _has_secret_value(item):
                out.append(_SECRET_MASK)
            else:
                out.append(item)
        return out
    if isinstance(value, dict):
        return {k: _mask_secret_field(k, v) for k, v in value.items()}
    if _has_secret_value(value):
        return _SECRET_MASK
    return value


def _has_secret_value(value: Any) -> bool:
    """Trường bí mật này có đáng che không (có giá trị thật chứ không rỗng)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, str):
        return value.strip() != "" and value != _SECRET_MASK
    return value is not None


def _strip_mask_chars(value: Any) -> Any:
    """
    Bỏ ký hiệu che (•) khỏi một giá trị bí mật người dùng gửi lên.

    Ô bí mật trên giao diện hiện sẵn `••••••••`; người dùng dán khoá mới vào SAU
    ký hiệu thay vì xoá nó trước → gửi lên `••••••••<khoá>` và trước đây chuỗi đó
    được lưu nguyên (token Telegram hỏng, gateway không gửi được). Ký tự • không
    bao giờ có trong khoá/token thật. Còn rỗng sau khi bỏ = "giữ giá trị cũ".
    """
    if isinstance(value, str) and "•" in value:
        cleaned = value.replace("•", "").strip()
        return cleaned or _SECRET_MASK
    return value


def _restore_masked_secrets(payload: Any, existing: Any) -> Any:
    """
    Trả về `payload` với bí mật đang lưu được giữ lại.

    Ngược lại với `_mask_secrets`. Xử lý HAI trường hợp, cùng một ý nghĩa:
    "bí mật mà người dùng không chủ động gửi lên thì giữ nguyên".

    1. Gửi KÝ HIỆU. Giao diện đọc cấu hình, thấy `••••••••`, để nguyên ô rồi
       bấm Lưu — ký hiệu sẽ đi thẳng xuống config.json và thay khoá thật.

    2. KHÔNG GỬI FIELD ĐÓ, hoặc gửi RỖNG. Quan trọng hơn nhiều và dễ sót.
       `merged = {**existing, **payload}` ghép NÔNG, nên `payload["telegram"]`
       thay THẾ trọn khối telegram của bản lưu. Nếu khối đó không kèm
       `bot_token`, token biến mất khỏi config.json trong khi người dùng chỉ
       định sửa một trường khác. Bỏ field ở phía client KHÔNG cứu được:
       client không gửi field, nhưng merge vẫn thay cả khối. Phải chép lại
       từ bản lưu ở đây.

    Hệ quả có chủ ý: KHÔNG có cách xoá bí mật qua form này — ô trống luôn
    được hiểu là "giữ". Đổi lại: không bao giờ mất khoá một cách âm thầm. Muốn
    xoá thì sửa config.json trực tiếp. Trước Phase 80 điều này vốn đã không
    làm được với `llm.api_key` (nhánh `or existing...` chặn rồi), nên không
    mất tính năng nào.

    Chỉ chép trường THỰC SỰ là bí mật, và chỉ khi bản lưu có giá trị — không
    tự thêm khoá vào config khi chưa từng có.
    """
    if isinstance(payload, dict):
        if not isinstance(existing, dict):
            existing = {}
        payload = {
            k: (_strip_mask_chars(v) if _is_secret_field(k) and not isinstance(v, list)
                else ([_strip_mask_chars(i) for i in v] if _is_secret_field(k) and isinstance(v, list) else v))
            for k, v in payload.items()
        }
        # (1)+(2) Bí mật nào KHÔNG bị người dùng ghi đè bằng giá trị có
        # thật thì lấy lại từ bản lưu. Ghi đè ở đây nghĩa là: payload có
        # field đó VÀ giá trị gửi lên là thật (không phải rỗng, không phải
        # ký hiệu).
        out = {
            k: v
            for k, v in existing.items()
            if _is_secret_field(k)
            and _has_secret_value(v)
            and not (
                k in payload
                and payload[k] != _SECRET_MASK
                and _has_secret_value(payload[k])
            )
        }
        # Đệ quy xuống khối con — payload lồng nhau (`{"llm": {...}}`,
        # `{"telegram": {...}}`) và bí mật nằm sâu bên trong.
        for k, v in payload.items():
            if k in out and not isinstance(v, (dict, list)):
                continue  # đã chép bản lưu ở trên
            out[k] = _restore_masked_secrets(v, existing.get(k))
        return out
    if isinstance(payload, list):
        # `api_keys` là danh sách: thay từng phần tử đang là ký hiệu.
        if isinstance(existing, list):
            return [
                existing[i] if (i < len(existing) and item == _SECRET_MASK) else
                _restore_masked_secrets(item, None)
                for i, item in enumerate(payload)
            ]
        return [_restore_masked_secrets(item, None) for item in payload]
    return payload
