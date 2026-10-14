# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/connectors/dev_fleet_master.py
====================================================
Bộ nối tới Ubuntu Master (Master Control API v1) — cài đặt `DevFleetProvider` bằng `BaseConnector` (retry, timeout,
che bí mật dùng lại) cộng cầu dao riêng cho Master. Chỉ nói chuyện với Master, KHÔNG với từng Mac, KHÔNG SSH / Ansible /
CLI OpenClaw (Master lo phần đó).

Hợp đồng: docs/integrations/dev-fleet.md. Phiên bản hợp đồng lấy từ `GET /api/v1/fleet/health` (`api_version`) và được
service kiểm tra — bộ nối KHÔNG đoán giao thức OpenClaw; chi tiết OpenClaw (pairing, exec approval) nằm sau Master.
Thao tác GHI không thử lại (`retries=0`) và mang `Idempotency-Key`.
"""
from __future__ import annotations

import ipaddress
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

from mateai.application.devfleet.provider import FleetError
from mateai.infrastructure.connectors.base_connector import BaseConnector, ConnectorConfig, ConnectorResult

API = "/api/v1/fleet"
_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")
_BREAKER_FAILS = 3
_BREAKER_COOLDOWN_S = 15.0


def _private_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home.arpa")) or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


class DevFleetMasterClient(BaseConnector):
    def __init__(self, endpoint: str, token: str = "", *, tls_verify: bool = True, ca_bundle: str = "",
                 timeout_s: float = 5.0, retries: int = 2) -> None:
        super().__init__(ConnectorConfig(name="dev_fleet_master", timeout_seconds=timeout_s, retry_count=retries,
                                         retry_backoff_seconds=0.5, extra={}))
        self.endpoint = (endpoint or "").rstrip("/")
        self._token = token or ""
        self._verify: Any = ca_bundle if ca_bundle else bool(tls_verify)
        self._fails = 0
        self._open_until = 0.0

    @classmethod
    def from_config(cls, cfg: Any) -> "DevFleetMasterClient":
        return cls(cfg.endpoint, cfg.api_token, tls_verify=cfg.tls_verify, ca_bundle=cfg.ca_bundle, timeout_s=cfg.timeout_s)

    # ── BaseConnector ──────────────────────────────────────────────────────
    async def authenticate(self) -> bool:
        return bool(self.endpoint)

    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        try:
            return ConnectorResult(success=True, data=await self._call("GET", str(params.get("path") or "/health")),
                                   source=self.config.name)
        except FleetError as exc:
            return ConnectorResult(success=False, error=str(exc), source=self.config.name)

    async def health_check(self) -> ConnectorResult:
        t0 = time.monotonic()
        try:
            data = await self._call("GET", "/health")
        except FleetError as exc:
            return ConnectorResult(success=False, error=str(exc), source=self.config.name)
        return ConnectorResult(success=True, data=data, latency_ms=(time.monotonic() - t0) * 1000, source=self.config.name)

    # ── lõi gọi HTTP ───────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        parts = urlsplit(self.endpoint)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise FleetError("`dev_fleet.endpoint` không hợp lệ (cần https://host[:port])", "rejected")
        if parts.scheme == "http" and not _private_host(parts.hostname):
            raise FleetError("Endpoint dùng http:// tới máy ngoài mạng nội bộ — chỉ cho phép https (token sẽ lộ).", "rejected")
        return f"{self.endpoint}{API}{path}"

    @staticmethod
    def _id(value: str, what: str) -> str:
        if not _ID_RE.match(str(value or "")):
            raise FleetError(f"{what} chứa ký tự không an toàn", "rejected")
        return quote(str(value), safe="")

    async def _call(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                    params: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None,
                    write: bool = False) -> Any:
        url = self._url(path)
        if time.monotonic() < self._open_until:
            raise FleetError("Master tạm thời bị ngắt (nhiều lần lỗi liên tiếp) — thử lại sau ít giây", "unavailable")
        headers = {"Accept": "application/json", "X-Client": "vn-mateai-dev-fleet"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        result = await self._request_with_retry(method, url, headers=headers, json_body=json_body, params=params,
                                                verify=self._verify, retries=0 if write else None)
        if result.success:
            self._fails = 0
            return result.data
        error = self._mask_secrets(str(result.error or "lỗi không rõ"))
        status = re.match(r"HTTP (\d{3})", error)
        code = int(status.group(1)) if status else 0
        if code == 404:
            raise FleetError(f"Master không có tài nguyên này ({path})", "not_found")
        if code in (401, 403):
            raise FleetError("Master từ chối thông tin xác thực / quyền (kiểm tra dev_fleet.api_token)", "rejected")
        if 400 <= code < 500 and code != 429:
            raise FleetError(f"Master từ chối yêu cầu: {error}", "rejected")
        self._fails += 1
        if self._fails >= _BREAKER_FAILS:
            self._open_until = time.monotonic() + _BREAKER_COOLDOWN_S
        raise FleetError(f"Master không liên lạc được: {error}", "unavailable")

    @staticmethod
    def _items(data: Any, key: str) -> List[Dict[str, Any]]:
        items = data.get(key) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise FleetError(f"Phản hồi của Master thiếu danh sách `{key}`", "bad_response")
        return [i for i in items if isinstance(i, dict)]

    @staticmethod
    def _obj(data: Any, what: str) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise FleetError(f"Phản hồi của Master cho {what} không phải đối tượng JSON", "bad_response")
        return data

    # ── DevFleetProvider ───────────────────────────────────────────────────
    async def health(self) -> Dict[str, Any]:
        return self._obj(await self._call("GET", "/health"), "health")

    async def list_workers(self) -> List[Dict[str, Any]]:
        return self._items(await self._call("GET", "/workers"), "workers")

    async def get_worker(self, worker_id: str) -> Dict[str, Any]:
        return self._obj(await self._call("GET", f"/workers/{self._id(worker_id, 'worker_id')}"), "worker")

    async def list_agents(self) -> List[Dict[str, Any]]:
        return self._items(await self._call("GET", "/agents"), "agents")

    async def get_metrics(self, worker_id: str) -> Dict[str, Any]:
        return self._obj(await self._call("GET", f"/workers/{self._id(worker_id, 'worker_id')}/metrics"), "metrics")

    async def get_git_status(self, worker_id: str, repository: str) -> Dict[str, Any]:
        return self._obj(await self._call("GET", f"/workers/{self._id(worker_id, 'worker_id')}/git",
                                          params={"repository": repository}), "git")

    async def dispatch_task(self, spec: Dict[str, Any], *, worker_id: str, idempotency_key: str) -> Dict[str, Any]:
        body = {"worker_id": worker_id, "task": spec}
        return self._obj(await self._call("POST", "/tasks/dispatch", json_body=body, idempotency_key=idempotency_key,
                                          write=True), "dispatch")

    async def get_task_status(self, master_task_id: str) -> Dict[str, Any]:
        return self._obj(await self._call("GET", f"/tasks/{self._id(master_task_id, 'task_id')}"), "task")

    async def get_task_logs(self, master_task_id: str, tail: int = 200) -> Dict[str, Any]:
        return self._obj(await self._call("GET", f"/tasks/{self._id(master_task_id, 'task_id')}/logs",
                                          params={"tail": max(1, min(int(tail), 2000))}), "logs")

    async def _control(self, verb: str, master_task_id: str, idempotency_key: str) -> Dict[str, Any]:
        return self._obj(await self._call("POST", f"/tasks/{self._id(master_task_id, 'task_id')}/{verb}",
                                          idempotency_key=idempotency_key, write=True), verb)

    async def cancel_task(self, master_task_id: str, *, idempotency_key: str) -> Dict[str, Any]:
        return await self._control("cancel", master_task_id, idempotency_key)

    async def pause_task(self, master_task_id: str, *, idempotency_key: str) -> Dict[str, Any]:
        return await self._control("pause", master_task_id, idempotency_key)

    async def resume_task(self, master_task_id: str, *, idempotency_key: str) -> Dict[str, Any]:
        return await self._control("resume", master_task_id, idempotency_key)
