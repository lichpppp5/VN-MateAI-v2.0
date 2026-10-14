# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/proactive_manager.py
===========================
Phase 56: Re-export proactive manager skills for dynamic plugin discovery.
"""

from __future__ import annotations

from mateai.application.skills.builtin.proactive_manager import (
    assign_task_intelligently,
    run_proactive_task_audit,
)

__all__ = [
    "assign_task_intelligently",
    "run_proactive_task_audit",
]
