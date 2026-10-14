# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/integration_tools.py
===========================
Facade re-exporting Enterprise Integration Tools from core/skills/integration_tools.py.

Single Source of Truth: core/skills/integration_tools.py
"""

from __future__ import annotations

from mateai.application.skills.builtin.integration_tools import (
    check_aws_cost,
    check_aws_instances,
    check_connector_health,
    check_einvoice_daily,
    check_oci_instances,
    check_oci_metrics,
    download_paperless_document,
    get_einvoice_details,
    get_paperless_document,
    search_einvoices,
    search_paperless_documents,
)

__all__ = [
    "check_aws_cost",
    "check_aws_instances",
    "check_oci_instances",
    "check_oci_metrics",
    "search_paperless_documents",
    "get_paperless_document",
    "download_paperless_document",
    "check_einvoice_daily",
    "search_einvoices",
    "get_einvoice_details",
    "check_connector_health",
]