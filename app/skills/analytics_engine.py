# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/analytics_engine.py
==========================
Phase 57: Re-export dynamic analytics skills for plugin manager discovery.
"""

from __future__ import annotations

from mateai.application.analytics.analytics_engine import (
    check_cashflow_predictive_health,
    generate_dynamic_sql_chart,
)

__all__ = [
    "generate_dynamic_sql_chart",
    "check_cashflow_predictive_health",
]
