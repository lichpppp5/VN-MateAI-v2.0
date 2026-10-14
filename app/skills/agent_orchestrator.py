# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/agent_orchestrator.py
============================
Phase 57: Re-export multi-agent skills for dynamic plugin discovery.
"""

from __future__ import annotations

from mateai.application.agent.agent_orchestrator import (
    delegate_to_multi_agent,
    get_enterprise_executive_summary,
    get_executive_standup_briefing,
)

__all__ = [
    "delegate_to_multi_agent",
    "get_enterprise_executive_summary",
    "get_executive_standup_briefing",
]
