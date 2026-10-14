# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/playbooks/definition.py
==========================================
Định nghĩa KỊCH BẢN vận hành (playbook): kiểm tra khi lưu, ép kiểu tham số, thay mẫu `{{…}}` và điều kiện `when` / `expect`.
Thuần (không I/O) — mọi thứ chạy được mà không cần máy chủ. KHÔNG dùng `eval`: điều kiện là dữ liệu có phép toán cố định.

Mẫu: `{{params.host}}` · `{{steps.kiem_tra.result.summary.health}}`. Chuỗi chỉ gồm MỘT mẫu giữ nguyên kiểu (số, danh sách…);
mẫu nhúng trong chuỗi dài được ghép thành chuỗi. Tham chiếu `params` lạ hoặc bước chưa khai báo / chưa chạy bị từ chối ngay khi lưu.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
MAX_STEPS = 20
MAX_ARGS_BYTES = 8000
ON_FAILURE = ("stop", "continue", "rollback")
OPS = ("eq", "ne", "in", "not_in", "gt", "lt", "gte", "lte", "exists", "contains", "not_exists")
PARAM_TYPES = ("string", "integer", "number", "boolean")


class PlaybookError(ValueError):
    """Định nghĩa / tham số sai — thông báo nói rõ phải sửa gì."""


class RenderError(PlaybookError):
    pass


# ── đường dẫn + mẫu ─────────────────────────────────────────────────────────

def get_path(obj: Any, dotted: str) -> Any:
    """Lấy `a.b.0.c`; thiếu thì ném KeyError."""
    node = obj
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(dotted)
    return node


def _templates_in(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield from TEMPLATE_RE.findall(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _templates_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from _templates_in(v)


def render(value: Any, ctx: Dict[str, Any], *, lenient: bool = False) -> Any:
    """Thay mẫu trong `value`. `lenient=True` (chạy thử): chỗ chưa biết thành «dấu vết» thay vì lỗi."""
    if isinstance(value, str):
        whole = TEMPLATE_RE.fullmatch(value.strip())

        def lookup(path: str) -> Any:
            try:
                return get_path(ctx, path)
            except KeyError:
                if lenient:
                    return f"‹{path}›"
                raise RenderError(f"Mẫu {{{{{path}}}}} chưa có giá trị (bước trước chưa chạy hoặc không có trường này)")
        if whole:
            return lookup(whole.group(1))
        return TEMPLATE_RE.sub(lambda m: str(lookup(m.group(1))), value)
    if isinstance(value, dict):
        return {k: render(v, ctx, lenient=lenient) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx, lenient=lenient) for v in value]
    return value


def evaluate(cond: Optional[Dict[str, Any]], ctx: Dict[str, Any]) -> bool:
    """`{"path": "steps.s1.result.n", "op": "gt", "value": 3}` — thiếu đường dẫn: chỉ `not_exists` đúng, các phép khác sai."""
    if not cond:
        return True
    op = cond.get("op", "eq")
    try:
        actual = get_path(ctx, cond["path"])
    except KeyError:
        return op == "not_exists"
    want = cond.get("value")
    try:
        if op == "exists":
            return True
        if op == "not_exists":
            return False
        if op == "eq":
            return actual == want
        if op == "ne":
            return actual != want
        if op == "in":
            return actual in (want or [])
        if op == "not_in":
            return actual not in (want or [])
        if op == "contains":
            return want in actual
        a, w = float(actual), float(want)
        return {"gt": a > w, "lt": a < w, "gte": a >= w, "lte": a <= w}[op]
    except (TypeError, ValueError):
        return False


# ── kiểm tra định nghĩa ─────────────────────────────────────────────────────

def _cond(raw: Any, where: str) -> Optional[Dict[str, Any]]:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str) or raw.get("op", "eq") not in OPS:
        raise PlaybookError(f"{where}: điều kiện phải là {{path, op, value}} với op thuộc {', '.join(OPS)}")
    return {"path": raw["path"], "op": raw.get("op", "eq"), "value": raw.get("value")}


def _check_refs(value: Any, params: Set[str], earlier: Set[str], where: str) -> None:
    for ref in _templates_in(value):
        head, _, rest = ref.partition(".")
        if head == "params":
            if rest.split(".")[0] not in params:
                raise PlaybookError(f"{where}: dùng tham số «{ref}» chưa khai báo")
        elif head == "steps":
            if rest.split(".")[0] not in earlier:
                raise PlaybookError(f"{where}: tham chiếu «{ref}» tới bước chưa khai báo hoặc đứng SAU bước này")
        else:
            raise PlaybookError(f"{where}: mẫu «{ref}» không hợp lệ (chỉ params.* hoặc steps.*)")


def _args(raw: Any, where: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlaybookError(f"{where}: `args` phải là một đối tượng")
    try:
        blob = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        raise PlaybookError(f"{where}: `args` phải là JSON thuần")
    if len(blob) > MAX_ARGS_BYTES:
        raise PlaybookError(f"{where}: `args` quá lớn (tối đa {MAX_ARGS_BYTES} ký tự)")
    return copy.deepcopy(raw)


def validate_definition(raw: Any, known_tools: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Chuẩn hoá một định nghĩa; ném PlaybookError nói rõ chỗ sai."""
    if not isinstance(raw, dict):
        raise PlaybookError("Kịch bản phải là một đối tượng JSON")
    pid = str(raw.get("id") or "").strip().lower()
    if not ID_RE.match(pid):
        raise PlaybookError("`id` chỉ gồm chữ thường, số, gạch ngang / dưới (2–49 ký tự), vd khoi-dong-lai-iis")
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > 120:
        raise PlaybookError("Thiếu `name` (tối đa 120 ký tự)")
    trig = raw.get("trigger") or {"type": "manual"}
    if not isinstance(trig, dict) or trig.get("type", "manual") != "manual":
        raise PlaybookError("`trigger` chỉ nhận {\"type\": \"manual\"} — tự kích hoạt (lịch / cảnh báo / webhook) khai trong `triggers`")

    params: Dict[str, Dict[str, Any]] = {}
    for pname, spec in (raw.get("params") or {}).items():
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", str(pname)):
            raise PlaybookError(f"Tên tham số «{pname}» không hợp lệ (chữ thường, số, gạch dưới)")
        spec = spec if isinstance(spec, dict) else {}
        ptype = spec.get("type", "string")
        if ptype not in PARAM_TYPES:
            raise PlaybookError(f"Tham số «{pname}»: type phải thuộc {', '.join(PARAM_TYPES)}")
        pat = spec.get("pattern")
        if pat:
            try:
                re.compile(str(pat))
            except re.error:
                raise PlaybookError(f"Tham số «{pname}»: pattern không hợp lệ")
        params[pname] = {k: spec[k] for k in ("type", "required", "default", "enum", "description", "pattern", "minimum", "maximum") if k in spec}
        params[pname]["type"] = ptype

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlaybookError("Cần ít nhất một bước trong `steps`")
    if len(raw_steps) > MAX_STEPS:
        raise PlaybookError(f"Tối đa {MAX_STEPS} bước")
    steps: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for i, st in enumerate(raw_steps, 1):
        where = f"Bước {i}"
        if not isinstance(st, dict):
            raise PlaybookError(f"{where}: phải là một đối tượng")
        sid = str(st.get("id") or f"b{i}").strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", sid) or sid in seen:
            raise PlaybookError(f"{where}: `id` «{sid}» không hợp lệ hoặc bị trùng")
        tool = str(st.get("tool") or "").strip()
        if not tool:
            raise PlaybookError(f"{where} («{sid}»): thiếu `tool`")
        if known_tools is not None and tool not in known_tools:
            raise PlaybookError(f"{where} («{sid}»): không có công cụ «{tool}» trong hệ thống")
        args = _args(st.get("args"), f"{where} («{sid}»)")
        on_failure = st.get("on_failure", "stop")
        if on_failure not in ON_FAILURE:
            raise PlaybookError(f"{where} («{sid}»): on_failure phải thuộc {', '.join(ON_FAILURE)}")
        try:
            timeout = max(1, min(int(st.get("timeout_s", 120)), 600))
        except (TypeError, ValueError):
            raise PlaybookError(f"{where} («{sid}»): timeout_s phải là số")
        when = _cond(st.get("when"), f"{where} («{sid}») when")
        rollback = st.get("rollback")
        if rollback is not None:
            if not isinstance(rollback, dict) or not str(rollback.get("tool") or "").strip():
                raise PlaybookError(f"{where} («{sid}»): rollback cần {{tool, args}}")
            if known_tools is not None and rollback["tool"] not in known_tools:
                raise PlaybookError(f"{where} («{sid}»): rollback dùng công cụ «{rollback['tool']}» không có trong hệ thống")
            rollback = {"tool": str(rollback["tool"]).strip(), "args": _args(rollback.get("args"), f"{where} («{sid}») rollback")}
        if on_failure == "rollback" and not any(s.get("rollback") for s in steps) and rollback is None:
            raise PlaybookError(f"{where} («{sid}»): on_failure=rollback nhưng chưa bước nào (kể cả bước này) có `rollback`")
        _check_refs(args, set(params), set(seen), f"{where} («{sid}»)")
        _check_refs(rollback["args"] if rollback else {}, set(params), set(seen), f"{where} («{sid}») rollback")
        if when:
            _check_refs({"p": "{{" + when["path"] + "}}"}, set(params), set(seen), f"{where} («{sid}») when")
        seen.add(sid)
        steps.append({"id": sid, "title": str(st.get("title") or sid)[:160], "tool": tool, "args": args, "on_failure": on_failure,
                      "timeout_s": timeout, "when": when, "rollback": rollback})

    verify: List[Dict[str, Any]] = []
    for i, v in enumerate(raw.get("verify") or [], 1):
        where = f"Kiểm chứng {i}"
        if not isinstance(v, dict) or not str(v.get("tool") or "").strip():
            raise PlaybookError(f"{where}: cần {{tool, args, expect}}")
        tool = str(v["tool"]).strip()
        if known_tools is not None and tool not in known_tools:
            raise PlaybookError(f"{where}: không có công cụ «{tool}»")
        expect = _cond(v.get("expect"), f"{where} expect")
        if not expect:
            raise PlaybookError(f"{where}: thiếu `expect` — kiểm chứng phải nói rõ kết quả mong đợi")
        args = _args(v.get("args"), where)
        _check_refs(args, set(params), set(seen), where)
        try:
            retries, delay = max(0, min(int(v.get("retries", 0)), 10)), max(0, min(float(v.get("delay_s", 5)), 60))
        except (TypeError, ValueError):
            raise PlaybookError(f"{where}: retries / delay_s phải là số")
        verify.append({"id": str(v.get("id") or f"v{i}"), "tool": tool, "args": args, "expect": expect, "retries": retries, "delay_s": delay})

    from mateai.application.playbooks import triggers as _triggers
    try:
        auto = _triggers.validate(raw.get("triggers"), params)
    except _triggers.TriggerError as exc:
        raise PlaybookError(str(exc)) from exc
    return {"id": pid, "name": name, "description": str(raw.get("description") or "")[:1000], "params": params,
            "trigger": {"type": "manual"}, "triggers": auto, "steps": steps, "verify": verify}


def coerce_params(defn: Dict[str, Any], supplied: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    supplied = dict(supplied or {})
    out: Dict[str, Any] = {}
    unknown = sorted(set(supplied) - set(defn["params"]))
    if unknown:
        raise PlaybookError(f"Tham số không khai báo: {', '.join(unknown)}")
    for name, spec in defn["params"].items():
        if name not in supplied or supplied[name] in (None, ""):
            if "default" in spec:
                out[name] = spec["default"]
                continue
            if spec.get("required"):
                raise PlaybookError(f"Thiếu tham số bắt buộc «{name}»")
            continue
        val = supplied[name]
        try:
            if spec["type"] == "integer":
                if isinstance(val, bool) or (isinstance(val, float) and val != int(val)):
                    raise ValueError
                val = int(val)
            elif spec["type"] == "number":
                if isinstance(val, bool):
                    raise ValueError
                val = float(val)
            elif spec["type"] == "boolean":
                if isinstance(val, str):
                    if val.strip().lower() not in ("true", "false", "1", "0", "có", "không"):
                        raise ValueError
                    val = val.strip().lower() in ("true", "1", "có")
                else:
                    val = bool(val)
            else:
                val = str(val)
        except (TypeError, ValueError):
            raise PlaybookError(f"Tham số «{name}» phải là {spec['type']}")
        if "enum" in spec and val not in spec["enum"]:
            raise PlaybookError(f"Tham số «{name}» phải thuộc {spec['enum']}")
        if spec["type"] in ("integer", "number"):
            if "minimum" in spec and val < spec["minimum"]:
                raise PlaybookError(f"Tham số «{name}» phải ≥ {spec['minimum']}")
            if "maximum" in spec and val > spec["maximum"]:
                raise PlaybookError(f"Tham số «{name}» phải ≤ {spec['maximum']}")
        if spec["type"] == "string":
            if len(val) > 500:
                raise PlaybookError(f"Tham số «{name}» quá dài (tối đa 500 ký tự)")
            if spec.get("pattern") and not re.fullmatch(str(spec["pattern"]), val):
                raise PlaybookError(f"Tham số «{name}» không đúng định dạng {spec['pattern']}")
        out[name] = val
    return out


def plan_hash(defn: Dict[str, Any], params: Dict[str, Any]) -> str:
    blob = json.dumps({"steps": defn["steps"], "verify": defn["verify"], "params": params}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
