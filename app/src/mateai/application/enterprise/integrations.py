# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/enterprise/integrations.py
=============================================
Danh mục / trạng thái connector, kênh cảnh báo, nguồn dữ liệu — dữ liệu cho trang
Tích hợp và Quản trị (Phase 59, 62, 81). Chuyển nguyên văn từ
`interfaces/http/routers/enterprise.py` (Supervisor Phase 10, §198: nghiệp vụ không
nằm trong HTTP route); router chỉ còn xác thực + trả kết quả.

Không bao giờ trả giá trị bí mật — chỉ TÊN khoá còn thiếu.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Khoá nào phải che (không bao giờ trả về giá trị, kể cả khi đã cấu hình).
_CONNECTOR_SECRET_FIELDS = frozenset({
    "webhook_url",
    "hmac_secret",
    "password",
    "secret_access_key",
    "api_token",
    "client_secret",
    "access_key_id",
    "private_key",
    "api_key",
    "token",
})


_CONNECTOR_DISPLAY: Dict[str, Dict[str, str]] = {
    "aws": {
        "display_name": "Amazon Web Services",
        "description": "Chi phí & trạng thái EC2/CloudWatch qua AWS API.",
    },
    "oci": {
        "display_name": "Oracle Cloud Infrastructure",
        "description": "Danh sách instance & chỉ số compute của OCI.",
    },
    "paperless": {
        "display_name": "Paperless-ngx",
        "description": "Tra cứu và tải tài liệu trong kho Paperless.",
    },
    "einvoice": {
        "display_name": "Hóa đơn điện tử",
        "description": "Phát hành & tra cứu hóa đơn qua nhà cung cấp hóa đơn.",
    },
}


def _build_connector_config_schema(name: str) -> Dict[str, Any]:
    """
    Sinh JSON Schema cấu hình cho một connector từ bảng khai báo sẵn có.

    Mục tiêu: giao diện Admin không phải viết tay form cho từng connector. Backend
    đã biết (a) khoá nào BẮT BUỘC và (b) khoá nào có sẵn giá trị mặc định —
    hai bảng đó đủ để dựng form. Thêm connector mới ở Python là có form mới,
    không cần sửa frontend.

    Chỉ trả TÊN khoá, tuyệt đối không trả giá trị: `missing_required_fields()`
    cũng vậy. Nếu form đã lưu khoá rồi, người dùng thấy dấu "đã đặt" chứ không
    thấy khoá bí mật của họ.
    """
    from mateai.infrastructure.connectors.base_connector import (
        CONNECTOR_DEFAULTS,
        CONNECTOR_REQUIRED_FIELDS,
        missing_required_fields,
    )

    defaults = CONNECTOR_DEFAULTS.get(name, {})
    required = set(CONNECTOR_REQUIRED_FIELDS.get(name, ()))
    missing = set(missing_required_fields(name))

    properties: Dict[str, Any] = {}

    # Trường bắt buộc đưa lên trước — đó là phần người vận hành phải điền.
    ordered_keys = sorted(required) + sorted(k for k in defaults if k not in required)

    for key in ordered_keys:
        is_required = key in required
        is_missing = key in missing
        default_val = defaults.get(key)

        prop: Dict[str, Any] = {
            "type": "boolean" if isinstance(default_val, bool) else "string",
            "title": key.replace("_", " ").capitalize(),
        }
        if is_required:
            prop["description"] = "Bắt buộc — connector sẽ không chạy nếu thiếu."
        if key in _CONNECTOR_SECRET_FIELDS:
            # `format: secret` khiến DynamicForm hiện dấu *** và có nút bật/tắt.
            prop["format"] = "secret"
            prop["ui"] = {"widget": "text"}
        if default_val not in (None, ""):
            prop["default"] = default_val
        elif is_required and key not in _CONNECTOR_SECRET_FIELDS:
            prop["ui"] = {"widget": "text"}

        properties[key] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
    }


def _alert_channel_catalog() -> Dict[str, Any]:
    """Kênh cảnh báo (Teams, Email, Outlook, Slack, Webhook) + quy tắc chung, cùng
    định dạng connector để Portal dựng form "chờ kết nối" và lưu vào config.json."""
    from mateai.infrastructure.notifications import CHANNELS, RULES_ID, config_schema, load_settings, missing_fields
    out: Dict[str, Any] = {
        RULES_ID: {
            "id": RULES_ID, "kind": "alert_rules", "display_name": "Quy tắc cảnh báo",
            "description": "Mức gửi tối thiểu, chống lặp, tự báo khi thành phần trên sơ đồ hệ thống bị lỗi.",
            "configured": True, "missing_fields": [], "actions": [], "max_risk_level": None,
            "config_schema": config_schema(RULES_ID),
        },
    }
    for cid, spec in CHANNELS.items():
        s = load_settings(cid)
        missing = missing_fields(cid, s)
        out[cid] = {
            "id": cid, "kind": "alert_channel", "display_name": f"Cảnh báo · {spec['display_name']}",
            "description": spec["description"], "configured": not missing, "enabled": bool(s.get("enabled")),
            "missing_fields": missing, "actions": [], "max_risk_level": None,
            "config_schema": config_schema(cid),
        }
    return out


def connector_health() -> Dict[str, Any]:
    """Trạng thái cấu hình 4 connector (chỉ đọc cấu hình trong bộ nhớ, không gọi ra ngoài)."""
    try:
        from mateai.infrastructure.connectors import CONNECTOR_REGISTRY, CONNECTOR_RISK_LEVELS
        from mateai.infrastructure.connectors.base_connector import missing_required_fields

        items: Dict[str, Any] = {}
        for name in ("aws", "oci", "paperless", "einvoice"):
            connector = CONNECTOR_REGISTRY.get(name)
            if connector is None:
                items[name] = {"configured": False, "error": "Connector chưa được nạp"}
                continue
            try:
                cfg = connector.config
                # "Đã cấu hình" phải trả lời đúng câu hỏi "connector này có
                # dùng được không", chứ không phải "có trường nào khác rỗng
                # không". `CONNECTOR_DEFAULTS` cấp sẵn region/provider/profile
                # nên cách sau LUÔN trả True — kể cả khi chưa có một thông tin
                # đăng nhập nào, và mọi lời gọi thật đều hỏng với
                # "Authentication failed". Đây là báo cáo thành công giả.
                missing = missing_required_fields(name)
                # CONNECTOR_RISK_LEVELS khoá theo "<connector>:<action>",
                # không phải theo tên connector — phải lọc theo tiền tố.
                risks = sorted({
                    lvl for key, lvl in CONNECTOR_RISK_LEVELS.items()
                    if key.split(":", 1)[0] == name
                })
                # KHÔNG tiết lộ giá trị bí mật — chỉ nêu TÊN khoá còn thiếu,
                # đủ để người vận hành biết cần điền gì mà không lộ nội dung.
                items[name] = {
                    "configured": not missing,
                    "enabled": bool(getattr(cfg, "enabled", True)),
                    "actions": sorted(
                        key.split(":", 1)[1]
                        for key in CONNECTOR_RISK_LEVELS
                        if key.split(":", 1)[0] == name
                    ),
                    "max_risk_level": max(risks) if risks else None,
                    **(
                        {"missing_fields": missing, "note": "Chưa đủ thông tin đăng nhập — mọi lời gọi sẽ thất bại."}
                        if missing
                        else {}
                    ),
                }
            except Exception as exc:  # pragma: no cover - phòng thủ
                items[name] = {"configured": False, "error": str(exc)}

        return {"status": "success", "connectors": items}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def connector_catalog() -> Dict[str, Any]:
    """Danh mục connector + JSON Schema cấu hình + kênh cảnh báo."""
    try:
        from mateai.infrastructure.connectors import CONNECTOR_REGISTRY
        from mateai.infrastructure.connectors.base_connector import missing_required_fields

        items: Dict[str, Any] = {}
        for name, connector in CONNECTOR_REGISTRY.items():
            meta = _CONNECTOR_DISPLAY.get(name, {})
            missing = missing_required_fields(name)
            actions: List[str] = []
            max_risk: Optional[int] = None
            try:
                from mateai.infrastructure.connectors import CONNECTOR_RISK_LEVELS

                actions = sorted(
                    k.split(":", 1)[1]
                    for k in CONNECTOR_RISK_LEVELS
                    if k.split(":", 1)[0] == name
                )
                risks = [
                    lvl for k, lvl in CONNECTOR_RISK_LEVELS.items()
                    if k.split(":", 1)[0] == name
                ]
                max_risk = max(risks) if risks else None
            except Exception:  # pragma: no cover - phòng thủ
                pass

            items[name] = {
                "id": name,
                "display_name": meta.get("display_name", name),
                "description": meta.get("description", ""),
                # `configured` = đủ khoá bắt buộc. Chưa có thì giao diện hiện
                # "chờ kết nối" thay vì "Đã kết nối" (giống quy ước Phase 73-76).
                "configured": not missing,
                "missing_fields": missing,
                "actions": actions,
                "max_risk_level": max_risk,
                "config_schema": _build_connector_config_schema(name),
            }

        items.update(_alert_channel_catalog())
        return {"status": "success", "connectors": items}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def data_sources_overview() -> Dict[str, Any]:
    """Nguồn dữ liệu tuỳ chỉnh (đã che bí mật) + 4 connector dựng sẵn."""
    try:
        from mateai.infrastructure.connectors import custom_registry
        from mateai.infrastructure.connectors.base_connector import missing_required_fields

        custom = custom_registry.list_sources(include_secrets=False)

        builtin: Dict[str, Any] = {}
        for name in ("aws", "oci", "paperless", "einvoice"):
            missing = missing_required_fields(name)
            builtin[name] = {
                "id": name,
                "title": name.upper(),
                "kind": "builtin",
                "enabled": True,
                "has_auth": not missing,
                "missing_fields": missing,
            }

        return {
            "status": "success",
            "custom": custom,
            "builtin": builtin,
            "total": len(custom) + len(builtin),
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] list data sources lỗi")
        return {"status": "error", "error": str(e)}
