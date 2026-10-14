# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/graph_rag.py
===================
Phase 57: Re-export Enterprise GraphRAG skill for dynamic plugin discovery.
"""

from __future__ import annotations

from mateai.application.knowledge.graph_rag import query_enterprise_graph_rag

__all__ = [
    "query_enterprise_graph_rag",
]
