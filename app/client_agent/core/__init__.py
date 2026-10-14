# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Client Agent Core package."""
try:
    from core.plugin_manager import export_skill, client_plugin_manager
except ImportError:
    from client_agent.core.plugin_manager import export_skill, client_plugin_manager
