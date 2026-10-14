# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/erp_organization.py
==========================
Đăng ký kỹ năng tra cứu tổ chức ERP cho plugin_manager.

Single Source of Truth: core/skills/erp_organization.py
"""

from __future__ import annotations

from mateai.application.skills.builtin.erp_organization import query_organization_data

__all__ = ["query_organization_data"]
