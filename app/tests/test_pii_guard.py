# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Che dữ liệu cá nhân theo quyền (đạo đức AI, điểm 1 — 2026-10-12). Lớp chính ở tool_gate: kết quả công cụ đã che TRƯỚC khi AI
đọc (AI không thể lộ thứ nó chưa thấy, ở mọi kênh); lớp phụ ở đầu ra lượt thoại / lượt chữ. Sổ tác vụ (manager đọc được)
luôn lưu bản đã che. Robot / thiết bị / agent ngoài luôn bản đã che.
"""
from __future__ import annotations

import pytest

import mateai.application.agent.tool_gate as tg
from mateai.application.security import pii_guard as g
from mateai.config.loader import settings

PROFILE = {"name": "Nguyễn Văn A", "phone": "0912345678", "email": "nguyenvana@congty.vn", "cccd": "001090012345",
           "salary": 25000000, "bank_account": "0123456789012", "dob": "1990-05-03", "dept": "Kế toán",
           "ip_address": "10.0.0.5", "note": "Liên hệ 0987 654 321"}


def test_text_patterns_mask_personal_data_but_keep_operational_data():
    text = ("SĐT 0912 345 678, email nguyenvana@congty.vn, CCCD 001090012345, STK 0123456789012, lương 25 triệu. "
            "Máy chủ 192.168.1.10 lúc 2026-10-12 10:30, đơn DH-1024, mã 12345678, CPU 45%.")
    out, n = g.mask_text(text)
    assert n == 5
    for secret in ("0912 345 678", "nguyenvana@", "001090012345", "0123456789012", "25 triệu"):
        assert secret not in out, secret
    assert "0912***678" in out and "n***@congty.vn" in out and "001******345" in out and "****9012" in out
    for keep in ("192.168.1.10", "2026-10-12 10:30", "DH-1024", "12345678", "45%"):
        assert keep in out, keep


def test_structured_fields_masked_by_name():
    out, n = g.mask_obj({"employees": [PROFILE]})
    e = out["employees"][0]
    assert e["phone"] == "0912***678" and e["email"] == "n***@congty.vn" and e["cccd"] == "001******345"
    assert e["salary"] == "***" and e["bank_account"] == "****9012" and e["dob"] == "**/**/1990"
    assert e["name"] == "Nguyễn Văn A" and e["dept"] == "Kế toán" and e["ip_address"] == "10.0.0.5"
    assert e["note"] == "Liên hệ 0987***321" and n == 7
    assert PROFILE["phone"] == "0912345678"                                  # không sửa bản gốc


@pytest.mark.parametrize("key,kind", [("so_dien_thoai", "phone"), ("phoneNumber", "phone"), ("employee_email", "email"),
                                      ("base_salary", "money"), ("dia_chi", "address"), ("ip_address", None),
                                      ("smtp_server", None), ("mac_address", None), ("department", None)])
def test_field_name_detection(key, kind):
    assert g.pii_kind(key) == kind


@pytest.fixture
def principals(monkeypatch):
    table = {}
    from mateai.application.security import security_guard as sg
    monkeypatch.setattr(sg.security_guard, "principal", lambda who: table.get(who, {"role": "viewer"}))
    monkeypatch.setattr(g, "privacy_settings", lambda: dict(g._DEFAULTS))
    return table


def test_who_sees_full_personal_data(principals):
    principals.update({
        "admin": {"role": "admin", "department": None, "clearance": 4},
        "hr_lead": {"role": "operator", "department": "Nhân sự", "clearance": 3},
        "hr_staff": {"role": "operator", "department": "Nhân sự", "clearance": 2},
        "ke_toan": {"role": "manager", "department": "Kế toán", "clearance": 3},
        "device:robot-01": {"role": "admin", "department": None, "clearance": 4},
    })
    assert g.can_view_pii("admin") and g.can_view_pii("hr_lead")
    assert not g.can_view_pii("hr_staff") and not g.can_view_pii("ke_toan") and not g.can_view_pii("viewer_x")
    assert not g.can_view_pii("device:robot-01")                             # robot đọc to trong phòng: luôn che
    assert not g.can_view_pii("agent:crm-bot") and not g.can_view_pii("")


def test_masking_can_be_disabled_by_config(monkeypatch):
    monkeypatch.setattr(g, "privacy_settings", lambda: {**g._DEFAULTS, "mask_pii": False})
    assert g.can_view_pii("anyone")


@pytest.fixture
def gate(monkeypatch, principals):
    import core.plugin_manager as pm
    import mateai.application.skills.plugin_registry as pr
    from mateai.application.tasks import ledger
    stored = []

    async def fake_exec(name, args):
        return {"status": "success", "employee": dict(PROFILE)}
    monkeypatch.setattr(pm.plugin_manager, "execute_skill", fake_exec)
    monkeypatch.setattr(pr.plugin_registry, "get_tool_names", lambda: [])
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    monkeypatch.setattr(ledger, "record_step", lambda *a, **kw: stored.append(kw.get("result")))
    audits = []
    monkeypatch.setattr(tg.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    principals.update({"admin": {"role": "admin", "clearance": 4}})
    return stored, audits


@pytest.mark.parametrize("caller,masked", [("admin", False), ("viewer_x", True), ("device:robot-01", True)])
async def test_tool_results_are_masked_before_the_ai_reads_them(gate, caller, masked):
    stored, audits = gate
    res = (await tg.run_tool_with_policy("get_system_info", {}, caller=caller, source_device="portal"))["result"]
    phone = res["employee"]["phone"]
    assert (phone == "0912***678") is masked and (phone == "0912345678") is (not masked)
    assert stored and stored[-1]["employee"]["phone"] == "0912***678"        # sổ tác vụ (manager đọc) luôn bản đã che
    assert ("pii_masked" in audits[-1][4]) is masked


async def test_voice_and_text_replies_get_a_second_mask(monkeypatch, principals):
    from mateai.application.agent import llm_engine as le

    async def impl(self, query, **kw):
        return {"reply": "Số của anh A là 0912345678", "speech_reply": "0912345678", "success": True}
    monkeypatch.setattr(le.LLMEngine, "_ask_async_impl", impl)
    out = await le.llm_engine.ask_async("số điện thoại anh A", caller="viewer_x")
    assert "0912345678" not in out["reply"] and "0912***678" in out["speech_reply"]
    from pathlib import Path
    vt = Path("src/mateai/application/voice/voice_turn.py").read_text(encoding="utf-8")
    assert "_full_pii = can_view_pii(caller or source_device)" in vt and "sentence = mask_text(sentence)[0]" in vt
