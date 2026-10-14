# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Kịch bản tự kích hoạt (application/playbooks/triggers.py):
  - định nghĩa: cron / cảnh báo / webhook được kiểm (tham số phải có thật, mẫu đúng nguồn, cảnh báo cần ít nhất một điều kiện, không
    dùng cảnh báo của chính kịch bản); `triggers_owner` = người lưu;
  - lịch: đúng phút mới chạy, chạy lại cùng phút không chạy hai lần;
  - cảnh báo: chỉ cảnh báo khớp, thời gian nghỉ, thông báo khôi phục và cảnh báo `playbook*` bị bỏ qua, tham số lấy từ cảnh báo;
  - an toàn: chạy bằng danh tính người đặt (human=False) — bước ghi vẫn CHỜ DUYỆT; kill switch / tắt / giới hạn mỗi giờ chặn hết;
    thiếu dữ liệu đầu vào thì không chạy (không chạy với tham số giả);
  - webhook: token riêng (chỉ hiện một lần, sai token 401), đi qua middleware không cần JWT, khoá chống trùng.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_playbooks import READ, _clean, admin, tools  # noqa: E402,F401  (fixture dùng chung)

from mateai.application.playbooks import definition as D  # noqa: E402
from mateai.application.playbooks import engine as E  # noqa: E402
from mateai.application.playbooks import triggers as T  # noqa: E402
from mateai.config.loader import settings  # noqa: E402
from mateai.infrastructure.database.db_manager import db_manager  # noqa: E402

HOST = {"host": {"type": "string", "required": True}}
STEP_P = {"id": "kt", "tool": "get_system_info", "args": {"host": "{{params.host}}"}}
WRITE = {"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    T._last_fire.clear()
    T._recent_runs.clear()
    monkeypatch.setattr(settings.autonomy, "kill_switch", False)
    monkeypatch.setattr(settings.playbook_triggers, "enabled", True)
    monkeypatch.setattr(settings.playbook_triggers, "max_auto_runs_per_hour", 20)
    with db_manager._get_connection() as conn:
        conn.execute("DELETE FROM pb_webhook_tokens;")
        conn.commit()
    yield


def save(admin, pid, steps, triggers, params=None):
    return E.save_playbook({"id": pid, "name": pid, "steps": steps, "params": params or {}, "triggers": triggers}, actor=admin)


async def _settle(results):
    for r in results:
        if r.get("run_id"):
            await E.wait(r["run_id"])


# ── định nghĩa ──────────────────────────────────────────────────────────────

def test_cron_parser():
    spec = T.parse_cron("*/15 8-17 * * 1-5")
    assert T.cron_matches(spec, datetime(2026, 10, 12, 8, 30)) and not T.cron_matches(spec, datetime(2026, 10, 12, 8, 31))
    assert not T.cron_matches(spec, datetime(2026, 10, 11, 9, 0))                         # 11/10/2026 là Chủ nhật
    assert T.cron_matches(T.parse_cron("0 0 * * 7"), datetime(2026, 10, 11, 0, 0))        # 7 = Chủ nhật
    for bad in ("* * * *", "61 * * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *"):
        with pytest.raises(T.TriggerError):
            T.parse_cron(bad)


@pytest.mark.parametrize("trig, why", [
    ({"type": "cron"}, "type"),
    ({"type": "schedule", "cron": "bad"}, "cron"),
    ({"type": "alert", "match": {}}, "ít nhất một điều kiện"),
    ({"type": "alert", "match": {"category_prefix": "playbook"}}, "vòng lặp"),
    ({"type": "alert", "match": {"title_contains": "disk", "severity": "fatal"}}, "severity"),
    ({"type": "alert", "match": {"title_contains": "x"}, "params": {"khong_co": "1"}}, "không có tham số"),
    ({"type": "alert", "match": {"title_contains": "x"}, "params": {"host": "{{payload.host}}"}}, "không dùng được"),
    ({"type": "webhook", "params": {"host": "{{alert.title}}"}}, "không dùng được"),
    ({"type": "schedule", "cron": "0 8 * * *", "params": {"host": "{{alert.title}}"}}, "không dùng được"),
])
def test_bad_triggers_are_rejected(trig, why):
    with pytest.raises(D.PlaybookError, match=why):
        D.validate_definition({"id": "x1", "name": "x", "steps": [STEP_P], "params": HOST, "triggers": [trig]})


def test_owner_is_recorded_and_manual_only_playbooks_have_none(tools, admin):
    pb = save(admin, "co-lich", [READ], [{"type": "schedule", "cron": "0 8 * * *"}])
    assert pb["definition"]["triggers_owner"] == admin and pb["definition"]["triggers"][0]["cron"] == "0 8 * * *"
    plain = E.save_playbook({"id": "tay", "name": "tay", "steps": [READ]}, actor=admin)
    assert plain["definition"]["triggers"] == [] and "triggers_owner" not in plain["definition"]


# ── lịch ────────────────────────────────────────────────────────────────────

async def test_schedule_fires_on_the_minute_once(tools, admin):
    save(admin, "sang", [READ], [{"type": "schedule", "cron": "30 8 * * *"}])
    assert await T.tick(datetime(2026, 10, 12, 8, 29)) == []
    first = await T.tick(datetime(2026, 10, 12, 8, 30))
    await _settle(first)
    assert first[0]["status"] == "running" and len(tools.calls) == 1
    again = await T.tick(datetime(2026, 10, 12, 8, 30, 40))
    assert again[0]["status"] == "duplicate" and len(tools.calls) == 1                       # cùng phút: không chạy hai lần
    run = db_manager.dev_get("pb_runs", first[0]["run_id"])
    assert run["caller"] == admin and run["agent_id"] == E.AGENT_ID and run["idempotency_key"].startswith("cron:sang:0:")


# ── cảnh báo ────────────────────────────────────────────────────────────────

def alert(**kw):
    return {"title": "Ổ đĩa srv-db sắp đầy", "message": "92%", "severity": "critical", "category": "infra:disk", "source": "prometheus", **kw}


async def test_alert_trigger_matches_maps_params_and_cools_down(tools, admin):
    save(admin, "don-dia", [STEP_P], [{"type": "alert", "match": {"category_prefix": "infra:", "severity": ["critical"]},
                                       "params": {"host": "{{alert.source}}"}, "cooldown_min": 30}], params=HOST)
    assert await T.on_alert(alert(severity="warning")) == []
    assert await T.on_alert(alert(category="sentinel:backup")) == []
    assert await T.on_alert(alert(resolved=True)) == []
    out = await T.on_alert(alert())
    await _settle(out)
    assert out[0]["status"] == "running" and tools.calls[0]["args"] == {"host": "prometheus"}
    assert (await T.on_alert(alert()))[0]["reason"] == "đang trong thời gian nghỉ" and len(tools.calls) == 1


async def test_alert_from_a_playbook_never_triggers_one(tools, admin):
    save(admin, "vong", [READ], [{"type": "alert", "match": {"title_contains": "kịch bản"}}])
    assert await T.on_alert(alert(title="kịch bản lỗi", category="playbook:vong")) == []


async def test_dispatcher_feeds_triggers(tools, admin, monkeypatch):
    seen = []
    monkeypatch.setattr(T, "spawn_on_alert", lambda a: seen.append(a))
    from mateai.application.operations import alert_dispatcher
    await alert_dispatcher.dispatch("Máy chủ mất kết nối", "srv-1", severity="critical", category="infra:down", force=True, only="__none__")
    await alert_dispatcher.dispatch("Đã khôi phục", "srv-1", category="infra:down", resolved=True, force=True, only="__none__")
    assert [a["title"] for a in seen] == ["Máy chủ mất kết nối"]


# ── an toàn ─────────────────────────────────────────────────────────────────

async def test_auto_runs_still_need_approval_for_risky_steps(tools, admin, monkeypatch):
    approvals = []
    from mateai.application.security.zero_trust import hitl_manager
    monkeypatch.setattr(hitl_manager, "request_approval", lambda **kw: approvals.append(kw) or {"id": "AP-9"})
    save(admin, "ghi-tep", [WRITE], [{"type": "alert", "match": {"title_contains": "đĩa"}}])
    out = await T.on_alert(alert())
    assert out[0]["status"] == "awaiting_approval" and approvals and tools.calls == []


async def test_kill_switch_disable_and_hourly_cap_block_everything(tools, admin, monkeypatch):
    save(admin, "chan", [READ], [{"type": "alert", "match": {"title_contains": "đĩa"}, "cooldown_min": 1}])
    monkeypatch.setattr(settings.autonomy, "kill_switch", True)
    assert (await T.on_alert(alert()))[0]["reason"] == "kill switch đang bật"
    monkeypatch.setattr(settings.autonomy, "kill_switch", False)
    monkeypatch.setattr(settings.playbook_triggers, "enabled", False)
    assert "enabled = false" in (await T.on_alert(alert()))[0]["reason"]
    monkeypatch.setattr(settings.playbook_triggers, "enabled", True)
    monkeypatch.setattr(settings.playbook_triggers, "max_auto_runs_per_hour", 1)
    first = await T.on_alert(alert())
    await _settle(first)
    T._last_fire.clear()
    assert "mỗi giờ" in (await T.on_alert(alert(category="infra:disk2")))[0]["reason"] and len(tools.calls) == 1
    E.set_enabled("chan", False, "t")
    assert await T.on_alert(alert()) == []


async def test_missing_input_means_no_run(tools, admin):
    save(admin, "can-host", [STEP_P], [{"type": "webhook", "params": {"host": "{{payload.host}}"}}], params=HOST)
    out = await T.on_webhook("can-host", {"khac": 1}, "k1")
    assert out["status"] == "error" and "payload.host" in out["error"] and tools.calls == []


# ── webhook qua HTTP ────────────────────────────────────────────────────────

def test_webhook_token_and_http_trigger(tools, admin):
    save(admin, "tu-itsm", [STEP_P], [{"type": "webhook", "params": {"host": "{{payload.ci.name}}"}}], params=HOST)
    with pytest.raises(T.TriggerError):
        T.issue_webhook_token("khong-co", admin)
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.interfaces.http.routers.playbooks import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": "manager", "username": "m"}
    assert TestClient(app).post("/api/v1/playbooks/tu-itsm/webhook-token").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin", "username": admin}
    tok = TestClient(app).post("/api/v1/playbooks/tu-itsm/webhook-token").json()
    assert "/api/v1/playbooks/tu-itsm/trigger" in tok["usage"] and tok["token"] not in str(db_manager.dev_get("pb_webhook_tokens", "tu-itsm"))

    from mateai.interfaces.http.server import app as real_app
    c = TestClient(real_app)
    url = "/api/v1/playbooks/tu-itsm/trigger"
    assert c.post(url, json={"ci": {"name": "srv-9"}}).status_code == 401                       # qua middleware, route từ chối
    assert c.post(url, json={"ci": {"name": "srv-9"}}, headers={"Authorization": "Bearer sai"}).status_code == 401
    h = {"Authorization": f"Bearer {tok['token']}", "X-Idempotency-Key": "ticket-42"}
    assert c.post(url, content=b"[1,2]", headers=h).status_code == 422
    r = c.post(url, json={"ci": {"name": "srv-9"}}, headers=h).json()
    assert r["status"] == "running"
    assert c.post(url, json={"ci": {"name": "srv-9"}}, headers=h).json()["status"] in ("duplicate", "skipped")
    assert c.post("/api/v1/playbooks/khac/trigger", json={}, headers=h).status_code == 401
