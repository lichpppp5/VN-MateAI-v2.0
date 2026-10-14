# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Giám sát hạ tầng qua Prometheus / Grafana, chạy với máy chủ giả đúng hình dạng API thật (tests/fake_enterprise.py):
chuẩn hoá cảnh báo / target / số đo, nguồn hỏng không làm mất nguồn khác, không bịa số liệu, chỉ GET, truy vấn PromQL có giới hạn,
cảnh báo nặng -> sự cố trong sổ tác vụ (không nhân bản, chỉ đóng khi nguồn còn liên lạc được).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_enterprise import FakeServer  # noqa: E402

from mateai.application.monitoring import infra_monitor as im  # noqa: E402
from mateai.application.operations import alert_dispatcher  # noqa: E402
from mateai.application.tasks import ledger  # noqa: E402
from mateai.config.loader import settings  # noqa: E402
from mateai.infrastructure.connectors import custom_registry as reg  # noqa: E402
from mateai.infrastructure.connectors import presets  # noqa: E402


@pytest.fixture(scope="module")
def srv():
    with FakeServer() as s:
        yield s


def preset(key, **over):
    return {**next(p for p in presets.PRESETS if p["key"] == key)["declaration"], **over}


@pytest.fixture
def mon(srv, tmp_path, monkeypatch):
    """Sổ nguồn tạm + Prometheus (không xác thực) + Grafana (token GOOD) trỏ vào máy chủ giả; trạng thái giả sạch."""
    monkeypatch.setattr(reg, "STORE_PATH", tmp_path / "data_sources.json")
    for attr in ("prom_alerts", "prom_targets", "graf_alerts", "graf_rules", "graf_dashboards"):
        setattr(srv.state, attr, [])
    srv.state.prom_vectors = {}
    srv.state.calls.clear()
    sent = []

    async def fake_dispatch(title, message="", **kw):
        sent.append({"title": title, **kw})
        return {"status": "ok"}
    monkeypatch.setattr(alert_dispatcher, "dispatch", fake_dispatch)
    im.reset()

    class Ctl:
        sent_alerts = sent
        server = srv

        def add_prom(self, **over):
            reg.upsert_source("prom-1", {**preset("prometheus"), "base_url": srv.url + "/prom", "id": "prom-1", **over})

        def add_grafana(self, token="GOOD", **over):
            reg.upsert_source("graf-1", {**preset("grafana"), "base_url": srv.url + "/graf", "id": "graf-1", "auth_value": token, **over})
    yield Ctl()
    im.reset()


def prom_alert(name="HighCPU", severity="critical", instance="srv-1:9100", state="firing"):
    return {"labels": {"alertname": name, "severity": severity, "instance": instance, "job": "node"},
            "annotations": {"summary": f"{name} trên {instance}"}, "state": state, "activeAt": "2026-10-07T09:00:00Z", "value": "97"}


def target(job, instance, health, err=""):
    return {"labels": {"job": job, "instance": instance}, "health": health, "lastError": err, "scrapeUrl": f"http://{instance}/metrics"}


def vec(instance, value):
    return [{"metric": {"instance": instance}, "value": [1700000000, str(value)]}]


# ── chuẩn hoá ───────────────────────────────────────────────────────────────

async def test_nothing_configured_is_reported_honestly(mon):
    snap = await im.snapshot(force=True)
    assert snap["configured"] is False and snap["summary"]["health"] == "UNKNOWN" and snap["alerts"] == []


async def test_presets_declare_every_query_the_monitor_needs():
    prom = preset("prometheus")["queries"]
    graf = preset("grafana")["queries"]
    assert set(im.PROM_Q.values()) <= set(prom) and set(im.GRAFANA_Q.values()) <= set(graf)
    assert preset("prometheus")["monitor_type"] == "prometheus" and preset("grafana")["monitor_type"] == "grafana"


async def test_prometheus_alerts_targets_and_metrics(mon):
    mon.add_prom()
    s = mon.server.state
    s.prom_alerts = [prom_alert(), prom_alert("DiskLow", "warning", "srv-2:9100"), prom_alert("Info", "none", "srv-3:9100", "pending")]
    s.prom_targets = [target("node", "srv-1:9100", "up"), target("node", "srv-2:9100", "down", "connection refused"), target("db", "db-1:9187", "up")]
    s.prom_vectors = {"node_cpu_seconds_total": vec("srv-1:9100", 97.26), "node_memory": vec("srv-1:9100", 55.0),
                      "node_filesystem": vec("srv-2:9100", 88.3)}
    snap = await im.snapshot(force=True)
    src = snap["sources"][0]
    assert src["reachable"] and src["type"] == "prometheus" and src["latency_ms"] is not None
    assert [(a["name"], a["severity"], a["state"]) for a in snap["alerts"]] == [("HighCPU", "critical", "firing"), ("DiskLow", "warning", "firing"),
                                                                              ("Info", "info", "pending")]
    assert src["targets"]["total"] == 3 and src["targets"]["down"] == 1 and src["targets"]["down_list"][0]["error"] == "connection refused"
    m = {(r["instance"], r["metric"]): r for r in src["metrics"]}
    assert m[("srv-1:9100", "cpu")]["value"] == 97.3 and m[("srv-1:9100", "cpu")]["level"] == "crit"
    assert m[("srv-1:9100", "memory")]["level"] == "ok" and m[("srv-2:9100", "disk")]["level"] == "warn"
    assert snap["summary"]["health"] == "CRITICAL" and snap["summary"]["targets_down"] == 1 and snap["summary"]["alerts_critical"] == 1


async def test_windows_exporter_is_the_fallback_and_missing_metrics_are_not_invented(mon):
    mon.add_prom()
    mon.server.state.prom_vectors = {"windows_cpu_time_total": vec("pc-01", 40)}
    src = (await im.snapshot(force=True))["sources"][0]
    assert [(r["instance"], r["metric"]) for r in src["metrics"]] == [("pc-01", "cpu")]
    assert "memory" in src["metrics_note"] and "disk" in src["metrics_note"]          # nói rõ thiếu gì, không điền 0


async def test_healthy_when_nothing_is_wrong(mon):
    mon.add_prom()
    mon.server.state.prom_targets = [target("node", "srv-1:9100", "up")]
    mon.server.state.prom_vectors = {"node_cpu_seconds_total": vec("srv-1:9100", 10)}
    assert (await im.snapshot(force=True))["summary"]["health"] == "HEALTHY"


async def test_grafana_alerts_rules_and_dashboards(mon):
    mon.add_grafana()
    s = mon.server.state
    s.graf_alerts = [
        {"fingerprint": "f1", "labels": {"alertname": "API 5xx", "severity": "critical", "grafana_folder": "Web"}, "annotations": {"summary": "5xx cao"},
         "status": {"state": "active"}, "startsAt": "2026-10-07T09:00:00Z"},
        {"fingerprint": "f2", "labels": {"alertname": "Tắt tiếng"}, "status": {"state": "suppressed"}}]
    s.graf_rules = [{"name": "a", "state": "firing", "health": "ok"}, {"name": "b", "state": "inactive", "health": "ok"},
                    {"name": "c", "state": "pending", "health": "error"}]
    s.graf_dashboards = [{"title": "Máy chủ", "url": "/d/abc/may-chu", "folderTitle": "Hạ tầng", "tags": ["node"]}]
    snap = await im.snapshot(force=True)
    src = snap["sources"][0]
    assert src["reachable"] and [a["name"] for a in snap["alerts"]] == ["API 5xx"]                       # đã bỏ cảnh báo bị tắt tiếng
    assert src["rules"] == {"total": 3, "firing": 1, "pending": 1, "normal": 1, "error": 1}
    assert src["dashboards"][0]["url"] == f"{mon.server.url}/graf/d/abc/may-chu" and src["dashboards"][0]["folder"] == "Hạ tầng"


async def test_one_broken_source_does_not_hide_the_others_and_secrets_stay_out(mon):
    mon.add_prom()
    mon.add_grafana(token="SAI-TOKEN-RAT-DAI")
    mon.server.state.prom_targets = [target("node", "srv-1:9100", "up")]
    snap = await im.snapshot(force=True)
    by = {s["id"]: s for s in snap["sources"]}
    assert by["prom-1"]["reachable"] is True and by["graf-1"]["reachable"] is False
    assert "401" in by["graf-1"]["error"] and "SAI-TOKEN" not in str(snap)
    assert snap["summary"]["health"] == "DEGRADED" and snap["summary"]["sources_reachable"] == 1


async def test_everything_unreachable_is_unknown_not_healthy(mon):
    mon.add_prom(base_url="http://127.0.0.1:1")
    snap = await im.snapshot(force=True)
    assert snap["sources"][0]["reachable"] is False and snap["summary"]["health"] == "UNKNOWN"


async def test_monitoring_only_ever_reads(mon):
    mon.add_prom()
    mon.add_grafana()
    await im.snapshot(force=True)
    await im.query_promql("up == 0")
    assert mon.server.state.calls and {c["method"] for c in mon.server.state.calls} == {"GET"}


async def test_disabled_monitoring_does_nothing(mon, monkeypatch):
    mon.add_prom()
    monkeypatch.setattr(settings.monitoring, "enabled", False)
    snap = await im.snapshot(force=True)
    assert snap["enabled"] is False and mon.server.state.calls == []


# ── PromQL cho AI ───────────────────────────────────────────────────────────

async def test_promql_query_returns_real_rows_and_refuses_bad_input(mon):
    mon.add_prom()
    mon.server.state.prom_vectors = {"up": vec("srv-1:9100", 0)}
    out = await im.query_promql('up{job="node"} == 0')
    assert out["source"] == "prom-1" and out["rows"][0]["metric"]["instance"] == "srv-1:9100" and out["rows"][0]["value"] == "0"
    for bad in ("", "x" * (im.MAX_PROMQL + 1), "up\x00"):
        with pytest.raises(im.MonitorError):
            await im.query_promql(bad)
    with pytest.raises(im.MonitorError, match="Chưa có nguồn Prometheus"):
        await im.query_promql("up", source_id="khong-co")


async def test_promql_without_any_prometheus_says_so(mon):
    with pytest.raises(im.MonitorError, match="Chưa có nguồn Prometheus"):
        await im.query_promql("up")


# ── sự cố từ cảnh báo ───────────────────────────────────────────────────────

async def test_critical_alert_opens_one_incident_and_notifies_once(mon):
    mon.add_prom()
    mon.server.state.prom_alerts = [prom_alert(), prom_alert("DiskLow", "warning", "srv-2:9100")]
    first = await im.poll_once()
    assert len(first["opened"]) == 1                                       # chỉ cảnh báo critical (mức tối thiểu mặc định)
    again = await im.poll_once()
    assert again["opened"] == []                                           # không nhân bản
    tasks = [t for t in ledger.list_tasks(kind="incident", limit=50) if str(t.get("source", "")).startswith("sentinel:infra:prom-1:HighCPU")]
    assert len(tasks) == 1 and tasks[0]["status"] == "ESCALATED" and "HighCPU" in tasks[0]["title"]
    assert [a["severity"] for a in mon.sent_alerts] == ["critical"] and mon.sent_alerts[0]["category"].startswith("sentinel:infra:")


async def test_incident_closes_when_the_alert_stops_firing(mon):
    mon.add_prom()
    mon.server.state.prom_alerts = [prom_alert("OldAlert")]
    await im.poll_once()
    mon.server.state.prom_alerts = []
    res = await im.poll_once()
    assert len(res["closed"]) == 1
    task = [t for t in ledger.list_tasks(kind="incident", limit=50) if "OldAlert" in str(t.get("source", ""))][0]
    assert task["status"] == "COMPLETED" and task["verification_status"] == "passed"
    assert any(a.get("resolved") for a in mon.sent_alerts)


async def test_losing_contact_never_closes_an_incident(mon):
    mon.add_prom()
    mon.server.state.prom_alerts = [prom_alert("KeepOpen")]
    await im.poll_once()
    reg.upsert_source("prom-1", {**preset("prometheus"), "id": "prom-1", "base_url": "http://127.0.0.1:1"})      # Prometheus "biến mất"
    im.reset()
    res = await im.poll_once()
    assert res["closed"] == []
    task = [t for t in ledger.list_tasks(kind="incident", limit=50) if "KeepOpen" in str(t.get("source", ""))][0]
    assert task["status"] == "ESCALATED"


async def test_incident_threshold_and_storm_cap_are_configurable(mon, monkeypatch):
    mon.add_prom()
    monkeypatch.setattr(settings.monitoring, "incident_min_severity", "warning")
    monkeypatch.setattr(settings.monitoring, "max_incidents_per_cycle", 2)
    mon.server.state.prom_alerts = [prom_alert(f"Storm{i}", "warning", f"srv-{i}:9100") for i in range(5)]
    res = await im.poll_once()
    assert len(res["opened"]) == 2                                          # bão cảnh báo không nhấn chìm hệ thống
    assert len((await im.poll_once())["opened"]) == 2                       # chu kỳ sau mở tiếp phần còn lại


async def test_poll_without_sources_is_a_noop(mon):
    assert await im.poll_once() == {"configured": False}
