# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/base_connector.py
=================================
Base Connector Architecture — Phase 59 Universal Enterprise Connector Hub.

Cung cấp lớp nền tảng trừu tượng (Abstract Base Class) cho tất cả các adapter
kết nối hệ thống ngoại vi (AWS, OCI, Paperless, eInvoice, ERP...).

Nguyên tắc thiết kế:
  - Adapter Pattern: Mỗi hệ thống ngoại vi là một Concrete Adapter kế thừa BaseConnector.
  - Vault Integration: Tuyệt đối KHÔNG hardcode API Key/Secret. Đọc từ biến môi trường
    hoặc config.json thông qua `settings` singleton.
  - Fail-fast: `health_check()` bắt buộc để AI biết khi nào hệ thống ngoại vi "ngủ".
  - Normalized Output: `fetch_data()` luôn trả về JSON chuẩn (dict/list) để LLM dễ consume.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cấu hình: config.json  <  biến môi trường
# ---------------------------------------------------------------------------
# Vì sao cần lớp này: trước đây mỗi adapter tự đọc `os.getenv()` trong
# `__init__`, nghĩa là cấu hình lưu từ giao diện (config.json) không bao giờ
# tới được connector, và cũng không nạp lại được khi người dùng bấm "Lưu".
# Thứ tự ưu tiên (cao -> thấp): biến môi trường > config.json > mặc định.

#: env var của từng trường cấu hình, theo connector
CONNECTOR_ENV_MAP: Dict[str, Dict[str, str]] = {
    "aws": {
        "region": "AWS_REGION",
        "access_key_id": "AWS_ACCESS_KEY_ID",
        "secret_access_key": "AWS_SECRET_ACCESS_KEY",
        "cost_explorer_enabled": "AWS_COST_EXPLORER_ENABLED",
    },
    "oci": {
        "config_file": "OCI_CONFIG_FILE",
        "profile": "OCI_PROFILE",
        "compartment_id": "OCI_COMPARTMENT_ID",
        "region": "OCI_REGION",
    },
    "paperless": {
        "base_url": "PAPERLESS_BASE_URL",
        "api_token": "PAPERLESS_API_TOKEN",
        "verify_ssl": "PAPERLESS_VERIFY_SSL",
    },
    "einvoice": {
        "base_url": "EINVOICE_BASE_URL",
        "client_id": "EINVOICE_CLIENT_ID",
        "client_secret": "EINVOICE_CLIENT_SECRET",
        "tax_code": "EINVOICE_TAX_CODE",
        "provider": "EINVOICE_PROVIDER",
        "verify_ssl": "EINVOICE_VERIFY_SSL",
    },
}

#: trường nên ép về bool khi đọc từ config.json (config.json lưu JSON thật,
#: env var lại là chuỗi "true"/"false" — phải chuẩn hoá về cùng một kiểu)
_BOOL_FIELDS = frozenset({"cost_explorer_enabled", "verify_ssl", "enabled"})

#: giá trị mặc định khi không có ở cả config.json lẫn env
CONNECTOR_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "aws": {"region": "ap-southeast-1", "cost_explorer_enabled": True},
    "oci": {
        "config_file": "~/.oci/config",
        "profile": "DEFAULT",
        "compartment_id": "",
        "region": "ap-singapore-1",
    },
    "paperless": {"base_url": "", "api_token": "", "verify_ssl": True},
    "einvoice": {
        "base_url": "",
        "client_id": "",
        "client_secret": "",
        "tax_code": "",
        "provider": "custom",
        "verify_ssl": True,
    },
}

#: Khoá BẮT BUỘC phải có thật thì connector mới chạy được.
#:
#: `CONNECTOR_DEFAULTS` cấp sẵn `region`, `provider`, `profile`,
#: `cost_explorer_enabled`... — tức mọi connector LUÔN có ít nhất một giá
#: trị khác rỗng. Vì vậy câu hỏi kiểu "có phải trường nào khác rỗng không"
#: luôn trả True, kể cả khi connector chưa có BẤT KỲ thông tin đăng nhập
#: nào. Báo như vậy là báo cáo thành công giả: giao diện hiện "đã cấu hình",
#: người vận hành tin là đã sẵn sàng, rồi mọi lời gọi thật đều hỏng với
#: "Authentication failed".
#:
#: Danh sách dưới đây là các khoá thật sự quyết định connector có dùng
#: được hay không. Chỉ ghi TÊN khoá, không bao giờ trả về giá trị.
CONNECTOR_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    # region có sẵn trong DEFAULT; cần cặp khoá/khoá bí mật thật.
    "aws": ("access_key_id", "secret_access_key"),
    # config_file/profile có sẵn; compartment_id thì không.
    "oci": ("compartment_id",),
    "paperless": ("base_url", "api_token"),
    "einvoice": ("base_url", "client_id", "client_secret"),
}


def missing_required_fields(connector_name: str, settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Trả về danh sách TÊN khoá bắt buộc còn thiếu (không bao giờ trả giá trị).

    Dùng `settings` đã nạp nếu có, nếu không thì tự gọi
    `load_connector_settings()`. Danh sách rỗng = connector đủ thông tin
    đăng nhập.
    """
    required = CONNECTOR_REQUIRED_FIELDS.get(connector_name, ())
    if not required:
        return []
    if settings is None:
        try:
            settings = load_connector_settings(connector_name)
        except Exception:  # config hỏng -> coi như thiếu hết, an toàn
            return list(required)
    merged = dict(CONNECTOR_DEFAULTS.get(connector_name, {}))
    merged.update(settings or {})
    return [k for k in required if not str(merged.get(k) or "").strip()]



def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _read_config_json_block(connector_name: str) -> Dict[str, Any]:
    """
    Khối `<connector_name>` trong config.json ({} nếu không có), đọc qua
    mateai.config.loader — cổng duy nhất vào config.json (RULE-013).

    Đọc khối thô thay vì qua `settings` vì `AppSettings` khai báo `extra="ignore"`:
    khóa lạ (aws/oci/paperless/einvoice) bị pydantic loại khỏi object. Mỗi lần
    đọc là đọc MỚI — không còn cache riêng phải nhớ xoá sau khi lưu cấu hình.
    """
    from mateai.config.loader import get_config_section
    return dict(get_config_section(connector_name))


def load_connector_settings(connector_name: str) -> Dict[str, Any]:
    """
    Dựng `extra` cho connector từ 3 nguồn: config.json -> env var -> mặc định.
    """
    merged: Dict[str, Any] = dict(CONNECTOR_DEFAULTS.get(connector_name, {}))
    merged.update(_read_config_json_block(connector_name))

    for key, env_name in CONNECTOR_ENV_MAP.get(connector_name, {}).items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        merged[key] = _coerce_bool(raw) if key in _BOOL_FIELDS else raw

    for key in list(merged):
        if key in _BOOL_FIELDS:
            merged[key] = _coerce_bool(merged[key])

    # base_url không nên có dấu / cuối — sẽ thành "//api" khi nối chuỗi
    if isinstance(merged.get("base_url"), str):
        merged["base_url"] = merged["base_url"].rstrip("/")
    if isinstance(merged.get("config_file"), str) and merged["config_file"].startswith("~"):
        merged["config_file"] = os.path.expanduser(merged["config_file"])

    return merged



@dataclass
class ConnectorConfig:
    """
    Cấu hình chung cho mọi connector.
    Các field cụ thể của từng adapter (region, endpoint, timeout...) được lưu
    trong `extra` để linh hoạt.
    """
    name: str                                    # Tên định danh: "aws", "oci", "paperless", "einvoice"
    enabled: bool = True                         # Bật/tắt connector này
    timeout_seconds: float = 10.0                # Timeout cho mọi request HTTP/SDK
    retry_count: int = 2                         # Số lần retry khi lỗi transient
    retry_backoff_seconds: float = 1.5           # Exponential backoff factor
    extra: Dict[str, Any] = field(default_factory=dict)  # Config đặc thù (region, api_version...)

    @classmethod
    def from_settings(cls, connector_name: str, settings_extra: Optional[Dict[str, Any]] = None) -> "ConnectorConfig":
        """
        Tạo config từ `settings` (config.json + env vars).
        Ưu tiên: ENV VARS > config.json > defaults.
        """
        extra = settings_extra or {}
        # Cho phép override timeout/retry qua env
        timeout = float(os.getenv(f"VNMATE_CONNECTOR_{connector_name.upper()}_TIMEOUT", extra.get("timeout_seconds", 10.0)))
        retry = int(os.getenv(f"VNMATE_CONNECTOR_{connector_name.upper()}_RETRY", extra.get("retry_count", 2)))
        enabled = os.getenv(f"VNMATE_CONNECTOR_{connector_name.upper()}_ENABLED", str(extra.get("enabled", True))).lower() == "true"
        return cls(
            name=connector_name,
            enabled=enabled,
            timeout_seconds=timeout,
            retry_count=retry,
            extra=extra,
        )


@dataclass
class ConnectorResult:
    """
    Kết quả chuẩn hóa từ mọi connector.
    """
    success: bool
    data: Any = None                          # Payload JSON đã chuẩn hóa
    error: Optional[str] = None               # Mô tả lỗi nếu failed
    latency_ms: float = 0.0                   # Độ trễ thực tế
    source: str = ""                          # Tên connector
    metadata: Dict[str, Any] = field(default_factory=dict)  # Metadata bổ sung (pagination, rate_limit_remaining...)


def parse_response_body(response: Any) -> Any:
    """Thân phản hồi -> JSON khi đúng là JSON. Nhận mọi kiểu `*/*json*` (application/json, vnd.api+json,
    hal+json, json-rpc, text/json…) và cả máy chủ khai sai `text/plain` nhưng thân là JSON. Không phải JSON
    thì trả chuỗi (không ném lỗi)."""
    text = response.text
    ctype = str(response.headers.get("content-type", "")).lower()
    head = text.lstrip()[:1]
    if "json" in ctype or head in ("{", "["):
        try:
            return response.json()
        except ValueError:
            return text
    return text


class BaseConnector(abc.ABC):
    """
    Abstract Base Class cho tất cả Enterprise Connectors.

    Mỗi Concrete Adapter PHẢI implement:
      - authenticate(): Xử lý token/key, refresh nếu cần.
      - fetch_data(params): Kéo dữ liệu, trả về ConnectorResult.
      - health_check(): Ping endpoint, trả True/False.

    Các method tiện ích:
      - _request_with_retry(): Wrapper HTTP/SDK có retry + timeout + circuit-breaker logic đơn giản.
      - _mask_secrets(): Loại bỏ secret khỏi log.
    """

    def __init__(self, config: Optional[ConnectorConfig] = None):
        self.config = config or ConnectorConfig(name=self.__class__.__name__.lower().replace("_connector", ""))
        self._authenticated = False
        self._last_auth_time: float = 0.0
        self._auth_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def reload_config(self) -> Dict[str, Any]:
        """
        Nạp lại cấu hình từ config.json + env var, xoá session đăng nhập cũ.

        Cần gọi sau khi người dùng bấm "Lưu cấu hình" trên giao diện, nếu
        không connector vẫn giữ thông số cũ tới lần restart server.

        Trả về `config.extra` mới để caller ghi log (không ghi kèm secret).
        """
        name = self.config.name
        new_extra = load_connector_settings(name)
        merged = ConnectorConfig.from_settings(name, new_extra)
        self.config = merged
        # Buộc xác thực lại: client/token cũ có thể dùng key đã bị thay.
        self._authenticated = False
        self._auth_token = None
        self._token_expires_at = 0.0
        self._last_auth_time = 0.0
        self._reset_cached_clients()
        logger.info("[%s] Đã nạp lại cấu hình (%d trường)", name, len(new_extra))
        return new_extra

    def _reset_cached_clients(self) -> None:
        """
        Hook cho subclass: xoá các SDK client đã khởi tạo.

        Mặc định không làm gì — chỉ AWS/OCI mới giữ client trong thuộc tính.
        """
        return None

    # ================================================================
    # Abstract Methods — BẮT BUỘC override trong subclass
    # ================================================================

    @abc.abstractmethod
    async def authenticate(self) -> bool:
        """
        Thực hiện xác thực với hệ thống ngoại vi.

        Returns:
            True nếu auth thành công (token/key hợp lệ), False nếu thất bại.

        Lưu ý:
            - Nên cache token và chỉ refresh khi hết hạn (check `_token_expires_at`).
            - Tuyệt đối KHÔNG log raw secret/key. Dùng `_mask_secrets()`.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        """
        Kéo dữ liệu từ hệ thống ngoại vi theo tham số `params`.

        Args:
            params: Dict tham số truy vấn (ví dụ: {"start_date": "2024-01-01", "service": "EC2"}).

        Returns:
            ConnectorResult: success=True/False, data=JSON chuẩn hóa, error message nếu fail.

        Quy ước params phổ biến:
            - start_date / end_date: ISO 8601 date string
            - limit / offset: pagination
            - filters: dict tùy hệ thống
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health_check(self) -> ConnectorResult:
        """
        Kiểm tra hệ thống ngoại vi còn sống và reachable không.

        Returns:
            ConnectorResult: success=True nếu OK, False nếu down/unreachable.
            data có thể chứa {"status": "healthy"} hoặc thông tin version.
        """
        raise NotImplementedError

    # ================================================================
    # Concrete Helpers — Có thể dùng chung, override nếu cần tùy chỉnh
    # ================================================================

    async def _ensure_authenticated(self) -> bool:
        """Đảm bảo đã authenticated. Tự refresh token nếu hết hạn."""
        if self._authenticated and time.time() < self._token_expires_at - 60:  # 60s buffer
            return True
        return await self.authenticate()

    def _mask_secrets(self, text: str) -> str:
        """Loại bỏ/mask các pattern secret phổ biến khỏi chuỗi log."""
        import re
        patterns = [
            (r"(?i)(aws_secret_access_key|secret_access_key|api_key|api_token|access_token|bearer_token)\s*[:=]\s*['\"]?([^'\"\\s,;}]+)", r"\1=***MASKED***"),
            (r"(?i)(authorization|bearer)\s+([a-zA-Z0-9_\-\.]{20,})", r"\1 ***MASKED***"),
            (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?([^'\"\\s,;}]+)", r"\1=***MASKED***"),
        ]
        for pattern, repl in patterns:
            text = re.sub(pattern, repl, text)
        return text

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        verify: Any = True,
        cert: Any = None,
        form_body: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
    ) -> ConnectorResult:
        """
        Wrapper HTTP request có retry + timeout + circuit-breaker đơn giản.

        `verify`: True (mặc định) | False (bỏ kiểm chứng chứng chỉ — chỉ cho hệ thống nội bộ tự ký, do người
        quản trị khai báo) | đường dẫn tệp CA (CA nội bộ). `cert`: đường dẫn chứng chỉ máy khách hoặc
        (chứng chỉ, khoá) cho mTLS. `form_body`: gửi dạng `application/x-www-form-urlencoded`.

        Returns ConnectorResult thay vì raise exception để caller dễ handle.
        """
        import httpx

        timeout = timeout or self.config.timeout_seconds
        last_error = None
        metadata: Dict[str, Any] = {}

        # `retries=0` cho thao tác GHI: thử lại một POST bị timeout có thể thực hiện việc đó hai lần.
        attempts = (self.config.retry_count if retries is None else max(0, retries)) + 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout, verify=verify, cert=cert) as client:
                    t0 = time.monotonic()
                    response = await client.request(
                        method=method.upper(),
                        url=url,
                        headers=headers,
                        json=json_body,
                        data=form_body,
                        params=params,
                    )
                    latency = (time.monotonic() - t0) * 1000

                    if response.is_success:
                        return ConnectorResult(
                            success=True,
                            data=parse_response_body(response),
                            latency_ms=latency,
                            source=self.config.name,
                            metadata={"http_status": response.status_code,
                                      "link": response.headers.get("link", ""),
                                      "headers": {k.lower(): v for k, v in response.headers.items()
                                                  if k.lower() in ("x-total-count", "x-next-page", "x-total")}},
                        )
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                        logger.warning(
                            "[%s] Request failed (attempt %d/%d): %s",
                            self.config.name, attempt + 1, attempts, last_error,
                        )
                        # 4xx (trừ 429) là lỗi của request, không phải lỗi
                        # tạm thời: token sai, path sai, không đủ quyền — thử
                        # lại y hệt sẽ cho cùng kết quả. Retry ở đây chỉ tốn
                        # thêm thời gian chờ và bắn thêm request vào hệ thống
                        # ngoài (đã thấy: 401 bị thử 3 lần, mất ~3s).
                        if 400 <= response.status_code < 500 and response.status_code != 429:
                            return ConnectorResult(
                                success=False,
                                error=last_error,
                                latency_ms=latency,
                                source=self.config.name,
                                metadata={"http_status": response.status_code},
                            )

            except httpx.TimeoutException:
                last_error = f"Timeout after {timeout}s"
                logger.warning("[%s] Request timeout (attempt %d/%d)", self.config.name, attempt + 1, attempts)
            except httpx.ConnectError as e:
                last_error = f"Connection error: {e}"
                if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e).lower():
                    # Lỗi đặc trưng của hệ thống nội bộ dùng chứng chỉ tự ký / CA nội bộ: nói rõ cách sửa.
                    last_error = ("Không xác thực được chứng chỉ TLS của máy chủ (chứng chỉ tự ký hoặc CA nội bộ). "
                                  "Khai báo `ca_bundle` (tệp CA nội bộ) hoặc đặt `verify_ssl` = false cho nguồn này.")
                    return ConnectorResult(success=False, error=last_error, latency_ms=0.0,
                                           source=self.config.name, metadata=metadata)
                logger.warning("[%s] Connection error (attempt %d/%d): %s", self.config.name, attempt + 1, attempts, e)
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.error("[%s] Request error (attempt %d/%d): %s", self.config.name, attempt + 1, attempts, e, exc_info=True)

            if attempt < attempts - 1:
                await asyncio.sleep(self.config.retry_backoff_seconds ** attempt)

        return ConnectorResult(
            success=False,
            error=last_error or "Unknown error after retries",
            latency_ms=0.0,
            source=self.config.name,
            metadata=metadata,
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.config.name}, enabled={self.config.enabled})>"