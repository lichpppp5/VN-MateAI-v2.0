# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/robotics_tools.py
=========================
Phase 52: Full Autonomous Robotics (Body-Mind Sync).

Khai báo và liên kết các kỹ năng điều khiển Robot vật lý từ core/skills/robotics_tools.py.
"""

from __future__ import annotations

from mateai.application.skills.builtin.robotics_tools import (
    move_robot,
    animate_robot,
)

__all__ = [
    "move_robot",
    "animate_robot",
]
