# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/__init__.py
===========================
Enterprise Connectors Package — Phase 59 Universal Enterprise Connector Hub.

Exports all concrete adapters and the base class.
"""

from __future__ import annotations

from mateai.infrastructure.connectors.base_connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    CONNECTOR_DEFAULTS,
    CONNECTOR_ENV_MAP,
    load_connector_settings,
)

from mateai.infrastructure.connectors.aws_connector import AWSConnector, aws_connector
from mateai.infrastructure.connectors.oci_connector import OCIConnector, oci_connector
from mateai.infrastructure.connectors.paperless_connector import PaperlessConnector, paperless_connector
from mateai.infrastructure.connectors.einvoice_connector import EInvoiceConnector, einvoice_connector

# Phase 62: connector động — khai báo qua JSON, không cần code mới cho mỗi app.
from mateai.infrastructure.connectors import custom_registry
from mateai.infrastructure.connectors.generic_connector import (
    GenericConnector,
    fetch_data_source,
    probe_data_source,
)

# Registry of all available connectors
CONNECTOR_REGISTRY: dict = {
    "aws": aws_connector,
    "oci": oci_connector,
    "paperless": paperless_connector,
    "einvoice": einvoice_connector,
}

# Risk level mapping for Zero-Trust integration
# Level 1-2: Auto-approved | Level 3-5: Require HITL approval
CONNECTOR_RISK_LEVELS: dict = {
    "aws:billing_summary": 1,
    "aws:instance_status": 1,
    "oci:instance_list": 1,
    "oci:cloud_metrics": 1,
    "paperless:search": 1,
    "paperless:details": 1,
    "paperless:download": 2,
    "einvoice:daily_summary": 1,
    "einvoice:search": 1,
    "einvoice:details": 1,
    # Phase 63: tool AI Ly Ly đọc/xuất báo cáo từ nguồn tùy chỉnh.
    # Gọi ra hệ thống ngoài của khách hàng nên risk 2 (đi qua cổng HITL,
    # tự động nếu đã được tin cậy). Không có khoá này thì `_risk_for()`
    # mặc định 1 — tức tự động chạy, mất đúng lớp phòng thủ đó.
    "datasource:list": 1,
    "datasource:fetch": 2,
    "datasource:export": 2,
    # Thao tác can thiệp khai báo trong nguồn dữ liệu: mức thật lấy từ chính khai báo (>= 3).
    "datasource:action": 3,
}

__all__ = [
    # Base
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorResult",
    # Concrete
    "AWSConnector",
    "aws_connector",
    "OCIConnector",
    "oci_connector",
    "PaperlessConnector",
    "paperless_connector",
    "EInvoiceConnector",
    "einvoice_connector",
    # Phase 62: data source tùy chỉnh
    "GenericConnector",
    "custom_registry",
    "fetch_data_source",
    "probe_data_source",
    # Registry
    "CONNECTOR_REGISTRY",
    "CONNECTOR_RISK_LEVELS",
    "CONNECTOR_ENV_MAP",
    "CONNECTOR_DEFAULTS",
    # Cấu hình runtime
    "load_connector_settings",
]