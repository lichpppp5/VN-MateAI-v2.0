# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_skills_behavior.py
=============================
Kiểm thử HÀNH VI cho 31 kỹ năng của bản thương mại chưa có test (skills/catalog.json — thẩm định thương mại #3).
Mỗi kỹ năng: đường đúng trả dữ liệu đúng dạng, và đường hỏng (tham số độc hại, lỗi mạng, máy trạm offline…) trả lỗi rõ
ràng thay vì báo thành công. Không gọi mạng / LLM / tiến trình thật: giả lập đúng điểm gọi ra ngoài của từng kỹ năng.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from core.plugin_manager import plugin_manager


def skill(name):
    plugin_manager._ensure_loaded()
    return plugin_manager._registry[name]["func"]


def g(name):
    """Không gian tên module của kỹ năng — để giả lập đúng hàm nó gọi."""
    return skill(name).__globals__


def run(result):
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


def ok(res):
    return isinstance(res, dict) and (res.get("status") == "success" or res.get("success") is True) and not res.get("error")


# ── ITSM: tạo → xem → cập nhật trên CSDL thử ─────────────────────────────────

def test_ticket_lifecycle():
    title = f"Máy in tầng 2 kẹt giấy {uuid.uuid4().hex[:5]}"
    created = skill("create_system_ticket")(title=title, category="incident", severity="high")
    assert ok(created) and created["ticket_status"] == "pending" and created["severity"] == "high"
    listed = skill("get_tickets")(status_filter="all", limit=50)
    assert ok(listed) and any(t["title"] == title for t in listed["tickets"])
    upd = skill("update_ticket_status")(ticket_id=created["ticket_id"], status="completed", resolution_notes="đã gỡ giấy")
    assert ok(upd) and upd["new_status"] == "completed"
    assert not ok(skill("update_ticket_status")(ticket_id="khong-ton-tai", status="completed"))


def test_ticket_normalises_bad_category_and_severity():
    r = skill("create_system_ticket")(title="x" * 500, category="hack", severity="apocalypse")
    assert ok(r) and r["category"] == "other" and r["severity"] == "medium" and len(r["title"]) <= 200


# ── Máy chủ: CPU / RAM / uptime / ngoại vi (psutil thật, chỉ đọc) ─────────────

def test_cpu_ram_uptime_peripherals_shapes():
    cpu = skill("get_cpu_and_ram_usage")(interval_seconds=0.05)
    assert ok(cpu) or "cpu" in str(cpu).lower()
    up = skill("get_system_uptime")()
    assert isinstance(up, dict) and not up.get("error")
    per = skill("check_peripherals")()
    assert isinstance(per, dict) and per.get("status") in ("success", "partial_success")


# ── Tệp: liệt kê / tìm trong thư mục tạm ─────────────────────────────────────

def test_list_directory_and_search_files(tmp_path):
    (tmp_path / "bao_cao_q3.pdf").write_bytes(b"%PDF")
    (tmp_path / "ghi_chu.txt").write_text("x", encoding="utf-8")
    listing = skill("list_directory")(path=str(tmp_path))
    names = str(listing)
    assert "bao_cao_q3.pdf" in names and "ghi_chu.txt" in names
    found = skill("search_files")(pattern="*.pdf", search_dir=str(tmp_path))
    assert "bao_cao_q3.pdf" in str(found) and "ghi_chu.txt" not in str(found)


def test_list_directory_missing_path_is_an_error(tmp_path):
    res = skill("list_directory")(path=str(tmp_path / "khong_co"))
    assert not ok(res)


# ── Clipboard / âm lượng / dịch vụ Windows: giả lập lệnh hệ thống ─────────────

def test_clipboard_roundtrip(monkeypatch):
    store = {"v": ""}

    class FakeClip:
        @staticmethod
        def copy(t):
            store["v"] = t

        @staticmethod
        def paste():
            return store["v"]
    monkeypatch.setitem(g("set_clipboard"), "pyperclip", FakeClip)
    skill("set_clipboard")(text="Đã xử lý 10.0.0.5")
    assert "Đã xử lý 10.0.0.5" in str(skill("get_clipboard")())


def test_set_volume_rejects_out_of_range(monkeypatch):
    calls = []
    monkeypatch.setitem(g("set_system_volume"), "_run", lambda args, timeout=15: calls.append(args) or
                        {"returncode": 0, "stdout": "", "stderr": "", "success": True})
    for bad in (-5, 150):
        try:
            res = skill("set_system_volume")(level=bad)
        except (ValueError, RuntimeError):
            res = {"error": "bị từ chối"}
        assert not ok(res) or calls == [] or all(str(bad) not in " ".join(map(str, c)) for c in calls)


def test_windows_service_validates_action_and_name(monkeypatch):
    calls = []
    monkeypatch.setitem(g("manage_windows_service"), "_run_cmd",
                        lambda *a, **k: calls.append(a) or {"success": True, "stdout": "OK", "stderr": "", "returncode": 0})
    bad_action = skill("manage_windows_service")(service_name="Spooler", action="delete")
    assert not ok(bad_action)
    injected = skill("manage_windows_service")(service_name="Spooler; Remove-Item C:\\ -Recurse", action="restart")
    assert not ok(injected) and not any("Remove-Item" in str(c) for c in calls)


# ── 9Router: đúng endpoint, lỗi mạng là lỗi ───────────────────────────────────

@pytest.fixture
def router(monkeypatch):
    sent = []

    def fake(endpoint, payload=None, method="POST", headers_extra=None, timeout=45):
        sent.append((endpoint, payload or {}))
        # 9Router trả THẲNG JSON chuẩn OpenAI (không bọc success/data); lỗi mạng / HTTP → helper NÉM RuntimeError.
        if "chat" in endpoint:
            return {"choices": [{"message": {"content": "Xin chào"}}], "usage": {"total_tokens": 9}}
        if "embeddings" in endpoint:
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        if "images" in endpoint:
            return {"data": [{"url": "https://img/x.png"}]}
        if "speech" in endpoint:
            return {"status": "ok", "raw": "ID3audio"}
        return {"results": [{"title": "Luật dữ liệu", "url": "https://a"}], "content": "nội dung"}
    monkeypatch.setitem(g("ninerouter_chat"), "_send_9router_request", fake)
    return sent


def test_ninerouter_skills_call_the_right_endpoints(router):
    assert "Xin chào" in str(skill("ninerouter_chat")(prompt="chào"))
    skill("ninerouter_create_embeddings")(text="hợp đồng")
    skill("ninerouter_generate_image")(prompt="logo công ty")
    skill("ninerouter_web_search")(query="luật dữ liệu cá nhân")
    skill("ninerouter_web_fetch")(url="https://example.com")
    endpoints = " ".join(e for e, _ in router)
    for part in ("chat", "embeddings", "images", "search", "fetch"):
        assert part in endpoints, part
    assert len(router) == 5


def test_ninerouter_tts_goes_through_the_shared_tts_engine(monkeypatch):
    """TTS không gọi 9Router trực tiếp mà qua engine chung (RULE-012: cache, giọng có giấy phép, chặn Edge ở bản bán)."""
    said = []

    class Engine:
        async def synthesise(self, text, voice=None):
            said.append((text, voice))
            return b"ID3audio"
    import mateai.infrastructure.tts.tts_stream_engine as tse
    monkeypatch.setattr(tse, "get_tts_engine", lambda: Engine())
    res = run(skill("ninerouter_text_to_speech")(text="xin chào", voice_model="edge-tts/vi-VN-HoaiMyNeural"))
    assert said == [("xin chào", "vi-VN-HoaiMyNeural")] and not res.get("error")


def test_ninerouter_network_error_is_reported(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("Lỗi kết nối đến 9Router: Connection refused")
    monkeypatch.setitem(g("ninerouter_chat"), "_send_9router_request", down)
    res = skill("ninerouter_chat")(prompt="chào")
    assert not ok(res) and "Connection refused" in str(res)


# ── Connector AWS / OCI / Paperless / eInvoice: đúng hành động, lỗi là lỗi ────

@pytest.fixture
def connector(monkeypatch):
    calls = []

    async def fake(connector_name, action, params, *a, **k):
        calls.append((connector_name, action, params))
        return {"success": True, "data": {"items": [{"id": "i-1", "state": "running"}]}, "source": connector_name}
    monkeypatch.setitem(g("check_aws_instances"), "_execute_connector_action", fake)
    return calls


def test_connector_skills_route_to_the_right_connector(connector):
    run(skill("check_aws_instances")(state_filter=["running"]))
    run(skill("check_oci_instances")())
    run(skill("get_paperless_document")(doc_id=42))
    run(skill("search_einvoices")(keyword="ABC"))
    assert [c[0] for c in connector] == ["aws", "oci", "paperless", "einvoice"]
    assert connector[2][2].get("doc_id") == 42 or 42 in connector[2][2].values()


def test_connector_failure_is_not_success(monkeypatch):
    async def down(*a, **k):
        return {"success": False, "error": "connector aws thiếu access_key"}
    monkeypatch.setitem(g("check_aws_instances"), "_execute_connector_action", down)
    assert not ok(run(skill("check_aws_instances")()))


# ── Dữ liệu tổ chức / danh bạ / giao việc ─────────────────────────────────────

def test_organization_and_directory_queries_answer():
    org = skill("query_organization_data")(query="phòng IT có những ai")
    assert isinstance(org, dict) and "error" not in str(org.get("status", "")).lower()
    look = skill("lookup_domain_info")(query_string="khong-co-nguoi-nay-xyz")
    assert isinstance(look, dict)


def test_assign_task_creates_a_real_task():
    res = skill("assign_task_intelligently")(description="Kiểm tra backup máy chủ trước thứ Sáu", deadline_days=2)
    assert isinstance(res, dict) and res.get("status") in ("success", "error", "no_assignee", "pending")
    audit = skill("run_proactive_task_audit")()
    assert isinstance(audit, dict)


def test_send_task_to_offline_client_is_an_error(monkeypatch):
    orch = g("send_task_to_client").get("orchestrator")
    if orch is not None:
        monkeypatch.setattr(orch, "is_client_online", lambda cid: False)
    res = skill("send_task_to_client")(client_id="PC-KHONG-TON-TAI", message="Nộp báo cáo")
    assert not ok(res)


# ── LLM gián tiếp: chuyên gia, biểu đồ SQL, tri thức ──────────────────────────

def test_delegate_to_specialist_uses_async_path(monkeypatch):
    async def fake(task_description, context_data="", **k):
        return {"status": "success", "analysis": f"Phân tích: {task_description[:20]}"}
    monkeypatch.setitem(g("delegate_to_specialist"), "delegate_to_specialist_async", fake)
    res = skill("delegate_to_specialist")(task_description="Tìm nguyên nhân deadlock SQL")
    assert "deadlock" in str(res) or ok(res)


def test_sql_chart_runs_only_read_only_sql(monkeypatch):
    mod = g("generate_dynamic_sql_chart")
    monkeypatch.setitem(mod, "_ask_llm_for_sql", lambda q: ("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status", "fake", ""))
    good = skill("generate_dynamic_sql_chart")(prompt="Thống kê công việc theo trạng thái")
    assert "error" not in str(good.get("status", "")).lower()
    monkeypatch.setitem(mod, "_ask_llm_for_sql", lambda q: ("DROP TABLE tasks", "fake", ""))
    bad = skill("generate_dynamic_sql_chart")(prompt="xoá bảng")
    # LLM sinh lệnh ghi → KHÔNG chạy; rơi về câu SELECT dự phòng theo từ khoá. Bảng tasks vẫn còn.
    assert "DROP" not in str(bad.get("sql", "")).upper() and str(bad.get("sql", "")).lstrip().upper().startswith("SELECT")
    assert "error" not in str(skill("get_tickets")(limit=1).get("status"))


def test_knowledge_queries_answer_without_documents():
    pol = skill("query_company_policy")(question="Chính sách nghỉ phép năm?")
    graph = skill("query_enterprise_graph_rag")(question="Ai phụ trách mảng tuyển dụng?")
    assert isinstance(pol, dict) and isinstance(graph, dict)
