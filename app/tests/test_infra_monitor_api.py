# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Giám sát hạ tầng: lớp HTTP (phân quyền, lỗi) và công cụ AI (chỉ đọc, L0, nói thẳng khi chưa cấu hình)."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_infra_monitor import mon, prom_alert, srv, target, vec  # noqa: E402,F401  (fixture dùng chung)

from mateai.application.security import security_guard as sg  # noqa: E402
from mateai.application.security.risk_engine import assess_risk  # noqa: E402
from mateai.interfaces.http.auth_dependencies import get_current_user  # noqa: E402
from mateai.interfaces.http.routers.monitoring import router  # noqa: E402
from skills import infra_monitor_tools as tools  # noqa: E402


def client(role="admin"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": "boss"}
    return TestClient(app)


def test_roles_and_overview(mon):
    mon.add_prom()
    mon.server.state.prom_alerts = [prom_alert()]
    assert client("viewer").get("/api/v1/monitoring/overview").status_code == 403
    body = client("manager").get("/api/v1/monitoring/overview?force=true").json()
    assert body["configured"] and body["summary"]["alerts_critical"] == 1 and body["alerts"][0]["name"] == "HighCPU"
    assert client("manager").post("/api/v1/monitoring/refresh").status_code == 403      # mở sự cố là việc của admin
    assert client("admin").post("/api/v1/monitoring/refresh").json()["configured"] is True


def test_promql_endpoint_is_read_only_and_validates(mon):
    mon.add_prom()
    mon.server.state.prom_vectors = {"up": vec("srv-1:9100", 1)}
    ok = client("manager").post("/api/v1/monitoring/query", json={"promql": "up"}).json()
    assert ok["rows"][0]["metric"]["instance"] == "srv-1:9100"
    r = client("manager").post("/api/v1/monitoring/query", json={"promql": ""})
    assert r.status_code == 422 and "PromQL" in r.json()["detail"]
    assert client("viewer").post("/api/v1/monitoring/query", json={"promql": "up"}).status_code == 403


async def test_tools_answer_with_real_data(mon):
    mon.add_prom()
    mon.add_grafana()
    s = mon.server.state
    s.prom_alerts = [prom_alert(), prom_alert("DiskLow", "warning", "srv-2:9100")]
    s.prom_targets = [target("node", "srv-2:9100", "down", "timeout")]
    s.prom_vectors = {"node_cpu_seconds_total": vec("srv-1:9100", 99), "up": vec("srv-2:9100", 0)}
    s.graf_dashboards = [{"title": "Máy chủ Linux", "url": "/d/x/linux", "folderTitle": "Hạ tầng"}, {"title": "Mạng", "url": "/d/y/net", "folderTitle": "Hạ tầng"}]
    st = await tools.get_infra_status()
    assert st["success"] and st["summary"]["health"] == "CRITICAL" and st["critical_alerts"][0]["name"] == "HighCPU"
    assert st["targets_down"][0]["instance"] == "srv-2:9100" and st["hot_metrics"][0]["value"] == 99.0
    only_crit = await tools.get_infra_alerts(severity="critical")
    assert [a["name"] for a in only_crit["alerts"]] == ["HighCPU"]
    q = await tools.query_prometheus("up == 0")
    assert q["success"] and q["rows"][0]["metric"]["instance"] == "srv-2:9100"
    bad = await tools.query_prometheus("")
    assert bad["success"] is False and "PromQL" in bad["error"]
    dash = await tools.list_grafana_dashboards(keyword="linux")
    assert dash["count"] == 1 and dash["dashboards"][0]["url"].endswith("/graf/d/x/linux")


async def test_tools_do_not_invent_data_when_nothing_is_configured(mon):
    for res in (await tools.get_infra_status(), await tools.get_infra_alerts(), await tools.query_prometheus("up"),
                await tools.list_grafana_dashboards()):
        assert res["success"] is False and "Chưa" in res["error"]


def test_tools_are_read_only_level_one_and_rbac_scoped():
    for name in ("get_infra_status", "get_infra_alerts", "query_prometheus", "list_grafana_dashboards"):
        assert assess_risk(name) == 1, name                     # chạy được cả khi bật kill switch
    allowed = lambda role, tool: sg.security_guard._evaluate_rbac(tool, role, "t")[0]
    assert allowed("it_support", "get_infra_status") and allowed("admin", "query_prometheus")
    assert not allowed("viewer", "get_infra_status") and not allowed("operator", "query_prometheus")
