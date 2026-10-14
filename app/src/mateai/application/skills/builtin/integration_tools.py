# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/integration_tools.py
================================
Integration Tools — Phase 59 Universal Enterprise Connector Hub.

Bọc các Connector (AWS, OCI, Paperless, eInvoice) thành Tool Calling
cho 9router (Claude/Gemini). Mỗi tool tương ứng 1 action cụ thể.

System Prompt hướng dẫn AI:
---
"Bạn có khả năng truy cập các hệ thống ngoài qua các công cụ sau:
- check_aws_cost: Kiểm tra chi phí AWS (Cost Explorer). Dùng khi user hỏi về chi phí cloud, billing.
- check_aws_instances: Lấy trạng thái EC2 instances. Dùng khi hỏi về server, instance.
- check_oci_metrics: Lấy metrics CPU/RAM OCI. Dùng khi hỏi về Oracle Cloud, hiệu năng server.
- check_oci_instances: Lấy danh sách OCI instances.
- search_paperless_documents: Tìm tài liệu/hợp đồng trong Paperless. Dùng khi hỏi về hợp đồng, văn bản, OCR.
- get_paperless_document: Lấy chi tiết 1 tài liệu Paperless.
- check_einvoice_daily: Thống kê hóa đơn điện tử ngày. Dùng khi hỏi về doanh thu, HĐĐT, lỗi hóa đơn.
- search_einvoices: Tìm kiếm hóa đơn điện tử.
---
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from mateai.config.loader import settings
from mateai.infrastructure.connectors import (
    aws_connector,
    oci_connector,
    paperless_connector,
    einvoice_connector,
    CONNECTOR_RISK_LEVELS,
)
from mateai.infrastructure.connectors.base_connector import ConnectorResult
from core.plugin_manager import export_skill
from mateai.application.security.zero_trust import hitl_manager, execute_with_hitl

logger = logging.getLogger(__name__)


# ================================================================
# Helper: Execute connector with HITL integration
# ================================================================

async def _execute_connector_action(
    connector_name: str,
    action: str,
    params: Dict[str, Any],
    requested_by: str = "AI_Agent",
) -> Dict[str, Any]:
    """
    Thực thi action của connector qua Zero-Trust HITL gate.
    Async-native implementation - không tạo event loop mới.
    """
    # Map connector:action -> risk key
    risk_key = f"{connector_name}:{action}"
    risk_level = CONNECTOR_RISK_LEVELS.get(risk_key, 1)

    # Get connector instance
    connectors = {
        "aws": aws_connector,
        "oci": oci_connector,
        "paperless": paperless_connector,
        "einvoice": einvoice_connector,
    }
    connector = connectors.get(connector_name)
    if not connector:
        return {"success": False, "error": f"Unknown connector: {connector_name}"}

    # Define async executor
    async def _async_executor() -> Dict[str, Any]:
        try:
            result: ConnectorResult = await connector.fetch_data({"action": action, **params})
            return {
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "latency_ms": result.latency_ms,
                "source": result.source,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Execute with HITL
    result = await execute_with_hitl(
        action_name=f"connector_{connector_name}_{action}",
        params=params,
        executor=_async_executor,
        requested_by=requested_by,
        agent_id="VN-MATEAI-CONNECTOR",
        check_rbac=False,
        description=f"Gọi {connector_name.upper()} {action} với tham số: {json.dumps(params, ensure_ascii=False)[:200]}",
    )

    # If awaiting approval, return standardized awaiting response
    if result.get("status") == "denied":
        return {"success": False, "error": result.get("message"), "policy_denied": True}
    if result.get("status") == "awaiting_approval":
        return {
            "success": False,
            "awaiting_approval": True,
            "approval_id": result.get("approval_id"),
            "message": result.get("message"),
            "risk_level": result.get("risk_level"),
        }

    # If executed, normalize result
    exec_result = result.get("result", {})
    return {
        "success": exec_result.get("success", False),
        "data": exec_result.get("data"),
        "error": exec_result.get("error"),
        "latency_ms": exec_result.get("latency_ms"),
        "source": exec_result.get("source"),
    }


# ================================================================
# AWS Tools (async wrappers)
# ================================================================

@export_skill(
    name="check_aws_cost",
    data_classification="CONFIDENTIAL",
    description="Kiểm tra chi phí AWS (AWS Cost Explorer) trong khoảng thời gian. Trả về tổng chi phí USD và breakdown theo service.",
    parameters_schema={
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Ngày bắt đầu (YYYY-MM-DD). Mặc định: 30 ngày trước."},
            "end_date": {"type": "string", "description": "Ngày kết thúc (YYYY-MM-DD). Mặc định: hôm nay."},
            "granularity": {"type": "string", "enum": ["DAILY", "MONTHLY", "HOURLY"], "description": "Độ chi tiết. Mặc định: MONTHLY."},
            "group_by": {"type": "array", "items": {"type": "string"}, "description": "Group theo dimension (SERVICE, LINKED_ACCOUNT...). Mặc định: ['SERVICE']."},
        },
        "required": [],
    },
)
async def check_aws_cost(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    granularity: str = "MONTHLY",
    group_by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Tool: Kiểm tra chi phí AWS.
    Dùng khi user hỏi: "Chi phí AWS tháng này bao nhiêu?", "Billing AWS ra sao?", "Cost Explorer".
    """
    return await _execute_connector_action(
        connector_name="aws",
        action="billing_summary",
        params={"start_date": start_date, "end_date": end_date, "granularity": granularity, "group_by": group_by},
        requested_by="AI_Agent",
    )


@export_skill(
    name="check_aws_instances",
    description="Lấy danh sách EC2 instances đang chạy trên AWS. Trả về instance ID, state, type, IP, tags.",
    parameters_schema={
        "type": "object",
        "properties": {
            "state_filter": {"type": "array", "items": {"type": "string"}, "description": "Lọc theo state (running, stopped, terminated...). Mặc định: ['running']."},
            "tag_filters": {"type": "object", "description": "Lọc theo tag key-value (ví dụ: {'Environment': 'prod'})."},
        },
        "required": [],
    },
)
async def check_aws_instances(
    state_filter: Optional[List[str]] = None,
    tag_filters: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Tool: Lấy trạng thái EC2 instances.
    Dùng khi user hỏi: "Server AWS nào đang chạy?", "Danh sách EC2", "Instance status".
    """
    return await _execute_connector_action(
        connector_name="aws",
        action="instance_status",
        params={"state_filter": state_filter, "tag_filters": tag_filters},
        requested_by="AI_Agent",
    )


# ================================================================
# OCI Tools (async wrappers)
# ================================================================

@export_skill(
    name="check_oci_instances",
    description="Lấy danh sách Compute instances trên Oracle Cloud (OCI). Trả về OCID, display name, shape, state, IP.",
    parameters_schema={
        "type": "object",
        "properties": {
            "compartment_id": {"type": "string", "description": "OCID compartment. Mặc định: config OCI_COMPARTMENT_ID."},
            "state_filter": {"type": "array", "items": {"type": "string"}, "description": "Lọc theo lifecycle state (RUNNING, STOPPED...). Mặc định: ['RUNNING']."},
        },
        "required": [],
    },
)
async def check_oci_instances(
    compartment_id: Optional[str] = None,
    state_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Tool: Lấy danh sách OCI instances.
    Dùng khi user hỏi: "Server Oracle Cloud nào đang chạy?", "OCI instances", "Compute OCI".
    """
    return await _execute_connector_action(
        connector_name="oci",
        action="instance_list",
        params={"compartment_id": compartment_id, "state_filter": state_filter},
        requested_by="AI_Agent",
    )


@export_skill(
    name="check_oci_metrics",
    description="Lấy metrics CPU/RAM/Network của OCI Compute instances (Monitoring API).",
    parameters_schema={
        "type": "object",
        "properties": {
            "compartment_id": {"type": "string", "description": "OCID compartment. Mặc định: config."},
            "instance_ids": {"type": "array", "items": {"type": "string"}, "description": "List OCID instance để lọc. None = tất cả."},
            "metrics": {"type": "array", "items": {"type": "string"}, "description": "Metric names (CpuUtilization, MemoryUtilization, NetworkBytesIn, NetworkBytesOut). Mặc định: tất cả."},
            "start_time": {"type": "string", "description": "ISO datetime bắt đầu. Mặc định: 1 giờ trước."},
            "end_time": {"type": "string", "description": "ISO datetime kết thúc. Mặc định: now."},
            "interval_minutes": {"type": "integer", "description": "Khoảng lấy mẫu (phút). Mặc định: 5."},
        },
        "required": [],
    },
)
async def check_oci_metrics(
    compartment_id: Optional[str] = None,
    instance_ids: Optional[List[str]] = None,
    metrics: Optional[List[str]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval_minutes: int = 5,
) -> Dict[str, Any]:
    """
    Tool: Lấy metrics OCI (CPU, Memory, Network).
    Dùng khi user hỏi: "CPU OCI bao nhiêu?", "RAM Oracle Cloud", "Hiệu năng server OCI", "Monitoring OCI".
    """
    return await _execute_connector_action(
        connector_name="oci",
        action="cloud_metrics",
        params={
            "compartment_id": compartment_id,
            "instance_ids": instance_ids,
            "metrics": metrics,
            "start_time": start_time,
            "end_time": end_time,
            "interval_minutes": interval_minutes,
        },
        requested_by="AI_Agent",
    )


# ================================================================
# Paperless Tools (async wrappers)
# ================================================================

@export_skill(
    name="search_paperless_documents",
    description="Tìm kiếm tài liệu/hợp đồng/văn bản trong Paperless-ngx (full-text OCR). Trả về danh sách document với snippet nội dung.",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm kiếm (tên file, nội dung OCR, correspondent...)."},
            "limit": {"type": "integer", "description": "Số kết quả tối đa (mặc định 10, max 100)."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Lọc theo tag names."},
            "correspondents": {"type": "array", "items": {"type": "string"}, "description": "Lọc theo correspondent (người gửi/nhận)."},
            "document_types": {"type": "array", "items": {"type": "string"}, "description": "Lọc theo loại tài liệu (Hợp đồng, Hóa đơn, CV...)."},
            "created_after": {"type": "string", "description": "Ngày tạo sau (YYYY-MM-DD)."},
            "created_before": {"type": "string", "description": "Ngày tạo trước (YYYY-MM-DD)."},
        },
        "required": ["query"],
    },
)
async def search_paperless_documents(
    query: str,
    limit: int = 10,
    tags: Optional[List[str]] = None,
    correspondents: Optional[List[str]] = None,
    document_types: Optional[List[str]] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
) -> Dict[str, Any]:
    return await _execute_connector_action(
        connector_name="paperless",
        action="search",
        params={
            "query": query,
            "limit": limit,
            "tags": tags,
            "correspondents": correspondents,
            "document_types": document_types,
            "created_after": created_after,
            "created_before": created_before,
        },
        requested_by="AI_Agent",
    )


@export_skill(
    name="get_paperless_document",
    description="Lấy chi tiết đầy đủ 1 tài liệu Paperless (full OCR content, metadata, download URL).",
    parameters_schema={
        "type": "object",
        "properties": {
            "doc_id": {"type": "integer", "description": "Document ID từ kết quả search."},
        },
        "required": ["doc_id"],
    },
)
async def get_paperless_document(doc_id: int) -> Dict[str, Any]:
    """
    Tool: Chi tiết tài liệu Paperless.
    Dùng sau khi search để lấy full content.
    """
    return await _execute_connector_action(
        connector_name="paperless",
        action="details",
        params={"doc_id": doc_id},
        requested_by="AI_Agent",
    )


@export_skill(
    name="download_paperless_document",
    description="Tải file gốc (PDF) của tài liệu Paperless. Trả về base64 encoded.",
    parameters_schema={
        "type": "object",
        "properties": {
            "doc_id": {"type": "integer", "description": "Document ID."},
            "original": {"type": "boolean", "description": "True = file gốc, False = archived PDF/A. Mặc định: False."},
        },
        "required": ["doc_id"],
    },
)
async def download_paperless_document(doc_id: int, original: bool = False) -> Dict[str, Any]:
    """
    Tool: Download tài liệu Paperless.
    Dùng khi user cần file gốc.
    """
    return await _execute_connector_action(
        connector_name="paperless",
        action="download",
        params={"doc_id": doc_id, "original": original},
        requested_by="AI_Agent",
    )


# ================================================================
# eInvoice Tools (async wrappers)
# ================================================================

@export_skill(
    name="check_einvoice_daily",
    data_classification="CONFIDENTIAL",
    description="Thống kê hóa đơn điện tử (HĐĐT) trong 1 ngày: số lượng, tổng tiền, thuế, số lỗi, hủy.",
    parameters_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Ngày thống kê (YYYY-MM-DD). Mặc định: hôm nay."},
            "tax_code": {"type": "string", "description": "Mã số thuế doanh nghiệp. Mặc định: config EINVOICE_TAX_CODE."},
        },
        "required": [],
    },
)
async def check_einvoice_daily(
    date: Optional[str] = None,
    tax_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Tool: Thống kê HĐĐT ngày.
    Dùng khi user hỏi: "Hôm nay xuất bao nhiêu hóa đơn?", "Doanh thu hôm nay", "HĐĐT lỗi bao nhiêu", "eInvoice daily".
    """
    return await _execute_connector_action(
        connector_name="einvoice",
        action="daily_summary",
        params={"date": date, "tax_code": tax_code},
        requested_by="AI_Agent",
    )


@export_skill(
    name="search_einvoices",
    data_classification="CONFIDENTIAL",
    description="Tìm kiếm hóa đơn điện tử theo từ khóa, mã số thuế người mua, trạng thái, khoảng ngày.",
    parameters_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "Từ khóa (số hóa đơn, tên người mua...)."},
            "buyer_tax_code": {"type": "string", "description": "Mã số thuế người mua."},
            "status": {"type": "string", "enum": ["success", "failed", "cancelled"], "description": "Trạng thái HĐĐT."},
            "date_from": {"type": "string", "description": "Từ ngày (YYYY-MM-DD)."},
            "date_to": {"type": "string", "description": "Đến ngày (YYYY-MM-DD)."},
            "limit": {"type": "integer", "description": "Số kết quả tối đa (mặc định 20)."},
            "offset": {"type": "integer", "description": "Phân trang (mặc định 0)."},
        },
        "required": [],
    },
)
async def search_einvoices(
    keyword: Optional[str] = None,
    buyer_tax_code: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Tool: Tìm kiếm HĐĐT.
    Dùng khi user hỏi: "Hóa đơn 001/2024 ra sao?", "HĐĐT lỗi ngày nào?", "Tìm hóa đơn khách X".
    """
    return await _execute_connector_action(
        connector_name="einvoice",
        action="search",
        params={
            "keyword": keyword,
            "buyer_tax_code": buyer_tax_code,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        },
        requested_by="AI_Agent",
    )


@export_skill(
    name="get_einvoice_details",
    data_classification="CONFIDENTIAL",
    description="Lấy chi tiết 1 hóa đơn điện tử (items, seller, buyer, XML, PDF URL).",
    parameters_schema={
        "type": "object",
        "properties": {
            "invoice_id": {"type": "string", "description": "Invoice ID/UUID từ kết quả search."},
        },
        "required": ["invoice_id"],
    },
)
async def get_einvoice_details(invoice_id: str) -> Dict[str, Any]:
    """
    Tool: Chi tiết HĐĐT.
    Dùng sau search để lấy full info.
    """
    return await _execute_connector_action(
        connector_name="einvoice",
        action="details",
        params={"invoice_id": invoice_id},
        requested_by="AI_Agent",
    )


# ================================================================
# Health Check Tools (cho monitoring)
# ================================================================
# Health Check Tools (async wrappers)
# ================================================================

@export_skill(
    name="check_connector_health",
    description="Kiểm tra sức khỏe kết nối các hệ thống ngoại vi (AWS, OCI, Paperless, eInvoice).",
    parameters_schema={
        "type": "object",
        "properties": {
            "connectors": {"type": "array", "items": {"type": "string", "enum": ["aws", "oci", "paperless", "einvoice"]}, "description": "Danh sách connector cần check. Mặc định: tất cả."},
        },
        "required": [],
    },
)
async def check_connector_health(connectors: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Tool: Health check tất cả connector.
    Dùng cho monitoring, proactive agent.
    """
    connectors = connectors or ["aws", "oci", "paperless", "einvoice"]
    connector_map = {
        "aws": aws_connector,
        "oci": oci_connector,
        "paperless": paperless_connector,
        "einvoice": einvoice_connector,
    }

    results = {}
    overall_healthy = True

    for name in connectors:
        conn = connector_map.get(name)
        if not conn:
            results[name] = {"success": False, "error": "Unknown connector"}
            overall_healthy = False
            continue

        try:
            health = await conn.health_check()
            results[name] = {
                "success": health.success,
                "data": health.data,
                "error": health.error,
                "latency_ms": health.latency_ms,
            }
            if not health.success:
                overall_healthy = False
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
            overall_healthy = False

    return {
        "success": overall_healthy,
        "overall_healthy": overall_healthy,
        "connectors": results,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }