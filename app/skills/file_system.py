# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/file_system.py
=====================
Phase 38: Native File System & OS Toolkit (OpenClaw Parity).

Khai báo và liên kết các kỹ năng thao tác File System gốc từ core/skills/file_system.py.
"""

from __future__ import annotations

from mateai.application.skills.builtin.file_system import (
    list_directory,
    read_file,
    write_file,
    delete_item,
)

__all__ = [
    "list_directory",
    "read_file",
    "write_file",
    "delete_item",
]
