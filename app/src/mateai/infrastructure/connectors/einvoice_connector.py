# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/einvoice_connector.py
=====================================
eInvoice Adapter — Phase 59 Universal Enterprise Connector Hub.

Kết nối cổng Hóa đơn điện tử (General Department of Taxation - Việt Nam hoặc các
nhà cung cấp dịch vụ trung gian như VNPT, Viettel, MISA, FPT...).

Cung cấp:
  - get_daily_invoices(): Thống kê số lượng HĐĐT đã xuất, tổng tiền, số HĐ lỗi trong ngày.
  - get_invoice_details(invoice_id): Chi tiết 1 hóa đơn.
  - search_invoices(): Tìm kiếm hóa đơn theo điều kiện.

Lưu ý: API thực tế tùy từng nhà cung cấp. File này cung cấp interface chuẩn
và implementation mẫu cho API chuẩn hóa (giả định REST + JSON).

Cấu hình (ENV VARS / config.json):
  EINVOICE_BASE_URL (ví dụ: https://api.einvoice.vnpt.vn)
  EINVOICE_CLIENT_ID
  EINVOICE_CLIENT_SECRET
  EINVOICE_TAX_CODE (Mã số thuế doanh nghiệp)
  EINVOICE_PROVIDER (vnpt/viettel/misa/fpt/custom)
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from mateai.infrastructure.connectors.base_connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    load_connector_settings,
)

logger = logging.getLogger(__name__)


class EInvoiceConnector(BaseConnector):
    """
    Adapter kết nối cổng Hóa đơn điện tử.
    """

    def __init__(self, config: Optional[ConnectorConfig] = None):
        if config is None:
            config = ConnectorConfig.from_settings("einvoice", load_connector_settings("einvoice"))
        super().__init__(config)
        self._client = None
        self._access_token = None
        self._token_expires_at = 0.0

    def _reset_cached_clients(self) -> None:
        self._client = None
        self._access_token = None
        self._token_expires_at = 0.0

    # ------------------------------------------------------------------
    # Authentication (OAuth2 Client Credentials flow - common pattern)
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Lấy access token qua OAuth2 Client Credentials."""
        import httpx

        base_url = self.config.extra.get("base_url")
        client_id = self.config.extra.get("client_id")
        client_secret = self.config.extra.get("client_secret")
        provider = self.config.extra.get("provider", "custom")
        verify_ssl = self.config.extra.get("verify_ssl", True)

        if not base_url or not client_id or not client_secret:
            logger.error("[EInvoiceConnector] Missing config: base_url, client_id, client_secret required.")
            self._authenticated = False
            return False

        # Token endpoint tùy provider
        token_url_map = {
            "vnpt": f"{base_url}/oauth/token",
            "viettel": f"{base_url}/api/v1/oauth/token",
            "misa": f"{base_url}/connect/token",
            "fpt": f"{base_url}/token",
            "custom": f"{base_url}/oauth/token",
        }
        token_url = token_url_map.get(provider, f"{base_url}/oauth/token")

        try:
            async with httpx.AsyncClient(timeout=10.0, verify=verify_ssl) as client:
                # Client Credentials Grant
                auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
                resp = await client.post(
                    token_url,
                    headers={
                        "Authorization": f"Basic {auth_header}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"grant_type": "client_credentials"},
                )

            if not resp.is_success:
                logger.error("[EInvoiceConnector] Token request failed: HTTP %d - %s", resp.status_code, resp.text[:200])
                self._authenticated = False
                return False

            token_data = resp.json()
            self._access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = __import__("time").time() + expires_in - 60  # 60s buffer

            # Setup API client with bearer token
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                timeout=self.config.timeout_seconds,
                verify=verify_ssl,
            )

            logger.info("[EInvoiceConnector] Authenticated with provider=%s", provider)
            self._authenticated = True
            self._last_auth_time = __import__("time").time()
            return True

        except Exception as e:
            logger.error("[EInvoiceConnector] Authentication failed: %s", e, exc_info=True)
            self._authenticated = False
            return False

    async def _ensure_authenticated(self) -> bool:
        if self._authenticated and __import__("time").time() < self._token_expires_at:
            return True
        return await self.authenticate()

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> ConnectorResult:
        """Ping endpoint đơn giản (thường là /api/v1/status hoặc /health)."""
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)
        try:
            t0 = __import__("time").time()
            # Thử endpoint phổ biến
            for endpoint in ["/api/v1/status", "/health", "/api/status", "/"]:
                resp = await self._client.get(endpoint, timeout=5.0)
                if resp.is_success:
                    latency = (__import__("time").time() - t0) * 1000
                    return ConnectorResult(
                        success=True,
                        data={"status": "healthy", "service": "eInvoice", "provider": self.config.extra.get("provider")},
                        latency_ms=latency,
                        source=self.config.name,
                    )
            return ConnectorResult(success=False, error="No healthy endpoint found", source=self.config.name)
        except Exception as e:
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    # ------------------------------------------------------------------
    # Business Methods
    # ------------------------------------------------------------------

    async def get_daily_invoices(
        self,
        date: Optional[str] = None,
        tax_code: Optional[str] = None,
    ) -> ConnectorResult:
        """
        Thống kê hóa đơn điện tử trong 1 ngày.

        Args:
            date: ISO date (YYYY-MM-DD). Mặc định: hôm nay.
            tax_code: Mã số thuế. Mặc định: config EINVOICE_TAX_CODE.

        Returns:
            ConnectorResult với data = {
                "date": "2024-01-15",
                "tax_code": "0123456789",
                "summary": {
                    "total_invoices": 150,
                    "total_amount_vnd": 1250000000,
                    "total_tax_vnd": 125000000,
                    "successful": 148,
                    "failed": 2,
                    "cancelled": 5,
                },
                "by_type": {
                    "GTGT": {"count": 120, "amount": 1000000000},
                    "Bán lẻ": {"count": 30, "amount": 250000000},
                },
                "errors": [
                    {"invoice_no": "001/2024", "error_code": "E001", "error_message": "Invalid buyer tax code"},
                    ...
                ]
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        tax_code = tax_code or self.config.extra.get("tax_code")

        if not tax_code:
            return ConnectorResult(success=False, error="tax_code is required (set EINVOICE_TAX_CODE)", source=self.config.name)

        # API endpoint tùy provider - đây là pattern phổ biến
        endpoint = f"/api/v1/invoices/summary"
        params = {
            "date": date,
            "tax_code": tax_code,
        }

        try:
            t0 = __import__("time").time()
            resp = await self._client.get(endpoint, params=params)
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                # Thử endpoint thay thế
                alt_endpoint = f"/api/v1/reports/daily"
                resp = await self._client.get(alt_endpoint, params=params)
                latency = (__import__("time").time() - t0) * 1000
                if not resp.is_success:
                    return ConnectorResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}", source=self.config.name)

            data = resp.json()
            # Chuẩn hóa response (các provider có format khác nhau)
            normalized = self._normalize_daily_summary(data, date, tax_code)

            return ConnectorResult(
                success=True,
                data=normalized,
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[EInvoiceConnector] get_daily_invoices failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    def _normalize_daily_summary(self, raw: Dict[str, Any], date: str, tax_code: str) -> Dict[str, Any]:
        """Chuẩn hóa response từ các provider khác nhau về format chung."""
        # Default structure
        result = {
            "date": date,
            "tax_code": tax_code,
            "summary": {
                "total_invoices": 0,
                "total_amount_vnd": 0,
                "total_tax_vnd": 0,
                "successful": 0,
                "failed": 0,
                "cancelled": 0,
            },
            "by_type": {},
            "errors": [],
        }

        # Thử parse các field phổ biến
        if isinstance(raw, dict):
            # Case 1: Flat summary
            if "total_invoices" in raw:
                result["summary"]["total_invoices"] = int(raw.get("total_invoices", 0))
            if "total_amount" in raw:
                result["summary"]["total_amount_vnd"] = int(float(raw.get("total_amount", 0)))
            if "total_tax" in raw:
                result["summary"]["total_tax_vnd"] = int(float(raw.get("total_tax", 0)))
            if "successful_count" in raw:
                result["summary"]["successful"] = int(raw.get("successful_count", 0))
            if "failed_count" in raw:
                result["summary"]["failed"] = int(raw.get("failed_count", 0))
            if "cancelled_count" in raw:
                result["summary"]["cancelled"] = int(raw.get("cancelled_count", 0))

            # Case 2: Nested data
            if "data" in raw and isinstance(raw["data"], dict):
                data = raw["data"]
                if "summary" in data:
                    s = data["summary"]
                    result["summary"]["total_invoices"] = s.get("total", result["summary"]["total_invoices"])
                    result["summary"]["total_amount_vnd"] = s.get("amount", result["summary"]["total_amount_vnd"])
                    result["summary"]["total_tax_vnd"] = s.get("tax", result["summary"]["total_tax_vnd"])

            # By type breakdown
            if "by_type" in raw:
                result["by_type"] = raw["by_type"]
            elif "invoice_types" in raw:
                result["by_type"] = raw["invoice_types"]

            # Errors
            if "errors" in raw:
                result["errors"] = raw["errors"]
            elif "failed_invoices" in raw:
                result["errors"] = raw["failed_invoices"]

        return result

    async def search_invoices(
        self,
        keyword: Optional[str] = None,
        buyer_tax_code: Optional[str] = None,
        status: Optional[str] = None,  # success, failed, cancelled
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ConnectorResult:
        """
        Tìm kiếm hóa đơn.

        Args:
            keyword: Tìm trong số hóa đơn, tên người mua.
            buyer_tax_code: Mã số thuế người mua.
            status: Trạng thái (success, failed, cancelled).
            date_from/date_to: ISO date.
            limit/offset: Pagination.

        Returns:
            ConnectorResult với data = {
                "total": int,
                "invoices": [
                    {"id": "...", "invoice_no": "001/2024", "date": "2024-01-15",
                     "buyer_name": "...", "buyer_tax_code": "...",
                     "total_amount": 1000000, "tax_amount": 100000,
                     "status": "success", "error_code": null},
                    ...
                ]
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        params = {
            "limit": min(limit, 100),
            "offset": offset,
        }
        if keyword:
            params["keyword"] = keyword
        if buyer_tax_code:
            params["buyer_tax_code"] = buyer_tax_code
        if status:
            params["status"] = status
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to

        try:
            t0 = __import__("time").time()
            resp = await self._client.get("/api/v1/invoices", params=params)
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                return ConnectorResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}", source=self.config.name)

            data = resp.json()
            # Normalize list response
            invoices = data.get("data", data.get("invoices", data.get("results", [])))
            total = data.get("total", data.get("count", len(invoices)))

            normalized = []
            for inv in invoices:
                normalized.append({
                    "id": inv.get("id") or inv.get("uuid") or inv.get("invoice_id"),
                    "invoice_no": inv.get("invoice_no") or inv.get("serial_no") or inv.get("number"),
                    "date": inv.get("date") or inv.get("invoice_date") or inv.get("created_at"),
                    "buyer_name": inv.get("buyer_name") or inv.get("buyer", {}).get("name"),
                    "buyer_tax_code": inv.get("buyer_tax_code") or inv.get("buyer", {}).get("tax_code"),
                    "total_amount": float(inv.get("total_amount", inv.get("amount", 0))),
                    "tax_amount": float(inv.get("tax_amount", inv.get("tax", 0))),
                    "status": inv.get("status", "unknown"),
                    "error_code": inv.get("error_code"),
                    "error_message": inv.get("error_message"),
                })

            return ConnectorResult(
                success=True,
                data={
                    "total": total,
                    "invoices": normalized,
                    "query_params": params,
                },
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[EInvoiceConnector] search_invoices failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def get_invoice_details(self, invoice_id: str) -> ConnectorResult:
        """Lấy chi tiết 1 hóa đơn."""
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        try:
            t0 = __import__("time").time()
            resp = await self._client.get(f"/api/v1/invoices/{invoice_id}")
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                return ConnectorResult(success=False, error=f"HTTP {resp.status_code}", source=self.config.name)

            inv = resp.json()
            return ConnectorResult(
                success=True,
                data={
                    "id": inv.get("id"),
                    "invoice_no": inv.get("invoice_no"),
                    "date": inv.get("date"),
                    "seller": inv.get("seller"),
                    "buyer": inv.get("buyer"),
                    "items": inv.get("items", []),
                    "total_amount": float(inv.get("total_amount", 0)),
                    "tax_amount": float(inv.get("tax_amount", 0)),
                    "status": inv.get("status"),
                    "xml_content": inv.get("xml_content"),  # Raw XML nếu có
                    "pdf_url": inv.get("pdf_url"),
                },
                latency_ms=latency,
                source=self.config.name,
            )
        except Exception as e:
            logger.error("[EInvoiceConnector] get_invoice_details failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        """
        Generic fetch_data router cho LLM tool calling.

        Supported actions:
          - "daily_summary" -> get_daily_invoices()
          - "search" -> search_invoices()
          - "details" -> get_invoice_details()
        """
        action = params.get("action", "daily_summary")

        if action == "daily_summary":
            return await self.get_daily_invoices(
                date=params.get("date"),
                tax_code=params.get("tax_code"),
            )
        elif action == "search":
            return await self.search_invoices(
                keyword=params.get("keyword"),
                buyer_tax_code=params.get("buyer_tax_code"),
                status=params.get("status"),
                date_from=params.get("date_from"),
                date_to=params.get("date_to"),
                limit=params.get("limit", 20),
                offset=params.get("offset", 0),
            )
        elif action == "details":
            invoice_id = params.get("invoice_id")
            if not invoice_id:
                return ConnectorResult(success=False, error="invoice_id required for details action", source=self.config.name)
            return await self.get_invoice_details(invoice_id)
        else:
            return ConnectorResult(
                success=False,
                error=f"Unknown action '{action}'. Supported: daily_summary, search, details",
                source=self.config.name,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
einvoice_connector = EInvoiceConnector()