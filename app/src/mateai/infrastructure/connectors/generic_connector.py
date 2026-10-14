# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/generic_connector.py
====================================
Phase 62 — Gọi REST API của bất kỳ app doanh nghiệp nào, không cần code mới.

Vấn đề
------
Mọi app doanh nghiệp đều trả JSON qua REST. Khác biệt giữa MISA và Odoo và
KiotViet không nằm ở *cách gọi* (đều là HTTP + header xác thực) mà ở *giá trị*
cấu hình: URL, kiểu xác thực, path lấy báo cáo. Nếu viết một adapter Python
cho từng app thì 90% code lặp lại y hệt nhau.

Cách giải quyết
---------------
`GenericConnector` gom phần chung (dựng header, ghép URL, parse, chuẩn hoá
thành bảng) và nhận phần khác biệt từ khai báo trong
`config/data_sources.json`. Thêm app mới = thêm một mục JSON.

Chuẩn hoá đầu ra
----------------
App ngoài trả về đủ kiểu: `[{...}]`, `{"data": [{...}]}`, `{"result": {"items":
[...]}}`, `{"rows": [...]}`. Người vận hành không nên phải biết app nào bọc
dữ liệu kiểu gì. `GenericConnector` dò các vị trí quen thuộc, trả về luôn
cùng một cấu trúc `{rows, columns, total}` để UI render một kiểu duy nhất.

Bảo mật
-------
- Chỉ GET/POST. Không có method ghi/xoá — data source chỉ để *lấy dữ liệu báo
  cáo*, không phải để thao tác nghiệp vụ.
- `auth_value` không bao giờ nằm trong `ConnectorResult` lẫn message lỗi; lỗi từ
  server ngoài được mask qua `_mask_secrets` trước khi trả lên.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from mateai.infrastructure.connectors import sql_connector
from mateai.infrastructure.connectors.base_connector import BaseConnector, ConnectorConfig, ConnectorResult
from mateai.infrastructure.connectors.operations import extract_path, render_request

logger = logging.getLogger(__name__)

#: Khoá thường gặp chứa mảng bản ghi trong payload JSON. Thứ tự là thứ tự ưu
#: tiên: khoá nào cụ thể hơn thì kiểm tra trước.
_ROW_CONTAINER_KEYS: Tuple[str, ...] = (
    "rows", "items", "records", "results", "result", "data", "list", "entries", "content", "docs", "issues", "values",
)

#: Khoá thường gặp chứa tổng số bản ghi (phân trang).
_TOTAL_KEYS: Tuple[str, ...] = ("total", "total_count", "totalCount", "count", "totalElements")

#: Khoá thường gặp chứa thông báo lỗi của app ngoài — dùng để báo lỗi rõ ràng
#: thay vì chỉ "HTTP 500".
_ERROR_KEYS: Tuple[str, ...] = ("error", "error_message", "message", "detail", "errors")


def _find_rows(payload: Any, depth: int = 0) -> Optional[List[Any]]:
    """
    Tìm mảng bản ghi trong payload lồng nhau.

    Bỏ qua `payload` là dict rỗng — `{"data": {}}` không phải "không có dữ
    liệu", mà là app bọc chưa đúng chỗ. Trả None để phía gọi thử container
    khác, thay vì kết luận sai rằng báo cáo rỗng.
    """
    if depth > 4:  # chống vòng lặp tự tham chiếu / cấu trúc quá sâu
        return None

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return None

    for key in _ROW_CONTAINER_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and value:
            nested = _find_rows(value, depth + 1)
            if nested is not None:
                return nested

    # Không có khoá quen thuộc: coi dict đầu tiên chứa dict/record là danh sách.
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return value
        if isinstance(value, dict) and value:
            nested = _find_rows(value, depth + 1)
            if nested is not None:
                return nested

    return None


def _find_total(payload: Any, rows_len: int, depth: int = 0) -> Optional[int]:
    """Tìm tổng số bản ghi trong payload; `None` nếu app không báo."""
    if depth > 3 or not isinstance(payload, dict):
        return None
    for key in _TOTAL_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    for value in payload.values():
        if isinstance(value, dict):
            nested = _find_total(value, rows_len, depth + 1)
            if nested is not None:
                return nested
    return None


def _extract_error(payload: Any, depth: int = 0) -> str:
    """
    Rút thông báo lỗi của app ngoài, nếu có.

    Nhiều app bọc lỗi trong thân phản hồi thành công (`{"success": false,
    "data": {"error_message": "..."}}`) — nên phải dò cả các tầng bọc, không
    chỉ khoá ở tầng ngoài cùng.
    """
    if depth > 4 or not isinstance(payload, dict):
        return ""

    for key in _ERROR_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]

    for value in payload.values():
        if isinstance(value, dict) and value:
            nested = _extract_error(value, depth + 1)
            if nested:
                return nested

    return ""


def _columns_of(rows: List[Any], max_cols: int = 12) -> List[str]:
    """
    Tập cột hiển thị: hợp nhất khoá của các bản ghi đầu, giữ đúng thứ tự xuất
    hiện. Giới hạn số cột vì bảng báo cáo rộng sẽ vỡ layout.
    """
    columns: List[str] = []
    for row in rows[:5]:  # 5 dòng đủ để thấy phần lớn cột
        if not isinstance(row, dict):
            continue
        for key in row:
            if key not in columns:
                columns.append(str(key))
                if len(columns) >= max_cols:
                    return columns
    return columns


#: Token đăng nhập / OAuth2 đã lấy: khoá = nguồn + vân tay cấu hình + khoá bí mật (đổi khoá / cấu hình -> đăng nhập lại).
_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()
_TEMPLATE_RE = re.compile(r"\{(secret|user|password|basic)\}")


def clear_token_cache(source_id: Optional[str] = None) -> None:
    with _TOKEN_LOCK:
        for key in [k for k in _TOKEN_CACHE if source_id is None or k.startswith(f"{source_id}:")]:
            _TOKEN_CACHE.pop(key, None)


class GenericConnector(BaseConnector):
    """
    Connector khai báo: đọc khai báo từ sổ đăng ký, dựng yêu cầu, xác thực, phân trang, chuẩn hoá kết quả.

    Hỗ trợ: xác thực none / bearer / basic / header / query / OAuth2 client-credentials / đăng nhập lấy token;
    TLS tuỳ chọn (CA nội bộ, chứng chỉ tự ký, mTLS); phân trang page / offset / cursor / next_url / Link header;
    truy vấn đặt tên có tham số (`queries`); thao tác can thiệp đặt tên (`actions`, không thử lại, luôn do tầng
    gọi đưa qua cổng duyệt); nguồn SQL chỉ-đọc (`kind="sql"`).
    """

    def __init__(self, source: Dict[str, Any], connector_config: Optional[ConnectorConfig] = None):
        """
        Args:
            source: bản ghi từ `custom_registry.get_source()` (có `auth_value`).
            connector_config: ghi đè cấu hình (chủ yếu dùng cho test).
        """
        if connector_config is None:
            timeout = float(source.get("timeout_seconds") or 10.0)
            extra = {k: v for k, v in source.items() if k not in ("id", "auth_value")}
            connector_config = ConnectorConfig(
                name=f"ds:{source.get('id', 'unknown')}",
                enabled=bool(source.get("enabled", True)),
                timeout_seconds=timeout,
                extra=extra,
            )
        super().__init__(connector_config)
        self.source: Dict[str, Any] = dict(source)
        self._secret = str(self.source.get("auth_value") or "").strip()
        self._headers: Dict[str, str] = self._build_headers()

    # ── Chuẩn bị yêu cầu ───────────────────────────────────────────────

    @property
    def kind(self) -> str:
        return str(self.source.get("kind") or "rest")

    def _build_headers(self) -> Dict[str, str]:
        """Header tĩnh: Accept + header khai báo + xác thực kiểu bearer / basic / header (không cần bắt tay)."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "VN-MateAI/1.0 (data-source)",
        }
        headers.update({str(k): str(v) for k, v in (self.source.get("extra_headers") or {}).items()})
        auth_type = str(self.source.get("auth_type") or "none").lower()
        secret = self._secret

        if not secret or auth_type == "none":
            return headers

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "basic":
            raw = str(self.source.get("auth_value") or "")
            # Người dùng nhập "user:pass"; cũng chấp nhận sẵn base64.
            if ":" in raw:
                encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
            else:
                encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif auth_type == "header":
            name = str(self.source.get("auth_header") or "X-Api-Key").strip()
            headers[name] = secret
        elif auth_type == "query":
            pass  # xử lý ở `build_url` vì query string không thuộc header
        return headers

    def build_url(self, path: Optional[str] = None) -> str:
        """Ghép `base_url` + path, cộng tham số query của kiểu xác thực."""
        base = str(self.source.get("base_url") or "").rstrip("/")
        rel = _normalise_join(path if path is not None else self.source.get("default_path"))

        url = f"{base}{rel}" if rel else base

        # auth_type=query: khoá nằm trên URL chứ không trong header.
        if str(self.source.get("auth_type") or "").lower() == "query":
            key = str(self.source.get("auth_query") or "api_key").strip() or "api_key"
            if self._secret:
                from urllib.parse import quote
                url = f"{url}{'&' if '?' in url else '?'}{quote(key)}={quote(self._secret)}"

        return url

    def _same_origin(self, url: str) -> bool:
        a, b = urlsplit(url), urlsplit(str(self.source.get("base_url") or ""))
        return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())

    def _tls(self) -> Tuple[Any, Any, Optional[str]]:
        """(verify, cert, lỗi). `verify_ssl=false` -> không kiểm chứng; có `ca_bundle` -> dùng CA nội bộ."""
        verify: Any = True
        if not self.source.get("verify_ssl", True):
            verify = False
        elif self.source.get("ca_bundle"):
            ca = str(self.source["ca_bundle"])
            if not Path(ca).is_file():
                return True, None, f"Không thấy tệp CA nội bộ (ca_bundle): {ca}"
            verify = ca
        cert: Any = None
        if self.source.get("client_cert"):
            crt, key = str(self.source["client_cert"]), str(self.source.get("client_key") or "")
            for f in (crt, key):
                if f and not Path(f).is_file():
                    return verify, None, f"Không thấy tệp chứng chỉ máy khách: {f}"
            cert = (crt, key) if key else crt
        return verify, cert, None

    def _mask(self, text: str) -> str:
        text = self._mask_secrets(text or "")
        if len(self._secret) >= 4:
            text = text.replace(self._secret, "***MASKED***")
            for part in self._secret.split(":"):
                if len(part) >= 6:
                    text = text.replace(part, "***MASKED***")
        return text

    def _fail(self, error: str, **meta: Any) -> ConnectorResult:
        return ConnectorResult(success=False, error=self._mask(error), source=self.config.name, metadata=meta)

    # ── Xác thực có bắt tay (đăng nhập / OAuth2) ───────────────────────

    def _cache_key(self) -> str:
        fp = hashlib.sha256(json.dumps([self._secret, self.source.get("login"), self.source.get("oauth2")],
                                       sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"{self.source.get('id', 'x')}:{fp}"

    def _template_vars(self) -> Dict[str, str]:
        raw = self._secret
        user, _, password = raw.partition(":") if ":" in raw else ("", "", raw)
        basic = base64.b64encode((raw if ":" in raw else raw).encode("utf-8")).decode("ascii")
        return {"secret": raw, "user": user, "password": password, "basic": basic}

    def _render_tpl(self, node: Any) -> Any:
        vars_ = self._template_vars()
        if isinstance(node, str):
            return _TEMPLATE_RE.sub(lambda m: vars_[m.group(1)], node)
        if isinstance(node, dict):
            return {k: self._render_tpl(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._render_tpl(v) for v in node]
        return node

    async def _token(self, force: bool = False) -> Tuple[Optional[str], Optional[ConnectorResult]]:
        """Token hiện hành cho auth_type login / oauth2_client (lấy mới khi chưa có / hết hạn / `force`)."""
        key = self._cache_key()
        if not force:
            with _TOKEN_LOCK:
                hit = _TOKEN_CACHE.get(key)
            if hit and hit[1] > time.monotonic():
                return hit[0], None
        verify, cert, tls_err = self._tls()
        if tls_err:
            return None, self._fail(tls_err)
        auth_type = str(self.source.get("auth_type") or "").lower()
        if auth_type == "oauth2_client":
            cfg = self.source.get("oauth2") or {}
            cid, sep, csecret = self._secret.partition(":")
            if not sep or not cid or not csecret:
                return None, self._fail("OAuth2: khoá phải có dạng client_id:client_secret")
            form = {"grant_type": "client_credentials"}
            headers = {"Accept": "application/json", "User-Agent": "VN-MateAI/1.0 (data-source)"}
            if cfg.get("client_auth") == "basic":
                headers["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{csecret}".encode()).decode("ascii")
            else:
                form["client_id"], form["client_secret"] = cid, csecret
            for k in ("scope", "audience"):
                if cfg.get(k):
                    form[k] = cfg[k]
            res = await self._request_with_retry("POST", cfg["token_url"], headers=headers, form_body=form,
                                                 verify=verify, cert=cert, retries=0)
            if not res.success:
                return None, self._fail(f"Lấy token OAuth2 thất bại: {res.error}", **(res.metadata or {}))
            payload = res.data if isinstance(res.data, dict) else {}
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                return None, self._fail("Máy chủ OAuth2 không trả `access_token`")
            try:
                ttl = max(30.0, float(payload.get("expires_in", 1500)) - 30.0)
            except (TypeError, ValueError):
                ttl = 1500.0
        else:
            cfg = self.source.get("login") or {}
            headers = {"Accept": "application/json", "User-Agent": "VN-MateAI/1.0 (data-source)",
                       **{k: self._render_tpl(v) for k, v in (cfg.get("headers") or {}).items()}}
            body = self._render_tpl(cfg.get("body")) if cfg.get("body") is not None else None
            as_form = cfg.get("body_type") == "form" and isinstance(body, dict)
            res = await self._request_with_retry(
                cfg.get("method", "POST"), self.build_url(cfg["path"]), headers=headers,
                json_body=None if as_form or isinstance(body, str) else body, form_body=body if as_form else None,
                verify=verify, cert=cert, retries=0)
            if not res.success:
                return None, self._fail(f"Đăng nhập thất bại: {res.error}", **(res.metadata or {}))
            found, node = extract_path(res.data, cfg.get("token_path", ""))
            token = node.strip().strip('"') if found and isinstance(node, str) else None
            if not token:
                return None, self._fail("Đăng nhập được nhưng không tìm thấy token ở `login.token_path` — "
                                        f"phản hồi có: {_shape(res.data)}")
            ttl = float(cfg.get("ttl_seconds", 1500))
        with _TOKEN_LOCK:
            _TOKEN_CACHE[key] = (token, time.monotonic() + ttl)
        return token, None

    async def _auth_headers(self, force_token: bool = False) -> Tuple[Dict[str, str], Optional[ConnectorResult]]:
        headers = dict(self._headers)
        auth_type = str(self.source.get("auth_type") or "none").lower()
        if auth_type in ("login", "oauth2_client"):
            token, err = await self._token(force=force_token)
            if err:
                return headers, err
            if auth_type == "oauth2_client":
                headers["Authorization"] = f"Bearer {token}"
            else:
                cfg = self.source["login"]
                headers[cfg.get("token_header", "Authorization")] = f"{cfg.get('token_prefix', 'Bearer ')}{token}"
        return headers, None

    # ── BaseConnector interface ────────────────────────────────────────

    async def authenticate(self) -> bool:
        """Khai báo đủ dùng để gọi chưa (không bắt tay — chỉ cách gọi thật mới biết khoá đúng)."""
        if self.kind == "sql":
            c = self.source.get("connection") or {}
            return bool(c.get("driver") and c.get("database") and (c.get("driver") == "sqlite" or c.get("host")))
        if not str(self.source.get("base_url") or "").strip():
            return False
        auth_type = str(self.source.get("auth_type") or "none").lower()
        if auth_type != "none" and not self._secret:
            return False
        if auth_type == "login" and not self.source.get("login"):
            return False
        if auth_type == "oauth2_client" and not self.source.get("oauth2"):
            return False
        return True

    async def health_check(self) -> ConnectorResult:
        """Gọi THẬT một yêu cầu: kết nối + xác thực (kể cả đăng nhập / OAuth2) + một lần GET `health_path`
        (mặc định `default_path`). Khoẻ = máy chủ trả HTTP 2xx — không đòi phản hồi phải là bảng dữ liệu."""
        if not await self.authenticate():
            return ConnectorResult(
                success=False,
                error="Thiếu thông tin kết nối hoặc khoá xác thực",
                source=self.config.name,
            )
        t0 = time.monotonic()
        if self.kind == "sql":
            try:
                ms = await asyncio.to_thread(sql_connector.ping, self.source)
            except sql_connector.SqlSourceError as exc:
                return self._fail(str(exc), latency_ms=(time.monotonic() - t0) * 1000)
            return ConnectorResult(success=True, data={"status": "healthy"}, latency_ms=ms, source=self.config.name,
                                   metadata={"driver": (self.source.get("connection") or {}).get("driver")})
        path = self.source.get("health_path") or None
        result = await self._send("GET", path, query=None, body=None, body_type="json")
        latency = (time.monotonic() - t0) * 1000
        if not result.success:
            return ConnectorResult(success=False, error=result.error, latency_ms=latency, source=self.config.name,
                                   metadata=result.metadata)
        rows = _find_rows(result.data) if not isinstance(result.data, str) else None
        return ConnectorResult(success=True, data={"status": "healthy"}, latency_ms=latency, source=self.config.name,
                               metadata={"http_status": result.metadata.get("http_status"),
                                         "rows_returned": len(rows) if rows is not None else 0})

    async def fetch_data(self, params: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        """
        Kéo dữ liệu theo `params`:
          - `path`    tên TRUY VẤN đã khai báo (`queries`) | tên path đã khai báo (`paths`) | đường dẫn thô
          - `args`    tham số đã khai báo của truy vấn đặt tên
          - `method` / `query` / `body`   chỉ cho đường dẫn thô (giao diện); AI đi qua truy vấn đặt tên
          - `limit`   số bản ghi tối đa
        """
        params = params or {}
        if not await self.authenticate():
            return ConnectorResult(
                success=False,
                error="Thiếu thông tin kết nối hoặc khoá xác thực",
                source=self.config.name,
            )
        limit = _coerce_limit(params.get("limit"), self.source.get("row_limit"),
                              cap=int(self.source.get("max_rows") or 500))
        name = str(params.get("path") or params.get("query_name") or "").strip()
        queries = self.source.get("queries") or {}

        if self.kind == "sql":
            name = name or (next(iter(queries)) if len(queries) == 1 else "")
            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(sql_connector.run_query, self.source, name, params.get("args"), limit),
                    timeout=float(self.source.get("timeout_seconds") or 10.0) + 5.0)
            except asyncio.TimeoutError:
                return self._fail("Truy vấn SQL quá thời gian cho phép")
            except (ValueError, sql_connector.SqlSourceError) as exc:
                return self._fail(str(exc))
            return ConnectorResult(success=True, data=data, source=self.config.name, metadata={"query": name})

        if name and name in queries:
            return await self._run_query(name, params.get("args"), limit)

        method = str(params.get("method") or self.source.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            return self._fail("method chỉ nhận GET hoặc POST")

        path = params.get("path")
        # Cho phép gọi bằng TÊN path đã khai báo ("doanh thu") thay vì URL thô,
        # để AI và UI không phải nhớ đường dẫn.
        if path and not str(path).startswith("/"):
            named = (self.source.get("paths") or {}).get(str(path))
            if not named:
                declared = list((self.source.get("paths") or {})) + list(queries)
                return self._fail(f"Không có báo cáo nào tên '{path}'. Đã khai báo: {', '.join(declared) or '(chưa có)'}")
            path = named

        req = {"method": method, "path": path, "body_type": "json",
               "query": params.get("query") if isinstance(params.get("query"), dict) else {},
               "body": params.get("body") if isinstance(params.get("body"), dict) else None}
        return await self._paged_fetch(req, self.source.get("rows_path") or "", self.source.get("pagination"), limit)

    async def fetch_raw(self, name: str, args: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        """Chạy một truy vấn ĐẶT TÊN (chỉ đọc) và trả JSON gốc của máy chủ (không làm phẳng thành bảng). Dùng cho giám sát."""
        queries = self.source.get("queries") or {}
        if self.kind != "rest" or name not in queries:
            return self._fail(f"Nguồn này không có truy vấn tên '{name}'. Đã khai báo: {', '.join(queries) or '(chưa có)'}")
        if not await self.authenticate():
            return self._fail("Thiếu thông tin kết nối hoặc khoá xác thực")
        try:
            req = render_request(queries[name], args)
        except ValueError as exc:
            return self._fail(str(exc))
        return await self._send(req["method"], req["path"], query=req["query"], body=req["body"], body_type=req["body_type"])

    async def _run_query(self, name: str, args: Optional[Dict[str, Any]], limit: int) -> ConnectorResult:
        op = (self.source.get("queries") or {})[name]
        try:
            req = render_request(op, args)
        except ValueError as exc:
            return self._fail(str(exc))
        return await self._paged_fetch(req, op.get("rows_path") or self.source.get("rows_path") or "",
                                       op.get("pagination") or self.source.get("pagination"), limit)

    async def run_action(self, name: str, args: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        """Thực hiện một thao tác can thiệp đã khai báo. KHÔNG thử lại. Tầng gọi chịu trách nhiệm đưa qua cổng duyệt."""
        if self.kind != "rest":
            return self._fail("Nguồn này không có thao tác can thiệp")
        if not await self.authenticate():
            return self._fail("Thiếu thông tin kết nối hoặc khoá xác thực")
        actions = self.source.get("actions") or {}
        op = actions.get(name)
        if not op:
            return self._fail(f"Không có thao tác tên '{name}'. Đã khai báo: {', '.join(actions) or '(chưa có)'}")
        try:
            req = render_request(op, args)
        except ValueError as exc:
            return self._fail(str(exc))
        result = await self._send(req["method"], req["path"], query=req["query"], body=req["body"],
                                  body_type=req["body_type"], retries=0)
        if not result.success:
            return result
        return ConnectorResult(
            success=True, latency_ms=result.latency_ms, source=self.config.name,
            data={"http_status": result.metadata.get("http_status"), "response": _clip(result.data)},
            metadata={"action": name, "http_status": result.metadata.get("http_status")})

    # ── Nội bộ ─────────────────────────────────────────────────────────

    async def _send(self, method: str, path: Optional[str], *, query: Optional[Dict[str, Any]], body: Any,
                    body_type: str = "json", retries: Optional[int] = None) -> ConnectorResult:
        """Một yêu cầu HTTP đã xác thực. Token đăng nhập / OAuth2 bị từ chối (401) -> lấy token mới và thử MỘT lần nữa."""
        verify, cert, tls_err = self._tls()
        if tls_err:
            return self._fail(tls_err)
        if path and str(path).startswith(("http://", "https://")):
            if not self._same_origin(str(path)):
                return self._fail("Địa chỉ trang kế tiếp khác máy chủ đã khai báo — bị chặn để không gửi khoá sang nơi khác")
            url = str(path)
        else:
            url = self.build_url(path)
        if method == "GET" and isinstance(body, dict) and body:
            query, body = {**(query or {}), **body}, None      # GET không có body — chuyển sang query để không mất
        for attempt in (0, 1):
            headers, err = await self._auth_headers(force_token=attempt == 1)
            if err:
                return err
            as_form = body_type == "form" and isinstance(body, dict)
            result = await self._request_with_retry(
                method=method, url=url, headers=headers,
                json_body=None if as_form or isinstance(body, str) else body,
                form_body=body if as_form else None, params=query or None,
                verify=verify, cert=cert, retries=retries)
            token_flow = str(self.source.get("auth_type") or "").lower() in ("login", "oauth2_client")
            if not result.success and token_flow and attempt == 0 and (result.metadata or {}).get("http_status") == 401:
                continue
            break
        if not result.success:
            return ConnectorResult(success=False, error=self._mask(result.error or "Không xác định"),
                                   latency_ms=result.latency_ms, source=self.config.name, metadata=result.metadata)
        return result

    def _rows_from(self, payload: Any, rows_path: str) -> Tuple[Optional[List[Any]], str]:
        if isinstance(payload, str):
            return None, "Endpoint không trả JSON — kiểm tra lại path"
        if rows_path:
            found, node = extract_path(payload, rows_path)
            if not found:
                said = _extract_error(payload)
                return None, (f"Không thấy `{rows_path}` trong phản hồi (có: {_shape(payload)})"
                              + (f"; máy chủ báo: {said}" if said else ""))
            if isinstance(node, list):
                return node, ""
            if isinstance(node, dict):
                return [node], ""
            return None, f"`{rows_path}` không phải danh sách bản ghi"
        rows = _find_rows(payload)
        if rows is None:
            said = _extract_error(payload)
            return None, (f"Không tìm thấy danh sách dữ liệu trong phản hồi. Phản hồi có: {_shape(payload)}"
                          + (f"; máy chủ báo: {said}" if said else ""))
        return rows, ""

    async def _paged_fetch(self, req: Dict[str, Any], rows_path: str, pagination: Optional[Dict[str, Any]],
                           limit: int) -> ConnectorResult:
        """Gọi (nhiều trang nếu khai báo phân trang) và chuẩn hoá về `{rows, columns, total, returned, truncated}`."""
        pag = pagination or None
        collected: List[Any] = []
        total: Optional[int] = None
        more = False
        latency = 0.0
        last_meta: Dict[str, Any] = {}
        prev_first: Any = object()
        cursor: Any = None
        next_target: Optional[str] = None
        pages = int(pag["max_pages"]) if pag else 1
        fetched_raw = 0
        for page_no in range(pages):
            query = dict(req.get("query") or {})
            path = req.get("path")
            if pag:
                ptype = pag["type"]
                if ptype == "page":
                    query[pag["page_param"]] = pag["start_page"] + page_no
                    query[pag["size_param"]] = pag["page_size"]
                elif ptype == "offset":
                    query[pag["offset_param"]] = pag["start_offset"] + fetched_raw
                    query[pag["size_param"]] = pag["page_size"]
                elif ptype == "cursor" and cursor:
                    query[pag["cursor_param"]] = cursor
                elif ptype in ("next_url", "link_header") and next_target:
                    path, query = next_target, {}
            result = await self._send(req["method"], path, query=query, body=req.get("body"),
                                      body_type=req.get("body_type", "json"))
            latency += result.latency_ms or 0.0
            last_meta = result.metadata or {}
            if not result.success:
                if page_no == 0:
                    return result
                more = True                       # đã có dữ liệu: trả phần đã lấy, báo còn nữa
                break
            rows, why = self._rows_from(result.data, rows_path)
            if rows is None:
                if page_no == 0:
                    return self._fail(why, http_status=last_meta.get("http_status"))
                break
            if page_no == 0:
                total = _find_total(result.data, len(rows)) if isinstance(result.data, dict) else None
            if pag and rows and page_no > 0 and rows[0] == prev_first:
                break                              # máy chủ bỏ qua tham số trang: tránh lặp
            prev_first = rows[0] if rows else prev_first
            collected.extend(rows)
            fetched_raw += len(rows)
            if not pag or len(collected) >= limit + 1:
                more = bool(pag) and len(collected) > limit
                break
            ptype = pag["type"]
            if ptype in ("page", "offset"):
                if len(rows) < pag["page_size"] or (total is not None and fetched_raw >= total):
                    break
            elif ptype == "cursor":
                found, nxt = extract_path(result.data, pag["next_path"])
                cursor = nxt if found and nxt not in (None, "", False) else None
                if not cursor:
                    break
            elif ptype == "next_url":
                found, nxt = extract_path(result.data, pag["next_path"])
                next_target = str(nxt) if found and nxt else None
                if not next_target:
                    break
            elif ptype == "link_header":
                next_target = _link_next(last_meta.get("link", ""))
                if not next_target:
                    break
            if page_no == pages - 1:
                more = True                        # hết số trang cho phép mà server còn nữa
        trimmed = collected[:limit]
        grand = max(total or 0, len(collected)) if (total or not more) else max(len(collected) + 1, total or 0)
        return ConnectorResult(
            success=True,
            data={"rows": trimmed, "columns": _columns_of(trimmed), "total": grand, "returned": len(trimmed),
                  "truncated": grand > len(trimmed) or more},
            latency_ms=latency, source=self.config.name,
            metadata={"http_status": last_meta.get("http_status"), "path": req.get("path") or self.source.get("default_path")},
        )

    async def _call(self, method: str, path: Optional[str], query: Optional[Dict[str, Any]],
                    body: Optional[Dict[str, Any]], limit: int) -> ConnectorResult:
        """Giữ để tương thích: một lần gọi chuẩn hoá (không phân trang riêng)."""
        return await self._paged_fetch({"method": method, "path": path, "query": query or {}, "body": body,
                                        "body_type": "json"}, self.source.get("rows_path") or "",
                                       self.source.get("pagination"), limit)


# ── Hàm cấp module ───────────────────────────────────────────────────────

def _normalise_join(path: Any) -> str:
    """Chuẩn hoá path tương đối thành `/a/b` để ghép với base_url."""
    raw = str(path or "").strip()
    if not raw or raw == "/":
        return ""
    if raw.startswith(("http://", "https://")):
        return ""  # path tuyệt đối -> dùng luôn base_url, bỏ qua base
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _coerce_limit(value: Any, fallback: Any, cap: int = 500) -> int:
    """Giới hạn số bản ghi trả về (tránh kéo cả bảng 100k dòng về UI)."""
    raw = value if value is not None else fallback
    try:
        num = int(raw)
    except (TypeError, ValueError):
        num = 50
    return max(1, min(max(1, cap), num))


def _shape(payload: Any) -> str:
    if isinstance(payload, dict):
        return "khoá " + ", ".join(list(payload)[:6])
    return type(payload).__name__


def _clip(payload: Any, limit: int = 4000) -> Any:
    """Phản hồi của thao tác can thiệp trả cho AI: cắt gọn, không nuốt cả ngữ cảnh."""
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    if len(text) <= limit:
        return payload
    return {"_truncated": True, "preview": text[:limit]}


def _link_next(header: str) -> Optional[str]:
    """URL của `rel="next"` trong header Link (RFC 5988)."""
    for part in str(header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;(.*)', part)
        if m and re.search(r'rel\s*=\s*"?next"?', m.group(2), re.IGNORECASE):
            return m.group(1)
    return None


async def probe_data_source(source_id: str) -> ConnectorResult:
    """
    Kiểm tra sức khoẻ một data source tùy chỉnh theo id.

    Trả `success=False` kèm lý do nếu id không tồn tại hoặc cấu hình hỏng —
    thông điệp này hiện thẳng lên UI nên phải cụ thể.
    """
    from mateai.infrastructure.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=True)
    if not source:
        return ConnectorResult(
            success=False,
            error=f"Không tìm thấy nguồn dữ liệu '{source_id}'",
            source=f"ds:{source_id}",
        )
    if not source.get("enabled", True):
        return ConnectorResult(
            success=False,
            error="Nguồn dữ liệu đang bị tắt",
            source=f"ds:{source_id}",
        )

    return await GenericConnector(source).health_check()


async def fetch_data_source(source_id: str, params: Optional[Dict[str, Any]] = None) -> ConnectorResult:
    """Kéo dữ liệu báo cáo từ một data source tùy chỉnh theo id."""
    from mateai.infrastructure.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=True)
    if not source:
        return ConnectorResult(
            success=False,
            error=f"Không tìm thấy nguồn dữ liệu '{source_id}'",
            source=f"ds:{source_id}",
        )
    if not source.get("enabled", True):
        return ConnectorResult(
            success=False,
            error="Nguồn dữ liệu đang bị tắt",
            source=f"ds:{source_id}",
        )

    return await GenericConnector(source).fetch_data(params or {})


async def fetch_raw_query(source: Dict[str, Any], name: str, args: Optional[Dict[str, Any]] = None) -> ConnectorResult:
    """`GenericConnector(source).fetch_raw` cho nơi gọi chỉ có bản ghi nguồn (đã giải mã khoá)."""
    return await GenericConnector(source).fetch_raw(name, args)


async def run_source_action(source_id: str, action: str, args: Optional[Dict[str, Any]] = None) -> ConnectorResult:
    """Chạy một thao tác can thiệp đã khai báo. Nơi gọi PHẢI đã đưa qua cổng duyệt (`execute_with_hitl`)."""
    from mateai.infrastructure.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=True)
    if not source:
        return ConnectorResult(success=False, error=f"Không tìm thấy nguồn dữ liệu '{source_id}'", source=f"ds:{source_id}")
    if not source.get("enabled", True):
        return ConnectorResult(success=False, error="Nguồn dữ liệu đang bị tắt", source=f"ds:{source_id}")
    return await GenericConnector(source).run_action(action, args)
