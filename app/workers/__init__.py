# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
workers/__init__.py
===================
VN-MateAI Worker Cluster Execution Engine (Phase 90).
Specialized for macOS / Work Node Headless RPA Nodes.
"""

from workers.browser_session_vault import BrowserSessionVault
from workers.self_healing_engine import SelfHealingUIEngine
from workers.native_os_driver import NativeOSDriver

__all__ = [
    "BrowserSessionVault",
    "SelfHealingUIEngine",
    "NativeOSDriver",
]
