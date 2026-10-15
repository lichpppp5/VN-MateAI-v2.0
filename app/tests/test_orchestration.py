# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Lõi điều phối (docs/integrations/orchestration.md): phiếu giao việc / phiếu báo cáo, đội agent + bảng điểm + định tuyến, thẩm định
độc lập, vòng đời phiếu trên SỔ TÁC VỤ và HITL THẬT. Agent (LLM nội bộ, A2A) và AI thẩm định là giả — không gọi mạng.

Kiểm: agent báo xong chưa phải là xong; rủi ro ≥ 3 chờ duyệt; từ chối / hết hạn không kẹt; thử lại ưu tiên agent khác; hết lượt thì
thất bại; suy luận không đủ cho rủi ro cao; người xác nhận có bằng chứng; giới hạn chạy đồng thời; khởi động lại không chạy lại việc dở.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from mateai.application.orchestration import contracts as C
from mateai.application.orchestration import registry as R
from mateai.application.orchestration import service as S
from mateai.application.orchestration import verify as V
from mateai.application.security.zero_trust import hitl_manager
from mateai.application.tasks import ledger
from mateai.config.loader import settings
from mateai.infrastructure.database.db_manager import db_manager

TOOLS = {"run_local_sql_check", "list_data_sources", "get_service_status", "get_system_info"}


def report(status="success", result="Đã phân tích: chỉ mục thiếu trên bảng orders", **kw):
    return "```json\n" + json.dumps({"status": status, "result": result, **kw}, ensure_ascii=False) + "\n```"


def order(**kw):
    base = {"objective": "Tìm nguyên nhân báo cáo doanh thu chạy chậm", "capability": "dba",
            "deliverables": ["Nguyên nhân", "Đề xuất chỉ mục"], "done_criteria": [{"kind": "contains", "text": "chỉ mục"}], "risk": 2}
    base.update(kw)
    return base


class FakeLLM:
    """Thay llm_engine.ask_async: trả lời theo agent (đọc từ [VAI: …]) — chuỗi câu trả lời cấu hình được."""
    def __init__(self):
        self.calls = []
        self.replies = {}
        self.gate = None

    async def __call__(self, query, *, source_device=None, session_id=None, caller=None, tool_names=None, **_):
        self.calls.append({"query": query, "caller": caller, "tools": tool_names, "session": session_id,
                           "task": ledger.CURRENT_TASK.get()})
        if self.gate is not None:
            await self.gate.wait()
        name = query.split("]")[0].replace("[VAI: ", "")
        seq = self.replies.get(name) or [{"reply": report(), "success": True,
                                          "tool_calls_made": [{"skill": "run_local_sql_check", "result": {"success": True}}]}]
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        return out


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with db_manager._get_connection() as conn:
        for table in ("orch_orders", "orch_executors"):
            conn.execute(f"DELETE FROM {table};")
        conn.commit()
    monkeypatch.setattr(R, "known_tools", lambda: set(TOOLS))
    monkeypatch.setattr(S, "RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr(settings.autonomy, "kill_switch", False)
    S._tasks.clear()
    S._task_exec.clear()
    yield
    for t in list(S._tasks.values()):
        t.cancel()
    S._tasks.clear()


@pytest.fixture
def llm(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    fake = FakeLLM()
    monkeypatch.setattr(llm_engine, "ask_async", fake)
    return fake


@pytest.fixture
def review(monkeypatch):
    """AI thẩm định giả — mặc định đạt."""
    state = SimpleNamespace(result={"passed": True, "missing": [], "reason": "đủ sản phẩm"}, calls=0)

    async def fake(order_, report_):
        state.calls += 1
        return state.result
    monkeypatch.setattr(V, "_ai_review", fake)
    return state


@pytest.fixture
def boss():
    name = f"sep-{uuid.uuid4().hex[:6]}"
    db_manager.create_user({"username": name, "password": "Matkhau#2026", "role": "manager", "full_name": "Trưởng phòng"})
    return name


def local(eid="dba", name="Quản trị CSDL", caps=("dba",), **kw):
    return R.save(eid, {"kind": "local", "name": name, "capabilities": list(caps), "tools": ["run_local_sql_check", "list_data_sources"],
                        "description": "Chẩn đoán CSDL, chỉ đọc", **kw}, "admin")


async def done(order_id, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        o = S.get(order_id)
        if o and o["status"] not in ("PLANNED", "DISPATCHED", "REVIEWING") and order_id not in S._tasks:
            return o
        await asyncio.sleep(0.05)
    raise AssertionError(f"phiếu {order_id} chưa xong: {S.get(order_id)['status']}")


# ── phiếu (thuần) ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("patch,why", [
    ({"objective": "ngắn"}, "objective"), ({"capability": "Không Hợp Lệ!"}, "Năng lực"), ({"deliverables": []}, "deliverables"),
    ({"done_criteria": []}, "done_criteria"), ({"risk": 7}, "risk"), ({"deadline_s": 5}, "deadline_s"),
    ({"done_criteria": [{"kind": "probe", "tool": "get_service_status"}]}, "expect"),
    ({"done_criteria": [{"kind": "doan-mo"}]}, "kind"),
    ({"done_criteria": [{"kind": "probe", "tool": "x y", "expect": {"path": "a"}}]}, "tool"),
])
def test_bad_orders_are_refused_with_a_reason(patch, why):
    with pytest.raises(C.ContractError, match=why):
        C.parse_order(order(**patch))


def test_order_message_carries_done_criteria_fixed_boundaries_and_report_format():
    o = C.parse_order(order(done_criteria=["Có câu lệnh tạo chỉ mục", {"kind": "evidence", "min": 2}], boundaries=["Không sửa dữ liệu"]))
    assert [c["kind"] for c in o.done_criteria] == ["text", "evidence"] and o.done_criteria[1]["min"] == 2
    msg = C.render_message(o, "WO-1")
    assert "WO-1" in msg and "Có câu lệnh tạo chỉ mục" in msg and "Không sửa dữ liệu" in msg
    assert all(b in msg for b in C.FIXED_BOUNDARIES) and '"status": "success | partial | blocked | failed"' in msg


def test_report_parsing_is_strict_and_tolerant():
    r = C.parse_report("Mở đầu\n" + report(evidence=["đã chạy EXPLAIN"], risks=[{"level": "blocking", "text": "thiếu quyền"}]))
    assert r["structured"] and r["status"] == "partial"             # tự báo rủi ro chặn thì không thể là xong trọn
    assert r["evidence"] == [{"summary": "đã chạy EXPLAIN", "ref": None}]
    t = C.parse_report("Em đã làm xong rồi ạ", fallback_status="success")
    assert t == {**t, "status": "success", "result": "Em đã làm xong rồi ạ", "structured": False}
    assert C.parse_report(json.dumps({"status": "bịa", "result": "x"}))["status"] == "partial"
    assert len(C.parse_report({"status": "success", "result": "a" * 20000})["result"]) < 8100


# ── đội agent + định tuyến ──────────────────────────────────────────────────

def test_local_executor_needs_real_narrow_tools_and_versions_every_change():
    with pytest.raises(R.RegistryError, match="1–40 công cụ"):
        R.save("dba", {"kind": "local", "capabilities": ["dba"], "tools": []}, "admin")
    with pytest.raises(R.RegistryError, match="giao việc / sinh mã"):
        R.save("dba", {"kind": "local", "capabilities": ["dba"], "tools": ["dispatch_work_order"]}, "admin")
    with pytest.raises(R.RegistryError, match="Không có công cụ"):
        R.save("dba", {"kind": "local", "capabilities": ["dba"], "tools": ["cong_cu_ma"]}, "admin")
    with pytest.raises(R.RegistryError, match="max_risk"):
        R.save("dba", {"kind": "local", "capabilities": ["dba"], "tools": ["list_data_sources"], "max_risk": 5}, "admin")
    x = local()
    assert x["version"] == 1 and x["tools"] == ["list_data_sources", "run_local_sql_check"]
    assert local(name="DBA mới")["version"] == 2
    with pytest.raises(R.RegistryError, match="Không đổi loại"):
        R.save("dba", {"kind": "a2a", "capabilities": ["dba"]}, "admin")
    with pytest.raises(R.RegistryError, match="Danh bạ agent không có"):
        R.save("ngoai", {"kind": "a2a", "capabilities": ["dba"], "ref": "khong-co"}, "admin")


def test_routing_is_deterministic_by_verified_score_then_load():
    local("dba-a", "DBA A")
    local("dba-b", "DBA B")
    local("sys", "Hệ thống", caps=("sysadmin",))
    now = "2026-10-15 10:00:00"
    for i, (eid, st) in enumerate([("dba-a", "FAILED"), ("dba-b", "COMPLETED"), ("dba-b", "COMPLETED")]):
        db_manager.dev_insert("orch_orders", {"order_id": f"WO-old{i}", "executor_id": eid, "capability": "dba", "status": st, "risk": 2,
                                              "requested_by": "x", "spec_json": "{}", "created_at": S._now(), "updated_at": now})
    o = C.parse_order(order())
    r = R.route(o)
    assert r["executor_id"] == "dba-b" and [c["executor_id"] for c in r["candidates"]] == ["dba-b", "dba-a"]
    assert R.scorecard("dba-b")["score"] == 0.75 and R.scorecard("dba-a")["score"] == pytest.approx(0.333, abs=0.001)
    assert R.route(o, exclude=["dba-b"])["executor_id"] == "dba-a"
    R.set_status("dba-b", "suspended", "admin")
    r = R.route(o)
    assert r["executor_id"] == "dba-a" and {"executor_id": "dba-b", "reason": "đang tạm dừng"} in r["rejected"]
    assert R.route(C.parse_order(order(risk=3)))["executor_id"] is None          # trần rủi ro mặc định 2
    assert "Không có agent nào" in R.route(C.parse_order(order(capability="ba")))["error"]
    assert R.route(C.parse_order(order(executor_id="sys")))["executor_id"] is None   # ép agent không có năng lực


# ── vòng đời phiếu ──────────────────────────────────────────────────────────

async def test_local_role_runs_with_narrow_tools_as_the_requester_and_completes_only_after_verification(llm, review, boss):
    local()
    o = await S.create(order(done_criteria=[{"kind": "contains", "text": "chỉ mục"}, {"kind": "evidence", "min": 1},
                                            "Có đề xuất cụ thể"]), requested_by=boss)
    o = await done(o["order_id"])
    assert o["status"] == "COMPLETED" and o["attempt"] == 1, o
    call = llm.calls[0]
    assert call["caller"] == boss and set(call["tools"]) == {"run_local_sql_check", "list_data_sources"}
    assert call["task"] == o["task_id"] and "PHIẾU GIAO VIỆC" in call["query"] and "[VAI: Quản trị CSDL]" in call["query"]
    assert [c["ok"] for c in o["verification"]["checks"]] == [True, True, True]
    t = ledger.get_task(o["task_id"])
    assert t["kind"] == "work_order" and t["status"] == "COMPLETED" and t["verification_status"] == "passed"
    assert any("[ĐẠT]" in e["summary"] for e in t["evidence"])


async def test_agent_saying_done_is_not_done(llm, review, boss):
    local()
    llm.replies["Quản trị CSDL"] = [{"reply": report(result="Em đã xong việc"), "success": True, "tool_calls_made": []}]
    o = await S.create(order(done_criteria=[{"kind": "contains", "text": "chỉ mục"}, {"kind": "evidence", "min": 1}], max_attempts=1),
                       requested_by=boss)
    o = await done(o["order_id"])
    assert o["status"] == "FAILED" and ledger.get_task(o["task_id"])["status"] == "FAILED"
    assert [c["ok"] for c in o["verification"]["checks"]] == [False, False]
    llm.replies.clear()                                            # câu trả lời đủ hình thức, nhưng thẩm định thấy chưa đạt
    review.result = {"passed": False, "missing": ["đề xuất chỉ mục"], "reason": "chỉ mô tả cách làm"}
    o2 = await done((await S.create(order(max_attempts=1), requested_by=boss))["order_id"])
    assert o2["status"] == "FAILED" and "chỉ mô tả cách làm" in o2["error"]          # AI thẩm định luôn được đánh trượt


async def test_failed_attempt_retries_on_another_executor_then_gives_up(llm, review, boss):
    local("dba-a", "DBA A")
    local("dba-b", "DBA B")
    llm.replies["DBA A"] = [{"reply": report("failed", "không kết nối được CSDL"), "success": True, "tool_calls_made": []}]
    o = await done((await S.create(order(), requested_by=boss))["order_id"])
    assert o["status"] == "COMPLETED" and o["executor_id"] == "dba-b" and o["attempt"] == 2
    assert [h["event"] for h in o["history"]].count("attempt_failed") == 1
    llm.replies["DBA B"] = [{"reply": report("failed", "lỗi"), "success": True, "tool_calls_made": []}]
    o = await done((await S.create(order(), requested_by=boss))["order_id"])
    assert o["status"] == "FAILED" and o["attempt"] == 2 and "Thất bại sau 2 lượt" in ledger.get_task(o["task_id"])["result_summary"]


async def test_inference_alone_never_completes_a_risky_order_and_people_confirm_with_evidence(llm, review, boss, monkeypatch):
    local(max_risk=3)
    o = await S.create(order(risk=3, done_criteria=["Có đề xuất cụ thể"]), requested_by=boss)
    assert o["status"] == "WAITING_APPROVAL" and ledger.get_task(o["task_id"])["status"] == "WAITING_AUTHORIZATION" and not llm.calls
    await hitl_manager.approve_async(o["approval_id"], approved_by="giam-doc")
    o = await done(o["order_id"])
    assert o["status"] == "NEEDS_CONFIRMATION" and o["verification"]["checks"][0]["ok"] is None
    assert ledger.get_task(o["task_id"])["status"] == "ESCALATED"
    with pytest.raises(S.OrderError):
        S.confirm("WO-khong-co", "admin", True, "x")
    o = S.confirm(o["order_id"], "admin", True, "đã chạy thử chỉ mục trên bản sao")
    t = ledger.get_task(o["task_id"])
    assert o["status"] == "COMPLETED" and t["status"] == "COMPLETED"
    assert any(e["source"] == "human:admin" and "bản sao" in e["summary"] for e in t["evidence"])


async def test_low_risk_text_criteria_pass_on_ai_review_but_a_review_outage_needs_a_person(llm, review, boss):
    local()
    o = await done((await S.create(order(done_criteria=["Có đề xuất cụ thể"]), requested_by=boss))["order_id"])
    assert o["status"] == "COMPLETED" and o["verification"]["checks"][0]["kind"] == "INFERENCE"
    review.result = {"passed": None, "missing": [], "reason": "AI thẩm định không chạy được: TimeoutError"}
    o = await done((await S.create(order(done_criteria=["Có đề xuất cụ thể"]), requested_by=boss))["order_id"])
    assert o["status"] == "NEEDS_CONFIRMATION"
    o = S.confirm(o["order_id"], "admin", False, "đề xuất sai bảng")
    assert o["status"] == "FAILED" and ledger.get_task(o["task_id"])["status"] == "FAILED"


async def test_rejected_or_expired_approval_cancels_the_order_and_nothing_runs(llm, review, boss):
    local(max_risk=3)
    o = await S.create(order(risk=3), requested_by=boss)
    hitl_manager.reject(o["approval_id"], rejected_by="giam-doc", reason="chưa cần")
    assert S.reconcile_approvals() == 1
    o = S.get(o["order_id"])
    assert o["status"] == "CANCELLED" and ledger.get_task(o["task_id"])["status"] == "CANCELLED" and not llm.calls
    res = await hitl_manager.approve_async(o["approval_id"], approved_by="giam-doc")
    assert not llm.calls and res["status"] == "error"


async def test_probe_checks_reality_with_a_read_only_tool(llm, review, boss, monkeypatch):
    local()
    calls = []

    async def gate(tool, args, **kw):
        calls.append((tool, kw.get("caller"), kw.get("approved")))
        return {"result": {"success": True, "services": {"MSSQL": "Running"}.get(args.get("name"), "Stopped")}}
    import mateai.application.agent.tool_gate as tg
    monkeypatch.setattr(tg, "run_tool_with_policy", gate)
    probe = {"kind": "probe", "tool": "get_system_info", "args": {"name": "MSSQL"}, "retries": 0,
             "expect": {"path": "result.services", "op": "eq", "value": "Running"}}
    o = await done((await S.create(order(done_criteria=[probe]), requested_by=boss))["order_id"])
    assert o["status"] == "COMPLETED" and calls == [("get_system_info", boss, False)]
    bad = {**probe, "args": {"name": "Khac"}}
    o = await done((await S.create(order(done_criteria=[bad], max_attempts=1), requested_by=boss))["order_id"])
    assert o["status"] == "FAILED" and "không thoả result.services eq Running" in o["error"]
    risky = {**probe, "tool": "check_service_status", "args": {"service_name": "MSSQL"}}   # rủi ro 4 theo bộ đánh giá
    o = await done((await S.create(order(done_criteria=[risky]), requested_by=boss))["order_id"])
    assert o["status"] == "NEEDS_CONFIRMATION" and "không tự chạy được" in o["verification"]["checks"][0]["detail"]


async def test_a2a_agent_reports_are_untrusted_and_questions_block(review, boss, monkeypatch):
    from mateai.application.agent_gateway import directory
    monkeypatch.setattr(directory, "get", lambda ref: {"agent_id": ref, "kind": "a2a", "status": "active", "card": {"url": "https://a"}})
    R.save("ngoai", {"kind": "a2a", "capabilities": ["dba"], "ref": "dba-vendor"}, "admin")
    answers = [{"state": "input-required", "content": "Cần chuỗi kết nối CSDL"}]

    async def call(ref, message, context_id=None):
        return answers.pop(0)
    monkeypatch.setattr(directory, "call_a2a", call)
    o = await done((await S.create(order(), requested_by=boss))["order_id"])
    assert o["status"] == "BLOCKED" and o["report"]["untrusted"] and "chuỗi kết nối" in o["report"]["risks"][0]["text"]
    answers.append({"state": "completed", "content": report(evidence=["đã chạy EXPLAIN"])})
    o = await done((await S.create(order(done_criteria=[{"kind": "evidence", "min": 1}]), requested_by=boss))["order_id"])
    assert o["status"] == "COMPLETED" and o["verification"]["checks"][0]["kind"] == "INFERENCE"   # lời agent ngoài tự khai
    answers.append({"state": "completed", "content": report(evidence=["đã chạy EXPLAIN"])})
    R.save("ngoai", {"max_risk": 3}, "admin")
    o = await S.create(order(risk=3, done_criteria=[{"kind": "evidence", "min": 1}]), requested_by=boss)
    await hitl_manager.approve_async(o["approval_id"], approved_by="giam-doc")
    assert (await done(o["order_id"]))["status"] == "NEEDS_CONFIRMATION"               # rủi ro 3: lời tự khai không đủ


async def test_guards_kill_switch_l5_idempotency_and_open_limits(llm, review, boss, monkeypatch):
    local()
    monkeypatch.setattr(settings.autonomy, "kill_switch", True)
    with pytest.raises(S.OrderError, match="dừng khẩn cấp"):
        await S.create(order(), requested_by=boss)
    monkeypatch.setattr(settings.autonomy, "kill_switch", False)
    with pytest.raises(S.OrderError, match="L5"):
        await S.create(order(risk=5), requested_by=boss)
    with pytest.raises(S.OrderError, match="Không có agent"):
        await S.create(order(capability="ba"), requested_by=boss)
    a = await S.create(order(), requested_by=boss, idempotency_key="k1")
    b = await S.create(order(), requested_by=boss, idempotency_key="k1")
    assert b["duplicate"] is True and b["order_id"] == a["order_id"]
    await done(a["order_id"])
    monkeypatch.setattr(S, "MAX_OPEN_PER_REQUESTER", 0)
    with pytest.raises(S.OrderError, match="chưa xong") as exc:
        await S.create(order(), requested_by=boss)
    assert exc.value.code == "busy"


async def test_concurrency_limit_per_executor_queues_in_the_database(llm, review, boss):
    local(max_concurrent=1)
    llm.gate = asyncio.Event()
    a = await S.create(order(), requested_by=boss)
    b = await S.create(order(), requested_by=boss)
    await asyncio.sleep(0.1)
    assert S.get(a["order_id"])["status"] == "DISPATCHED" and S.get(b["order_id"])["status"] == "PLANNED" and len(llm.calls) == 1
    llm.gate.set()
    assert (await done(a["order_id"]))["status"] == "COMPLETED"
    assert (await done(b["order_id"]))["status"] == "COMPLETED" and len(llm.calls) == 2


async def test_deadline_cancel_and_restart_recovery(llm, review, boss, monkeypatch):
    local()
    monkeypatch.setattr(S, "DEADLINE_GRACE_S", -29.8)            # thời hạn 30 s → cắt sau 0,2 s
    llm.gate = asyncio.Event()
    o = await done((await S.create(order(deadline_s=30, max_attempts=1), requested_by=boss))["order_id"])
    assert o["status"] == "FAILED" and "quá thời hạn" in o["error"]
    monkeypatch.setattr(S, "DEADLINE_GRACE_S", 30.0)
    o = await S.create(order(), requested_by=boss)
    await asyncio.sleep(0.1)
    assert S.cancel(o["order_id"], boss)["status"] == "cancelled"
    await asyncio.sleep(0.05)
    llm.gate.set()
    await asyncio.sleep(0.1)
    o = S.get(o["order_id"])
    assert o["status"] == "CANCELLED" and ledger.get_task(o["task_id"])["status"] == "CANCELLED"
    # khởi động lại: việc đang giao dở → INTERRUPTED; việc chưa giao giữ trong hàng đợi
    for oid, st in (("WO-dang-lam", "DISPATCHED"), ("WO-cho-giao", "PLANNED")):
        db_manager.dev_insert("orch_orders", {"order_id": oid, "executor_id": "dba", "capability": "dba", "status": st, "risk": 2,
                                              "requested_by": boss, "spec_json": json.dumps(order()), "created_at": S._now(),
                                              "updated_at": S._now()})
    monkeypatch.setattr(S, "pump", lambda: 0)
    assert S.recover_interrupted() == 1
    assert S.get("WO-dang-lam")["status"] == "INTERRUPTED" and S.get("WO-cho-giao")["status"] == "PLANNED"


async def test_retry_is_a_new_order_linked_both_ways(llm, review, boss):
    local()
    llm.replies["Quản trị CSDL"] = [{"reply": report("failed", "lỗi"), "success": True, "tool_calls_made": []},
                                    {"reply": report(), "success": True, "tool_calls_made": [{"skill": "x", "result": {"success": True}}]}]
    o = await done((await S.create(order(max_attempts=1), requested_by=boss))["order_id"])
    assert o["status"] == "FAILED"
    with pytest.raises(S.OrderError):
        await S.retry("WO-khong-co", boss)
    n = await S.retry(o["order_id"], boss)
    n = await done(n["order_id"])
    assert n["status"] == "COMPLETED" and any(h["event"] == "retry_of" and h["of"] == o["order_id"] for h in n["history"])
    assert any(h["event"] == "retried_as" for h in S.get(o["order_id"])["history"])
