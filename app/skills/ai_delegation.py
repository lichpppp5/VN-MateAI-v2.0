# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/ai_delegation.py
=======================
Phase 40: Dual-LLM Orchestration (Gemini Router & Claude Specialist).

Khai báo và liên kết kỹ năng ủy quyền chuyên gia (delegate_to_specialist)
từ core/skills/ai_delegation.py.
"""

from __future__ import annotations

from mateai.application.skills.builtin.ai_delegation import delegate_to_specialist

__all__ = [
    "delegate_to_specialist",
]
