# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/custom_registry.py
===================================
Phase 62 — Sổ đăng ký data source tùy chỉnh.

Vấn đề Phase 59/61 giải quyết
-----------------------------
Phase 59 hardcode 4 connector (AWS/OCI/Paperless/eInvoice) trong
`CONNECTOR_REGISTRY`. Thêm app thứ 5 (MISA, Odoo, KiotViet, SAP...) thì phải
viết Python mới, sửa `__init__.py`, sửa `CONNECTOR_RISK_LEVELS`, sửa UI, rồi
deploy lại. Với mỗi khách hàng một bộ app khác nhau thì đó là chi phí lặp lại
cho từng lần onboard.

Cách giải quyết ở đây
---------------------
Tách phần *khai báo* (app nào, URL nào, xác thực kiểu gì, path nào lấy báo
cáo) khỏi phần *gọi HTTP* (giống nhau với mọi REST API). Khai báo lưu vào
`config/data_sources.json`; `GenericConnector` đọc khai báo đó để gọi. Thêm
app mới = thêm một mục JSON, không cần deploy.

Vì sao lưu file riêng chứ không nhét vào `config.json`
-----------------------------------------------------
1. `config.json` đi qua `AppSettings` với `extra="ignore"` — khóa lạ bị pydantic
   loại khỏi object singleton (xem `_read_config_json_block` trong
   `base_connector.py`, phải đọc thẳng file vì lý do đúng y vậy).
2. Credential của app doanh nghiệp là dữ liệu bí mật của *khách hàng*, không
   phải cấu hình hệ thống. Tách file giúp `.gitignore` gọn và tránh rò rỉ khi
   ai đó paste config.json vào ticket.

Bất biến an toàn
----------------
- KHÔNG bao giờ trả về giá trị secret (`auth_value`). Chỉ trả tên khoá đã
  điền / chưa điền. Xem `mask_source`.
- `id` là slug an toàn để ghép vào URL path, không phải chuỗi tự do.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Thư mục config/ (không commit — chứa credential khách hàng).
# Thư mục gốc dự án — không suy từ vị trí file mã nguồn (chuyển module mà lệch
# đường dẫn là mọi nguồn dữ liệu đã cấu hình biến mất).
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
STORE_PATH = _PROJECT_ROOT / "config" / "data_sources.json"

#: Khoá bí mật của một data source. Giá trị KHÔNG BAO GIỜ đọc lên UI.
SECRET_FIELDS = ("auth_value",)

#: `id` phải an toàn để ghép vào URL path server (`/data-sources/{id}/fetch`).
#: Cho phép: chữ thường, số, gạch dưới, gạch ngang. 2–49 ký tự.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")

#: Các kiểu xác thực mà `GenericConnector` hiểu.
#:   login          POST/GET tới một đường dẫn đăng nhập, lấy token từ phản hồi, gửi kèm mọi yêu cầu (vCenter,
#:                  GLPI, Veeam, Zabbix cũ …); token được giữ tới khi hết hạn / bị từ chối rồi tự đăng nhập lại
#:   oauth2_client  OAuth2 client-credentials (auth_value = "client_id:client_secret")
AUTH_TYPES = ("none", "bearer", "basic", "header", "query", "oauth2_client", "login")
#: Loại nguồn giám sát — màn "Giám sát hạ tầng" và công cụ AI chỉ đọc từ nguồn mang nhãn này.
MONITOR_TYPES = ("", "prometheus", "grafana")

#: Loại nguồn: REST (HTTP) hoặc SQL chỉ-đọc.
KINDS = ("rest", "sql")
SQL_DRIVERS = ("postgresql", "mysql", "mssql", "oracle", "sqlite")

#: Header người quản trị KHÔNG được đặt qua `extra_headers` (xác thực đi qua auth_type).
_BLOCKED_HEADERS = frozenset({"authorization", "host", "content-length", "transfer-encoding", "connection", "cookie",
                              "proxy-authorization"})
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9\-]{1,40}$")
_LOGIN_TEMPLATE_VARS = frozenset({"secret", "user", "password", "basic"})

#: Cấu hình mặc định khi tạo mục mới.
DEFAULTS: Dict[str, Any] = {
    "auth_type": "none",
    "auth_header": "X-Api-Key",
    "auth_query": "api_key",
    "method": "GET",
    "default_path": "/",
    "timeout_seconds": 10.0,
    "row_limit": 50,
}

#: Khoá trong JSON mà sẽ tự bị chặn. Đây là đường đánh cắp credential của hệ
#: thống ngoài; app doanh nghiệp không bao giờ cần endpoint metadata này.
_BLOCKED_URL_FRAGMENTS = (
    "169.254.169.254",   # AWS/Azure/GCP instance metadata
    "metadata.google.internal",
    "100.100.100.200",   # Alibaba Cloud
)

_write_lock = threading.Lock()


# ── Lưu trữ ──────────────────────────────────────────────────────────────

def _read_raw() -> Dict[str, Any]:
    """Đọc file khai báo. Trả `{"sources": {}}` nếu chưa có/hỏng."""
    if not STORE_PATH.exists():
        return {"sources": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        # File hỏng thì KHÔNG ném — endpoint list vẫn phải trả được danh sách
        # rỗng thay vì 500. Người vận hành sẽ thấy warning trong log.
        logger.warning("[DataSource] Không đọc được %s: %s", STORE_PATH, exc)
        return {"sources": {}}

    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"sources": {}}
    # Bỏ mục hỏng thay vì làm hỏng cả danh sách.
    clean = {k: v for k, v in data["sources"].items() if isinstance(v, dict) and _ID_RE.match(str(k))}
    return {"sources": clean}


def _write_raw(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Ghi file tạm rồi đổi tên: đứt giữa chừng không để lại file nửa vời.
    os.replace(tmp, STORE_PATH)
    try:
        os.chmod(STORE_PATH, 0o600)  # chỉ owner đọc/ghi — chứa credential
    except OSError:  # pragma: no cover - filesystem không hỗ trợ
        pass


# ── Mã hoá khoá xác thực trên đĩa ────────────────────────────────────────

def _seal(record: Dict[str, Any]) -> Dict[str, Any]:
    """Bản ghi để GHI xuống đĩa: `auth_value` mã hoá (cùng khoá `certs/config_secret.key` của config.json)."""
    out = dict(record)
    val = str(out.get("auth_value") or "")
    if val:
        from mateai.config import secret_box
        if secret_box.enabled():
            out["auth_value"] = secret_box.encrypt_value(val)
    return out


def _unseal(record: Dict[str, Any]) -> Dict[str, Any]:
    """Bản ghi đọc từ đĩa -> `auth_value` dạng rõ (giá trị cũ chưa mã hoá vẫn đọc được, chuyển đổi dần)."""
    out = dict(record)
    val = out.get("auth_value")
    if isinstance(val, str) and val:
        from mateai.config import secret_box
        out["auth_value"] = secret_box.decrypt_value(val)
    return out


def has_plaintext_secrets() -> bool:
    """Còn khoá xác thực chưa mã hoá trên đĩa? (dùng cho bước khởi động / chẩn đoán)."""
    from mateai.config import secret_box
    return any(isinstance(r.get("auth_value"), str) and r["auth_value"] and not r["auth_value"].startswith(secret_box.PREFIX)
               for r in _read_raw()["sources"].values())


def encrypt_existing() -> int:
    """Mã hoá mọi khoá xác thực còn ở dạng rõ. Trả số nguồn đã chuyển. Idempotent."""
    from mateai.config import secret_box
    if not secret_box.enabled():
        return 0
    with _write_lock:
        data = _read_raw()
        changed = 0
        for sid, rec in data["sources"].items():
            val = rec.get("auth_value")
            if isinstance(val, str) and val and not val.startswith(secret_box.PREFIX):
                rec["auth_value"] = secret_box.encrypt_value(val)
                changed += 1
        if changed:
            _write_raw(data)
    return changed


# ── Chuẩn hoá ────────────────────────────────────────────────────────────

def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _coerce_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    # Chặn ngoài khoảng hợp lý: timeout 0 là treo, timeout 600 là treo cả server.
    return max(lo, min(hi, num))


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def validate_base_url(url: str) -> str:
    """
    Kiểm tra `base_url` và trả về bản đã bỏ dấu `/` cuối.

    Raise ValueError kèm lý do nếu không dùng được — thông báo này hiện thẳng
    lên UI nên phải nói rõ là người dùng cần sửa gì.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Thiếu base_url — ví dụ: https://erp.congty.vn/api")
    if not raw.lower().startswith(("http://", "https://")):
        raise ValueError("base_url phải bắt đầu bằng http:// hoặc https://")
    if any(frag in raw for frag in _BLOCKED_URL_FRAGMENTS):
        raise ValueError("URL này là endpoint metadata của cloud provider — bị chặn để tránh lộ credential")
    return raw.rstrip("/")


def _normalise_path(path: Any) -> str:
    """
    Chuẩn hoá path thành dạng `/a/b`. Path rỗng = gốc.
    """
    raw = str(path or "").strip()
    if not raw or raw == "/":
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _normalise_source(source_id: str, payload: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Dựng bản ghi hoàn chỉnh từ payload của UI + bản cũ (để giữ secret khi
    người dùng để trống ô mật khẩu).

    Raise ValueError nếu dữ liệu không dùng được.
    """
    if not _ID_RE.match(source_id):
        raise ValueError(
            "Mã nguồn dữ liệu chỉ gồm chữ thường, số, gạch dưới, gạch ngang "
            "(2–49 ký tự) — ví dụ: misa-amh, odoo-erp"
        )

    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("Thiếu tên hiển thị")
    if len(title) > 80:
        raise ValueError("Tên hiển thị tối đa 80 ký tự")

    kind = str(payload.get("kind") or (previous or {}).get("kind") or "rest").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"Loại nguồn không hợp lệ — chỉ nhận: {', '.join(KINDS)}")

    auth_type = str(payload.get("auth_type") or DEFAULTS["auth_type"]).strip().lower()
    if kind == "sql":
        auth_type = "none"                              # SQL: mật khẩu nằm ở `auth_value`, đăng nhập do driver lo
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"Kiểu xác thực không hợp lệ — chỉ nhận: {', '.join(AUTH_TYPES)}")

    record: Dict[str, Any] = {
        "id": source_id,
        "title": title,
        "description": str(payload.get("description") or "").strip()[:160],
        "category": str(payload.get("category") or "custom").strip() or "custom",
        "kind": kind,
        "base_url": validate_base_url(payload.get("base_url")) if kind == "rest" else "",
        "default_path": _normalise_path(payload.get("default_path", "/")),
        "auth_type": auth_type,
        # Ô secret trống = giữ giá trị đang lưu (giống hành vi form connector).
        "auth_value": str(payload.get("auth_value") or "").strip()
        or str((previous or {}).get("auth_value") or ""),
        "auth_header": (str(payload.get("auth_header") or "").strip() or DEFAULTS["auth_header"])[:64],
        "auth_query": (str(payload.get("auth_query") or "").strip() or DEFAULTS["auth_query"])[:64],
        "method": (str(payload.get("method") or "GET").strip().upper() or "GET"),
        "timeout_seconds": _coerce_float(payload.get("timeout_seconds"), DEFAULTS["timeout_seconds"], 1.0, 60.0),
        "row_limit": _coerce_int(payload.get("row_limit"), DEFAULTS["row_limit"], 1, 500),
        "enabled": _coerce_bool(payload.get("enabled"), True),
        "created_at": (previous or {}).get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if record["method"] not in ("GET", "POST"):
        raise ValueError("method chỉ nhận GET hoặc POST — chỉ đọc dữ liệu báo cáo")
    _normalise_extensions(record, payload, previous, kind, auth_type)

    # paths: tên -> path tương đối, để gọi nhiều báo cáo trên cùng một app
    # (vd. "doanh thu" -> /reports/revenue, "tồn kho" -> /reports/stock).
    raw_paths = payload.get("paths")
    paths: Dict[str, str] = {}
    if isinstance(raw_paths, dict):
        for key, val in raw_paths.items():
            name = str(key).strip()
            if name and len(name) <= 40:
                paths[name] = _normalise_path(val)
    record["paths"] = paths

    return record


def _normalise_extensions(record: Dict[str, Any], payload: Dict[str, Any], previous: Optional[Dict[str, Any]],
                          kind: str, auth_type: str) -> None:
    """TLS, đăng nhập / OAuth2, phân trang, truy vấn, thao tác can thiệp, kết nối SQL. Ném ValueError rõ ràng."""
    from mateai.infrastructure.connectors import operations as ops

    def pick(key: str) -> Any:
        return payload[key] if key in payload else (previous or {}).get(key)

    monitor = str(pick("monitor_type") or "").strip().lower()
    if monitor not in MONITOR_TYPES:
        raise ValueError(f"monitor_type chỉ nhận: {', '.join(t or '(trống)' for t in MONITOR_TYPES)}")
    record["monitor_type"] = monitor                 # nguồn này cũng là nguồn GIÁM SÁT (Prometheus / Grafana)
    record["max_rows"] = _coerce_int(pick("max_rows"), 500, 1, 10000)
    record["row_limit"] = min(record["row_limit"], record["max_rows"])
    record["verify_ssl"] = _coerce_bool(pick("verify_ssl"), True)
    for key in ("ca_bundle", "client_cert", "client_key"):
        val = str(pick(key) or "").strip()
        if len(val) > 260 or any(c in val for c in "\r\n\x00"):
            raise ValueError(f"{key} không hợp lệ (đường dẫn tệp, tối đa 260 ký tự)")
        record[key] = val
    if bool(record["client_cert"]) != bool(record["client_key"]) and record["client_key"]:
        raise ValueError("client_key cần đi kèm client_cert")
    rows_path = str(pick("rows_path") or "").strip()
    if rows_path and not re.match(r"^[A-Za-z0-9_\-\[\]\.]{1,120}$", rows_path):
        raise ValueError("rows_path không hợp lệ (dạng a.b.c)")
    record["rows_path"] = rows_path
    health_path = str(pick("health_path") or "").strip()
    record["health_path"] = _normalise_path(health_path) if health_path else ""

    headers = pick("extra_headers") or {}
    if not isinstance(headers, dict) or len(headers) > 10:
        raise ValueError("extra_headers phải là một đối tượng, tối đa 10 header")
    clean_headers: Dict[str, str] = {}
    for k, v in headers.items():
        name = str(k).strip()
        if not _HEADER_NAME_RE.match(name) or name.lower() in _BLOCKED_HEADERS:
            raise ValueError(f"Header '{name}' không được phép (xác thực khai báo qua auth_type, không qua extra_headers)")
        val = str(v)
        if len(val) > 200 or any(c in val for c in "\r\n\x00"):
            raise ValueError(f"Giá trị header '{name}' không hợp lệ")
        clean_headers[name] = val
    record["extra_headers"] = clean_headers

    record["pagination"] = ops.normalise_pagination(pick("pagination"))

    if auth_type == "login":
        record["login"] = _normalise_login(pick("login"))
    else:
        record["login"] = None
    if auth_type == "oauth2_client":
        record["oauth2"] = _normalise_oauth2(pick("oauth2"))
    else:
        record["oauth2"] = None

    if kind == "sql":
        record["connection"] = _normalise_connection(pick("connection"))
        record["queries"] = ops.normalise_sql_queries(pick("queries"))
        record["actions"] = {}
        record["paths"] = {}
        record["default_path"] = "/"
    else:
        record["connection"] = None
        record["queries"] = ops.normalise_operations(pick("queries"), writes=False)
        record["actions"] = ops.normalise_operations(pick("actions"), writes=True)
        clash = sorted(set(record["queries"]) & set(record["actions"]))
        if clash:
            raise ValueError(f"Tên trùng giữa `queries` và `actions`: {', '.join(clash)}")


def _normalise_login(raw: Any) -> Dict[str, Any]:
    from mateai.infrastructure.connectors import operations as ops
    if not isinstance(raw, dict):
        raise ValueError("auth_type='login' cần khối `login` {method, path, headers, body, token_path, ...}")
    method = str(raw.get("method") or "POST").upper()
    if method not in ("GET", "POST"):
        raise ValueError("login.method chỉ nhận GET hoặc POST")
    path = _normalise_path(raw.get("path"))
    if path == "/":
        raise ValueError("login.path là bắt buộc (đường dẫn tới điểm đăng nhập)")
    body_type = str(raw.get("body_type") or "json").lower()
    if body_type not in ops.BODY_TYPES:
        raise ValueError("login.body_type chỉ nhận json hoặc form")
    headers = raw.get("headers") or {}
    if not isinstance(headers, dict) or len(headers) > 8:
        raise ValueError("login.headers phải là một đối tượng, tối đa 8 header")
    for k, v in headers.items():
        if not _HEADER_NAME_RE.match(str(k)) or len(str(v)) > 200:
            raise ValueError(f"login.headers['{k}'] không hợp lệ")
    token_header = str(raw.get("token_header") or "Authorization").strip()
    if not _HEADER_NAME_RE.match(token_header) or token_header.lower() in _BLOCKED_HEADERS - {"authorization"}:
        raise ValueError("login.token_header không hợp lệ")
    token_path = str(raw.get("token_path") or "").strip()
    if token_path and not re.match(r"^[A-Za-z0-9_\-\[\]\.]{1,120}$", token_path):
        raise ValueError("login.token_path không hợp lệ (dạng a.b.c; bỏ trống = cả phản hồi là token)")
    body = raw.get("body")
    if body is not None and not isinstance(body, (dict, str)):
        raise ValueError("login.body phải là đối tượng hoặc chuỗi")
    out = {
        "method": method, "path": path, "headers": {str(k): str(v) for k, v in headers.items()},
        "body": body, "body_type": body_type, "token_path": token_path, "token_header": token_header,
        "token_prefix": str(raw.get("token_prefix") if raw.get("token_prefix") is not None else "Bearer ")[:20],
        "ttl_seconds": _coerce_int(raw.get("ttl_seconds"), 1500, 60, 86400),
    }
    text = json.dumps([out["headers"], out["body"]], ensure_ascii=False)
    bad = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text)) - _LOGIN_TEMPLATE_VARS)
    if bad:
        raise ValueError(f"login: biến không có: {{{', '.join(bad)}}} — chỉ dùng {{secret}}, {{user}}, {{password}}, {{basic}}")
    return out


def _normalise_oauth2(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("auth_type='oauth2_client' cần khối `oauth2` {token_url, scope, client_auth}")
    token_url = validate_base_url(raw.get("token_url"))
    client_auth = str(raw.get("client_auth") or "body").lower()
    if client_auth not in ("body", "basic"):
        raise ValueError("oauth2.client_auth chỉ nhận body hoặc basic")
    return {"token_url": token_url, "scope": str(raw.get("scope") or "")[:200],
            "audience": str(raw.get("audience") or "")[:200], "client_auth": client_auth}


def _normalise_connection(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Nguồn SQL cần khối `connection` {driver, host, port, database, user}")
    driver = str(raw.get("driver") or "").lower()
    if driver not in SQL_DRIVERS:
        raise ValueError(f"connection.driver chỉ nhận: {', '.join(SQL_DRIVERS)}")
    database = str(raw.get("database") or "").strip()
    host = str(raw.get("host") or "").strip()
    if not database:
        raise ValueError("connection.database là bắt buộc" + (" (đường dẫn tệp SQLite)" if driver == "sqlite" else ""))
    if driver != "sqlite" and not host:
        raise ValueError("connection.host là bắt buộc")
    if any(frag in host for frag in _BLOCKED_URL_FRAGMENTS):
        raise ValueError("Địa chỉ này là endpoint metadata của cloud provider — bị chặn")
    for key, val in (("host", host), ("database", database), ("user", str(raw.get("user") or ""))):
        if len(val) > 260 or any(c in val for c in "\r\n\x00"):
            raise ValueError(f"connection.{key} không hợp lệ")
    return {"driver": driver, "host": host, "port": _coerce_int(raw.get("port"), 0, 0, 65535), "database": database,
            "user": str(raw.get("user") or "").strip(),
            "ssl": _coerce_bool(raw.get("ssl"), False),
            "service_name": str(raw.get("service_name") or "")[:120],        # Oracle
            "odbc_driver": str(raw.get("odbc_driver") or "ODBC Driver 18 for SQL Server")[:80]}   # SQL Server


# ── API công khai ────────────────────────────────────────────────────────

def mask_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bản an toàn để trả về client: có `has_auth` nhưng KHÔNG có `auth_value`.

    UI chỉ cần biết "đã điền khoá chưa" để quyết định có gợi ý nhập lại hay
    không; tuyệt đối không cần — và không được — thấy giá trị.
    """
    out = {k: v for k, v in source.items() if k not in SECRET_FIELDS}
    out["has_auth"] = bool(source.get("auth_value"))
    out["available_paths"] = list((source.get("paths") or {}).keys()) + list((source.get("queries") or {}).keys())
    out["has_actions"] = bool(source.get("actions"))
    out.pop("paths", None)
    return out


def list_sources(include_secrets: bool = False) -> List[Dict[str, Any]]:
    """Danh sách data source tùy chỉnh, sắp theo `title`."""
    sources = _read_raw()["sources"].values()
    items = [_unseal(s) for s in sources] if include_secrets else [dict(s) for s in sources]
    if not include_secrets:
        items = [mask_source(s) for s in items]
    return sorted(items, key=lambda s: str(s.get("title") or s.get("id") or "").lower())


def get_source(source_id: str, include_secrets: bool = True) -> Optional[Dict[str, Any]]:
    """
    Lấy một bản ghi. Mặc định kèm secret vì đây là đường nội bộ — chỉ
    `GenericConnector` dùng. Endpoint HTTP phải gọi `include_secrets=False`.
    """
    record = _read_raw()["sources"].get(source_id)
    if not record:
        return None
    if not include_secrets:
        return mask_source(dict(record))
    return _unseal(record)


def upsert_source(source_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tạo mới hoặc cập nhật một data source. Trả về bản đã mask.

    Raise ValueError nếu payload không hợp lệ.
    """
    source_id = str(source_id or "").strip().lower()
    with _write_lock:
        data = _read_raw()
        previous = data["sources"].get(source_id)
        record = _normalise_source(source_id, payload, _unseal(previous) if previous else None)
        data["sources"][source_id] = _seal(record)
        _write_raw(data)

    action = "Cập nhật" if previous else "Thêm"
    logger.info("[DataSource] %s nguồn '%s' (%s)", action, record["title"], source_id)
    return mask_source(record)


def delete_source(source_id: str) -> bool:
    """Xoá một data source. Trả `False` nếu không tồn tại."""
    source_id = str(source_id or "").strip().lower()
    with _write_lock:
        data = _read_raw()
        if source_id not in data["sources"]:
            return False
        removed = data["sources"].pop(source_id)
        _write_raw(data)

    logger.info("[DataSource] Xoá nguồn '%s' (%s)", removed.get("title"), source_id)
    return True
