# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_alert_dispatcher.py
==============================
Khâu cảnh báo chung (application/operations/alert_dispatcher) + kênh gửi
(infrastructure/notifications): Telegram, Teams, Email SMTP, Outlook (M365),
Slack, Webhook. Yêu cầu 2026-10-05: "các cấu hình chờ kết nối, sẵn sàng kết nối
vào phần mềm doanh nghiệp".

Kiểm:
  - kênh thiếu khoá = "chờ kết nối": không gửi, không tính lỗi, không báo "đã gửi";
  - kết quả từng kênh là kết quả THẬT (2xx / SMTP chấp nhận), một kênh hỏng
    không chặn kênh khác; lọc mức, chống lặp, "đã khôi phục" chỉ khi đã báo;
  - sơ đồ hệ thống: Lỗi kéo dài -> báo một lần, hồi phục -> báo khôi phục;
  - định dạng Teams (Adaptive Card), chữ ký HMAC webhook, SMTP;
  - URL webhook bị che khi đọc cấu hình; API theo vai trò.
Không gọi mạng.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.operations import alert_dispatcher as ad
from mateai.application.operations import topology_events
from mateai.infrastructure.notifications import channels as ch


@pytest.fixture
def cfg(monkeypatch):
    """config.json giả (không đụng file thật) + xoá biến môi trường kênh."""
    data = {}
    import mateai.config.loader as loader
    monkeypatch.setattr(loader, "get_config_section", lambda name: dict(data.get(name, {})))
    import os
    for k in list(os.environ):
        if k.startswith(("ALERT_", "ALERT_RULES_")):
            monkeypatch.delenv(k)
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
    monkeypatch.setattr(telegram_gateway, "_outbound_config", lambda: None)
    ad.reset_for_tests()
    topology_events.clear()
    yield data
    ad.reset_for_tests()


@pytest.fixture
def sent(monkeypatch):
    """Thay tầng gửi của từng kênh; ghi lại cảnh báo đã tới kênh nào."""
    calls = []
    outcome = {}

    async def fake_send(cid, settings, alert):
        calls.append((cid, alert))
        return outcome.get(cid, (True, "HTTP 200"))

    monkeypatch.setattr(ch, "send", fake_send)
    return calls, outcome


def test_unconfigured_channels_are_waiting_not_failed(cfg):
    st = {c["id"]: c for c in ad.channel_status()}
    for cid in ("alert_teams", "alert_email", "alert_outlook", "alert_slack", "alert_webhook"):
        assert st[cid]["configured"] is False and st[cid]["missing_fields"]
    assert st["alert_email"]["missing_fields"] == ["smtp_host", "from_address", "to_addresses"]


async def test_no_channel_connected_is_reported_honestly(cfg, sent):
    res = await ad.dispatch("CPU 99%", "máy chủ quá tải", severity="critical")
    assert res["status"] == "no_channel" and res["delivered"] == 0 and sent[0] == []


async def test_env_var_connects_a_channel_without_touching_config(cfg, sent, monkeypatch):
    monkeypatch.setenv("ALERT_TEAMS_WEBHOOK_URL", "https://prod.example/teams-hook")
    res = await ad.dispatch("CPU 99%", severity="critical")
    assert [r["channel"] for r in res["results"]] == ["alert_teams"] and res["delivered"] == 1


async def test_every_connected_channel_gets_it_and_one_failure_does_not_block(cfg, sent):
    calls, outcome = sent
    cfg["alert_teams"] = {"webhook_url": "https://t"}
    cfg["alert_slack"] = {"webhook_url": "https://s"}
    cfg["alert_email"] = {"smtp_host": "smtp.x", "from_address": "a@x", "to_addresses": "b@x"}
    outcome["alert_slack"] = (False, "bị từ chối HTTP 403: invalid_token")
    res = await ad.dispatch("LLM không phản hồi", "9Router timeout", severity="critical", category="c1")
    by = {r["channel"]: r for r in res["results"]}
    assert by["alert_teams"]["status"] == "ok" and by["alert_email"]["status"] == "ok"
    assert by["alert_slack"]["status"] == "error" and "403" in by["alert_slack"]["detail"]
    assert res["delivered"] == 2
    state = {c["id"]: c for c in ad.channel_status()}
    assert state["alert_slack"]["failed"] == 1 and state["alert_teams"]["sent"] == 1
    evs = [e for e in topology_events.recent() if e["kind"] == "alert" and e.get("stage") == "notify"]
    assert {e["target"] for e in evs} == {"notify:teams", "notify:slack", "notify:email"}


async def test_disabled_channel_and_severity_floor(cfg, sent):
    calls, _ = sent
    cfg["alert_teams"] = {"webhook_url": "https://t", "enabled": False}
    cfg["alert_slack"] = {"webhook_url": "https://s", "min_severity": "critical"}
    await ad.dispatch("RAM 86%", severity="warning", category="w")
    assert calls == []                                   # Teams tắt, Slack chỉ nhận critical
    assert (await ad.dispatch("thông tin", severity="info", category="i"))["status"] == "skipped"


async def test_cooldown_and_resolution_only_after_an_alert(cfg, sent):
    calls, _ = sent
    cfg["alert_teams"] = {"webhook_url": "https://t"}
    assert (await ad.dispatch("DB khôi phục", category="db", resolved=True))["status"] == "skipped"
    await ad.dispatch("DB lỗi", severity="critical", category="db")
    assert (await ad.dispatch("DB lỗi", severity="critical", category="db"))["status"] == "skipped"
    res = await ad.dispatch("DB đã hoạt động lại", category="db", resolved=True)
    assert res["delivered"] == 1 and calls[-1][1]["resolved"] is True
    assert calls[-1][1]["severity"] == "critical"         # khôi phục mang mức của sự cố gốc


async def test_topology_down_long_enough_alerts_once_then_recovers(cfg, sent, monkeypatch):
    calls, _ = sent
    cfg["alert_teams"] = {"webhook_url": "https://t"}
    cfg["alert_rules"] = {"down_after_s": 30}
    monkeypatch.setattr(ad, "_sentinel_running", lambda: False)
    down = {"nodes": [{"id": "tts", "label": "TTS", "status": "down", "detail": "9Router TTS lỗi"}]}
    up = {"nodes": [{"id": "tts", "label": "TTS", "status": "ok", "detail": ""}]}
    assert await ad.evaluate_topology(down, now=1000) == []          # chớp nhoáng: chưa báo
    assert len(await ad.evaluate_topology(down, now=1031)) == 1
    assert await ad.evaluate_topology(down, now=1100) == []          # không báo lặp
    await ad.evaluate_topology(up, now=1200)
    assert [c[1]["resolved"] for c in calls] == [False, True]
    assert "TTS" in calls[0][1]["title"] and calls[0][1]["severity"] == "critical"


async def test_topology_skips_ignored_and_sentinel_covered(cfg, sent, monkeypatch):
    calls, _ = sent
    cfg["alert_teams"] = {"webhook_url": "https://t"}
    cfg["alert_rules"] = {"down_after_s": 0}
    monkeypatch.setattr(ad, "_sentinel_running", lambda: True)
    snap = {"nodes": [{"id": "voice", "label": "Lõi", "status": "down"},
                      {"id": "llm", "label": "LLM", "status": "down"}]}
    await ad.evaluate_topology(snap, now=1)
    await ad.evaluate_topology(snap, now=2)
    assert calls == []        # voice: bỏ qua mặc định; llm: Sentinel đang canh


def test_teams_payload_is_an_adaptive_card():
    a = {"title": "CPU 99%", "message": "quá tải", "severity": "critical", "source": "Sentinel", "time": 0}
    p = ch._teams_payload(a)
    att = p["attachments"][0]
    assert p["type"] == "message" and att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["content"]["body"][0]["color"] == "Attention" and "CPU 99%" in att["content"]["body"][0]["text"]


async def test_custom_webhook_is_signed(monkeypatch):
    seen = {}

    async def post(url, payload, headers=None, body=None):
        seen.update(url=url, headers=headers, body=body)
        return True, "HTTP 202"

    monkeypatch.setattr(ch, "_post_json", post)
    alert = {"title": "t", "message": "m", "severity": "warning", "category": "c", "source": "s",
             "resolved": False, "time": 0}
    ok, _ = await ch._send_webhook({"webhook_url": "https://itsm", "hmac_secret": "k"}, alert)
    expect = hmac.new(b"k", seen["body"], hashlib.sha256).hexdigest()
    assert ok and seen["headers"]["X-VNMate-Signature"] == f"sha256={expect}"
    assert json.loads(seen["body"])["severity"] == "warning"


def test_smtp_reports_auth_failure_and_success(monkeypatch):
    import smtplib

    class FakeSMTP:
        login_ok = True
        sent = []

        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def has_extn(self, n): return True
        def starttls(self, **k): pass

        def login(self, u, p):
            if not FakeSMTP.login_ok:
                raise smtplib.SMTPAuthenticationError(535, b"bad")

        def send_message(self, msg):
            FakeSMTP.sent.append(msg)
            return {}

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    s = {"smtp_host": "smtp.x", "smtp_port": 587, "username": "u", "password": "p",
         "from_address": "a@x", "to_addresses": "b@x; c@x", "use_ssl": False}
    alert = {"title": "CPU", "message": "cao", "severity": "critical", "source": "s", "time": 0}
    ok, detail = ch._smtp_send_blocking(s, alert)
    assert ok and "2 người nhận" in detail and FakeSMTP.sent[0]["To"] == "b@x, c@x"
    assert "NGHIÊM TRỌNG" in FakeSMTP.sent[0]["Subject"]
    FakeSMTP.login_ok = False
    assert ch._smtp_send_blocking(s, alert) == (False, "sai tài khoản / mật khẩu SMTP")


def test_webhook_urls_are_masked_when_config_is_read():
    from mateai.interfaces.http.secret_masking import _mask_secrets
    masked = _mask_secrets({"alert_teams": {"webhook_url": "https://prod/x?sig=SECRET"},
                            "alert_email": {"password": "p", "smtp_host": "smtp.x"}})
    assert "SECRET" not in json.dumps(masked) and masked["alert_email"]["smtp_host"] == "smtp.x"
    assert masked["alert_email"]["password"] != "p"


def test_portal_catalog_offers_every_channel_as_waiting(cfg):
    from mateai.application.enterprise.integrations import _alert_channel_catalog
    cat = _alert_channel_catalog()
    assert set(cat) == {"alert_rules", "alert_teams", "alert_email", "alert_outlook", "alert_slack", "alert_webhook"}
    teams = cat["alert_teams"]
    assert teams["configured"] is False and teams["config_schema"]["properties"]["webhook_url"]["format"] == "secret"
    assert teams["config_schema"]["required"] == ["webhook_url"]


def _client(role):
    import mateai.interfaces.http.routers.system as system
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_api_roles(cfg):
    assert _client("viewer").get("/api/v1/system/notifications").status_code == 403
    data = _client("manager").get("/api/v1/system/notifications").json()
    assert {c["id"] for c in data["channels"]} >= {"telegram", "alert_teams"}
    assert "webhook_url" not in json.dumps(data["channels"]).replace('"missing_fields": ["webhook_url"]', "")
    assert _client("manager").post("/api/v1/system/notifications/test", json={}).status_code == 403
    assert _client("admin").post("/api/v1/system/notifications/test", json={"channel": "zalo"}).status_code == 400
    res = _client("admin").post("/api/v1/system/notifications/test", json={"channel": "alert_teams"}).json()
    assert res["status"] == "no_channel"          # chưa kết nối -> nói thẳng, không "đã gửi"
