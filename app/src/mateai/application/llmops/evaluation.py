# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/llmops/evaluation.py
=======================================
Bộ đánh giá chất lượng AI (docs/ai/llmops.md): quản trị viên viết các ca kiểm (câu hỏi + điều kiện đạt), chạy sau mỗi lần đổi
model / prompt / tri thức, so với lần chạy trước để thấy ca nào TỤT (đạt → hỏng).

- Điều kiện là DỮ LIỆU: contains / not_contains / regex / tool_called / tool_not_called / min_chars / max_chars. Không dùng "AI chấm AI".
- Chạy qua ĐÚNG vòng agent thật nhưng CHỈ cho dùng công cụ chỉ đọc (rủi ro L1) và danh tính `ai-eval` (quyền thấp): đánh giá không được
  làm thay đổi hệ thống.
- Mỗi lượt vẫn tính token vào mức dùng + chịu ngân sách; một lần chạy tối đa `llmops.eval_max_cases` ca, tuần tự.
- Kết quả phụ thuộc model thật: bộ kiểm cho biết ĐẠT / KHÔNG ĐẠT theo điều kiện đã viết, không chứng minh câu trả lời đúng ngoài điều kiện đó.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EVAL_PRINCIPAL = "ai-eval"
CHECK_TYPES = ("contains", "not_contains", "regex", "tool_called", "tool_not_called", "min_chars", "max_chars")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RUNNING, COMPLETED, FAILED, INTERRUPTED = "RUNNING", "COMPLETED", "FAILED", "INTERRUPTED"
_tasks: Dict[str, "asyncio.Task[Any]"] = {}


class EvalError(ValueError):
    pass


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_precise() -> str:
    """Thời điểm tạo lần chạy, tới micro giây: hai lần chạy trong cùng một giây vẫn so được thứ tự (compare())."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _audit(actor: str, action: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "AI_EVAL", "SUCCESS", details)
    except Exception:  # noqa: BLE001
        pass


# ── định nghĩa bộ kiểm ──────────────────────────────────────────────────────

def validate_cases(raw: Any) -> List[Dict[str, Any]]:
    from mateai.config.loader import settings
    cap = settings.llmops.eval_max_cases
    if not isinstance(raw, list) or not raw:
        raise EvalError("Cần ít nhất một ca kiểm")
    if len(raw) > cap:
        raise EvalError(f"Tối đa {cap} ca kiểm")
    seen, out = set(), []
    for i, c in enumerate(raw, 1):
        if not isinstance(c, dict):
            raise EvalError(f"Ca {i} phải là đối tượng")
        cid = str(c.get("id") or "")
        if not _ID.match(cid):
            raise EvalError(f"Ca {i}: id chỉ gồm a-z 0-9 _ - (tối đa 64 ký tự)")
        if cid in seen:
            raise EvalError(f"Trùng id ca kiểm «{cid}»")
        seen.add(cid)
        prompt = str(c.get("prompt") or "").strip()
        if not prompt or len(prompt) > 2000:
            raise EvalError(f"Ca «{cid}»: câu hỏi cần 1–2000 ký tự")
        checks = c.get("checks")
        if not isinstance(checks, list) or not checks:
            raise EvalError(f"Ca «{cid}»: cần ít nhất một điều kiện")
        norm = []
        for ck in checks:
            t = str((ck or {}).get("type") or "")
            if t not in CHECK_TYPES:
                raise EvalError(f"Ca «{cid}»: loại điều kiện «{t}» không hợp lệ (dùng: {', '.join(CHECK_TYPES)})")
            v = (ck or {}).get("value")
            if t in ("min_chars", "max_chars"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise EvalError(f"Ca «{cid}»: {t} cần một số nguyên")
            else:
                v = str(v if v is not None else "").strip()
                if not v or len(v) > 300:
                    raise EvalError(f"Ca «{cid}»: giá trị điều kiện {t} cần 1–300 ký tự")
            if t == "regex":
                try:
                    re.compile(v)
                except re.error as exc:
                    raise EvalError(f"Ca «{cid}»: regex sai ({exc})")
            norm.append({"type": t, "value": v})
        out.append({"id": cid, "prompt": prompt, "checks": norm})
    return out


def save_set(set_id: str, name: str, cases: Any, actor: str) -> Dict[str, Any]:
    if not _ID.match(str(set_id or "")):
        raise EvalError("Mã bộ kiểm chỉ gồm a-z 0-9 _ - (tối đa 64 ký tự)")
    if not str(name or "").strip():
        raise EvalError("Cần tên bộ kiểm")
    norm = validate_cases(cases)
    db, now = _db(), _now()
    old = db.dev_get("ai_eval_sets", set_id)
    if old:
        db.dev_update("ai_eval_sets", set_id, {"name": str(name).strip()[:200], "cases_json": json.dumps(norm, ensure_ascii=False), "updated_at": now})
    else:
        db.dev_insert("ai_eval_sets", {"set_id": set_id, "name": str(name).strip()[:200], "cases_json": json.dumps(norm, ensure_ascii=False),
                                       "created_by": actor, "created_at": now, "updated_at": now})
    _audit(actor, "ai_eval_set_save", {"set_id": set_id, "cases": len(norm)})
    return get_set(set_id)  # type: ignore[return-value]


def _set_view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"set_id": row["set_id"], "name": row["name"], "cases": json.loads(row["cases_json"]), "created_by": row.get("created_by"),
            "updated_at": row.get("updated_at")}


def get_set(set_id: str) -> Optional[Dict[str, Any]]:
    row = _db().dev_get("ai_eval_sets", set_id)
    return _set_view(row) if row else None


def list_sets() -> List[Dict[str, Any]]:
    out = []
    for row in _db().dev_list("ai_eval_sets", order_by="updated_at DESC", limit=200):
        v = _set_view(row)
        last = list_runs(v["set_id"], 1)
        v["cases"] = len(v["cases"])
        v["last_run"] = last[0] if last else None
        out.append(v)
    return out


def delete_set(set_id: str, actor: str) -> bool:
    n = _db().dev_delete("ai_eval_sets", set_id)
    if n:
        _audit(actor, "ai_eval_set_delete", {"set_id": set_id})
    return bool(n)


# ── chấm một ca ─────────────────────────────────────────────────────────────

def grade(case: Dict[str, Any], reply: str, tools_called: List[str]) -> List[Dict[str, Any]]:
    text = str(reply or "")
    low = text.casefold()
    called = {t.casefold() for t in tools_called}
    res = []
    for ck in case["checks"]:
        t, v = ck["type"], ck["value"]
        if t == "contains":
            ok = str(v).casefold() in low
        elif t == "not_contains":
            ok = str(v).casefold() not in low
        elif t == "regex":
            ok = re.search(str(v), text, re.IGNORECASE) is not None
        elif t == "tool_called":
            ok = str(v).casefold() in called
        elif t == "tool_not_called":
            ok = str(v).casefold() not in called
        elif t == "min_chars":
            ok = len(text.strip()) >= int(v)
        else:
            ok = len(text) <= int(v)
        res.append({"type": t, "value": v, "ok": bool(ok)})
    return res


Responder = Callable[[str, str], Awaitable[Dict[str, Any]]]


async def _default_responder(prompt: str, session: str) -> Dict[str, Any]:
    """Vòng agent thật, CHỈ công cụ chỉ đọc, danh tính quyền thấp."""
    from core.plugin_manager import plugin_manager
    from mateai.application.agent.llm_engine import llm_engine
    from mateai.application.security.risk_engine import assess_risk
    names = [(t.get("function") or {}).get("name") for t in plugin_manager.get_all_tools()]
    read_only = [n for n in names if n and assess_risk(n) <= 1]
    return await llm_engine.ask_async(prompt, source_device=EVAL_PRINCIPAL, caller=EVAL_PRINCIPAL, session_id=session, tool_names=read_only or ["__none__"])


async def _run_case(case: Dict[str, Any], run_id: str, responder: Responder) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        out = await asyncio.wait_for(responder(case["prompt"], f"eval-{run_id}-{case['id']}"), timeout=180)
    except Exception as exc:  # noqa: BLE001
        return {"id": case["id"], "passed": False, "error": f"{type(exc).__name__}: {exc}"[:300], "checks": [], "tools": [], "tokens": 0,
                "latency_ms": int((time.monotonic() - t0) * 1000), "reply": ""}
    if not out.get("success", True) and out.get("error") in ("rate_limited", "budget_exceeded", "ALL_MODELS_FAILED"):
        return {"id": case["id"], "passed": False, "error": f"Không có câu trả lời ({out.get('error')}): {str(out.get('reply') or '')[:200]}",
                "checks": [], "tools": [], "tokens": 0, "latency_ms": int((time.monotonic() - t0) * 1000), "reply": ""}
    tools = [str(tc.get("skill")) for tc in out.get("tool_calls_made") or [] if tc.get("skill")]
    checks = grade(case, str(out.get("reply") or ""), tools)
    return {"id": case["id"], "passed": all(c["ok"] for c in checks), "error": None, "checks": checks, "tools": tools,
            "tokens": int((out.get("usage") or {}).get("total_tokens") or 0), "model": (out.get("route_info") or {}).get("model"),
            "latency_ms": int((time.monotonic() - t0) * 1000), "reply": str(out.get("reply") or "")[:400]}


# ── chạy bộ kiểm ────────────────────────────────────────────────────────────

def _run_view(row: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    v = {"run_id": row["run_id"], "set_id": row["set_id"], "status": row["status"], "actor": row.get("actor"), "passed": row["passed"],
         "total": row["total"], "pass_rate": round(row["passed"] / row["total"], 4) if row["total"] else None, "total_tokens": row["total_tokens"],
         "models": json.loads(row.get("models") or "[]"), "error": row.get("error"), "created_at": row["created_at"], "finished_at": row.get("finished_at")}
    if full:
        v["results"] = json.loads(row.get("results_json") or "[]")
    return v


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    row = _db().dev_get("ai_eval_runs", run_id)
    if not row:
        return None
    v = _run_view(row, full=True)
    v["comparison"] = compare(v)
    return v


def list_runs(set_id: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    rows = _db().dev_list("ai_eval_runs", {"set_id": set_id} if set_id else None, order_by="created_at DESC", limit=limit)
    return [_run_view(r) for r in rows]


def compare(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """So với lần chạy HOÀN TẤT liền trước của cùng bộ kiểm: ca nào đạt → hỏng (tụt) và ca nào hỏng → đạt."""
    prev = [r for r in _db().dev_list("ai_eval_runs", {"set_id": run["set_id"], "status": COMPLETED}, order_by="created_at DESC", limit=50)
            if r["run_id"] != run["run_id"] and r["created_at"] < run["created_at"]]
    if not prev:
        return None
    p = _run_view(prev[0], full=True)
    before = {r["id"]: r["passed"] for r in p["results"]}
    now = {r["id"]: r["passed"] for r in run.get("results", [])}
    regressed = sorted(i for i, ok in now.items() if not ok and before.get(i) is True)
    fixed = sorted(i for i, ok in now.items() if ok and before.get(i) is False)
    delta = None if p["pass_rate"] is None or run.get("pass_rate") is None else round(run["pass_rate"] - p["pass_rate"], 4)
    return {"previous_run": p["run_id"], "previous_pass_rate": p["pass_rate"], "delta": delta, "regressed": regressed, "fixed": fixed}


async def start(set_id: str, actor: str, responder: Optional[Responder] = None) -> Dict[str, Any]:
    s = get_set(set_id)
    if not s:
        raise EvalError("Không có bộ kiểm này")
    if any(t for t in _tasks.values() if not t.done()):
        raise EvalError("Đang có một lần đánh giá chạy — chờ nó xong")
    from mateai.application.llmops import usage
    blocked = usage.check(EVAL_PRINCIPAL)
    if blocked:
        raise EvalError(blocked)
    run_id = f"EVR-{uuid.uuid4().hex[:10]}"
    _db().dev_insert("ai_eval_runs", {"run_id": run_id, "set_id": set_id, "status": RUNNING, "actor": actor, "models": "[]", "passed": 0,
                                      "total": len(s["cases"]), "total_tokens": 0, "results_json": "[]", "created_at": _now_precise()})
    _audit(actor, "ai_eval_start", {"set_id": set_id, "run_id": run_id, "cases": len(s["cases"])})
    _tasks[run_id] = asyncio.get_running_loop().create_task(_execute(run_id, s, responder or _default_responder))
    return {"run_id": run_id, "status": RUNNING, "total": len(s["cases"])}


async def _execute(run_id: str, s: Dict[str, Any], responder: Responder) -> None:
    db = _db()
    results: List[Dict[str, Any]] = []
    try:
        for case in s["cases"]:
            results.append(await _run_case(case, run_id, responder))
            db.dev_update("ai_eval_runs", run_id, {"results_json": json.dumps(results, ensure_ascii=False),
                                                   "passed": sum(1 for r in results if r["passed"]), "total_tokens": sum(r["tokens"] for r in results)})
        models = sorted({r["model"] for r in results if r.get("model")})
        db.dev_update("ai_eval_runs", run_id, {"status": COMPLETED, "models": json.dumps(models), "finished_at": _now()})
    except Exception as exc:  # noqa: BLE001
        logger.error("[AIEval] Lần chạy %s lỗi: %s", run_id, exc)
        db.dev_update("ai_eval_runs", run_id, {"status": FAILED, "error": f"{type(exc).__name__}: {exc}"[:300], "finished_at": _now()})
    finally:
        _tasks.pop(run_id, None)


async def wait(run_id: str) -> None:
    t = _tasks.get(run_id)
    if t:
        await t


def recover_interrupted() -> int:
    n = 0
    for r in _db().dev_list("ai_eval_runs", {"status": RUNNING}, limit=200):
        if r["run_id"] not in _tasks:
            _db().dev_update("ai_eval_runs", r["run_id"], {"status": INTERRUPTED, "error": "Máy chủ khởi động lại giữa chừng", "finished_at": _now()})
            n += 1
    return n
