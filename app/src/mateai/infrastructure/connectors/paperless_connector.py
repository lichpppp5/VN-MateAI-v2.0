# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/paperless_connector.py
======================================
Paperless-ngx Adapter — Phase 59 Universal Enterprise Connector Hub.

Tương tác với REST API của Paperless-ngx (https://github.com/paperless-ngx/paperless-ngx).
Cung cấp:
  - search_document(keyword): Tìm hợp đồng/tài liệu OCR, trả về URL + text snippet.
  - get_document_details(doc_id): Lấy chi tiết 1 tài liệu.
  - download_document(doc_id): Tải file gốc (PDF) hoặc archive.

Yêu cầu cài đặt:
  pip install httpx

Cấu hình (ENV VARS / config.json):
  PAPERLESS_BASE_URL (ví dụ: https://paperless.company.com)
  PAPERLESS_API_TOKEN (Token từ Paperless: Settings > API Tokens)
  PAPERLESS_VERIFY_SSL (true/false, mặc định true)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from mateai.infrastructure.connectors.base_connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    load_connector_settings,
)

logger = logging.getLogger(__name__)


def _label(value: Any) -> Any:
    """Paperless-ngx trả khoá ngoại là SỐ ID (hoặc đối tượng khi mở rộng) — lấy tên nếu có, không thì giữ ID."""
    if isinstance(value, dict):
        return value.get("name") or value.get("id")
    return value if value not in ("", None) else None


class PaperlessConnector(BaseConnector):
    """
    Adapter kết nối Paperless-ngx qua REST API.
    """

    def __init__(self, config: Optional[ConnectorConfig] = None):
        if config is None:
            config = ConnectorConfig.from_settings("paperless", load_connector_settings("paperless"))
        super().__init__(config)
        self._client = None  # httpx.AsyncClient

    def _reset_cached_clients(self) -> None:
        self._client = None

    # ------------------------------------------------------------------
    # Authentication & Client Initialization
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Tạo httpx client với API Token header."""
        import httpx

        base_url = self.config.extra.get("base_url")
        api_token = self.config.extra.get("api_token")
        verify_ssl = self.config.extra.get("verify_ssl", True)

        if not base_url:
            logger.error("[PaperlessConnector] PAPERLESS_BASE_URL not configured.")
            self._authenticated = False
            return False
        if not api_token:
            logger.error("[PaperlessConnector] PAPERLESS_API_TOKEN not configured.")
            self._authenticated = False
            return False

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Token {api_token}",
                "Accept": "application/json",
            },
            timeout=self.config.timeout_seconds,
            verify=verify_ssl,
        )

        # Test connection
        try:
            resp = await self._client.get("/api/", timeout=5.0)
            if resp.is_success:
                logger.info("[PaperlessConnector] Connected to Paperless at %s", base_url)
                self._authenticated = True
                self._last_auth_time = __import__("time").time()
                self._token_expires_at = self._last_auth_time + 86400  # Token long-lived
                return True
            else:
                logger.error("[PaperlessConnector] Auth test failed: HTTP %d", resp.status_code)
                self._authenticated = False
                return False
        except Exception as e:
            logger.error("[PaperlessConnector] Connection test failed: %s", e)
            self._authenticated = False
            return False

    async def _ensure_client(self) -> bool:
        if not self._authenticated or self._client is None:
            return await self.authenticate()
        return True

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> ConnectorResult:
        """Ping /api/ endpoint."""
        if not await self._ensure_client():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)
        try:
            t0 = __import__("time").time()
            resp = await self._client.get("/api/", timeout=5.0)
            latency = (__import__("time").time() - t0) * 1000
            if resp.is_success:
                return ConnectorResult(
                    success=True,
                    data={"status": "healthy", "service": "Paperless-ngx", "version": resp.json().get("version", "unknown")},
                    latency_ms=latency,
                    source=self.config.name,
                )
            return ConnectorResult(success=False, error=f"HTTP {resp.status_code}", source=self.config.name)
        except Exception as e:
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    # ------------------------------------------------------------------
    # Business Methods
    # ------------------------------------------------------------------

    async def search_document(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        tags: Optional[List[str]] = None,
        correspondents: Optional[List[str]] = None,
        document_types: Optional[List[str]] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
    ) -> ConnectorResult:
        """
        Tìm kiếm tài liệu trong Paperless.

        Args:
            query: Từ khóa tìm kiếm (full-text search trên OCR content + title).
            limit: Số kết quả tối đa (mặc định 10).
            offset: Phân trang.
            tags: Lọc theo tag names.
            correspondents: Lọc theo correspondent names.
            document_types: Lọc theo document type names.
            created_after/created_before: ISO date (YYYY-MM-DD).

        Returns:
            ConnectorResult với data = {
                "total_results": int,
                "documents": [
                    {"id": 123, "title": "...", "correspondent": "...", "document_type": "...",
                     "tags": [...], "created": "...", "added": "...",
                     "ocr_content_snippet": "...", "download_url": "/api/documents/123/download/",
                     "archive_url": "/api/documents/123/"},
                    ...
                ]
            }
        """
        if not await self._ensure_client():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        params = {
            "query": query,
            "page_size": min(limit, 100),
            "offset": offset,
        }
        if tags:
            params["tags__name__in"] = ",".join(tags)
        if correspondents:
            params["correspondents__name__in"] = ",".join(correspondents)
        if document_types:
            params["document_types__name__in"] = ",".join(document_types)
        if created_after:
            params["created__gte"] = created_after
        if created_before:
            params["created__lte"] = created_before

        try:
            t0 = __import__("time").time()
            resp = await self._client.get("/api/documents/", params=params)
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                return ConnectorResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}", source=self.config.name)

            data = resp.json()
            results = data.get("results", [])
            documents = []
            for doc in results:
                documents.append({
                    "id": doc.get("id"),
                    "title": doc.get("title"),
                    "correspondent": _label(doc.get("correspondent")),
                    "document_type": _label(doc.get("document_type")),
                    "tags": [_label(t) for t in (doc.get("tags") or [])],
                    "created": doc.get("created"),
                    "added": doc.get("added"),
                    "ocr_content_snippet": (doc.get("content", "") or "")[:300],
                    "download_url": f"{self.config.extra['base_url']}/api/documents/{doc.get('id')}/download/",
                    "archive_url": f"{self.config.extra['base_url']}/api/documents/{doc.get('id')}/",
                    "original_file_name": doc.get("original_file_name"),
                    "archive_serial_number": doc.get("archive_serial_number"),
                })

            return ConnectorResult(
                success=True,
                data={
                    "total_results": data.get("count", 0),
                    "documents": documents,
                    "query": query,
                },
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[PaperlessConnector] search_document failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def get_document_details(self, doc_id: int) -> ConnectorResult:
        """Lấy chi tiết đầy đủ 1 tài liệu."""
        if not await self._ensure_client():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        try:
            t0 = __import__("time").time()
            resp = await self._client.get(f"/api/documents/{doc_id}/")
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                return ConnectorResult(success=False, error=f"HTTP {resp.status_code}", source=self.config.name)

            doc = resp.json()
            return ConnectorResult(
                success=True,
                data={
                    "id": doc.get("id"),
                    "title": doc.get("title"),
                    "correspondent": _label(doc.get("correspondent")),
                    "document_type": _label(doc.get("document_type")),
                    "tags": [_label(t) for t in (doc.get("tags") or [])],
                    "created": doc.get("created"),
                    "added": doc.get("added"),
                    "modified": doc.get("modified"),
                    "content": doc.get("content", ""),  # Full OCR text
                    "original_file_name": doc.get("original_file_name"),
                    "archive_serial_number": doc.get("archive_serial_number"),
                    "owner": doc.get("owner"),
                    "permissions": doc.get("permissions"),
                    "download_url": f"{self.config.extra['base_url']}/api/documents/{doc_id}/download/",
                    "thumbnail_url": f"{self.config.extra['base_url']}/api/documents/{doc_id}/thumb/",
                },
                latency_ms=latency,
                source=self.config.name,
            )
        except Exception as e:
            logger.error("[PaperlessConnector] get_document_details failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def download_document(self, doc_id: int, original: bool = False) -> ConnectorResult:
        """
        Tải file tài liệu.

        Args:
            doc_id: Document ID.
            original: True = file gốc (PDF), False = archived version (PDF/A).

        Returns:
            ConnectorResult với data = bytes của file (base64 encoded để JSON-serializable).
        """
        if not await self._ensure_client():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        endpoint = f"/api/documents/{doc_id}/download/" if original else f"/api/documents/{doc_id}/download/?format=pdf"

        try:
            t0 = __import__("time").time()
            resp = await self._client.get(endpoint, timeout=30.0)
            latency = (__import__("time").time() - t0) * 1000

            if not resp.is_success:
                return ConnectorResult(success=False, error=f"HTTP {resp.status_code}", source=self.config.name)

            import base64
            file_bytes = resp.content
            b64 = base64.b64encode(file_bytes).decode("ascii")

            return ConnectorResult(
                success=True,
                data={
                    "doc_id": doc_id,
                    "filename": f"document_{doc_id}.pdf",
                    "size_bytes": len(file_bytes),
                    "content_base64": b64,
                    "content_type": resp.headers.get("content-type", "application/pdf"),
                },
                latency_ms=latency,
                source=self.config.name,
            )
        except Exception as e:
            logger.error("[PaperlessConnector] download_document failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        """
        Generic fetch_data router cho LLM tool calling.

        Supported actions:
          - "search" -> search_document()
          - "details" -> get_document_details()
          - "download" -> download_document()
        """
        action = params.get("action", "search")

        if action == "search":
            return await self.search_document(
                query=params.get("query", ""),
                limit=params.get("limit", 10),
                offset=params.get("offset", 0),
                tags=params.get("tags"),
                correspondents=params.get("correspondents"),
                document_types=params.get("document_types"),
                created_after=params.get("created_after"),
                created_before=params.get("created_before"),
            )
        elif action == "details":
            doc_id = params.get("doc_id")
            if not doc_id:
                return ConnectorResult(success=False, error="doc_id required for details action", source=self.config.name)
            return await self.get_document_details(int(doc_id))
        elif action == "download":
            doc_id = params.get("doc_id")
            if not doc_id:
                return ConnectorResult(success=False, error="doc_id required for download action", source=self.config.name)
            return await self.download_document(int(doc_id), original=params.get("original", False))
        else:
            return ConnectorResult(
                success=False,
                error=f"Unknown action '{action}'. Supported: search, details, download",
                source=self.config.name,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
paperless_connector = PaperlessConnector()