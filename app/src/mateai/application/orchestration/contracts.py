# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/orchestration/contracts.py
=============================================
Hợp đồng giữa VN-MateAI và MỌI agent thực thi (docs/integrations/orchestration.md): **phiếu giao việc** và **phiếu báo cáo**.
Thuần (không I/O) — kiểm tra chặt đầu vào; agent nội bộ, agent ngoài (A2A) hay Dev Fleet đều nhận và trả cùng một dạng.

Nguồn ý tưởng (đã tái hiện, không chép): gói giao việc có "định nghĩa xong" của OpenCrew, định dạng báo cáo + mức chặn
`info / warning / blocking` của openclaw-multi-agent-team (docs/architecture/integration-candidates-2026-10-15.md §4).

Tiêu chí "xong" (`done_criteria`) — mỗi tiêu chí một loại, kiểm bởi VN-MateAI, KHÔNG tin lời agent tự báo:
  - `probe`    : chạy MỘT công cụ chỉ đọc (qua cổng chính sách, bằng danh tính người giao) rồi so kết quả — bằng chứng FACT.
  - `evidence` : báo cáo phải kèm ít nhất N bằng chứng (agent nội bộ: các bước công cụ đã chạy thành công).
  - `contains` : sản phẩm nộp phải chứa đoạn chữ này (kiểm hình thức của sản phẩm, không phải sự thật).
  - `text`     : tiêu chí bằng lời — chỉ AI thẩm định chấm được (suy luận); rủi ro ≥ 3 thì cần người xác nhận.
  - `human`    : luôn cần người xác nhận.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,40}$")
EXECUTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
CRITERION_KINDS = ("probe", "evidence", "contains", "text", "human")
EXPECT_OPS = ("eq", "ne", "in", "not_in", "gt", "lt", "gte", "lte", "exists", "contains", "not_exists")
REPORT_STATUSES = ("success", "partial", "blocked", "failed")
RISK_FLAGS = ("info", "warning", "blocking")

MAX_OBJECTIVE, MAX_CONTEXT, MAX_ITEM = 1000, 4000, 300
MAX_DELIVERABLES = MAX_CRITERIA = MAX_BOUNDARIES = 10
DEADLINE_MIN_S, DEADLINE_MAX_S, DEADLINE_DEFAULT_S = 30, 86400, 900
MAX_RESULT_CHARS = 8000

#: Ranh giới luôn kèm mọi phiếu (người giao không bỏ được).
FIXED_BOUNDARIES = (
    "Không làm việc không đảo ngược được (xoá dữ liệu, gửi ra ngoài, giao dịch, phát hành) nếu phiếu không yêu cầu rõ.",
    "Không tự giao tiếp việc này cho agent khác.",
    "Thiếu thông tin hoặc quyền thì dừng và báo `blocked`, không đoán.",
)


class ContractError(ValueError):
    pass


def _text(value: Any, what: str, *, required: bool = False, limit: int = MAX_ITEM, min_len: int = 0) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip() if what != "context" else str(value or "").strip()
    if required and len(s) < max(1, min_len):
        raise ContractError(f"Thiếu `{what}`" + (f" (ít nhất {min_len} ký tự)" if min_len > 1 else ""))
    if len(s) > limit:
        raise ContractError(f"`{what}` dài quá {limit} ký tự")
    return s


def _items(value: Any, what: str, *, limit: int, required: bool = False) -> List[str]:
    if value in (None, ""):
        value = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"`{what}` phải là danh sách")
    out = [_text(v, what) for v in value if str(v or "").strip()]
    if len(out) > limit:
        raise ContractError(f"`{what}` tối đa {limit} mục")
    if required and not out:
        raise ContractError(f"Thiếu `{what}`")
    return out


def normalize_capability(value: Any) -> str:
    cap = str(value or "").strip().lower()
    if not CAPABILITY_RE.match(cap):
        raise ContractError("Năng lực (`capability`) chỉ gồm a-z 0-9 . _ - (2–41 ký tự), vd `dba`, `sysadmin`, `ba`")
    return cap


def _criterion(raw: Any, i: int) -> Dict[str, Any]:
    where = f"done_criteria[{i}]"
    if isinstance(raw, str):
        return {"kind": "text", "text": _text(raw, where, required=True)}
    if not isinstance(raw, dict):
        raise ContractError(f"{where}: phải là chữ hoặc đối tượng")
    kind = str(raw.get("kind") or "text").strip().lower()
    if kind not in CRITERION_KINDS:
        raise ContractError(f"{where}: `kind` phải thuộc {', '.join(CRITERION_KINDS)}")
    if kind in ("text", "human", "contains"):
        return {"kind": kind, "text": _text(raw.get("text"), f"{where}.text", required=True)}
    if kind == "evidence":
        try:
            n = int(raw.get("min", 1))
        except (TypeError, ValueError):
            raise ContractError(f"{where}: `min` phải là số nguyên") from None
        return {"kind": "evidence", "min": max(1, min(20, n)), "text": _text(raw.get("text") or f"ít nhất {n} bằng chứng", where)}
    tool = str(raw.get("tool") or "").strip()
    if not re.match(r"^[A-Za-z0-9_.-]{2,64}$", tool):
        raise ContractError(f"{where}: phép thử cần `tool` (tên công cụ chỉ đọc)")
    args = raw.get("args") or {}
    if not isinstance(args, dict) or len(json.dumps(args, ensure_ascii=False, default=str)) > 4000:
        raise ContractError(f"{where}: `args` phải là đối tượng ≤ 4000 ký tự")
    exp = raw.get("expect")
    if not isinstance(exp, dict) or not str(exp.get("path") or "").strip():
        raise ContractError(f"{where}: phép thử cần `expect` {{path, op, value}} — vd {{\"path\": \"result.status\", \"op\": \"eq\", \"value\": \"Running\"}}")
    op = str(exp.get("op") or "eq")
    if op not in EXPECT_OPS:
        raise ContractError(f"{where}: `expect.op` phải thuộc {', '.join(EXPECT_OPS)}")
    try:
        retries = max(0, min(5, int(raw.get("retries", 2))))
    except (TypeError, ValueError):
        retries = 2
    return {"kind": "probe", "tool": tool, "args": args, "retries": retries,
            "expect": {"path": str(exp["path"]).strip()[:200], "op": op, "value": exp.get("value")},
            "text": _text(raw.get("text") or f"{tool}: {expect_text(exp['path'], op, exp.get('value'))}", where)}


def expect_text(path: str, op: str, value: Any) -> str:
    return f"{path} {op}" if op in ("exists", "not_exists") else f"{path} {op} {value}"


@dataclass
class WorkOrder:
    """Phiếu giao việc — đủ để một agent KHÔNG có ngữ cảnh hội thoại làm đúng ý."""
    objective: str
    capability: str
    deliverables: List[str]
    done_criteria: List[Dict[str, Any]]
    context: str = ""
    boundaries: List[str] = field(default_factory=list)
    risk: int = 2
    deadline_s: int = DEADLINE_DEFAULT_S
    max_attempts: int = 2
    executor_id: Optional[str] = None
    review: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def parse_order(payload: Any) -> WorkOrder:
    if not isinstance(payload, dict):
        raise ContractError("Phiếu giao việc phải là một đối tượng")
    objective = _text(payload.get("objective"), "objective", required=True, limit=MAX_OBJECTIVE, min_len=10)
    capability = normalize_capability(payload.get("capability"))
    deliverables = _items(payload.get("deliverables"), "deliverables", limit=MAX_DELIVERABLES, required=True)
    raw_criteria = payload.get("done_criteria")
    if isinstance(raw_criteria, (str, dict)):
        raw_criteria = [raw_criteria]
    if not isinstance(raw_criteria, (list, tuple)) or not raw_criteria:
        raise ContractError("Thiếu `done_criteria` — thế nào là xong? (ít nhất một tiêu chí)")
    if len(raw_criteria) > MAX_CRITERIA:
        raise ContractError(f"`done_criteria` tối đa {MAX_CRITERIA} mục")
    criteria = [_criterion(c, i) for i, c in enumerate(raw_criteria)]
    try:
        risk = int(payload.get("risk", 2))
        deadline_s = int(payload.get("deadline_s", DEADLINE_DEFAULT_S))
        attempts = int(payload.get("max_attempts", 2))
    except (TypeError, ValueError):
        raise ContractError("`risk`, `deadline_s`, `max_attempts` phải là số nguyên") from None
    if not 1 <= risk <= 5:
        raise ContractError("`risk` phải từ 1 đến 5")
    if not DEADLINE_MIN_S <= deadline_s <= DEADLINE_MAX_S:
        raise ContractError(f"`deadline_s` phải từ {DEADLINE_MIN_S} đến {DEADLINE_MAX_S} giây")
    executor_id = str(payload.get("executor_id") or "").strip().lower() or None
    if executor_id and not EXECUTOR_ID_RE.match(executor_id):
        raise ContractError("`executor_id` không hợp lệ")
    return WorkOrder(objective=objective, capability=capability, deliverables=deliverables, done_criteria=criteria,
                     context=_text(payload.get("context"), "context", limit=MAX_CONTEXT),
                     boundaries=_items(payload.get("boundaries"), "boundaries", limit=MAX_BOUNDARIES),
                     risk=risk, deadline_s=deadline_s, max_attempts=max(1, min(3, attempts)),
                     executor_id=executor_id, review=bool(payload.get("review", True)))


def order_from_dict(d: Dict[str, Any]) -> WorkOrder:
    """Đọc lại phiếu đã lưu (đã kiểm khi tạo) — không kiểm lại để phiếu cũ vẫn đọc được khi luật đổi."""
    return WorkOrder(**{k: d[k] for k in WorkOrder.__dataclass_fields__ if k in d})


REPORT_FORMAT = (
    "Trả lời bằng ĐÚNG MỘT khối JSON (có thể đặt trong ```json ... ```), không thêm chữ ngoài khối:\n"
    '{"status": "success | partial | blocked | failed",\n'
    ' "result": "sản phẩm / kết quả chính, đầy đủ",\n'
    ' "evidence": [{"summary": "đã kiểm / đo / đọc được gì", "ref": "lệnh, đường dẫn, mã phiếu… (nếu có)"}],\n'
    ' "risks": [{"level": "info | warning | blocking", "text": "rủi ro / điều cần người biết"}],\n'
    ' "next_steps": ["việc nên làm tiếp (tối đa 3)"]}\n'
    "`success` chỉ khi mọi sản phẩm đã có; thiếu quyền / thiếu thông tin → `blocked` và nói rõ cần gì."
)


def render_message(order: WorkOrder, order_id: str) -> str:
    """Phiếu giao việc dạng chữ cho agent (agent không thấy hội thoại gốc)."""
    lines = [f"PHIẾU GIAO VIỆC {order_id} (từ VN-MateAI)", "", f"Mục tiêu: {order.objective}"]
    if order.context:
        lines += ["", "Ngữ cảnh:", order.context]
    lines += ["", "Sản phẩm cần nộp:"] + [f"- {d}" for d in order.deliverables]
    lines += ["", "Thế nào là xong (VN-MateAI sẽ tự kiểm, không dựa vào lời báo):"]
    lines += [f"- {c['text']}" for c in order.done_criteria]
    lines += ["", "Ranh giới:"] + [f"- {b}" for b in (*order.boundaries, *FIXED_BOUNDARIES)]
    lines += ["", f"Thời hạn: {order.deadline_s} giây.", "", REPORT_FORMAT]
    return "\n".join(lines)


def _json_candidates(text: str) -> List[str]:
    out = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        out.append(text[start:end + 1])
    return out


def parse_report(raw: Any, *, fallback_status: str = "partial") -> Dict[str, Any]:
    """Chuẩn hoá phiếu báo cáo từ dict hoặc chữ (agent ngoài thường trả chữ). Không đọc được JSON → giữ nguyên chữ làm
    `result`, trạng thái `fallback_status`, và đánh dấu `structured=False` (thẩm định sẽ khắt khe hơn)."""
    data: Optional[Dict[str, Any]] = raw if isinstance(raw, dict) else None
    text = "" if isinstance(raw, dict) else str(raw or "")
    if data is None:
        for cand in _json_candidates(text):
            try:
                obj = json.loads(cand)
            except ValueError:
                continue
            if isinstance(obj, dict) and ("status" in obj or "result" in obj):
                data = obj
                break
    structured = data is not None
    data = data or {"status": fallback_status, "result": text}
    status = str(data.get("status") or fallback_status).strip().lower()
    if status not in REPORT_STATUSES:
        status = fallback_status
    result = data.get("result")
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False, default=str) if result is not None else ""
    if len(result) > MAX_RESULT_CHARS:
        result = result[:MAX_RESULT_CHARS] + "\n… (cắt)"
    evidence = []
    for e in data.get("evidence") or []:
        if isinstance(e, str):
            e = {"summary": e}
        if isinstance(e, dict) and str(e.get("summary") or "").strip():
            evidence.append({"summary": str(e["summary"]).strip()[:300], "ref": str(e.get("ref") or "")[:200] or None})
    risks = []
    for r in data.get("risks") or []:
        if isinstance(r, str):
            r = {"level": "info", "text": r}
        if isinstance(r, dict) and str(r.get("text") or "").strip():
            lvl = str(r.get("level") or "info").lower()
            risks.append({"level": lvl if lvl in RISK_FLAGS else "info", "text": str(r["text"]).strip()[:300]})
    steps = [str(s).strip()[:200] for s in (data.get("next_steps") or []) if str(s).strip()][:3]
    if any(r["level"] == "blocking" for r in risks) and status == "success":
        status = "partial"                                   # tự báo có rủi ro chặn thì không thể là xong trọn
    return {"status": status, "result": result, "evidence": evidence[:20], "risks": risks[:10], "next_steps": steps,
            "structured": structured}
