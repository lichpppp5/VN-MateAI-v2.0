# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
VN-MateAI — Production & Enterprise Architecture
================================================
Gói mã nguồn lõi chuẩn hóa theo mô hình Clean Modular Monolith.

Các tầng kiến trúc (Architectural Layers):
- domain: Chứa Pure Business Entities & Value Objects (độc lập với framework & infrastructure).
- application: Chứa Use Cases, Workflows & Orchestrators (điều phối nghiệp vụ).
- infrastructure: Chứa Technical Adapters (Database, Redis, LLM, TTS, STT, Security, Observability).
- interfaces: Chứa Delivery Mechanisms (HTTP REST API, WebSocket Endpoints, CLI).
- config: Chứa quản lý cấu hình hệ thống tập trung.
"""

__version__ = "2.0.0-enterprise"
