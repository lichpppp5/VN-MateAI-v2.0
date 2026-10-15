# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Kịch bản vận hành: định nghĩa (thuần) + engine với CHÍNH SÁCH THẬT (policy_engine, HITL, Sổ tác vụ) và công cụ giả.
Kiểm: dry-run không tác dụng; bước bị chặn thì không chạy; một lần duyệt cho cả kế hoạch và snapshot đóng băng; điều kiện, hoàn tác,
bước tuỳ chọn; "xong" cần kiểm chứng; huỷ; gián đoạn; chống chạy trùng.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import pytest

from mateai.application.playbooks import definition as D
from mateai.application.playbooks import engine as E
from mateai.application.security.zero_trust import hitl_manager
from mateai.application.tasks import ledger
from mateai.config.loader import settings
from mateai.infrastructure.database.db_manager import db_manager

# ── định nghĩa (thuần) ──────────────────────────────────────────────────────

BASE = {"id": "khoi-dong-lai", "name": "Khởi động lại dịch vụ", "description": "mẫu",
        "params": {"host": {"type": "string", "required": True, "pattern": r"[a-z0-9.-]+"}, "retries": {"type": "integer", "default": 2, "minimum": 0, "maximum": 5}},
        "steps": [{"id": "kt", "title": "Kiểm tra trước", "tool": "get_system_info", "args": {"target_client_id": "{{params.host}}"}},
                  {"id": "ghi", "title": "Ghi dấu", "tool": "write_file", "args": {"path": "C:/tmp/{{params.host}}.txt", "content": "{{steps.kt.result.summary}}"},
                   "when": {"path": "steps.kt.ok", "op": "eq", "value": True}, "rollback": {"tool": "delete_item", "args": {"path": "C:/tmp/{{params.host}}.txt"}}}],
        "verify": [{"id": "v1", "tool": "get_system_info", "args": {}, "expect": {"path": "result.ok", "op": "eq", "value": True}}]}


def test_valid_definition_is_normalised():
    d = D.validate_definition(BASE, known_tools={"get_system_info", "write_file", "delete_item"})
    assert d["id"] == "khoi-dong-lai" and [s["id"] for s in d["steps"]] == ["kt", "ghi"] and d["steps"][0]["on_failure"] == "stop"
    assert d["steps"][1]["rollback"]["tool"] == "delete_item" and d["verify"][0]["retries"] == 0


@pytest.mark.parametrize("patch,why", [
    ({"id": "Sai Id!"}, "id"), ({"name": ""}, "name"), ({"steps": []}, "ít nhất một bước"), ({"trigger": {"type": "cron"}}, "manual"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "args": {"x": "{{params.khong_co}}"}}]}, "chưa khai báo"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "args": {"x": "{{steps.b.result.y}}"}}, {"id": "b", "tool": "get_system_info"}]}, "SAU bước"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "on_failure": "bo-qua"}]}, "on_failure"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "on_failure": "rollback"}]}, "rollback"),
    ({"steps": [{"id": "a", "tool": "cong-cu-ma"}]}, "không có công cụ"),
    ({"steps": [{"id": "a", "tool": "get_system_info"}, {"id": "a", "tool": "get_system_info"}]}, "trùng"),
    ({"steps": [{"id": f"s{i}", "tool": "get_system_info"} for i in range(25)]}, "Tối đa"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "when": {"path": "x", "op": "gioi-han"}}]}, "điều kiện"),
    ({"verify": [{"tool": "get_system_info"}]}, "expect"),
    ({"steps": [{"id": "a", "tool": "get_system_info", "args": {"x": "{{system.secret}}"}}]}, "không hợp lệ"),
])
def test_bad_definitions_are_refused_with_a_reason(patch, why):
    raw = {**copy.deepcopy(BASE), **patch}
    with pytest.raises(D.PlaybookError) as exc:
        D.validate_definition(raw, known_tools={"get_system_info", "write_file", "delete_item"})
    assert why in str(exc.value), str(exc.value)


def test_templates_keep_types_and_missing_values_fail_loudly():
    ctx = {"params": {"n": 5, "host": "srv"}, "steps": {"a": {"result": {"rows": [1, 2], "ok": True}}}}
    assert D.render("{{params.n}}", ctx) == 5 and D.render("{{steps.a.result.rows}}", ctx) == [1, 2]
    assert D.render("máy {{params.host}} có {{params.n}}", ctx) == "máy srv có 5"
    assert D.render({"x": ["{{params.host}}", 1]}, ctx) == {"x": ["srv", 1]}
    with pytest.raises(D.RenderError):
        D.render("{{steps.a.result.khong_co}}", ctx)
    assert D.render("{{steps.b.result.x}}", ctx, lenient=True) == "‹steps.b.result.x›"


def test_conditions_are_data_not_code():
    ctx = {"steps": {"a": {"result": {"n": 7, "tags": ["x", "y"], "h": "OK"}, "ok": True}}}
    cases = [({"path": "steps.a.result.n", "op": "gt", "value": 5}, True), ({"path": "steps.a.result.n", "op": "lt", "value": 5}, False),
             ({"path": "steps.a.result.h", "op": "in", "value": ["OK", "WARN"]}, True), ({"path": "steps.a.result.tags", "op": "contains", "value": "y"}, True),
             ({"path": "steps.zz.ok", "op": "exists"}, False), ({"path": "steps.zz.ok", "op": "not_exists"}, True),
             ({"path": "steps.a.result.h", "op": "gt", "value": 1}, False), (None, True)]
    for cond, expected in cases:
        assert D.evaluate(cond, ctx) is expected, cond
    assert D.evaluate({"path": "steps.a.result.n", "op": "eq", "value": "__import__('os').system('x')"}, ctx) is False


def test_params_are_typed_defaulted_and_validated():
    d = D.validate_definition(BASE, known_tools=None)
    assert D.coerce_params(d, {"host": "srv-01"}) == {"host": "srv-01", "retries": 2}
    assert D.coerce_params(d, {"host": "srv-01", "retries": "3"})["retries"] == 3
    for bad, why in (({}, "bắt buộc"), ({"host": "SRV 01"}, "định dạng"), ({"host": "a", "retries": 9}, "≤"), ({"host": "a", "x": 1}, "không khai báo"),
                     ({"host": "a", "retries": "abc"}, "integer")):
        with pytest.raises(D.PlaybookError, match=why):
            D.coerce_params(d, bad)


def test_plan_hash_changes_when_the_plan_changes():
    d = D.validate_definition(BASE, known_tools=None)
    h = D.plan_hash(d, {"host": "a"})
    assert h == D.plan_hash(d, {"host": "a"}) and h != D.plan_hash(d, {"host": "b"})
    d2 = copy.deepcopy(d)
    d2["steps"][0]["args"]["x"] = 1
    assert h != D.plan_hash(d2, {"host": "a"})


# ── engine với chính sách thật ──────────────────────────────────────────────

class Tools:
    """Công cụ giả: ghi lại lời gọi, kết quả cấu hình được theo tên công cụ (hoặc hàm)."""
    def __init__(self):
        self.calls = []
        self.results = {}
        self.delay = 0.0

    async def __call__(self, tool, args, *, caller, human, approved):
        self.calls.append({"tool": tool, "args": args, "approved": approved, "caller": caller, "human": human})
        if self.delay:
            await asyncio.sleep(self.delay)
        r = self.results.get(tool, {"success": True, "summary": f"{tool} ok", "ok": True})
        r = r(len(self.calls)) if callable(r) else r
        return r

    def names(self):
        return [c["tool"] for c in self.calls]


@pytest.fixture
def tools(monkeypatch):
    t = Tools()
    monkeypatch.setattr(E, "_run_tool", t)
    monkeypatch.setattr(E, "known_tools", lambda: {"get_system_info", "write_file", "delete_item", "kill_process", "format_drive", "get_infra_status"})
    return t


@pytest.fixture
def admin():
    name = f"pb-{uuid.uuid4().hex[:8]}"
    db_manager.create_user({"username": name, "password": "Matkhau#2026", "role": "admin", "full_name": "Quản trị thử"})
    return name


@pytest.fixture(autouse=True)
def _clean():
    with db_manager._get_connection() as conn:
        for table in ("pb_runs", "pb_playbooks"):
            conn.execute(f"DELETE FROM {table};")
        conn.commit()
    E._cancel.clear()
    yield


def make(steps, verify=None, pid=None, params=None, **extra):
    pid = pid or f"pb-{uuid.uuid4().hex[:6]}"
    return E.save_playbook({"id": pid, "name": f"Kịch bản {pid}", "steps": steps, "verify": verify or [], "params": params or {}, **extra}, actor="tester")


READ = {"id": "kt", "tool": "get_system_info", "args": {"host": "srv"}}
READ_P = {"id": "kt", "tool": "get_system_info", "args": {"host": "{{params.host}}"}}
OK_VERIFY = [{"id": "v", "tool": "get_infra_status", "args": {}, "expect": {"path": "result.summary", "op": "eq", "value": "good"}}]


def test_playbooks_are_stored_versioned_and_listed(tools):
    pb = make([READ], params={"host": {"type": "string", "required": True}}, pid="luu-tru")
    assert pb["version"] == 1 and pb["definition"]["steps"][0]["tool"] == "get_system_info"
    again = E.save_playbook({**pb["definition"], "name": "Tên mới"}, actor="tester")
    assert again["version"] == 2 and again["name"] == "Tên mới"
    assert [p["id"] for p in E.list_playbooks()] == ["luu-tru"]
    assert E.set_enabled("luu-tru", False, "t") and E.get_playbook("luu-tru")["enabled"] is False
    assert E.delete_playbook("luu-tru", "t") and E.get_playbook("luu-tru") is None
    with pytest.raises(D.PlaybookError, match="không có công cụ"):
        E.save_playbook({"id": "xau", "name": "x", "steps": [{"id": "a", "tool": "cong-cu-ma"}]}, actor="t")


def test_dry_run_reports_policy_risk_and_changes_nothing(tools, admin):
    pb = make([READ_P, {"id": "ghi", "tool": "write_file", "args": {"path": "C:/t.txt"}, "rollback": {"tool": "delete_item", "args": {"path": "C:/t.txt"}}}],
              params={"host": {"type": "string", "required": True}}, verify=OK_VERIFY)
    p = E.plan(pb["definition"], {"host": "srv-1"}, caller=admin)
    assert [(r["tool"], r["decision"]) for r in p["steps"]] == [("get_system_info", "allow"), ("write_file", "require_approval")]
    assert p["steps"][0]["args"] == {"host": "srv-1"} and p["steps"][1]["has_rollback"] is True and p["steps"][1]["rollback"]["tool"] == "delete_item"
    assert p["needs_approval"] and p["approval_steps"] == ["ghi"] and p["max_risk"] >= 3 and not p["blocked"] and len(p["plan_hash"]) == 16
    assert tools.calls == [] and E.list_runs() == []                                       # không chạy, không ghi lượt chạy nào
    assert any("Chưa khai báo `verify`" in n for n in E.plan(make([READ])["definition"], {}, caller=admin)["notes"])


def test_dry_run_shows_what_the_policy_would_block(tools, admin, monkeypatch):
    pb = make([READ, {"id": "x", "tool": "format_drive", "args": {"drive": "C:"}}])
    p = E.plan(pb["definition"], {}, caller=admin)
    assert p["blocked"] and p["denied"][0]["tool"] == "format_drive" and "cấm" in p["denied"][0]["reason"]
    # AI + công tắc dừng khẩn cấp: bước ghi bị chặn, bước đọc vẫn được
    monkeypatch.setattr(settings.autonomy, "kill_switch", True)
    w = make([READ, {"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}])
    ai = E.plan(w["definition"], {}, caller=admin, human=False)
    assert ai["blocked"] and ai["denied"][0]["rule"] == "kill_switch" and ai["steps"][0]["decision"] == "allow"


def test_rbac_applies_to_the_person_who_asked(tools):
    viewer = f"pb-v-{uuid.uuid4().hex[:6]}"
    db_manager.create_user({"username": viewer, "password": "Matkhau#2026", "role": "viewer"})
    pb = make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}])
    p = E.plan(pb["definition"], {}, caller=viewer)
    assert p["blocked"] and p["denied"][0]["rule"] == "rbac"


async def test_low_risk_playbook_runs_in_order_with_templates_and_conditions(tools, admin):
    tools.results["get_system_info"] = {"success": True, "summary": "cpu 12%", "n": 7}
    pb = make([READ_P,
               {"id": "ghi", "tool": "get_infra_status", "args": {"note": "{{steps.kt.result.summary}}", "n": "{{steps.kt.result.n}}"},
                "when": {"path": "steps.kt.result.n", "op": "gt", "value": 5}},
               {"id": "bo", "tool": "get_infra_status", "args": {"x": 1}, "when": {"path": "steps.kt.result.n", "op": "gt", "value": 50}}],
              verify=[{"id": "v", "tool": "get_infra_status", "args": {}, "expect": {"path": "result.summary", "op": "eq", "value": "get_infra_status ok"}}],
              params={"host": {"type": "string", "required": True}})
    out = await E.start(pb["id"], {"host": "srv-9"}, caller=admin)
    assert out["status"] == "running"
    run = await E.wait(out["run_id"])
    assert run["status"] == E.SUCCEEDED, run
    assert tools.names() == ["get_system_info", "get_infra_status", "get_infra_status"]             # bước 2 chạy, bước 3 bị bỏ qua, rồi kiểm chứng
    assert tools.calls[0]["args"] == {"host": "srv-9"} and tools.calls[1]["args"] == {"note": "cpu 12%", "n": 7}
    assert all(c["approved"] is False and c["caller"] == admin and c["human"] is True for c in tools.calls)
    assert [s["status"] for s in run["steps"]] == ["ok", "ok", "skipped"] and run["verify"][0]["ok"] is True
    task = ledger.get_task(run["task_id"])
    assert task["kind"] == "playbook" and task["status"] == "COMPLETED" and task["verification_status"] == "passed"


async def test_a_failing_step_stops_the_run_and_later_steps_do_not_run(tools, admin):
    tools.results["write_file"] = {"success": False, "error": "ổ đĩa đầy"}
    pb = make([READ, {"id": "ghi", "tool": "get_infra_status", "args": {}}, READ | {"id": "sau"}])
    tools.results["get_infra_status"] = {"success": False, "error": "Prometheus không trả lời"}
    run = await E.wait((await E.start(pb["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.FAILED and "Prometheus không trả lời" in run["error"] and tools.names() == ["get_system_info", "get_infra_status"]
    assert ledger.get_task(run["task_id"])["status"] == "FAILED"


async def test_optional_steps_may_fail_without_failing_the_run(tools, admin):
    tools.results["kill_process"] = {"success": False, "error": "không có tiến trình"}
    pb = make([{"id": "tuy", "tool": "get_infra_status", "args": {}, "on_failure": "continue"}, READ], verify=OK_VERIFY)
    tools.results["get_infra_status"] = lambda n: {"success": n != 1, "error": "lỗi tạm", "summary": "good"}
    run = await E.wait((await E.start(pb["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.SUCCEEDED and run["steps"][0]["status"] == "failed_optional"


async def test_rollback_undoes_completed_steps_in_reverse_order(tools, admin):
    steps = [{"id": "a", "tool": "get_system_info", "args": {"v": 1}, "rollback": {"tool": "get_infra_status", "args": {"undo": "a"}}},
             {"id": "b", "tool": "get_system_info", "args": {"v": 2}, "rollback": {"tool": "get_infra_status", "args": {"undo": "b"}}},
             {"id": "c", "tool": "kill_process", "args": {"pid": 1}, "on_failure": "rollback"}]
    pb = make(steps)
    tools.results["kill_process"] = {"success": False, "error": "bị từ chối"}
    out = await E.start(pb["id"], {}, caller=admin)
    assert out["status"] == "awaiting_approval"                                                   # kill_process rủi ro cao -> chờ duyệt cả kế hoạch
    res = await hitl_manager.approve_async(out["approval_id"], approved_by="boss")
    assert res.get("executed") is True, res
    run = await E.wait(out["run_id"])
    assert run["status"] == E.FAILED
    assert tools.names() == ["get_system_info", "get_system_info", "kill_process", "get_infra_status", "get_infra_status"]
    assert [c["args"] for c in tools.calls[3:]] == [{"undo": "b"}, {"undo": "a"}]                  # hoàn tác ngược thứ tự
    assert [s["id"] for s in run["steps"]][-2:] == ["rollback:b", "rollback:a"] and all(c["approved"] for c in tools.calls)


async def test_no_verification_means_not_done_until_a_human_confirms(tools, admin):
    pb = make([READ])
    run = await E.wait((await E.start(pb["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.UNVERIFIED
    t = ledger.get_task(run["task_id"])
    assert t["status"] == "VERIFYING" and t["verification_status"] == "unverified"                  # KHÔNG phải COMPLETED
    done = E.confirm(run["run_id"], "boss", "đã kiểm tra tay")
    assert done["status"] == "confirmed" and E.get_run(run["run_id"])["status"] == E.SUCCEEDED
    t = ledger.get_task(run["task_id"])
    assert t["status"] == "COMPLETED" and any("boss" in e["source"] for e in t["evidence"])
    assert E.confirm(run["run_id"], "boss")["status"] == "noop"


async def test_failed_verification_fails_the_run_and_retries_before_giving_up(tools, admin):
    calls = {"n": 0}

    def flaky(_n):
        calls["n"] += 1
        return {"success": True, "summary": "good" if calls["n"] >= 3 else "chưa"}
    tools.results["get_infra_status"] = flaky
    pb = make([READ], verify=[{**OK_VERIFY[0], "retries": 3, "delay_s": 0}])
    run = await E.wait((await E.start(pb["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.SUCCEEDED and calls["n"] == 3
    tools.results["get_infra_status"] = {"success": True, "summary": "xấu"}
    run2 = await E.wait((await E.start(make([READ], verify=OK_VERIFY)["id"], {}, caller=admin))["run_id"])
    assert run2["status"] == E.FAILED and "Kiểm chứng không đạt" in run2["error"]
    assert ledger.get_task(run2["task_id"])["verification_status"] == "failed"


async def test_one_approval_covers_the_whole_plan_and_the_definition_is_frozen(tools, admin):
    pb = make([READ, {"id": "ghi", "tool": "write_file", "args": {"path": "C:/t.txt"}}], verify=OK_VERIFY, pid="dong-bang")
    out = await E.start("dong-bang", {}, caller=admin)
    assert out["status"] == "awaiting_approval" and out["approval_id"] and tools.calls == []
    item = hitl_manager.get_pending(out["approval_id"])
    assert item["kind"] == "playbook" and "write_file" in item["description"] and out["plan"]["plan_hash"] in item["description"]
    assert E.get_run(out["run_id"])["status"] == E.WAITING_APPROVAL and ledger.get_task(E.get_run(out["run_id"])["task_id"])["status"] == "WAITING_AUTHORIZATION"
    # sửa kịch bản SAU khi xin duyệt: thứ được duyệt (bản đóng băng) vẫn là thứ chạy
    evil = copy.deepcopy(pb["definition"])
    evil["steps"].append({"id": "xau", "tool": "kill_process", "args": {"pid": 1}})
    E.save_playbook(evil, actor="ke-xau")
    tools.results["get_infra_status"] = {"success": True, "summary": "good"}
    await hitl_manager.approve_async(out["approval_id"], approved_by="boss")
    run = await E.wait(out["run_id"])
    assert run["status"] == E.SUCCEEDED and "kill_process" not in tools.names() and tools.names() == ["get_system_info", "write_file", "get_infra_status"]
    assert all(c["approved"] is True for c in tools.calls)


async def test_rejected_or_cancelled_approval_never_runs_anything(tools, admin):
    pb = make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}])
    out = await E.start(pb["id"], {}, caller=admin)
    hitl_manager.reject(out["approval_id"], rejected_by="boss", reason="không")
    assert tools.calls == []
    out2 = await E.start(pb["id"], {}, caller=admin)
    assert E.cancel(out2["run_id"], "boss")["status"] == "cancelled" and E.get_run(out2["run_id"])["status"] == E.CANCELLED
    res = await hitl_manager.approve_async(out2["approval_id"], approved_by="boss")             # duyệt muộn một lượt đã huỷ
    assert tools.calls == [] and E.get_run(out2["run_id"])["status"] == E.CANCELLED
    assert ledger.get_task(E.get_run(out2["run_id"])["task_id"])["status"] == "CANCELLED"


async def test_blocked_plans_never_start(tools, admin):
    pb = make([READ, {"id": "x", "tool": "format_drive", "args": {"drive": "C:"}}])
    out = await E.start(pb["id"], {}, caller=admin)
    assert out["status"] == "blocked" and out["plan"]["denied"] and tools.calls == []
    assert E.get_run(out["run_id"])["status"] == E.BLOCKED and ledger.get_task(E.get_run(out["run_id"])["task_id"])["status"] == "BLOCKED"


async def test_a_step_that_needs_its_own_approval_at_runtime_fails_the_run(tools, admin):
    tools.results["get_system_info"] = {"status": "need_confirm", "approval_id": "AP-9", "success": False}     # chính sách đổi giữa lúc chạy
    run = await E.wait((await E.start(make([READ])["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.FAILED and "phê duyệt riêng" in run["error"]


async def test_step_timeout_is_a_failure_with_an_honest_note(tools, admin):
    tools.delay = 1.5
    pb = make([{"id": "cham", "tool": "get_system_info", "args": {}, "timeout_s": 1}])
    run = await E.wait((await E.start(pb["id"], {}, caller=admin))["run_id"])
    assert run["status"] == E.FAILED and "CHƯA biết công cụ đã dừng chưa" in run["error"]


async def test_cancel_stops_after_the_running_step_and_does_not_undo(tools, admin):
    tools.delay = 0.3
    pb = make([READ, READ | {"id": "hai"}, READ | {"id": "ba"}])
    out = await E.start(pb["id"], {}, caller=admin)
    await asyncio.sleep(0.1)
    assert E.cancel(out["run_id"], "boss")["status"] == "cancelling"
    run = await E.wait(out["run_id"])
    assert run["status"] == E.CANCELLED and len(tools.calls) == 1


async def test_duplicate_requests_and_concurrency_are_bounded(tools, admin):
    tools.delay = 0.4
    pb = make([READ])
    a = await E.start(pb["id"], {}, caller=admin, idempotency_key="khoa-1")
    b = await E.start(pb["id"], {}, caller=admin, idempotency_key="khoa-1")
    assert b["status"] == "duplicate" and b["run_id"] == a["run_id"]
    others = [(await E.start(pb["id"], {}, caller=admin))["run_id"] for _ in range(E.MAX_CONCURRENT - 1)]
    with pytest.raises(D.PlaybookError, match="cùng lúc"):
        await E.start(pb["id"], {}, caller=admin)
    for rid in [a["run_id"], *others]:
        await E.wait(rid)


async def test_disabled_and_unknown_playbooks_are_refused(tools, admin):
    pb = make([READ])
    E.set_enabled(pb["id"], False, "t")
    with pytest.raises(D.PlaybookError, match="đang tắt"):
        await E.start(pb["id"], {}, caller=admin)
    with pytest.raises(D.PlaybookError, match="Không có kịch bản"):
        await E.start("khong-co", {}, caller=admin)


async def test_runs_interrupted_by_a_restart_are_marked_not_resumed(tools, admin):
    pb = make([READ])
    out = await E.start(pb["id"], {}, caller=admin)
    await E.wait(out["run_id"])
    run = E.get_run(out["run_id"])
    db_manager.dev_update("pb_runs", run["run_id"], {"status": E.RUNNING, "finished_at": None})
    assert E.recover_interrupted() == 1
    after = E.get_run(run["run_id"])
    assert after["status"] == E.INTERRUPTED and "khởi động lại" in after["error"]
    assert ledger.get_task(run["task_id"])["status"] in ("ESCALATED", "COMPLETED")


async def test_steps_really_go_through_the_policy_gate(monkeypatch, admin):
    """Không giả `_run_tool`: lời gọi đi qua `tool_gate.run_tool_with_policy` thật (RBAC theo người yêu cầu, audit, sổ tác vụ)."""
    from core.plugin_manager import plugin_manager
    seen = []

    async def fake_exec(name, args, *a, **k):
        seen.append((name, dict(args)))
        return {"status": "success", "success": True, "summary": "tốt"}
    monkeypatch.setattr(plugin_manager, "execute_skill", fake_exec)
    monkeypatch.setattr(E, "known_tools", lambda: {"get_system_info"})
    pb = make([{"id": "kt", "tool": "get_system_info", "args": {"host": "{{params.host}}"}}], params={"host": {"type": "string", "required": True}})
    run = await E.wait((await E.start(pb["id"], {"host": "srv-1"}, caller=admin))["run_id"])
    assert seen == [("get_system_info", {"host": "srv-1"})]
    assert run["steps"][0]["status"] == "ok"
    steps = ledger.get_task(run["task_id"])["steps"]
    assert steps and steps[0]["tool"] == "get_system_info" and steps[0]["decision"] == "allow"       # bước nằm trong Sổ tác vụ


async def test_editors_are_recorded_and_cannot_approve_the_run_under_separation_of_duties(tools, admin, monkeypatch):
    from mateai.application.security import zero_trust as zt
    pb = make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}], pid="tach-nhiem-vu")
    pb = E.save_playbook({**pb["definition"], "edited_by": []}, actor="nguoi-sua")   # không xoá được dấu vết bằng định nghĩa gửi lên
    assert pb["definition"]["edited_by"] == ["nguoi-sua", "tester"]
    monkeypatch.setattr(zt, "separation_of_duties", lambda: True)
    out = await E.start("tach-nhiem-vu", {}, caller=admin)
    assert hitl_manager.get_pending(out["approval_id"])["context"]["configured_by"] == ["nguoi-sua", "tester"]
    res = await hitl_manager.approve_async(out["approval_id"], approved_by="nguoi-sua")
    assert res["status"] == "error" and "người cấu hình" in res["message"] and tools.calls == []
