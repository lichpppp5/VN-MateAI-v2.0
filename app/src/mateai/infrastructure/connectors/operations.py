# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/connectors/operations.py
==============================================
"Thao tác khai báo" của một nguồn dữ liệu: người quản trị KHAI BÁO một lần (đường dẫn, tham số, mức rủi ro),
AI chỉ chọn TÊN thao tác và điền THAM SỐ đã khai báo. Không có đường nào cho AI tự dựng yêu cầu tuỳ ý.

  queries   đọc dữ liệu (REST GET/POST kiểu tìm kiếm / JSON-RPC, hoặc SQL chỉ-đọc)
  actions   can thiệp hệ thống (REST POST/PUT/PATCH/DELETE) — luôn đi qua cổng duyệt (rủi ro >= 3)

Cú pháp tham số trong đường dẫn / query / body REST: `{ten_tham_so}`; trong SQL: `:ten_tham_so`.

Bất biến an toàn (có test):
  - mọi `{x}` trong yêu cầu phải được khai báo trong `params` — gõ sai tên bị từ chối ngay lúc lưu;
  - giá trị tham số được ép kiểu + kiểm tra (enum / pattern / độ dài / khoảng) trước khi dùng;
  - giá trị chèn vào ĐƯỜNG DẪN bị mã hoá phần trăm (không thoát ra ngoài đoạn đường dẫn, cấm `.` và `..`);
  - SQL chỉ nhận một câu SELECT / WITH, không INSERT / UPDATE / DROP …, không nhiều câu lệnh.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

NAME_RE = re.compile(r"^[\w\-\. ]{1,40}$", re.UNICODE)
PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,39})\}")
_EXACT_PLACEHOLDER_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]{0,39})\}$")
_SQL_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]{0,39})\b")
_DOTTED_RE = re.compile(r"^[A-Za-z0-9_\-\[\]\.]{0,120}$")

READ_METHODS = ("GET", "POST")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
PARAM_TYPES = ("string", "integer", "number", "boolean")
PAGINATION_TYPES = ("none", "page", "offset", "cursor", "next_url", "link_header")
BODY_TYPES = ("json", "form")

#: Giá trị tham số chèn vào ĐƯỜNG DẪN: mặc định chỉ chữ / số / `_ - . : @ =`.
_PATH_SAFE_DEFAULT = re.compile(r"^[\w\-\.:@=]{1,200}$", re.UNICODE)

_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|exec|execute|call|into|pragma|attach|"
    r"detach|copy|vacuum|outfile|dumpfile|shutdown)\b", re.IGNORECASE)


# ── Tham số ─────────────────────────────────────────────────────────────────

def normalise_param(name: str, spec: Any) -> Dict[str, Any]:
    if not PARAM_RE.match(name):
        raise ValueError(f"Tên tham số '{name}' không hợp lệ (chữ, số, gạch dưới; bắt đầu bằng chữ; tối đa 40 ký tự)")
    spec = spec if isinstance(spec, dict) else {}
    ptype = str(spec.get("type") or "string").lower()
    if ptype not in PARAM_TYPES:
        raise ValueError(f"Tham số '{name}': kiểu '{ptype}' không hợp lệ — chỉ nhận {', '.join(PARAM_TYPES)}")
    out: Dict[str, Any] = {"type": ptype, "required": bool(spec.get("required", False)),
                           "description": str(spec.get("description") or "")[:120]}
    if "default" in spec and spec["default"] is not None:
        out["default"] = spec["default"]
        out["required"] = False
    if isinstance(spec.get("enum"), list):
        out["enum"] = [v for v in spec["enum"][:50] if isinstance(v, (str, int, float, bool))]
    if spec.get("pattern"):
        pat = str(spec["pattern"])[:200]
        try:
            re.compile(pat)
        except re.error as exc:
            raise ValueError(f"Tham số '{name}': pattern không hợp lệ ({exc})")
        out["pattern"] = pat
    try:
        out["max_length"] = max(1, min(1000, int(spec.get("max_length", 200))))
    except (TypeError, ValueError):
        out["max_length"] = 200
    for k in ("minimum", "maximum"):
        if spec.get(k) is not None:
            try:
                out[k] = float(spec[k])
            except (TypeError, ValueError):
                raise ValueError(f"Tham số '{name}': {k} phải là số")
    return out


def normalise_params(raw: Any) -> Dict[str, Dict[str, Any]]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("`params` phải là một đối tượng {tên: {type, required, ...}}")
    if len(raw) > 20:
        raise ValueError("Tối đa 20 tham số cho một thao tác")
    return {str(k): normalise_param(str(k), v) for k, v in raw.items()}


def coerce_args(params: Dict[str, Dict[str, Any]], args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Kiểm tra + ép kiểu tham số AI / người dùng truyền vào theo khai báo. Ném ValueError tiếng Việt."""
    args = dict(args or {})
    unknown = sorted(set(args) - set(params))
    if unknown:
        raise ValueError(f"Tham số không được khai báo: {', '.join(unknown)}. Chỉ nhận: {', '.join(params) or '(không có)'}")
    out: Dict[str, Any] = {}
    for name, spec in params.items():
        if name not in args or args[name] in (None, ""):
            if "default" in spec:
                out[name] = spec["default"]
                continue
            if spec.get("required"):
                raise ValueError(f"Thiếu tham số bắt buộc '{name}'" + (f" ({spec['description']})" if spec.get("description") else ""))
            continue
        out[name] = _coerce_one(name, spec, args[name])
    return out


def _coerce_one(name: str, spec: Dict[str, Any], value: Any) -> Any:
    ptype = spec["type"]
    try:
        if ptype == "integer":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            value = int(value)
        elif ptype == "number":
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
        elif ptype == "boolean":
            if isinstance(value, bool):
                pass
            elif str(value).strip().lower() in ("true", "1", "yes", "có"):
                value = True
            elif str(value).strip().lower() in ("false", "0", "no", "không"):
                value = False
            else:
                raise ValueError
        else:
            if isinstance(value, (dict, list)):
                raise ValueError
            value = str(value)
    except (TypeError, ValueError):
        raise ValueError(f"Tham số '{name}' phải là {ptype}")
    if ptype == "string":
        if len(value) > int(spec.get("max_length", 200)):
            raise ValueError(f"Tham số '{name}' dài quá {spec.get('max_length', 200)} ký tự")
        if "\x00" in value or any(ord(c) < 32 and c not in "\t" for c in value):
            raise ValueError(f"Tham số '{name}' chứa ký tự điều khiển")
    if spec.get("enum") and value not in spec["enum"]:
        raise ValueError(f"Tham số '{name}' chỉ nhận: {', '.join(map(str, spec['enum']))}")
    if spec.get("pattern") and ptype == "string" and not re.search(spec["pattern"], value):
        raise ValueError(f"Tham số '{name}' không đúng định dạng")
    if ptype in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"]:
            raise ValueError(f"Tham số '{name}' nhỏ hơn mức tối thiểu {spec['minimum']:g}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValueError(f"Tham số '{name}' lớn hơn mức tối đa {spec['maximum']:g}")
    return value


# ── Phân trang ──────────────────────────────────────────────────────────────

def normalise_pagination(raw: Any) -> Optional[Dict[str, Any]]:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("`pagination` phải là một đối tượng")
    ptype = str(raw.get("type") or "none").lower()
    if ptype not in PAGINATION_TYPES:
        raise ValueError(f"pagination.type '{ptype}' không hợp lệ — chỉ nhận {', '.join(PAGINATION_TYPES)}")
    if ptype == "none":
        return None
    name_re = re.compile(r"^[A-Za-z0-9_\-\.\[\]]{1,40}$")
    out: Dict[str, Any] = {"type": ptype}
    for key, default in (("page_param", "page"), ("size_param", "per_page"), ("offset_param", "offset"),
                         ("cursor_param", "cursor")):
        val = str(raw.get(key) or default)
        if not name_re.match(val):
            raise ValueError(f"pagination.{key} không hợp lệ")
        out[key] = val
    nxt = str(raw.get("next_path") or "")
    if nxt and not _DOTTED_RE.match(nxt):
        raise ValueError("pagination.next_path không hợp lệ (đường dẫn dạng a.b.c)")
    if ptype in ("cursor", "next_url") and not nxt:
        raise ValueError(f"pagination.type='{ptype}' cần `next_path` (khoá trong phản hồi chứa con trỏ / URL trang kế)")
    out["next_path"] = nxt
    out["page_size"] = max(1, min(1000, int(raw.get("page_size") or 100)))
    out["max_pages"] = max(1, min(50, int(raw.get("max_pages") or 10)))
    out["start_page"] = max(0, min(1000, int(raw.get("start_page", 1))))
    out["start_offset"] = max(0, min(10_000_000, int(raw.get("start_offset", 0))))
    return out


# ── Thao tác ────────────────────────────────────────────────────────────────

def _scalar_map(raw: Any, what: str) -> Dict[str, Any]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"`{what}` phải là một đối tượng")
    return {str(k)[:64]: v for k, v in list(raw.items())[:30] if isinstance(v, (str, int, float, bool)) or v is None}


def _walk_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)


def used_placeholders(op: Dict[str, Any]) -> Set[str]:
    found: Set[str] = set(_PLACEHOLDER_RE.findall(op.get("path", "")))
    for s in _walk_strings(op.get("query")):
        found |= set(_PLACEHOLDER_RE.findall(s))
    for s in _walk_strings(op.get("body")):
        found |= set(_PLACEHOLDER_RE.findall(s))
    return found


def normalise_operation(name: str, raw: Any, *, writes: bool) -> Dict[str, Any]:
    """Chuẩn hoá một thao tác REST. `writes=True`: thao tác can thiệp (POST/PUT/PATCH/DELETE, rủi ro >= 3)."""
    if not NAME_RE.match(name):
        raise ValueError(f"Tên thao tác '{name}' không hợp lệ (tối đa 40 ký tự: chữ, số, khoảng trắng, - _ .)")
    if not isinstance(raw, dict):
        raise ValueError(f"Thao tác '{name}' phải là một đối tượng")
    allowed = WRITE_METHODS if writes else READ_METHODS
    method = str(raw.get("method") or ("POST" if writes else "GET")).upper()
    if method not in allowed:
        raise ValueError(f"Thao tác '{name}': method chỉ nhận {', '.join(allowed)}" +
                         ("" if writes else " (đọc dữ liệu — thao tác ghi khai báo ở `actions`)"))
    path = str(raw.get("path") or "").strip()
    if not path.startswith("/") or len(path) > 400 or any(c in path for c in "\r\n\t ") or "//" in path[1:]:
        raise ValueError(f"Thao tác '{name}': path phải bắt đầu bằng '/', không chứa khoảng trắng hoặc '//'")
    if re.search(r"(^|/)\.\.?(/|$)", path):
        raise ValueError(f"Thao tác '{name}': path không được chứa '.' hoặc '..'")
    body_type = str(raw.get("body_type") or "json").lower()
    if body_type not in BODY_TYPES:
        raise ValueError(f"Thao tác '{name}': body_type chỉ nhận {', '.join(BODY_TYPES)}")
    body = raw.get("body")
    if body is not None and not isinstance(body, (dict, list, str)):
        raise ValueError(f"Thao tác '{name}': body phải là đối tượng / mảng / chuỗi")
    if body is not None and len(json.dumps(body, ensure_ascii=False)) > 8000:
        raise ValueError(f"Thao tác '{name}': body quá dài (tối đa 8000 ký tự)")
    rows_path = str(raw.get("rows_path") or "")
    if rows_path and not _DOTTED_RE.match(rows_path):
        raise ValueError(f"Thao tác '{name}': rows_path không hợp lệ (dạng a.b.c)")
    op: Dict[str, Any] = {
        "description": str(raw.get("description") or "")[:200],
        "method": method, "path": path,
        "query": _scalar_map(raw.get("query"), "query"),
        "body": body, "body_type": body_type,
        "rows_path": rows_path,
        "params": normalise_params(raw.get("params")),
    }
    pag = normalise_pagination(raw.get("pagination"))
    if pag:
        op["pagination"] = pag
    if writes:
        try:
            risk = int(raw.get("risk_level", 4))
        except (TypeError, ValueError):
            risk = 4
        op["risk_level"] = max(3, min(5, risk))      # thao tác ghi KHÔNG BAO GIỜ tự chạy
    missing = sorted(used_placeholders(op) - set(op["params"]))
    if missing:
        raise ValueError(f"Thao tác '{name}': {{{', '.join(missing)}}} chưa được khai báo trong `params`")
    return op


def normalise_operations(raw: Any, *, writes: bool, limit: int = 40) -> Dict[str, Dict[str, Any]]:
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("`actions` / `queries` phải là một đối tượng {tên: thao tác}")
    if len(raw) > limit:
        raise ValueError(f"Tối đa {limit} thao tác cho mỗi loại")
    return {str(k).strip(): normalise_operation(str(k).strip(), v, writes=writes) for k, v in raw.items()}


def _subst_str(text: str, args: Dict[str, Any], *, quote_value: bool) -> str:
    def repl(m: "re.Match[str]") -> str:
        name = m.group(1)
        if name not in args:
            return ""
        val = args[name]
        if isinstance(val, bool):
            val = "true" if val else "false"
        return quote(str(val), safe="") if quote_value else str(val)
    return _PLACEHOLDER_RE.sub(repl, text)


def _subst(node: Any, args: Dict[str, Any]) -> Any:
    if isinstance(node, str):
        m = _EXACT_PLACEHOLDER_RE.match(node)
        if m:                                           # "{n}" nguyên chuỗi -> giữ kiểu (số / bool)
            return args.get(m.group(1), "")
        return _subst_str(node, args, quote_value=False)
    if isinstance(node, dict):
        return {_subst_str(str(k), args, quote_value=False): _subst(v, args) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst(v, args) for v in node]
    return node


def render_request(op: Dict[str, Any], args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Thao tác + tham số đã kiểm tra -> {method, path, query, body, body_type}. Ném ValueError nếu tham số sai."""
    clean = coerce_args(op.get("params") or {}, args)
    path_params = set(_PLACEHOLDER_RE.findall(op["path"]))
    for name in path_params:
        if name not in clean:
            raise ValueError(f"Thiếu tham số '{name}' cho đường dẫn")
        val = str(clean[name])
        spec = op["params"].get(name, {})
        if val in (".", "..") or (not spec.get("pattern") and not _PATH_SAFE_DEFAULT.match(val)):
            raise ValueError(f"Tham số '{name}' có ký tự không an toàn cho đường dẫn")
    path = _subst_str(op["path"], clean, quote_value=True)
    query = {k: _subst(v, clean) for k, v in (op.get("query") or {}).items()}
    query = {k: v for k, v in query.items() if v not in ("", None)}
    body = _subst(op["body"], clean) if op.get("body") is not None else None
    return {"method": op["method"], "path": path, "query": query, "body": body, "body_type": op.get("body_type", "json")}


# ── SQL chỉ-đọc ─────────────────────────────────────────────────────────────

def _strip_sql(sql: str) -> str:
    """Bỏ chú thích và nội dung chuỗi ('...') để quét từ khoá không bị lừa."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and sql[j + 1:j + 2] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            out.append("''")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def validate_readonly_sql(sql: str) -> str:
    """Trả câu SQL đã chuẩn hoá; ném ValueError nếu không phải MỘT câu SELECT / WITH chỉ-đọc."""
    text = str(sql or "").strip().rstrip(";").strip()
    if not text or len(text) > 8000:
        raise ValueError("SQL trống hoặc quá dài (tối đa 8000 ký tự)")
    scan = _strip_sql(text)
    if ";" in scan:
        raise ValueError("Chỉ được một câu lệnh SQL (không dùng dấu ';' ở giữa)")
    if not re.match(r"^\s*\(?\s*(select|with)\b", scan, re.IGNORECASE):
        raise ValueError("SQL chỉ được bắt đầu bằng SELECT hoặc WITH")
    bad = _FORBIDDEN_SQL.search(scan)
    if bad:
        raise ValueError(f"SQL chứa từ khoá không được phép trong nguồn chỉ-đọc: {bad.group(1).upper()}")
    return text


def normalise_sql_query(name: str, raw: Any) -> Dict[str, Any]:
    if not NAME_RE.match(name):
        raise ValueError(f"Tên truy vấn '{name}' không hợp lệ")
    if not isinstance(raw, dict):
        raise ValueError(f"Truy vấn '{name}' phải là một đối tượng {{sql, params, description}}")
    sql = validate_readonly_sql(raw.get("sql"))
    params = normalise_params(raw.get("params"))
    used = set(_SQL_PLACEHOLDER_RE.findall(_strip_sql(sql)))
    missing = sorted(used - set(params))
    if missing:
        raise ValueError(f"Truy vấn '{name}': :{', :'.join(missing)} chưa được khai báo trong `params`")
    return {"description": str(raw.get("description") or "")[:200], "sql": sql, "params": params}


def normalise_sql_queries(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Nguồn SQL cần ít nhất một truy vấn đặt tên trong `queries`")
    if len(raw) > 40:
        raise ValueError("Tối đa 40 truy vấn")
    return {str(k).strip(): normalise_sql_query(str(k).strip(), v) for k, v in raw.items()}


def sql_placeholders(sql: str) -> List[str]:
    return _SQL_PLACEHOLDER_RE.findall(_strip_sql(sql))


# ── Tóm tắt cho giao diện / AI (không có bí mật) ────────────────────────────

def summarise(ops: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for name, op in ops.items():
        out.append({
            "name": name, "description": op.get("description", ""),
            "method": op.get("method"), "risk_level": op.get("risk_level"),
            "params": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "required", "default", "enum", "description", "pattern")}
                       for k, v in (op.get("params") or {}).items()},
        })
    return out


def extract_path(payload: Any, dotted: str) -> Tuple[bool, Any]:
    """Lấy giá trị theo đường dẫn `a.b.0.c` ('' = cả phản hồi). (tìm thấy?, giá trị)."""
    if not dotted:
        return True, payload
    node = payload
    for part in re.findall(r"[^.\[\]]+", dotted):
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return False, None
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return False, None
            node = node[part]
        else:
            return False, None
    return True, node
